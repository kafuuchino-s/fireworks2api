# Fireworks native inference matrix / Fireworks 原生推理矩阵

> 目的：把 **Fireworks 官方原生推理接口** 和 **fireworks2api 当前实现** 分开写清楚，避免把官方字段、项目 smoke 环境变量、以及保守的能力边界混在一起。

## 说明 / Legend

| Label | Meaning |
| --- | --- |
| 官方字段 | 来自 Fireworks 官方文档的请求/响应字段或行为 |
| 项目状态 | `fireworks2api` 当前实现/测试/烟雾结果 |
| smoke env vars | 仅用于本项目 smoke / 启停控制，不属于 Fireworks 官方字段 |

### 项目 smoke env vars（非官方字段）

- `FIREWORKS2API_SMOKE_DELETE_RESPONSE=true`：可选删除 response 的 smoke 开关。
- 其他 smoke 相关变量如有新增，应继续归类为项目控制项，而不是 Fireworks API 字段。

## 1) Fireworks 官方原生推理接口矩阵

| Category | Method | Path | Key top-level fields / surface | Stream / lifecycle / cache notes | Source doc filename |
| --- | --- | --- | --- | --- | --- |
| Chat Completions | POST | `/inference/v1/chat/completions` | `model`, `messages`, `temperature`, `max_tokens`, `service_tier`, `stream`, `tools`, `tool_choice`, `response_format`, `thinking`/reasoning-related fields, prompt-cache fields | Stream returns chat completion chunks; cache read/affinity fields may appear in live results; priority tier is a Fireworks-specific surface | `post-chatcompletions.md` |
| Completions | POST | `/inference/v1/completions` | `model`, `prompt`, `max_tokens`, `stream`, Fireworks prompt shapes, `images`, `echo`, `ignore_eos`, `return_token_ids`, `raw_output`, perf fields | Stream returns text chunks; Fireworks-native completion shapes include non-OpenAI prompt forms; image support is model/doc dependent | `post-completions.md` |
| Responses | POST / GET / DELETE | `/inference/v1/responses` and `/inference/v1/responses/{response_id}` | `model`, `input`, `store`, `previous_response_id`, `tools`, `tool_choice`, `reasoning`, `text`, `metadata`, `service_tier`, stream events | Lifecycle-oriented; supports create/get/delete and streaming event flow; cache fields can appear in usage/telemetry. Fireworks Responses does **not** document priority; in `fireworks2api`, simple non-stream text `/v1/responses` priority requests fall back to Chat Completions priority and synthesize a Responses-shaped response, without lifecycle binding. Complex/stream/tool/MCP/image/reasoning/lifecycle priority requests are rejected, and streaming fallback is unsupported. Live smoke confirmed canonical flat function tool loop with `tool_choice='required'`, `tool_output` / `tool_call_id` continuation, MCP/SSE continuation via `https://mcp.deepwiki.com/mcp`, bounded MCP, and image input with `image` content part + `image_url` object `{url, detail}`. Continuation requests should not include `max_tool_calls` after the observed 400. | `post-responses.md` / `list-responses.md` / `get-response.md` / `delete-response.md` |
| Embeddings | POST | `/inference/v1/embeddings` | `model`, `input`, `prompt_template`, `dimensions`, `return_logits`, `normalize` | Non-streaming; model availability is alias-driven; live smoke intentionally skipped per user | `create-embeddings.md` |
| Rerank | POST | `/inference/v1/rerank` | `model`, `query`, `documents`, `top_n`, `return_documents`, `task` | Non-streaming ranking endpoint; model availability is alias-driven; live smoke intentionally skipped per user | `rerank-documents.md` |
| Anthropic Messages | POST | `/inference/v1/messages` | `model`, `messages`, `max_tokens`, `thinking`, `tools`, `tool_choice`, image content blocks, `output_config`, `raw_output`, usage/cache fields | Stream emits Anthropic-style events; cache/read token fields may appear; behavior is model-dependent; `anthropic-version` is required on the public Anthropic surface | `anthropic-messages.md` |

> 注：上表只列出本项目当前已识别的 **核心官方 surface**。Fireworks 文档中还可能包含更细的模型/能力说明，需以各页面正文为准。

