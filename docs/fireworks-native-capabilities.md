# Fireworks-native capability matrix

This document tracks what `fireworks2api` currently understands about Fireworks'
native inference capabilities.

For a compact official-vs-project matrix, see [`docs/fireworks-inference-matrix.md`](./fireworks-inference-matrix.md).

It complements [`docs/inference-routing.md`](./inference-routing.md):

- `inference-routing.md` explains the routing architecture.
- This file explains feature coverage by endpoint.

## Status labels

| Label | Meaning |
| --- | --- |
| Implemented + tested | The adapter has endpoint-specific validation/conversion and tests. |
| Implemented + live smoked | The behavior has been exercised against a real local `fireworks2api` server backed by Fireworks. |
| Passthrough | The field is accepted and forwarded, but no full semantic/end-to-end workflow is proven. |
| Partial | Some behavior is implemented, but important combinations, smoke coverage, or workflows remain unverified. |
| Not covered | Not currently implemented or intentionally skipped. |

## Global coverage

| Capability | Current status | Notes |
| --- | --- | --- |
| Public route -> adapter -> Fireworks-native transport | Implemented + tested | Routes load body once, call endpoint-specific adapters, resolve Fireworks paths, then proxy. |
| Fireworks inference base path resolution | Implemented + tested | Handles upstream bases ending in `/inference` or `/inference/v1`. |
| Streaming passthrough | Implemented + live smoked / tested | Chat, Completions, Responses, and Anthropic Messages stream successfully; typed Responses stream passthrough and Anthropic tool-event passthrough are locally contract-tested. |
| Local schema/field validation | Implemented + tested | Top-level and selected nested fields are validated endpoint by endpoint. |
| Fireworks error taxonomy/failover | Implemented + tested | 401/403 disable key; 429/5xx/network cooldown+failover; local validation does not touch keys. |
| Prompt cache / affinity | Implemented + live observed | `cached_tokens`/cache-read fields observed in live smoke; explicit stable keys route consistently. |
| Responses lifecycle key binding | Implemented + live smoked | `POST -> GET` works by binding `response_id` to the Fireworks key used for create. DELETE was live-smoked separately. |
| Transform debug summaries | Implemented | Structured summaries only; no full prompt/body persistence. |
| Embeddings live smoke | Intentionally skipped | Skipped per user for this checkpoint. |
| Rerank live smoke | Intentionally skipped | Skipped per user for this checkpoint. |

## Final checkpoint / 最终检查点

### 已验证 / Live-smoked and verified（当前 Kimi / deepwiki 配置）

- Base chat / completions / responses / messages.
- Stream, local errors.
- Responses lifecycle create / get / list / delete（delete 为 opt-in）。
- Responses canonical flat function tool loop：`tool_choice='required'`，已发出 tool/function call，并通过 `tool_output` / `tool_call_id` continuation 返回 `42`。
- Responses MCP / SSE continuation：`https://mcp.deepwiki.com/mcp`（bounded MCP included）。
- Chat image URL with Kimi。
- Responses image with Kimi：message content part `type=image` + `image_url {url, detail}`。
- Anthropic image URL and base64 with Kimi.
- Chat `reasoning_effort` with Kimi.
- Cache telemetry observed / 已观察到缓存遥测字段。
- Smoke command: use `.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py`.

### Intentionally skipped / 有意跳过

- Embeddings live smoke.
- Rerank live smoke.
- Unless corresponding aliases are configured / 除非显式配置对应 alias。

### Still partial / 仍保持保守

- All-model guarantees / 所有模型统一保证。
- Production MCP SLA / 生产 MCP SLA。
- Full MCP tool schema variations / 完整 MCP tool schema 变体。
- All media types and model capability matrix / 所有媒体类型与模型能力矩阵。
- Detailed reasoning model-specific matrix / 更细的 reasoning 模型差异矩阵。
- Embeddings / rerank live smoke stay skipped per user.

### 说明 / Note

- `FIREWORKS2API_*` are smoke controls only / 仅为本项目 smoke 控制变量，不是 Fireworks 官方字段。
- 本文件只记录已验证结果与保守边界；不要把单一 Kimi / deepwiki smoke 推广到所有模型，也不要暗示生产 MCP SLA。
- SDK-shaped fixtures are present locally, but real SDK live smoke is still optional/skipped when the official packages are absent.

## Verified targets and conservative boundaries

