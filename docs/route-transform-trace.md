# Route Transform Trace / 三层路由转换追踪

`Route Transform Trace` is an internal developer debugging aid for the
fireworks2api inference path. It explains how one request moves through the
three layers:

```text
public route -> endpoint adapter -> Fireworks native endpoint
```

It is **not** a customer-facing UI feature and it does **not** change public API
responses.

Developer-only note: enable `TRANSFORM_DEBUG_ENABLED=true`, then query
`/admin/transform-debug?has_route_trace=true` with admin auth. Do not treat this
as a UI/customer feature.

## When to use it

Use route traces when debugging questions like:

- Which public route handled this request?
- Which endpoint-specific adapter transformed it?
- Which Fireworks native endpoint/path did it call?
- Which high-level capability was involved: tools, MCP, image, reasoning,
  streaming, cache, lifecycle, etc.?
- Was a field forwarded, mapped, omitted, or rejected?
- Which routing strategy/key-affinity path was used?
- What status/error/usage/cache metadata came back?

## Enable tracing

Route traces are stored only when transform debug is enabled:

```powershell
$env:TRANSFORM_DEBUG_ENABLED="true"
$env:TRANSFORM_DEBUG_RETENTION="50"
```

Then start the local server as usual:

```powershell
$env:ENABLE_ADMIN_STATIC="true"
$env:ADMIN_TOKEN="admin-local"
$env:PROXY_API_KEYS="sk-local-dev"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Query traces

```powershell
curl "http://127.0.0.1:8000/admin/transform-debug?has_route_trace=true" `
  -H "Authorization: Bearer admin-local"
```

Clear records:

```powershell
curl -X DELETE "http://127.0.0.1:8000/admin/transform-debug" `
  -H "Authorization: Bearer admin-local"
