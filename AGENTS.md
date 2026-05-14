# AGENTS.md

Guidance for coding agents working on `fireworks2api`.

## Project identity

`fireworks2api` is a lightweight Fireworks AI proxy and Admin dashboard:

- OpenAI-compatible proxy for Fireworks official inference APIs.
- Fireworks API key pool manager.
- Sticky routing + failover/cooldown for prompt cache friendliness.
- Admin-only static frontend for keys, model mappings, request/cache stats.

It is **not**:

- A Grok/xAI reverse proxy.
- A browser/session/cookie/clearance proxy.
- A full Fireworks training/deployment/account administration platform.
- A clone of `grok2api` backend logic.

## Repository structure

Current high-level layout:

```text
app/
  platform/   # config, auth, logging, redaction, bootstrap, SQLite storage
  control/    # repository and model resolver
  dataplane/  # Fireworks client/proxy, headers, stream proxy, routing, failover, usage
  products/   # OpenAI-compatible routes, Admin API, Web/Admin static routes, health
  statics/    # Admin-only frontend assets

tests/        # pytest suite
docs/         # committed docs notes only; local Fireworks cache is ignored
grok2api/     # local reference repo, gitignored
```

No legacy `app/api`, `app/core`, `app/storage`, `app/upstream`, `app/config.py`, `app/security.py`, or `app/logging_utils.py` compatibility shims should be reintroduced.

## Fireworks docs workflow

Fireworks backend behavior must follow official Fireworks documentation, not assumptions from OpenAI or `grok2api`.

Primary docs entrypoint:

```text
https://docs.fireworks.ai/llms.txt
```

Local docs cache:

```text
docs/fireworks/     # gitignored local cache
docs/README.md      # committed note describing the cache
```

Before implementing or changing Fireworks API behavior:

1. Check `docs/fireworks/llms.txt` if present.
2. If the relevant markdown is missing, fetch it from `docs.fireworks.ai` and store it under `docs/fireworks/` for local reference.
3. Prefer Fireworks docs and live smoke results over generic OpenAI expectations.
4. Keep `docs/fireworks/` out of git.

Important Fireworks docs/features already identified as relevant:

- OpenAI compatibility.
- Chat Completions.
- Responses API.
- Prompt caching.
- Serverless Priority and Fast.
- Inference error codes.
- Serverless rate limits.
- Account/API key/service account docs.
- Quota/billing metrics docs.
- Model/router discovery docs.
- Embeddings/rerank docs.
- Anthropic compatibility docs.

## `grok2api` reference boundary

`grok2api/` may exist locally as a gitignored reference directory.

Use it for:

- Admin UX patterns.
- Static Admin frontend organization.
- Deployment/.env/README experience.
- Broad `platform / control / dataplane / products / statics` structure ideas.

Do **not** copy or port:

- Grok/xAI reverse protocol logic.
- Account browser/session/cookie handling.
- Proxy clearance / FlareSolverr logic.
- Grok WebUI chat/chatkit/masonry features.
- Grok-specific account scheduler/state machine complexity.

## Currently implemented API surface

OpenAI-compatible/public proxy routes:

```text
GET  /v1/models
GET  /v1/models/{model}

POST /v1/chat/completions
POST /v1/completions
POST /v1/responses
GET  /v1/responses
GET  /v1/responses/{response_id}
DELETE /v1/responses/{response_id}

POST /v1/embeddings
POST /v1/rerank

POST /v1/messages
```

No unversioned public aliases are maintained (`/models`, `/responses`, `/messages`, `/rerank` return 404).

Implemented proxy behavior:

- Local model mappings.
- Chat Completions non-stream and stream passthrough.
- Responses non-stream and stream passthrough.
- Fireworks field passthrough except explicit model alias mapping and Chat priority injection.
- Fast router aliases via model mappings.
- Chat `service_tier=priority`.
- Responses `service_tier=priority` simple non-stream text fallback through Chat Completions priority, returning a synthesized Responses-shaped payload with `resp_fallback_chatcmpl-*`; complex/stream/tool/MCP/image/reasoning/lifecycle priority Responses requests are rejected.
- Prompt-cache-related headers/body fields passthrough.
- Stable key extraction and rendezvous sticky routing.
- Failover/cooldown based on Fireworks error classes.
- Usage and `cached_tokens` normalization from body, headers, and stream terminal chunks.

Admin API routes:

```text
GET    /admin/overview
GET    /admin/keys
POST   /admin/keys
POST   /admin/keys/bulk
PATCH  /admin/keys/{name}
DELETE /admin/keys/{name}
POST   /admin/keys/{name}/enable
POST   /admin/keys/{name}/disable
POST   /admin/keys/{name}/clear-cooldown

GET    /admin/models
POST   /admin/models
PATCH  /admin/models/{alias}
DELETE /admin/models/{alias}

GET    /admin/requests
```