This subsection records the Fireworks-native targets that are implemented and
live-smoked, plus the boundaries that remain intentionally conservative. The
goal is to avoid stale unverified-smoke wording while still not generalizing one
model/server smoke result to every Fireworks model or MCP server.

| Target | Docs-backed basis | Current implementation intent |
| --- | --- | --- |
| Responses tools / MCP | Fireworks Responses docs describe tools and MCP-style workflows as first-class native capabilities. | Responses function-tool loop is live-smoked, including `tool_choice='required'`, emitted tool/function call, and `tool_output` / `tool_call_id` continuation returning `42`. Responses MCP/SSE continuation is live-smoked with `https://mcp.deepwiki.com/mcp`, including bounded MCP. Remaining caveat: do not claim production MCP server stability, all tool types, or all-model behavior. |
| Multimodal images | Fireworks docs cover image inputs for relevant native endpoints. | Chat image URL, Responses image input, Anthropic image URL, and Anthropic image base64 were live-smoked with Kimi. Remaining caveat: do not claim all-model, all-media-type, or all-image-error behavior. |
| Thinking / reasoning | Fireworks docs and live smoke observations show reasoning-related fields/events on supported endpoints. | `reasoning_effort` live smoke passed with Kimi. Remaining caveat: do not claim a complete model-specific reasoning/thinking budget matrix. |

Notes:

- These targets are intentionally narrower than the eventual adapter work and
  are not a promise that every field combination is accepted today.
- If a model alias is not configured, the corresponding smoke case should skip
  cleanly rather than changing default script behavior.

## Endpoint matrix

### Chat Completions

Public route:

```text
POST /v1/chat/completions
```

Fireworks-native target:

```text
POST /inference/v1/chat/completions
```

Adapter:

```text
app/products/openai/fireworks_native/chat.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Basic non-stream text generation | Implemented + live smoked | Uses model alias resolution and Fireworks upstream model. |
| Streaming | Implemented + live smoked | SSE chunks are preserved; live smoke observed `chat.completion.chunk`. |
| `service_tier="priority"` | Implemented + live smoked previously | Forwarded to Fireworks. |
| `service_tier="auto/default/flex"` | Implemented + tested | Omitted upstream; Fireworks default behavior applies. |
| Invalid `service_tier` | Implemented + tested | Rejected locally. |
| Prompt cache fields | Implemented + live observed | `prompt_cache_key`, `prompt_cache_isolation_key`, and cache usage extraction are supported. |
| Affinity headers | Implemented + tested | Adapter/dataplane builds/forwards Fireworks affinity headers. |
| Thinking/reasoning fields | Partial + live smoked | `thinking` validation exists; `thinking + reasoning_effort` is rejected. Live smoke with `FIREWORKS2API_SMOKE_REASONING=true` and `FIREWORKS2API_REASONING_MODEL=kimi-k2.6` passed for `reasoning_effort`. Full model-specific reasoning matrix remains partial. |
| Tool/function fields | Implemented + live smoked | Chat function-only tools validation, `role=tool`, `tool_choice`, and assistant `tool_calls` response shape checks are covered; Responses canonical flat function tool loop passed with `tool_choice='required'`, emitted tool/function call, and continuation passed using `tool_output` / `tool_call_id` with calculator output `42`. |
| Response format | Passthrough + tested | Accepted as object and forwarded. Semantics are Fireworks/model-dependent. |
| Multimodal image content parts | Implemented + live smoked | HTTPS/data URL validation is covered; Responses image passed with Kimi using message input content part type `image` and `image_url` object `{url, detail}`. |
| Audio/modalities | Not covered | Unsupported locally unless Fireworks docs and tests are added. |

Key remaining gaps:

- broader model coverage for function tool loops and all-model matrix coverage;
- broader image/model coverage beyond the documented Kimi cases;
- detailed thinking budget/effort combinations by model.

### Completions

Public route:

```text
POST /v1/completions
```

Fireworks-native target:

```text
POST /inference/v1/completions
```

Adapter:

```text
app/products/openai/fireworks_native/completions.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Basic completion | Implemented + live smoked | Live smoke returns `text_completion`. |
| Streaming | Implemented + live smoked | SSE text chunks are preserved. |
| Fireworks prompt shapes | Implemented + tested | Supports string, list of strings, token IDs, and batched token IDs. |
| `images` field | Implemented + tested | Fireworks-native shape validation exists for data-url string lists. No real image smoke. |
| `max_completion_tokens` | Implemented + tested | Mapped to `max_tokens`; mutual exclusion enforced. |
| `reasoning_history` | Implemented + tested | Treated as Fireworks enum/null: `disabled`, `interleaved`, `preserved`, or null. |
| Thinking/reasoning conflict | Implemented + tested | `thinking` with `reasoning_effort` is rejected. |
| `echo`, `echo_last`, `ignore_eos` | Implemented + tested | Fireworks-native fields are forwarded. |
| `return_token_ids`, `raw_output`, perf metrics | Implemented + tested | Forwarded as native Fireworks fields. |
| `service_tier="priority"` | Implemented | Forwarded. |
| `service_tier="auto/default/flex"` | Implemented + tested | Omitted upstream. |

