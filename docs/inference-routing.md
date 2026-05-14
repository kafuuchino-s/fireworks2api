# Fireworks inference routing

`fireworks2api` routes each public inference request through an endpoint-specific adapter before it reaches Fireworks.

```text
public route -> endpoint adapter -> Fireworks-native route -> Fireworks transport
```

Official references:

- OpenAI API docs: `https://platform.openai.com/docs`
- Anthropic API docs: `https://docs.anthropic.com/`
- Fireworks docs entrypoint: `https://docs.fireworks.ai/llms.txt`

The project should not treat OpenAI or Anthropic request bodies as raw Fireworks payloads. Fireworks supports similar concepts, but field names, valid values, optional fields, streaming events, lifecycle behavior, and cache headers differ by endpoint.

## Why adapters exist

Adapters are the compatibility layer between public client requests and Fireworks-native inference APIs.

They decide, per endpoint:

- which public fields are accepted;
- which fields are translated or normalized;
- which fields are omitted because Fireworks treats them as default;
- which fields are rejected locally before calling Fireworks;
- which Fireworks-native headers are added;
- which Fireworks route should be called.

Routes should remain thin. They authenticate, load JSON once, build proxy context, invoke the endpoint adapter, record transform-debug metadata, resolve the Fireworks route, and call the transport.

## Layers

### 1. Public route layer

Public routes live under `app/products/*` and expose the API clients call:

```text
POST   /v1/chat/completions
POST   /v1/completions
POST   /v1/responses
GET    /v1/responses
GET    /v1/responses/{response_id}
DELETE /v1/responses/{response_id}
POST   /v1/embeddings
POST   /v1/rerank
POST   /v1/messages
```

Responsibilities:

1. verify local proxy auth;
2. load JSON once;
3. build a `ProxyRequestContext` from the body;
4. call the endpoint adapter;
5. resolve the Fireworks-native route path;
6. call the Fireworks transport.

Routes should not hand-build Fireworks payloads or duplicate endpoint policy.

### 2. Adapter layer

OpenAI-compatible adapters live in:

```text
app/products/openai/fireworks_native/
  common.py
  chat.py
  completions.py
  responses.py
  embeddings.py
  rerank.py
```

Anthropic Messages uses:

```text
app/products/anthropic/adapters.py
```

The old `app/products/openai/adapters.py` remains a compatibility re-export shim only.

Adapters return the Fireworks-native request pieces:

```text
payload
headers
transform report
```

The transform report is for diagnostics and must not contain full prompts or full request bodies.

### 3. Fireworks dataplane layer

Fireworks transport and shared helpers live under:

```text
app/dataplane/fireworks/
```

This layer should be product-neutral. It must not import `app.products.openai`. It receives a prepared Fireworks-native route path, payload, headers, and context, then handles:

- HTTP request/stream proxying;
- key selection and failover;
- cooldown/disable decisions;
- usage and cached-token extraction;
- request logging;
- response-id to Fireworks-key routing for Responses lifecycle.

## Fireworks route resolution

Fireworks inference base URLs can be configured either with or without `/v1`:

```text
https://api.fireworks.ai/inference
https://api.fireworks.ai/inference/v1
```

Routes use:

```python
resolve_inference_path(settings.upstream_base_url, endpoint)
```

from:

```text
app/dataplane/fireworks/paths.py
```

If the configured base ends with `/v1`, the resolver returns paths like:

```text
chat/completions
responses
messages
```

If the configured base does not end with `/v1`, it returns paths like:

```text
v1/chat/completions
v1/responses
v1/messages
```

This avoids hard-coding different Fireworks path variants throughout product routes.

## Current route map

| Public route | Adapter | Fireworks endpoint key | Fireworks native route from inference root |
| --- | --- | --- | --- |
| `POST /v1/chat/completions` | `openai/fireworks_native/chat.py` | `chat_completions` | `v1/chat/completions` |
| `POST /v1/completions` | `openai/fireworks_native/completions.py` | `completions` | `v1/completions` |
| `POST /v1/responses` | `openai/fireworks_native/responses.py` | `responses` | `v1/responses` |
| `GET /v1/responses` | `openai/fireworks_native/responses.py` | `responses_lifecycle` | `v1/responses` |
| `GET /v1/responses/{id}` | `openai/fireworks_native/responses.py` | `responses_lifecycle` | `v1/responses/{id}` |
| `DELETE /v1/responses/{id}` | `openai/fireworks_native/responses.py` | `responses_lifecycle` | `v1/responses/{id}` |
| `POST /v1/embeddings` | `openai/fireworks_native/embeddings.py` | `embeddings` | `v1/embeddings` |
| `POST /v1/rerank` | `openai/fireworks_native/rerank.py` | `rerank` | `v1/rerank` |
| `POST /v1/messages` | `anthropic/adapters.py` | `anthropic_messages` | `v1/messages` |

Unversioned compatibility aliases such as `/responses`, `/messages`, `/models`, and `/rerank` are intentionally not part of the public surface.

Anthropic `/v1/messages` requires `anthropic-version` and accepts either `x-api-key` or `Authorization: Bearer ...` at the public boundary.

## Endpoint-specific examples

### Chat Completions

Public request:

```json
{
  "model": "kimi-k2.6",
  "messages": [{"role": "user", "content": "hello"}],
  "service_tier": "priority",
  "thinking": {"type": "enabled", "budget_tokens": 1024},
  "prompt_cache_key": "chat-session-1"
}
```

Adapter behavior:

- maps the local model alias to the Fireworks upstream model;
- validates message/content-part shape;
- forwards `service_tier="priority"`;
- omits `service_tier="auto"`, `"default"`, or `"flex"`;
- rejects invalid service tiers;
- validates `thinking` and rejects `thinking` with `reasoning_effort`;
- forwards Fireworks cache fields such as `prompt_cache_key` and `prompt_cache_isolation_key`;
- builds affinity/cache headers;
- validates Chat function-only tools, `tool_choice`, assistant `tool_calls` shapes, and HTTPS/data URL image content parts.

Fireworks target:

```text
POST /inference/v1/chat/completions
```