```

## Trace shape

Each transform-debug item may include a safe `route_trace` object:

```json
{
  "route_trace": {
    "trace_version": 1,
    "public_route": {
      "method": "POST",
      "path_template": "/v1/responses",
      "product": "openai",
      "operation": "responses"
    },
    "adapter": {
      "module": "app.products.openai.fireworks_native.responses",
      "function": "build_responses_adapter"
    },
    "fireworks_endpoint": {
      "key": "cross_endpoint_fallback/priority:chat_completions",
      "path": "cross_endpoint_fallback/priority:chat_completions"
    },
    "model": {
      "alias": "kimi-k2.6",
      "upstream": "accounts/fireworks/models/kimi-k2p6"
    },
    "capability_tags": ["responses", "priority", "chat_completions", "cross_endpoint_fallback"],
    "request_shape": {
      "payload_fields": ["input", "model", "tools"],
      "forwarded_headers": ["x-session-affinity"],
      "stream": false
    },
    "field_actions": [
      {"field": "model", "action": "map"},
      {"field": "tool_choice", "action": "forward"}
    ],
    "routing": {
      "routing_mode": "account_aware_sticky",
      "primary_account_bucket": "account:acct_123",
      "selected_account_count": 2,
      "skipped_account_count": 0,
      "stable_key_hash_value": "safe-hash-only",
      "selected_key_count": 3
    },
    "result": {
      "status_code": 200,
      "error_type": null,
      "upstream_request_id": "...",
      "selected_key_fingerprint": "...",
      "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_tokens": 80
      },
      "routing": {
        "mode": "account_aware_sticky",
        "primary_account_bucket": "account:acct_123",
        "selected_account_count": 2,
        "selected_key_count": 3,
        "attempts": [
          {"action": "attempt", "account_bucket": "account:acct_123", "key_fingerprint": "..."}
        ]
      }
    },
    "warnings": []
  }
}
```

Exact fields can be absent when they are not relevant to an endpoint or when the
request fails before that stage.

Account-aware routing observability is intentionally limited to safe identifiers:
account buckets, key fingerprints, attempt/skip/failover actions, and error type.
It never records full Fireworks API keys, prompts, request bodies, or raw stable
keys. Use `result.routing.attempts` to check whether an account-level quota
failure skipped sibling keys in the same Fireworks account before retrying a
different account.

## Capability tags

Common tags include:

```text
chat
completions
responses
embeddings
rerank
anthropic_messages
stream
priority
prompt_cache
tools
tools:function
tools:mcp
tools:sse
tools:python
responses:continuation
responses:lifecycle
multimodal:image
reasoning
thinking
response_format
```

These tags are for filtering and debugging; they are not Fireworks official API
fields.

Every traced request also gets a base tag derived from the Fireworks endpoint
when possible. Examples:

```text
chat/completions -> chat
responses -> responses
completions -> completions
messages -> anthropic_messages
responses/{id} lifecycle routes -> responses:lifecycle
```

## Field actions

`field_actions` summarizes adapter decisions without storing sensitive values.
Examples:

```json
{"field": "model", "action": "map"}
{"field": "max_completion_tokens", "action": "rename", "to": "max_tokens"}
{"field": "service_tier", "action": "omit", "reason": "default Fireworks tier"}
{"field": "service_tier", "action": "fallback", "reason": "simple non-stream text via Chat Completions priority; synthesize Responses shape"}
{"field": "service_tier", "action": "reject", "reason": "complex/stream/tool/MCP/image/reasoning/lifecycle priority Responses requests unsupported"}
```

Use this to check whether a behavior comes from the public contract, the adapter,
or Fireworks native validation.

## Debug examples

### Responses function tool continuation

Look for:

```text
public_route.path_template = /v1/responses
adapter.module = app.products.openai.fireworks_native.responses
fireworks_endpoint.key = responses
capability_tags includes tools:function and responses:continuation
field_actions includes tool_choice forward
```

If Fireworks returns an error, check `result.error_type` and whether the trace
shows a field that should have been omitted on continuation, such as
`max_tool_calls`.

### MCP / SSE

Look for:

```text
capability_tags includes tools:sse or tools:mcp
fireworks_endpoint.key = responses
result.status_code
result.error_type
```

The trace records the tool type but not MCP arguments or tool outputs.

### Priority fallback

Look for:

```text
public_route.path_template = /v1/responses
capability_tags includes priority, responses, chat_completions, and cross_endpoint_fallback
field_actions includes service_tier fallback
fireworks_endpoint.key = cross_endpoint_fallback/priority:chat_completions
```

This trace pattern indicates the adapter used Chat Completions priority for a simple non-stream Responses priority text request and then synthesized a Responses-shaped response with a fallback id such as `resp_fallback_chatcmpl-*`. It should not appear for complex priority inputs or streaming/tool/MCP/image/reasoning/lifecycle requests.

### Images

Look for:

```text
capability_tags includes multimodal:image
request_shape.payload_fields includes input or messages
```

The trace never stores image URLs or base64 data.

### Prompt cache / sticky routing

Look for:

```text
capability_tags includes prompt_cache
routing.stable_key_hash is present
result.usage.cached_tokens
```

The raw prompt-cache key and raw route key are never stored.

## Safety rules

Route traces must never include:

```text
full prompt text
full request body
image URL values
base64 image data
tool arguments
tool output content
Authorization header values
Fireworks API keys
raw stable keys
raw route keys
full client identity
```

For route-trace capture and export, keep the same redaction boundary: no prompts,
full bodies, image URLs/base64, tool args, tool outputs, API keys, or raw keys.

They may include:

```text
field names
adapter name
Fireworks endpoint key/path
model alias and upstream model
capability tags
safe header names
selected key fingerprint
stable key hash
status/error/latency/usage/cache metadata
```

Redaction behavior is covered by tests in:

```text
tests/test_route_transform_trace.py
tests/test_route_transform_trace_redaction.py
tests/test_fireworks_proxy_route_trace.py
tests/test_openai_route_transform_trace.py
tests/test_anthropic_route_transform_trace.py
```

## Related files

```text
app/dataplane/fireworks/route_trace.py
app/dataplane/fireworks/proxy.py
app/control/repository.py
app/products/openai/*
app/products/anthropic/router.py
app/products/admin/transform_debug.py
```