Key remaining gaps:

- real completions image smoke;
- detailed reasoning/thinking output assertions.

### Responses

Public routes:

```text
POST   /v1/responses
GET    /v1/responses
GET    /v1/responses/{response_id}
DELETE /v1/responses/{response_id}
```

Fireworks-native targets:

```text
POST   /inference/v1/responses
GET    /inference/v1/responses
GET    /inference/v1/responses/{response_id}
DELETE /inference/v1/responses/{response_id}
```

Adapter:

```text
app/products/openai/fireworks_native/responses.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Basic create | Implemented + live smoked | Live smoke returns Fireworks `response` object. |
| List | Implemented + live smoked | `limit`, `after`, and `before` are validated. |
| Get by id | Implemented + live smoked | Uses stored `response_id -> key` binding. |
| Delete by id | Implemented + live smoked opt-in | Smoke supports `FIREWORKS2API_SMOKE_DELETE_RESPONSE=true`; route cleanup deletes local binding after success. |
| Streaming | Implemented + live smoked | Typed SSE events are preserved. Live smoke observed `response.created`, `response.in_progress`, `response.output_item.added`, and reasoning-summary events. |
| `store` | Implemented + live smoked | Used for lifecycle retrieval. |
| `previous_response_id` | Implemented + tested | Accepted and forwarded with validation. Continuation workflow not end-to-end smoked. |
| `tools` | Partial + live smoked | Minimal discriminator validation for `function`, `mcp`, `sse`, and `python` plus `server_url` handling; legacy `url` compatibility is preserved where supported. Responses function-tool live smoke passed, including canonical flat function tool loop with `tool_choice='required'` and `tool_output` / `tool_call_id` continuation. Full MCP lifecycle remains partial. |
| `tool_choice` | Implemented + tested | String/object accepted and validated. |
| `max_tool_calls`, `parallel_tool_calls` | Implemented + tested | Validated and forwarded on the initial request; continuation requests should not include `max_tool_calls` after the live 400 observed in smoke. |
| `reasoning`, `text`, `metadata` | Implemented + live smoked | Advanced schema validation and typed SSE usage coverage are in place; reasoning live smoke passed with Kimi, but the full model-specific matrix remains partial. |
| `service_tier` | Implemented + tested | Rejected locally for Responses until Fireworks support is confirmed. |
| Prompt cache/perf fields | Implemented + live observed | Cache token fields observed in Responses live smoke. |

Key remaining gaps:

- MCP tool/server lifecycle;
- `previous_response_id` continuation edge cases beyond the verified tool-output path;
- image/multimodal live smoke beyond the documented Kimi smoke;
- opt-in tools/images/reasoning smoke cases still need actual live execution.

### Embeddings

Public route:

```text
POST /v1/embeddings
```

Fireworks-native target:

```text
POST /inference/v1/embeddings
```

Adapter:

```text
app/products/openai/fireworks_native/embeddings.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Native input shapes | Implemented + tested | Supports non-empty string, list of strings, object, and list of objects. Lists are capped at 2048. |
| `prompt_template` | Implemented + tested | Non-empty string. |
| `dimensions` | Implemented + tested | Integer >= 1. |
| `return_logits` | Implemented + tested | List of integers; empty list allowed. |
| `normalize` | Implemented + tested | Boolean. |
| `encoding_format` | Implemented + tested rejection | Rejected because Fireworks native docs do not include it. |
| `user` | Implemented + tested rejection | Rejected because Fireworks native docs do not include it. |
| Live smoke | Intentionally skipped | No embedding alias configured/required right now. |

Key remaining gaps:

- real embedding model alias and live smoke when needed.

### Rerank

Public route:

```text
POST /v1/rerank
```

Fireworks-native target:

```text
POST /inference/v1/rerank
```

Adapter:

