from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

ROOT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = ROOT_DIR / "ui"
ASSETS_DIR = ROOT_DIR / "assets"
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from supabase.research_pipeline import (  # noqa: E402
    buildCandidateUniverse,
    classify_run,
    enrich_run,
    generateReportsForRun,
    runResearchPipeline,
    score_run,
)
from supabase.research_run_service import (  # noqa: E402
    deleteCandidateControl,
    prepareRunCandidates,
    updateCandidateControl,
)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set in the environment")
    return value


SUPABASE_URL = _required_env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = _required_env("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"
ALLOWED_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("steam_gtm.web")

app = Flask(__name__, static_folder=None)
app.config.update(
    DEBUG=False,
    TESTING=False,
    SECRET_KEY=_required_env("SESSION_SECRET"),
)


def proxy_request(method: str, path: str, params=None, json_body=None, extra_headers=None):
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    return requests.request(
        method,
        f"{SUPABASE_REST_URL}/{path}",
        params=params,
        json=json_body,
        headers=headers,
        timeout=int(os.getenv("SUPABASE_HTTP_TIMEOUT_SECONDS", "60")),
    )


def proxy_response(response: requests.Response) -> Response:
    if response.status_code >= 400:
        logger.error("Supabase request failed status=%s", response.status_code)
    return Response(response.content, response.status_code, content_type="application/json")


def ensure_steam_app(steam_appid: int, name: str | None = None):
    return proxy_request(
        "POST",
        "steam_apps",
        {"on_conflict": "appid"},
        {"appid": steam_appid, "name": name or f"Steam App {steam_appid}"},
        {"Prefer": "resolution=merge-duplicates"},
    )


@app.after_request
def add_security_and_cors_headers(response: Response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Vary"] = "Origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.errorhandler(Exception)
def unhandled_error(exc: Exception):
    if isinstance(exc, HTTPException):
        return exc
    logger.exception("Unhandled request error method=%s path=%s", request.method, request.path)
    return jsonify(error="Internal server error"), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify(status="ok")


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(UI_DIR, "candidate_controls.html")


@app.route("/candidate_controls.html", methods=["GET"])
def candidate_controls():
    return send_from_directory(UI_DIR, "candidate_controls.html")


@app.route("/assets/<path:filename>", methods=["GET"])
def assets(filename: str):
    return send_from_directory(ASSETS_DIR, filename)


@app.route("/api/<path:api_path>", methods=["OPTIONS"])
def options(api_path: str):
    return Response(status=204)


@app.route("/api/<path:api_path>", methods=["GET"])
def api_get(api_path: str):
    args = request.args
    if api_path == "organizations":
        return proxy_response(proxy_request("GET", "organizations", {"select": "*", "order": "name.asc"}))
    if api_path == "games":
        params = {"select": "*", "order": "updated_at.desc"}
        if args.get("organization_id"):
            params["organization_id"] = f"eq.{args['organization_id']}"
        return proxy_response(proxy_request("GET", "games", params))
    if api_path == "steam_apps":
        if not args.get("appid"):
            return jsonify(error="Missing appid query parameter"), 400
        params = {"select": "appid,name,steam_url,raw_appdetails_json,raw_page_signals_json", "appid": f"eq.{args['appid']}", "limit": "1"}
        return proxy_response(proxy_request("GET", "steam_apps", params))
    if api_path == "research_runs":
        run_id = args.get("run_id") or args.get("id")
        if run_id:
            return proxy_response(proxy_request("GET", "research_runs", {"select": "*", "id": f"eq.{run_id}"}))
        if not args.get("organization_id"):
            return jsonify(error="Missing run_id, id, or organization_id query parameter"), 400
        params = {"select": "*", "organization_id": f"eq.{args['organization_id']}", "order": "created_at.desc"}
        return proxy_response(proxy_request("GET", "research_runs", params))
    if api_path in {"run_events", "run_candidate_controls", "v_run_candidate_summary", "reports"}:
        if not args.get("run_id"):
            return jsonify(error="Missing run_id query parameter"), 400
        selects = {
            "run_events": "*",
            "run_candidate_controls": "*",
            "v_run_candidate_summary": "*",
            "reports": "id,run_id,report_type,title,content_md,generated_by,template_version,created_at",
        }
        params = {"select": selects[api_path], "run_id": f"eq.{args['run_id']}", "order": "created_at.desc"}
        if api_path == "run_events":
            params["limit"] = args.get("limit", "20")
            if args.get("event_type"):
                params["event_type"] = f"eq.{args['event_type']}"
        return proxy_response(proxy_request("GET", api_path, params))
    return jsonify(error="Unknown API path"), 404


PIPELINE_ACTIONS = {
    "prepare_run_candidates": prepareRunCandidates,
    "discover_and_enrich_candidates": enrich_run,
    "filter_score_shortlist_candidates": score_run,
    "run_research_pipeline": runResearchPipeline,
    "build_candidate_universe": buildCandidateUniverse,
    "generate_reports": generateReportsForRun,
}


@app.route("/api/<path:api_path>", methods=["POST"])
def api_post(api_path: str):
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(error="Invalid JSON body"), 400
    if api_path in PIPELINE_ACTIONS or api_path == "classify_candidates":
        run_id = payload.get("run_id")
        if not run_id:
            return jsonify(error="Missing run_id"), 400
        if api_path == "classify_candidates":
            return jsonify(classify_run(payload.get("rule_id") or "rule_based_v1", run_id))
        return jsonify(PIPELINE_ACTIONS[api_path](run_id))
    if api_path in {"games", "run_candidate_controls"} and payload.get("steam_appid") is not None:
        ensured = ensure_steam_app(int(payload["steam_appid"]), payload.get("title"))
        if ensured.status_code not in (200, 201):
            logger.error("Steam app upsert failed status=%s", ensured.status_code)
            return jsonify(error="Failed to ensure steam_app record"), 502
    if api_path in {"run_events", "games", "research_runs", "run_candidate_controls"}:
        headers = {"Prefer": "return=representation"} if api_path != "run_candidate_controls" else None
        return proxy_response(proxy_request("POST", api_path, json_body=payload, extra_headers=headers))
    return jsonify(error="Unknown API path"), 404


@app.route("/api/<path:api_path>", methods=["PATCH"])
def api_patch(api_path: str):
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(error="Invalid JSON body"), 400
    item_id = payload.get("id")
    updates = payload.get("updates") or {key: value for key, value in payload.items() if key != "id"}
    if not item_id or not updates:
        return jsonify(error="Missing id or update fields"), 400
    if api_path == "run_candidates":
        response = proxy_request("PATCH", "run_candidates", {"id": f"eq.{item_id}"}, updates, {"Prefer": "return=representation"})
        return proxy_response(response)
    if api_path == "run_candidate_controls":
        return jsonify(updateCandidateControl(item_id, updates))
    return jsonify(error="Unknown API path"), 404


@app.route("/api/<path:api_path>", methods=["DELETE"])
def api_delete(api_path: str):
    if api_path != "run_candidate_controls":
        return jsonify(error="Unknown API path"), 404
    if not request.args.get("id"):
        return jsonify(error="Missing control id query parameter"), 400
    return jsonify(deleteCandidateControl(request.args["id"]))


logger.info("Steam GTM application initialized allowed_origins=%d debug=false", len(ALLOWED_ORIGINS))
