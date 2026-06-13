# fireworks2api

Fireworks API proxy with OpenAI-compatible public routes, Fireworks-native adapters, sticky routing, failover/cooldown, request logs, and an Admin dashboard.

## Fast setup

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
copy .env.example .env
docker compose up -d
```

Then open:

```text
http://localhost:8000/admin/login
```

Use `ADMIN_TOKEN`, sign in, and add Fireworks API keys from Admin. For normal interactive use, keep Fireworks keys out of `.env` and add them in Admin instead.

The default Compose file pulls the published amd64 image:

```text
kafuuchino520/fireworks2api:latest
```

By default, persistent SQLite data is stored under the project `./data`
directory via Docker bind mount. The container entrypoint fixes the mounted
directory ownership before starting the app, so first startup should not require
manual `chown` on Linux servers.

For local image development, use the dev override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Public route list

Public inference routes are intentionally versioned under `/v1/...`:

- `GET /v1/models`
- `GET /v1/models/{model}`
- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /v1/responses`
- `GET /v1/responses`
- `GET /v1/responses/{id}`
- `DELETE /v1/responses/{id}`
- `POST /v1/embeddings`
- `POST /v1/rerank`
- `POST /v1/messages`

## Smoke commands

Use the real smoke script:

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

Advanced smoke example:

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
$env:FIREWORKS2API_SMOKE_STREAM="true"
$env:FIREWORKS2API_SMOKE_ERRORS="true"
$env:FIREWORKS2API_SMOKE_ADVANCED="true"
$env:FIREWORKS2API_SMOKE_TOOLS="true"
$env:FIREWORKS2API_SMOKE_MCP="true"
$env:FIREWORKS2API_MCP_SERVER_URL="https://mcp.deepwiki.com/mcp"
$env:FIREWORKS2API_SMOKE_IMAGES="true"
$env:FIREWORKS2API_VISION_MODEL="kimi-k2.6"
$env:FIREWORKS2API_IMAGE_URL="https://upload.wikimedia.org/wikipedia/commons/3/3f/Fronalpstock_big.jpg"
$env:FIREWORKS2API_SMOKE_REASONING="true"
$env:FIREWORKS2API_REASONING_MODEL="kimi-k2.6"
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

## Trace command

Enable route tracing and query the admin debug endpoint:

```powershell
$env:TRANSFORM_DEBUG_ENABLED="true"
$env:TRANSFORM_DEBUG_RETENTION="50"
$env:ADMIN_TOKEN="admin-local"
curl "http://127.0.0.1:8000/admin/transform-debug?has_route_trace=true" -H "Authorization: Bearer admin-local"
```

Current checkpoint notes:

- embeddings/rerank live smoke are intentionally skipped for now
- official SDK smoke has been verified with `openai 2.35.1` and `anthropic 0.100.0` against local `127.0.0.1:8000`
- no all-Fireworks model guarantee or production MCP SLA is claimed

## Official references

- OpenAI API docs: `https://platform.openai.com/docs`
- Anthropic API docs: `https://docs.anthropic.com/`
- Fireworks docs entrypoint: `https://docs.fireworks.ai/llms.txt`

## Authentication

- OpenAI-compatible `/v1/*` requests use `Authorization: Bearer ...`.
- Anthropic-compatible `/v1/messages` requests use `x-api-key` or `Authorization: Bearer ...` and require `anthropic-version`.
- `/admin/*` and Admin UI actions use `ADMIN_TOKEN`.
- If `PROXY_API_KEYS` is empty, proxy auth is disabled. This is not recommended for exposed deployments.
- If `ADMIN_TOKEN` is empty, admin read endpoints work but write endpoints are disabled.

## Model routing

Default mappings include versioned examples such as:

- `kimi-k2.6 -> accounts/fireworks/models/kimi-k2p6`
- `kimi-k2.6-turbo -> accounts/fireworks/routers/kimi-k2p6-turbo`
- `glm-5.1-fast -> accounts/fireworks/routers/glm-5p1-fast`

Admin model management now uses the built-in Fireworks official model registry as the primary catalog. The registry lives in `app/control/fireworks_model_registry.py` and is used for browse/discovery in Admin.

Current model-management rules:

- `/admin/fireworks/models` defaults to `source=official` and does not require a Fireworks key.
- `source=inference` and `source=account` are advanced discovery/import helpers, not the primary metadata source.
- Manual add is explicit `alias -> upstream_model/router` mapping.
- `/admin/models/import` requires explicit `alias` / `aliases` and no longer guesses basename or suggested aliases.
- Public `GET /v1/models` remains limited to local enabled mappings only.

Priority is supported for chat completions directly, and simple non-stream `/v1/responses` text requests with `service_tier=priority` now fall back cross-endpoint through Fireworks Chat Completions priority, synthesize a Responses-shaped payload with id `resp_fallback_chatcmpl-*`, and do not create lifecycle bindings. Complex/stream/tool/MCP/image/reasoning/lifecycle priority Responses requests are rejected. Fireworks Responses itself still does not document a priority surface; this fallback uses the Chat endpoint.

## Sticky routing

Stable affinity is derived in this order:

1. body `prompt_cache_key`
2. body `user`
3. `x-session-affinity`
4. `x-multi-turn-session-id`
5. `session_id`
6. `conversation_id`
7. fallback `model + client identity`

The raw stable key is not logged. Logs store an HMAC prefix only.

## Security notes

- Full Fireworks API keys are never returned by admin APIs or shown in the Admin UI.
- Request logs do not store full prompt, messages, input, Authorization, or upstream request body.
- SQLite stores Fireworks keys in the `api_key_ciphertext` column. In this MVP it may be plaintext; protect `./data` permissions.
- Do not expose this service publicly without `PROXY_API_KEYS`, `ADMIN_TOKEN`, and HTTPS/reverse proxy controls.
- `UPSTREAM_BASE_URL` is configured by env; WebUI does not allow arbitrary edits to reduce SSRF risk.

## Configuration model

This project follows grok2api's deployment UX boundary, but not its Grok account reverse-proxy backend:

- `.env` is for startup/deployment settings such as ports, local data path, admin token, client proxy token, and stable affinity secret.
- Fireworks API keys are normally added in the Admin UI and stored in SQLite under `DATA_DIR`; until the Admin frontend is finished, add them through the Admin API.
- `FIREWORKS_API_KEYS` and `FIREWORKS_API_KEYS_JSON` are optional bootstrap variables for Docker/automation only.
- Public request/response compatibility follows official OpenAI / Anthropic shapes where implemented; internal upstream behavior follows Fireworks official inference docs.
- Model catalog discovery is now registry-first through the built-in official Fireworks registry; discovery endpoints are helpers, not the authoritative source of model metadata.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

Live Fireworks smoke uses `scripts/fireworks_inference_smoke.py` and must be gated by env vars so keys do not print.