## 2) fireworks2api 当前实现矩阵

| Public route | Adapter | Fireworks endpoint key/path | Status |
| --- | --- | --- | --- |
| `POST /v1/chat/completions` | `app/products/openai/fireworks_native/chat.py` | `chat_completions` → `/inference/v1/chat/completions` | Implemented + tested; live-smoked |
| `POST /v1/completions` | `app/products/openai/fireworks_native/completions.py` | `completions` → `/inference/v1/completions` | Implemented + tested; live-smoked |
| `POST /v1/responses` | `app/products/openai/fireworks_native/responses.py` | `responses` → `/inference/v1/responses` | Implemented + tested; live-smoked |
| `GET /v1/responses` | `app/products/openai/fireworks_native/responses.py` | `responses_lifecycle` → `/inference/v1/responses` | Implemented + tested; live-smoked |
| `GET /v1/responses/{response_id}` | `app/products/openai/fireworks_native/responses.py` | `responses_lifecycle` → `/inference/v1/responses/{response_id}` | Implemented + tested; live-smoked |
| `DELETE /v1/responses/{response_id}` | `app/products/openai/fireworks_native/responses.py` | `responses_lifecycle` → `/inference/v1/responses/{response_id}` | Implemented; live-smoked opt-in |
| `POST /v1/embeddings` | `app/products/openai/fireworks_native/embeddings.py` | `embeddings` → `/inference/v1/embeddings` | Implemented + tested; live-smoke skipped unless aliases are configured |
| `POST /v1/rerank` | `app/products/openai/fireworks_native/rerank.py` | `rerank` → `/inference/v1/rerank` | Implemented + tested; live-smoke skipped unless aliases are configured |
| `POST /v1/messages` | `app/products/anthropic/adapters.py` | `anthropic_messages` → `/inference/v1/messages` | Implemented + tested; live-smoked |

### Current coverage notes / 当前覆盖说明

- **Implemented + tested**：适配层、字段校验、转换、路由已覆盖测试。
- **Live-smoked**：已对真实 Fireworks 后端完成过一次或多次烟雾验证；本文件仅记录已明确验证的能力，不代表所有模型都支持。
- **Official-compatible target**：对齐官方 OpenAI / Anthropic 语义的公开目标，但仍以保守实现为准。
- **Partial**：仅部分字段/流程已证实，完整闭环未证明。
- **Skipped**：当前有意不做 live smoke，通常是因为未配置对应 alias 或用户明确跳过。

### 需要特别区分的两类字段

**Fireworks 官方字段**

- `model`, `messages`, `input`, `tools`, `tool_choice`, `reasoning`, `store`, `previous_response_id`, `query`, `documents`, `images` 等。

**项目 smoke / 运行环境变量**

- `FIREWORKS2API_SMOKE_DELETE_RESPONSE=true`
- `ENABLE_ADMIN_STATIC=true`
- `ADMIN_TOKEN`
- `PROXY_API_KEYS`
- 以及其他 `FIREWORKS2API_*` smoke 控制项

### Public-vs-native adapter boundary / 对外-对内适配边界

Public OpenAI / Anthropic requests should enter with official public shapes first,
then be normalized by endpoint-specific adapters into the Fireworks-native shapes
listed in this matrix. The internal Fireworks matrix is therefore not a public
API promise by itself.

P0 public adapter alignment currently treats advanced fields as follows:

| Public surface | Official public shape | Adapter action | Fireworks-native result |
| --- | --- | --- | --- |
| OpenAI Chat | `role="tool"`, `tool_call_id`, `stream_options.include_usage`, `user` metadata | validate/map or validate/drop | Chat payload with Fireworks-supported messages/options; `user` is dropped |
| OpenAI Chat | `tool_choice="any"` | reject | no current official-compatible Fireworks mapping |
| OpenAI Responses | `input_image` content part with `image_url` | map | Fireworks image content shape |
| OpenAI Responses | `function_call_output` + `call_id` | map | Fireworks `tool_output` + `tool_call_id` continuation shape |
| OpenAI Responses | `service_tier` | fallback for simple non-stream text; otherwise reject | priority text fallback uses Chat Completions priority and synthesizes a Responses-shaped response; no lifecycle binding |
| OpenAI Embeddings | `encoding_format="float"`, `user` | accept + drop | default Fireworks float embeddings request |
| OpenAI Embeddings | `encoding_format="base64"` | reject | response transformation not implemented |
| Anthropic Messages | `anthropic-version`, `anthropic-beta`, official `tool_use` / `tool_result` / `tool_choice` shapes | validate or accept + drop headers | Fireworks Anthropic Messages payload; beta header is not forwarded |

