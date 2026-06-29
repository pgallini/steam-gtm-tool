from __future__ import annotations

import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from supabase.research_run_service import prepareRunCandidates

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
        if api_path == 'research_runs':
            run_id = query.get('run_id', query.get('id', ['']))[0]
            if not run_id:
                self.send_error(400, 'Missing run_id or id query parameter')
                return
            response = proxy_request('GET', 'research_runs', {'select': '*', 'id': f'eq.{run_id}'})
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
        if parsed.path == '/api/run_candidate_controls':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            payload = json.loads(body)
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
        super().do_POST()

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
