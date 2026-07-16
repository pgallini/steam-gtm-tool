# DigitalOcean App Platform deployment

## Application entry point

The WSGI entry point is `ui.app:app` in `ui/app.py`. Flask serves the UI at `/` and
`/candidate_controls.html`, repository assets at `/assets/...`, same-origin backend
routes at `/api/...`, and the health check at `/health`. Gunicorn is the production
HTTP server. Flask debug mode is explicitly disabled.

## App Platform configuration

Create one **Web Service** from this repository with these exact settings:

- Source directory: `/` (repository root)
- Build command: `pip install --no-cache-dir -r requirements.txt`
- Run command: `gunicorn --worker-tmp-dir /dev/shm --bind 0.0.0.0:$PORT --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-300} --access-logfile - --error-logfile - ui.app:app`
- Runtime: Python `3.12.13` (declared in `runtime.txt`)
- HTTP port: use DigitalOcean's injected `PORT`; do not set a fixed port
- Health check path: `/health`

The longer timeout accommodates the existing synchronous research pipeline. For
high traffic or pipelines longer than five minutes, move pipeline work to an App
Platform worker/queue rather than increasing web workers indefinitely.

## Environment variables

Set these as encrypted secrets unless marked otherwise:

| Variable | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `SUPABASE_URL` | Yes | No | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Yes | Server-only Supabase REST access |
| `SESSION_SECRET` | Yes | Yes | Flask signing secret; generate a long random value |
| `OPENAI_API_KEY` | For LLM stages | Yes | Server-only OpenAI access |
| `STEAM_API_KEY` | For Steam catalog sync | Yes | Server-only Steam Web API access |
| `ALLOWED_ORIGINS` | For cross-origin clients | No | Comma-separated exact HTTPS origins; same-origin UI needs no entry |
| `SUPABASE_HTTP_TIMEOUT_SECONDS` | No | No | Supabase request timeout; default `60` |
| `LOG_LEVEL` | No | No | Application log level; default `INFO` |
| `WEB_CONCURRENCY` | No | No | Gunicorn worker count; default `2` |
| `GUNICORN_THREADS` | No | No | Threads per worker; default `4` |
| `GUNICORN_TIMEOUT` | No | No | Worker timeout; default `300` |

`DATABASE_PASSWORD` is not used by this application because it accesses Supabase
through HTTPS rather than a direct PostgreSQL connection. Do not add it unless a
future server-only database client requires it. `SUPABASE_ANON_KEY` is also not
needed. Never define any secret as a build-time argument or a public/client
environment variable.

`ALLOWED_ORIGINS` is only needed when a separate frontend origin calls this app.
For example: `https://admin.example.com,https://preview.example.com`. Do not use
`*` for this administrative application. The bundled frontend uses relative
`/api/...` URLs and therefore works through the deployed origin without CORS.

## Storage and security notes

Application records, pipeline state, cached Steam data, reviews, and reports are
stored in Supabase. Runtime logs go to stdout/stderr for App Platform log capture;
the deployed service does not require persistent local disk. Repository JSON and
HTML files are static reference/input assets only. CLI scripts may still create
local export files when run manually, but the web application does not call them.

The Supabase service-role key, Steam API key, OpenAI API key, database password,
and session secret are read only by Python server code. No configuration endpoint
returns them, and the browser only calls the app's same-origin API proxy. Error
responses are generic for unexpected failures, while detailed tracebacks remain
in server logs. Logs must never include request headers or environment dumps.

## Smoke-test checklist

After deployment, replace `$APP_URL` with the App Platform URL and verify:

- [ ] `curl -fsS "$APP_URL/health"` returns `{"status":"ok"}` with HTTP 200.
- [ ] `curl -fsSI "$APP_URL/"` returns HTTP 200 and the UI loads in a browser.
- [ ] Browser developer tools show API requests to `$APP_URL/api/...`, not localhost.
- [ ] An authorized API read succeeds and the UI can create/load a research run.
- [ ] A pipeline action persists its events/results in Supabase.
- [ ] A restart/redeploy retains application data.
- [ ] Logs show Gunicorn startup, access records, and sanitized server errors.
- [ ] `curl -sS -H 'Origin: https://allowed.example' -I "$APP_URL/health"` returns `Access-Control-Allow-Origin` only when that exact origin is configured.
- [ ] An unlisted origin receives no `Access-Control-Allow-Origin` header.
- [ ] Responses and frontend source contain none of the configured secret values.
- [ ] Flask debug pages and interactive debugger are unavailable.
