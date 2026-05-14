# Public API Contract / 公共 API 合约

> Scope: `fireworks2api` public inference surface only.
>
> This document separates **public official shape** from **Fireworks extension shape**. If a field is not explicitly listed here or in a linked public contract doc, reject it unless another documented public rule explicitly allows it.

## 1. Contract goals / 目标

- Official public shapes come first.
- Fireworks-specific extensions are allowed only when explicitly marked.
- Unknown or unsupported fields are rejected unless they are harmless official fields that are explicitly dropped or are documented elsewhere in the public contract.
- No unversioned aliases: public routes use explicit `/v1/...` paths.

### Taxonomy / 分类

| Classification | Meaning |
| --- | --- |
| official | Part of the public OpenAI / Anthropic shape. |
| extension | Fireworks-specific surface, not part of the public official shape. |
| map | Adapter transforms the public field into a Fireworks-native shape. |
| drop | Adapter accepts the public field but removes it before upstream send. |
| reject | Adapter rejects the request or field. |
| validate | Adapter enforces public contract requirements before forwarding or mapping. |

## 2. `/v1` route list / 路由表

| Route | Status | Notes |
|---|---:|---|
| `GET /v1/models` | official-compatible | Official OpenAI model list shape only. |
| `GET /v1/models/{model}` | official-compatible | Official OpenAI model object shape only. |
| `POST /v1/chat/completions` | official-compatible | OpenAI Chat official surface. |
| `POST /v1/completions` | official-compatible legacy | OpenAI legacy completions surface. |
| `POST /v1/responses` | official-compatible | OpenAI Responses official surface where implemented; simple non-stream priority text requests may fall back to Chat Completions and synthesize a Responses-shaped response. |
| `GET /v1/responses` | extension | Local lifecycle/list support; not an official OpenAI target. |
| `GET /v1/responses/{id}` | official-compatible target | OpenAI Responses retrieve surface. |
| `DELETE /v1/responses/{id}` | official-compatible target | OpenAI Responses delete surface. |
| `POST /v1/embeddings` | official-compatible target | OpenAI embeddings surface. |
| `POST /v1/messages` | official-compatible target | Anthropic Messages surface. |
| `POST /v1/rerank` | extension | Fireworks rerank surface. |

Not public release routes:

```text
/models
/responses
/messages
/rerank
```

## 3. Auth / 鉴权约定

### OpenAI-compatible requests

- Use `Authorization: Bearer <api_key>`.
- API key format is treated as opaque by the public contract.

### Anthropic-compatible requests

- Accept `x-api-key: <api_key>`.
- Also accept `Authorization: Bearer <api_key>` for client compatibility.
- `anthropic-version` header is required when the Anthropic surface is used, and it must be non-empty.

## 4. Error envelopes / 错误包裹

### OpenAI local errors

- Return OpenAI-style error envelopes for local validation/auth/routing failures.
- Keep the public shape compatible with OpenAI clients: `error` object with message/type/code fields as applicable.

### Anthropic local errors

- Return Anthropic-style error response envelopes for the Anthropic surface.
- Prefer Anthropic-compatible error object naming/shape for local validation/auth failures.

## 5. `/v1/models` response shape / 模型列表

- `GET /v1/models` must expose the official OpenAI model-list shape only.
- Do not add extra top-level fields in the public response.
- `GET /v1/models/{model}` must likewise stay within the official model-object shape.
- Do not rely on unversioned `/models` aliases in the public contract.

## 6. Field handling / 字段处理策略

Policy priority: strict required/type/mutual validation > official harmless accept/drop > Fireworks allowlist pass-through > unknown reject.

- Required fields, type checks, and mutual-exclusion / dependency validation: strict.
- Official harmless fields: may be accepted and dropped if the upstream contract tolerates them.
- Fireworks-specific allowlist fields: pass through when explicitly supported.
- Unknown fields: reject unless they are officially harmless or explicitly allowlisted elsewhere in the public contract.

### Chat

- Official public shape: `model`, `messages`, `tools`, `tool_choice`, `stream`, `stream_options.include_usage`, `temperature`, `max_tokens`, and other documented OpenAI Chat fields.
- `role=tool` is supported as part of the official public message shape.
- `tool_choice='any'` is not an official OpenAI Chat value; reject it.

### Responses

- Official public shape: documented Responses input, tool, and lifecycle fields.
- `input_image` string is official and maps to the underlying Fireworks image representation.
- Fireworks image object input is an extension shape.
- `function_call_output` is official and maps through.
- Fireworks `tool_output` is an extension shape.
- MCP support stays minimal: accept only the documented core discriminator/validation and reject or defer extra MCP fields.
- `/v1/responses` with `service_tier=priority` only supports simple non-stream text fallback via Chat Completions; it does not bind lifecycle state, complex priority requests are rejected, and streaming fallback is unsupported.

### Embeddings

- `encoding_format=float` is accepted and dropped as an official harmless field.
- `user` is accepted and dropped as an official harmless field.
- `encoding_format=base64` is rejected.
- Fireworks `prompt_template`, `normalize`, and `return_logits` are extension fields.

### Anthropic

- `anthropic-version` is required and must be non-empty.
- `anthropic-beta` is accepted and dropped as an official harmless header field.
- Official `tool_use` / `tool_result` validation is enforced.
- `output_config`, `raw_output`, and `service_tier` are Fireworks extensions.

## 7. P0 completed items / P0 完成项

- OpenAI Chat: `role=tool` mapped, `stream_options.include_usage` preserved, user string normalized, `tool_choice='any'` rejected.
- OpenAI Responses: `input_image` mapped, `function_call_output` mapped, user string normalized, simple non-stream text priority fallback uses Chat Completions and synthesizes a Responses-shaped response, complex priority requests are rejected, streaming fallback unsupported, MCP extras rejected.
- Embeddings: `encoding_format=float` dropped, `encoding_format=base64` rejected, `user` dropped.
- Anthropic Messages: `anthropic-version` required, `anthropic-beta` dropped, `tool_use` / `tool_result` / `tool_choice` validated, `output_config` / `raw_output` / `service_tier` treated as extensions.

## 8. Streaming / 流式传输

- Streaming must be passed through as SSE/event stream without rewriting event semantics unnecessarily.
- Preserve terminal usage/metadata when the upstream protocol provides it.
- Do not invent new stream event shapes.

## 9. SDK compatibility goals / SDK 兼容目标

- Compatibility is a goal, not a promise.
- Public routes should remain usable by common OpenAI / Anthropic clients where the official shape is implemented.
- Extensions must not change the default behavior of official-compatible routes.
- This document does not guarantee every SDK edge case, vendor-private feature, or live-smoke outcome.

## 10. Known caveats / 已知注意事项

- Embeddings and rerank live smoke remain skipped in this gate; treat those routes as caveated until re-verified.
- Extension behavior must be called out explicitly; otherwise prefer the official public shape and reject or drop non-official fields.
- Fireworks Responses itself still does not document a priority surface; any `/v1/responses` priority behavior in this adapter is a Chat endpoint fallback for simple non-stream text only.
- Any route, field, or behavior not listed in this document should be treated as rejected by default unless another documented public rule explicitly covers it.