```text
app/products/openai/fireworks_native/rerank.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Required `query` | Implemented + tested | Must be non-empty string. |
| Required `documents` | Implemented + tested | Non-empty list of strings. |
| Optional/nullable `model` | Implemented + tested | If absent/null, upstream payload omits model and routing uses route seed. |
| `top_n` | Implemented + tested | Integer >= 1. |
| `return_documents` | Implemented + tested | Boolean. |
| `task` | Implemented + tested | String or null. |
| Unversioned `/rerank` | Intentionally not registered | Expected 404. |
| Live smoke | Intentionally skipped | No rerank alias configured/required right now. |

Key remaining gaps:

- real rerank model/default Fireworks behavior live smoke when needed.

### Anthropic Messages

Public route:

```text
POST /v1/messages
```

Fireworks-native target:

```text
POST /inference/v1/messages
```

Adapter:

```text
app/products/anthropic/adapters.py
```

| Feature | Status | Notes |
| --- | --- | --- |
| Basic non-stream messages | Implemented + live smoked | Preserves Anthropic-style response. |
| Streaming | Implemented + live smoked | Live smoke observed `message_start`, `ping`, and `content_block_start` events. |
| `max_tokens` optional | Implemented + tested | Fireworks-native Anthropic behavior differs from strict Anthropic SDK expectations. |
| `anthropic-version` header | Implemented + tested | Ignored; not required or forwarded as body. |
| `service_tier="priority"` | Implemented + tested | Forwarded. |
| `service_tier="auto/default/flex"` | Implemented + tested | Omitted. |
| `thinking` | Partial + live observed | Validated as object; live stream observed `content_block.type=thinking`. Full budget matrix not done. |
| `output_config`, `raw_output` | Implemented + tested | Forwarded. |
| `metadata`, `tools`, `tool_choice` | Implemented + tested | Tool choice and tool shape validation are covered. No full tool lifecycle smoke. |
| Image content blocks | Implemented + tested | Exact Anthropic base64 image block shape is validated. No real image live smoke. |
| Usage parsing | Implemented + tested | Handles Anthropic-style cache/read token fields. |

Key remaining gaps:

- real Anthropic image block smoke;
- end-to-end tool use;
- deeper thinking/output-config combinations.

## Advanced capability gaps

These are the main reasons we should not yet claim full Fireworks advanced feature
coverage.

### Tool calling and MCP

Current status:

- field-level validation/passthrough exists for Chat, Responses, and Anthropic;
- Responses has the most Fireworks-native tool/MCP shape awareness;
- Responses function-tool loop live-smoked, including `tool_output` / `tool_call_id` continuation returning `42`;
- Responses MCP/SSE continuation live-smoked with `https://mcp.deepwiki.com/mcp`;
- production MCP server stability remains partial.

Remaining proof:

```text
production MCP server stability across providers
streamed tool-event parsing across models
additional tool types beyond the currently verified function/MCP paths
Anthropic tool round-trip behavior beyond request validation
```

### Multimodal images

Current status:

- Chat accepts valid HTTPS and data URL image content parts and rejects malformed ones;
- Completions validates Fireworks `images` data-url shapes;
- Anthropic validates base64 image content blocks;
- Responses, Chat, and Anthropic image paths were live-smoked with Kimi; full model coverage is still not guaranteed.

Remaining proof:

```text
all-model image capability matrix
base64 size/media-type behavior across models
Fireworks error behavior for invalid image data
non-Kimi media/path combinations
```

### Thinking and reasoning

Current status:

- endpoint-specific fields are accepted/validated;
- conflicts such as `thinking + reasoning_effort` are rejected where applicable;
- advisory capability classification and warnings are in place;
- reasoning_effort live smoke passed with Kimi; full model-specific reasoning matrix remains partial.

Missing proof:

```text
thinking enabled/disabled combinations
budget_tokens behavior across models
reasoning_effort behavior across models
non-stream reasoning output shape assertions
model capability differences
```

## Validation status

Latest validation after external-condition live smoke sprint:

```text
pytest: 374 passed
compileall app tests scripts: passed
LSP: 0 diagnostics
live smoke: OpenAI SDK, Anthropic SDK, chat, completions, responses lifecycle, messages, stream, Responses tools, Responses MCP/SSE continuation, bounded MCP, Anthropic tool_use/tool_result round-trip, images, reasoning, and local negative errors passed where configured
```

Live smoke intentionally ignored/skipped for this priority:

```text
embeddings
rerank
```
