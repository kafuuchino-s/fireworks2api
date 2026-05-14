# Operator Runbook

This runbook covers day-to-day operator actions for a local or deployed `fireworks2api` instance. It stays conservative: do not assume a feature is live unless it has been verified in this repo.

## 1) Startup environment

Typical local startup uses explicit admin/proxy controls:

```powershell
$env:ENABLE_ADMIN_STATIC="true"
$env:ADMIN_TOKEN="admin-local"
$env:PROXY_API_KEYS="sk-local-dev"
```

Optional transform trace debugging:

```powershell
$env:TRANSFORM_DEBUG_ENABLED="true"
$env:TRANSFORM_DEBUG_RETENTION="50"
```

Then start the server with the project virtualenv:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Notes:

- `ADMIN_TOKEN` protects admin endpoints and the admin static UI.
- `PROXY_API_KEYS` is for local proxy auth in development and smoke runs.
- Keep `AFFINITY_HASH_SECRET` stable across restarts when cache affinity matters.

## 2) Admin key onboarding

1. Open the Admin UI or Admin API.
2. Add one or more Fireworks API keys.
3. Confirm the UI shows masked keys, not full secrets.
4. Verify the backend recorded fingerprints and enabled the keys.

Operational guidance:

- Treat Fireworks keys as secrets; never paste them into logs or docs.
- Use admin actions to enable, disable, or clear cooldowns for keys.
- Bootstrap env-based keys are for setup automation only; the normal workflow is Admin onboarding.

## 3) Model catalog and import flow

The Admin model catalog is registry-first:

- `/admin/fireworks/models` defaults to `source=official` and does not require a Fireworks key.
- `source=inference` and `source=account` are optional discovery/import helpers when you need to inspect live Fireworks state.
- The built-in official registry is the primary catalog source for browsing models.

Import and manual mapping rules:

- Manual add is an explicit `alias -> upstream_model/router` mapping.
- `/admin/models/import` requires the operator to provide explicit `alias` or `aliases`.
- The import flow no longer guesses basename or suggested aliases.
- Public `/v1/models` continues to show only local enabled mappings.

## 4) Proxy auth model

Public inference routes use the client-side API key in the request header:

```text
Authorization: Bearer <api_key>
```

Anthropic requests also accept:

```text
x-api-key: <api_key>
```

For admin endpoints, use the admin token:

```text
Authorization: Bearer admin-local
```

Do not assume unauthenticated access is allowed on public or admin surfaces.

## 5) Account / quota refresh model

`fireworks2api` is not a Fireworks account management system. It does not invent quota data.

Operationally:

- Admin key state is refreshed when keys are added, enabled, disabled, or cleared from cooldown.
- Usage, cache, and request metrics are observed from live traffic and request logs.
- If you need current account/quota status, refresh from the source of truth in Fireworks docs and the actual account state behind the configured keys.

Do not overclaim live quota introspection unless the current build explicitly exposes it.

## 6) Smoke commands

Use the project smoke script rather than ad hoc requests when validating a local build:

```powershell
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

Optional smoke controls are project-local variables, not Fireworks API fields. Common examples:

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
$env:FIREWORKS2API_SMOKE_STREAM="true"
$env:FIREWORKS2API_SMOKE_ERRORS="true"
$env:FIREWORKS2API_SMOKE_ADVANCED="true"
```

Important:

- Do not write that a smoke path is “live verified” unless you actually ran it in this environment.
- Embeddings and rerank may be intentionally skipped depending on aliases and test goals.

## 7) Trace debug safe use

Route transform traces are for internal debugging only.

Enable them only when necessary:

```powershell
$env:TRANSFORM_DEBUG_ENABLED="true"
```

Inspect traces with admin auth:

```powershell
curl "http://127.0.0.1:8000/admin/transform-debug?has_route_trace=true" `
  -H "Authorization: Bearer admin-local"
```

Safety rules:

- Trace data must not include full prompts, request bodies, image URLs, base64 data, tool args, tool outputs, API keys, or raw stable keys.
- Use traces to understand mapping, routing, and field actions only.
- Turn tracing off when you no longer need it.

## 8) Security notes

- Never log full Fireworks API keys or Authorization headers.
- Never store full prompts or full request bodies in request logs or traces.
- Use masked key displays and fingerprints instead of secrets.
- Keep admin credentials separate from proxy credentials.
- Treat `docs/fireworks/` as a local third-party docs cache, not a checked-in source of truth.

## 9) Troubleshooting

### 401 / 403

- Check the client API key.
- Check whether the key is disabled or cooled down.
- Confirm the admin token is correct for admin calls.

### 429 / 5xx / network errors

- Expect failover/cooldown behavior to select another key when available.
- Inspect request logs for status, error class, upstream request id, and selected key fingerprint.
- If a key repeatedly fails, disable it or clear cooldown only after confirming the issue is resolved.

### Validation errors

- Local validation failures should not put keys into cooldown.
- Check the public contract for the route in question before assuming Fireworks rejected the request.

### Route or field mapping questions

- Use transform debug traces to see whether a field was forwarded, mapped, omitted, or rejected.
- Compare against the public-api and adapter-matrix docs before changing behavior.

### Missing admin static UI

- Confirm `ENABLE_ADMIN_STATIC=true`.
- Confirm the server was started with the expected admin token.
