# Release checkpoint

This document captures the current release checkpoint for the inference surface.

## Verified status

- Public inference routes are documented as versioned `/v1/...` routes only.
- The route flow is documented as: public route -> endpoint adapter -> Fireworks-native route -> Fireworks transport.
- The real smoke script is `scripts/fireworks_inference_smoke.py`.
- Trace/debug guidance is documented in `docs/route-transform-trace.md`.
- Public contract and adapter mapping are documented in `docs/public-api.md` and `docs/public-to-fireworks-adapter-matrix.md`.
- Roadmap framing is documented in `docs/public-api-roadmap.md`.
- Model-management boundaries are documented in `docs/model-management.md`; the Admin catalog is registry-first and public `/v1/models` stays local-only.
- P0 public adapter alignment is complete; embeddings/rerank remain intentionally skipped for now per user.
- Official SDK packages are installed in the venv (`openai 2.35.1`, `anthropic 0.100.0`) and real SDK live smoke passed against local `127.0.0.1:8000` for OpenAI chat completions, OpenAI responses, Anthropic messages, plus SDK stream checks.
- Live inference smoke passed with `kimi-k2.6` for chat/completions/responses/messages, streams, Responses tools continuation, Responses MCP/SSE via `https://mcp.deepwiki.com/mcp`, bounded MCP, Anthropic live tool_use/tool_result round-trip returning `42`, Chat/Responses/Anthropic images using a small Wikimedia thumbnail URL, and reasoning. Latest verified priority behavior: `/v1/responses` with `service_tier='priority'` and simple non-stream text defaults to cross-endpoint fallback through Fireworks Chat Completions priority, returns a synthesized Responses-shaped object with id `resp_fallback_chatcmpl-*`, and does not create lifecycle bindings; complex/stream/tool/MCP/image/reasoning/lifecycle priority requests are rejected.
- `live_capability_smoke.py` artifact: inference returncode `0`, SDK returncode `0`, current_aliases chat `pass` / SDK `pass`, started_local_server `false`.
- Latest final validation: `387 passed`; compileall passed; LSP had `0 diagnostics`.

## Current conservative notes

- Embeddings and rerank are intentionally ignored/skipped for this checkpoint.
- Live smoke scope should remain key-safe and gated by environment variables.
- This checkpoint is conservative: it records the current implementation/doc status and does not claim all-model guarantees or production MCP SLA.

## Skipped / partial

- Embeddings and rerank remain intentionally skipped.
- No all-Fireworks model guarantee is claimed.
- No production MCP SLA is claimed.
- No Fireworks-native Responses priority contract is claimed; the documented priority path is a Chat-backed adapter fallback only.

## Exact validation commands

```powershell
.venv\Scripts\python.exe -m pytest
```

```powershell
.venv\Scripts\python.exe -m compileall app tests scripts
```

```powershell
# Optional: run only when official SDK packages are installed
.venv\Scripts\python.exe -m pytest tests/test_sdk_compat_optional.py
```

```powershell
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

```powershell
$env:TRANSFORM_DEBUG_ENABLED="true"
$env:TRANSFORM_DEBUG_RETENTION="50"
curl "http://127.0.0.1:8000/admin/transform-debug?has_route_trace=true" -H "Authorization: Bearer admin-local"
```

## Checklist reminders

- Preflight env: confirm required keys, base URL, admin token, and debug flags are set as needed.
- Run local tests and `compileall` before release.
- Run optional official SDK tests when installed.
- Gate live smoke on key-safe env vars and capture the observed status.
- Sanity-check traces for route transform coverage.
- Grep docs for overclaims: unversioned aliases, fake `test_smoke` commands, and unsupported guarantees.
