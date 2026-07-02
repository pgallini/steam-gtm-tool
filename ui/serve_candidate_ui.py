from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from supabase.research_run_service import prepareRunCandidates, updateCandidateControl, deleteCandidateControl
from supabase.research_pipeline import buildCandidateUniverse, generateReportsForRun, runResearchPipeline

load_dotenv(dotenv_path=Path('.env'))

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

if SUPABASE_URL is None or SUPABASE_ANON_KEY is None or SUPABASE_SERVICE_ROLE_KEY is None:
    raise RuntimeError('SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY must be set in .env')

SUPABASE_REST_URL = SUPABASE_URL.rstrip('/') + '/rest/v1'


def proxy_request(method: str, path: str, params: dict[str, str] | None = None, json_body: object | None = None, extra_headers: dict[str, str] | None = None) -> requests.Response:
    headers = {
        'apikey': SUPABASE_SERVICE_ROLE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_ROLE_KEY}',
        'Content-Type': 'application/json',
    }
    if extra_headers:
        headers.update(extra_headers)
    return requests.request(method, f'{SUPABASE_REST_URL}/{path}', params=params, json=json_body, headers=headers)


def ensure_steam_app(steam_appid: int, name: str | None = None) -> requests.Response:
    if steam_appid is None:
        raise ValueError('steam_appid is required')

    app_name = name or f'Steam App {steam_appid}'
    payload = {
        'appid': steam_appid,
        'name': app_name,
    }
    return proxy_request(
        'POST',
        'steam_apps',
        params={'on_conflict': 'appid'},
        json_body=payload,
        extra_headers={'Prefer': 'resolution=merge-duplicates'},
    )


class CandidateUIHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith('/assets/'):
            assets_dir = (Path(__file__).resolve().parent.parent / 'assets').resolve()
            asset_path = (assets_dir / parsed.path.removeprefix('/assets/')).resolve()
            if not asset_path.is_relative_to(assets_dir) or not asset_path.is_file():
                self.send_error(404, 'Asset not found')
                return
            self.send_response(200)
            content_type, _ = mimetypes.guess_type(asset_path.name)
            self.send_header('Content-Type', content_type or 'application/octet-stream')
            self.send_header('Content-Length', str(asset_path.stat().st_size))
            self.end_headers()
            with asset_path.open('rb') as asset_file:
                self.wfile.write(asset_file.read())
            return
        if parsed.path == '/config':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'url': SUPABASE_URL, 'key': SUPABASE_ANON_KEY}).encode('utf-8'))
            return
        if parsed.path.startswith('/api/'):
            self.handle_api_request(parsed)
            return
        super().do_GET()

    def handle_api_request(self, parsed):
        api_path = parsed.path[len('/api/'):]
        query = parse_qs(parsed.query)
        if api_path == 'organizations':
            response = proxy_request('GET', 'organizations', {'select': '*', 'order': 'name.asc'})
            self.respond_proxy(response)
            return
        if api_path == 'games':
            organization_id = query.get('organization_id', [''])[0]
            params = {'select': '*', 'order': 'updated_at.desc'}
            if organization_id:
                params['organization_id'] = f'eq.{organization_id}'
            response = proxy_request('GET', 'games', params)
            self.respond_proxy(response)
            return
        if api_path == 'steam_apps':
            appid = query.get('appid', [''])[0]
            if not appid:
                self.send_error(400, 'Missing appid query parameter')
                return
            response = proxy_request('GET', 'steam_apps', {'select': 'appid,name,steam_url,raw_appdetails_json,raw_page_signals_json', 'appid': f'eq.{appid}', 'limit': '1'})
            self.respond_proxy(response)
            return
        if api_path == 'research_runs':
            run_id = query.get('run_id', query.get('id', ['']))[0]
            if run_id:
                response = proxy_request('GET', 'research_runs', {'select': '*', 'id': f'eq.{run_id}'})
                self.respond_proxy(response)
                return
            organization_id = query.get('organization_id', [''])[0]
            if not organization_id:
                self.send_error(400, 'Missing run_id, id, or organization_id query parameter')
                return
            response = proxy_request('GET', 'research_runs', {'select': '*', 'organization_id': f'eq.{organization_id}', 'order': 'created_at.desc'})
            self.respond_proxy(response)
            return
        if api_path == 'run_events':
            run_id = query.get('run_id', [''])[0]
            if not run_id:
                self.send_error(400, 'Missing run_id query parameter')
                return
            response = proxy_request('GET', 'run_events', {'select': '*', 'run_id': f'eq.{run_id}', 'order': 'created_at.desc', 'limit': query.get('limit', ['20'])[0]})
            self.respond_proxy(response)
            return
        if api_path == 'run_candidate_controls':
            run_id = query.get('run_id', [''])[0]
            if not run_id:
                self.send_error(400, 'Missing run_id query parameter')
                return
            response = proxy_request('GET', 'run_candidate_controls', {'select': '*', 'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
            self.respond_proxy(response)
            return
        if api_path == 'v_run_candidate_summary':
            run_id = query.get('run_id', [''])[0]
            if not run_id:
                self.send_error(400, 'Missing run_id query parameter')
                return
            response = proxy_request('GET', 'v_run_candidate_summary', {'select': '*', 'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
            self.respond_proxy(response)
            return
        if api_path == 'reports':
            run_id = query.get('run_id', [''])[0]
            if not run_id:
                self.send_error(400, 'Missing run_id query parameter')
                return
            response = proxy_request('GET', 'reports', {'select': 'id,run_id,report_type,title,content_md,generated_by,template_version,created_at', 'run_id': f'eq.{run_id}', 'order': 'created_at.desc'})
            self.respond_proxy(response)
            return
        self.send_error(404, 'Unknown API path')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/prepare_run_candidates':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            run_id = payload.get('run_id')
            if not run_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing run_id'}).encode('utf-8'))
                return
            try:
                result = prepareRunCandidates(run_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as exc:
                print(f'Prepare run candidates error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        if parsed.path == '/api/run_research_pipeline':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            run_id = payload.get('run_id')
            if not run_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing run_id'}).encode('utf-8'))
                return
            try:
                result = runResearchPipeline(run_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as exc:
                print(f'Run research pipeline error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        if parsed.path == '/api/build_candidate_universe':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            run_id = payload.get('run_id')
            if not run_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing run_id'}).encode('utf-8'))
                return
            try:
                result = buildCandidateUniverse(run_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as exc:
                print(f'Build candidate universe error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        if parsed.path == '/api/generate_reports':
            length = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            run_id = payload.get('run_id')
            if not run_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing run_id'}).encode('utf-8'))
                return
            try:
                result = generateReportsForRun(run_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as exc:
                print(f'Generate reports error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        if parsed.path == '/api/run_events':
            try:
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            response = proxy_request('POST', 'run_events', json_body=payload, extra_headers={'Prefer': 'return=representation'})
            self.respond_proxy(response)
            return
        if parsed.path == '/api/games':
            try:
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            steam_appid = payload.get('steam_appid')
            if steam_appid:
                steam_app_response = ensure_steam_app(int(steam_appid), payload.get('title'))
                if steam_app_response.status_code not in (200, 201):
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Failed to ensure steam_app record', 'details': steam_app_response.text}).encode('utf-8'))
                    return
            response = proxy_request('POST', 'games', json_body=payload, extra_headers={'Prefer': 'return=representation'})
            self.respond_proxy(response)
            return
        if parsed.path == '/api/research_runs':
            try:
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            response = proxy_request('POST', 'research_runs', json_body=payload, extra_headers={'Prefer': 'return=representation'})
            self.respond_proxy(response)
            return
        if parsed.path == '/api/run_candidate_controls':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length).decode('utf-8')
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            steam_appid = payload.get('steam_appid')
            if steam_appid is not None:
                steam_app_response = ensure_steam_app(int(steam_appid), payload.get('title'))
                if steam_app_response.status_code not in (200, 201):
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Failed to ensure steam_app record', 'details': steam_app_response.text}).encode('utf-8'))
                    return
            response = proxy_request('POST', 'run_candidate_controls', json_body=payload)
            self.respond_proxy(response)
            return
        self.send_error(404, 'Unknown API path')

    def do_PATCH(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/run_candidates':
            try:
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            candidate_id = payload.get('id')
            updates = payload.get('updates') or {k: v for k, v in payload.items() if k != 'id'}
            if not candidate_id or not updates:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing candidate id or update fields'}).encode('utf-8'))
                return
            response = proxy_request('PATCH', 'run_candidates', {'id': f'eq.{candidate_id}'}, json_body=updates, extra_headers={'Prefer': 'return=representation'})
            self.respond_proxy(response)
            return
        if parsed.path == '/api/run_candidate_controls':
            try:
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except json.JSONDecodeError:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Invalid JSON body'}).encode('utf-8'))
                return
            control_id = payload.get('id')
            updates = payload.get('updates') or {k: v for k, v in payload.items() if k != 'id'}
            if not control_id or not updates:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing control id or update fields'}).encode('utf-8'))
                return
            try:
                updated = updateCandidateControl(control_id, updates)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(updated).encode('utf-8'))
            except Exception as exc:
                print(f'Update run candidate control error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        self.send_error(404, 'Unknown API path')

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/run_candidate_controls':
            query = parse_qs(parsed.query)
            control_id = query.get('id', [''])[0]
            if not control_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Missing control id query parameter'}).encode('utf-8'))
                return
            try:
                result = deleteCandidateControl(control_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as exc:
                print(f'Delete run candidate control error: {exc}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(exc)}).encode('utf-8'))
            return
        self.send_error(404, 'Unknown API path')

    def respond_proxy(self, response: requests.Response):
        if response.status_code >= 400:
            print(f'Proxy error: {response.status_code} {response.text}')
        self.send_response(response.status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(response.content)


def main() -> None:
    port = 8000
    ui_dir = Path(__file__).parent
    os.chdir(ui_dir)
    server = HTTPServer(('127.0.0.1', port), CandidateUIHandler)
    print(f'Serving candidate control UI at http://127.0.0.1:{port}/candidate_controls.html')
    server.serve_forever()


if __name__ == '__main__':
    main()