Fireworks-only fields such as Anthropic `output_config`, `raw_output`, and
`service_tier`, or Responses `tool_output` / `tool_call_id`, remain documented
extensions rather than official OpenAI / Anthropic public fields.

## 3) Missing / partial roadmap

| Area | Current state | Next step |
| --- | --- | --- |
| Tools / MCP full loop | Live-smoked with scoped caveats | Responses function tools and MCP/SSE continuation are live-smoked; bounded MCP passed for `https://mcp.deepwiki.com/mcp`; Anthropic `tool_use` / `tool_result` round-trip returned `42` with Kimi; production MCP SLA and cross-model guarantees remain out of scope |
| Image live smoke | Complete | 已完成并记录 Chat / Responses / Anthropic image smoke |
| Reasoning model-specific behavior | Partial + live smoked | `reasoning_effort` live smoke passed with Kimi; full model-specific reasoning/thinking matrix remains conservative |
| Embeddings / rerank live smoke | Intentionally ignored/skipped | 按当前优先级先不处理；若后续配置 alias 再开启 |
| Model discovery endpoints | Optional / internal | 作为 admin/internal 能力探索即可，不纳入公开 inference 路由承诺 |

## 4) 结论 / Takeaway

`fireworks2api` 已经覆盖了 Fireworks 的主要原生推理入口，但以下内容仍需保守表述：

1. 官方字段 ≠ 项目 smoke 环境变量；两者不要混写。
2. 适配器已实现不代表完整模型语义已证明，但 tools/MCP、image、reasoning 的关键 smoke 已完成并记录。
3. embeddings / rerank 目前在未配置 alias 时保守跳过，不应写成已完全验证。
4. OpenAI 侧只把 `/v1/models` 写成官方 shape；Anthropic `/v1/messages` 需要 `anthropic-version` 并支持 `x-api-key` 或 Bearer。
5. `/v1/responses` priority fallback is Chat-backed, simple-text-only, non-stream only, and not a Fireworks-native Responses priority contract; the synthesized id shape is `resp_fallback_chatcmpl-*`.

Smoke command: use `.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py` rather than `pytest tests/test_smoke.py`.

## 5) Repro / 复现已验证 live smoke

> 说明：这里记录的是**复现已验证 smoke** 的最小命令集。`FIREWORKS2API_*` 是本项目 smoke 环境变量，不是 Fireworks 官方字段。

### Prerequisites

- 本地服务运行在 `127.0.0.1:8000`。
- 设置 `PROXY_API_KEYS=sk-local-dev`，或按你的本地约定设置 `FIREWORKS2API_PROXY_KEY`。
- 在 Admin 中配置至少一个 Fireworks key。
- 确保 `kimi-k2.6` alias 已配置，用于 text / vision / reasoning smoke。
- embeddings / rerank 默认跳过，除非你额外配置了对应 alias。

### Base smoke

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

### Advanced smoke

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

### Optional delete response smoke

```powershell
$env:FIREWORKS2API_BASE_URL="http://127.0.0.1:8000"
$env:FIREWORKS2API_PROXY_KEY="sk-local-dev"
$env:FIREWORKS2API_SMOKE_DELETE_RESPONSE="true"
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

### Verified capabilities / 保守限制

- Verified: base smoke, stream, errors, Responses function tool loop, Responses MCP/SSE continuation via `https://mcp.deepwiki.com/mcp`, Responses image smoke with Kimi, Chat image smoke, Anthropic image URL/base64 smoke, and reasoning smoke with Kimi.
- Continuation caveat: Responses continuation should not send `max_tool_calls` after the observed `400`.
- Conservative limits: embeddings/rerank remain skipped unless aliases are configured; do not generalize these smoke results to all models.