Admin frontend routes, when `ENABLE_ADMIN_STATIC=true`:

```text
/
/admin
/admin/login
/admin/account
/admin/config
/admin/cache
/static/...
/favicon.ico
```

## Current live smoke status

Live Fireworks smoke with user-provided keys has passed for:

- `/v1/models` and `/models` local mapping responses.
- `/v1/models/kimi-k2.6`.
- Chat Completions non-stream and stream with `kimi-k2.6`.
- Responses non-stream and stream with `kimi-k2.6`.
- Chat `service_tier=priority`.
- Fast alias `kimi-k2.6-turbo`.
- Request logs capturing usage, cached tokens, service tier, upstream request id, key fingerprint, stable key hash.
- 400 validation errors not putting keys into cooldown.

Observed Fireworks stream behavior:

- Chat streaming final chunk can include `usage.prompt_tokens_details.cached_tokens`.
- Responses streaming terminal event can be `response.incomplete` and include `response.usage`.

## Security and privacy rules

Never expose or log full Fireworks API keys.

Admin key handling:

- Users paste Fireworks API keys in Admin.
- Frontend displays `masked_key`, e.g. `fw_3pb****Rx6x21`.
- `fingerprint = sha256(key)[:12]` is backend/internal for de-duplication, routing logs, and request logs.
- Fingerprint may be returned by Admin API but should not be the primary UI identity.

Request logging must not store:

- Full prompts.
- Full request bodies.
- Full Authorization headers.
- Full Fireworks keys.

Request logging may store:

- Metadata.
- Model alias/upstream model.
- Key fingerprint.
- Stable key hash.
- Usage/cached token counts.
- Latency/status/error type/upstream request id.

## Configuration conventions

Primary user setup should be grok2api-like:

- `.env` contains startup/admin/proxy configuration.
- Normal users add Fireworks API keys in Admin UI.
- `FIREWORKS_API_KEYS` / `FIREWORKS_API_KEYS_JSON` are bootstrap/dev/automation options only.

Important settings:

```text
ADMIN_TOKEN
PROXY_API_KEYS
AFFINITY_HASH_SECRET
LOG_HASH_SECRET
UPSTREAM_BASE_URL
ENABLE_ADMIN_STATIC
SYNC_ENV_KEYS_ON_STARTUP
```

`AFFINITY_HASH_SECRET` should be stable across restarts for cache affinity. Do not rely on a random logging secret for outbound affinity.

## Validation commands

Use the project virtualenv. Do not use bare `python` on this machine.

Install/update test deps:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[test]"
```

Run tests:

```powershell
.venv\Scripts\python.exe -m pytest
```

Compile check:

```powershell
.venv\Scripts\python.exe -m compileall app tests
```

Run local server manually when browser testing is needed:

```powershell
$env:ENABLE_ADMIN_STATIC="true"
$env:ADMIN_TOKEN="admin-local"
$env:PROXY_API_KEYS="sk-local-dev"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Do not start long-running `uvicorn` via a blocking shell tool unless explicitly requested. Prefer asking the user to run it, then use Playwright MCP for browser testing.

## Known backlog from Fireworks docs

High-value next items:

- `GET /admin/cache/analysis` for cache/sticky-routing analysis by model/key/stable key.
- Sticky routing consistency tests.
- Fireworks account/API key/service account introspection from official docs.
- Fireworks quota/rate-limit dashboard.
- Fireworks model/router discovery and import into local model mappings.
- Responses lifecycle proxy: `GET /v1/responses`, `GET /v1/responses/{id}`, `DELETE /v1/responses/{id}`.
- Embeddings proxy: `POST /v1/embeddings`.
- Rerank proxy: `POST /v1/rerank` or `/rerank`.
- Completions proxy: `POST /v1/completions`.
- Anthropic-compatible Messages proxy.

Lower priority / generally out of scope:

- Fine-tuning/RFT/DPO/training jobs.
- Dataset lifecycle.
- Model upload/prepare/custom model lifecycle.
- Image/video/audio product features.
- Fireworks enterprise SSO/user/identity-provider administration.
- Deployment write operations or scaling controls.

## Subagent workflow notes

- Empty fixer/designer task result should be treated as failure; rerun with narrower scope or do the work directly.
- Use explorer for broad codebase mapping or docs classification.
- Use oracle for backend architecture/code review when tradeoffs are non-trivial.
- Avoid delegating tasks that require reading outside the project unless permission has been granted.
