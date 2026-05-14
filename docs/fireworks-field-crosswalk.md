# Fireworks field crosswalk

Scope: docs only. This file maps **Fireworks-native fields/features** to the current `fireworks2api` adapter/testing state.

It is intentionally conservative:

- **Official fields/features** below come from Fireworks docs and the current `docs/fireworks-inference-matrix.md` / local docs cache.
- **Project smoke env vars** are listed separately and are **not** Fireworks API fields.
- Live smoke status is marked `Live smoked` when the current docs/session explicitly report it.
- MCP/images/reasoning are listed below as live-smoked where documented.
- Embeddings/rerank live smoke remains **skipped unless aliases are configured**.

## Official field crosswalk by endpoint

| Endpoint | Official field / feature | Source doc filename | Current adapter behavior | Tests | Live smoke status | Remaining action |
| --- | --- | --- | --- | --- | --- | --- |
| Chat Completions | `model`, `messages`, `temperature`, `max_tokens`, `service_tier`, `stream` | `post-chatcompletions.md` | Implemented; forwards Fireworks-native chat payloads through `/v1/chat/completions` → `/inference/v1/chat/completions` | Endpoint validation/conversion covered in adapter tests | Base text route: live smoked. Stream: live smoked | Keep conservative field coverage; continue model-specific reasoning/tool work |
| Chat Completions | `role=tool`, `tools`, `tool_choice`, `stream_options.include_usage` | `post-chatcompletions.md` | Implemented; `role=tool` and `stream_options.include_usage` follow the official shape, while `tool_choice='any'` is rejected unless explicitly extended | Tool field validation tests exist | Tested only; no full end-to-end tool loop smoke recorded here | Keep official Chat shape separate from Fireworks-only extensions |
| Chat Completions | `thinking`, reasoning-related fields, `reasoning_effort` | `post-chatcompletions.md` | Implemented conservatively; reasoning/thinking validation exists | Reasoning validation tests exist | Live smoked with `FIREWORKS2API_SMOKE_REASONING=true` and `FIREWORKS2API_REASONING_MODEL=kimi-k2.6` | Expand model-by-model reasoning matrix only when needed |
| Chat Completions | prompt-cache fields / cache usage | `post-chatcompletions.md`, `prompt-caching.md` | Supported and normalized from body/headers/stream terminal chunks | Cache-related tests exist | Live observed, but not reclassified beyond the stated smoke scope | Keep field passthrough and telemetry extraction stable |
| Chat Completions | image content parts | `post-chatcompletions.md` | Validation exists for HTTPS/data URL image parts | Image validation tests exist | Live smoked with `FIREWORKS2API_SMOKE_IMAGES=true`, `FIREWORKS2API_VISION_MODEL=kimi-k2.6`, and HTTPS Wikimedia image URL | Keep model-by-model image coverage conservative |
| Completions | `model`, `prompt`, `max_tokens`, `stream` | `post-completions.md` | Implemented; forwards Fireworks-native completions payloads through `/v1/completions` → `/inference/v1/completions` | Endpoint validation/conversion covered in adapter tests | Base text route: live smoked. Stream: live smoked | Keep payload passthrough aligned with Fireworks docs |
| Completions | Fireworks prompt shapes | `post-completions.md` | Supports string, string list, token IDs, batched token IDs | Shape validation tests exist | Not separately live smoked | No immediate action |
| Completions | `images` | `post-completions.md` | Validation exists for Fireworks-native image prompt shape | Image validation tests exist | Pending config / not live smoked here | Smoke only with an image-capable alias |
| Completions | `echo`, `ignore_eos`, `return_token_ids`, `raw_output`, perf fields | `post-completions.md` | Forwarded as native Fireworks fields | Field passthrough tests exist | Not separately live smoked | Keep passthrough behavior stable |
| Responses | `model`, `input`, `store`, `previous_response_id`, `tools`, `tool_choice`, `reasoning`, `text`, `metadata`, `stream_options.include_usage` | `post-responses.md`, `response-api-guide.md`, `list-responses.md`, `get-response.md`, `delete-response.md` | Implemented; routes `/v1/responses`, `/v1/responses/{id}` and lifecycle calls map to Fireworks native responses endpoints | Endpoint validation, lifecycle, and stream tests exist | Create/list/get/delete lifecycle: live smoked. Typed stream passthrough locally contract-tested | Keep `service_tier` behavior conservative; Fireworks Responses itself does not document priority |
| Responses | `input_image` string, `function_call_output`, `tools` / function tools | `post-responses.md`, `response-api-guide.md` | `input_image` string and `function_call_output` follow the official public shape; Fireworks image object and `tool_output` are extension shapes | Tool-shape tests exist | Live smoked; canonical flat function tool loop passed with `tool_choice='required'`, tool/function call emitted, and continuation passed using `tool_output` / `tool_call_id` with calculator output `42` | Keep official mapped forms separate from extension forms |
| Responses | `service_tier=priority` + simple non-stream text | `post-responses.md` + Chat priority docs | Cross-endpoint fallback via Chat Completions priority; synthesize Responses-shaped output with id `resp_fallback_chatcmpl-*`; no lifecycle binding | Implemented and tested in `tests/test_responses_priority_fallback.py` | Contract-tested; live trace verified tags `responses`, `priority`, `chat_completions`, `cross_endpoint_fallback` | Complex/stream/tool/MCP/image/reasoning/lifecycle priority requests rejected; Fireworks Responses itself still does not document priority |
| Responses | MCP tools / server lifecycle | `response-api-guide.md`, `post-responses.md` | Parsed conservatively; only the minimal documented MCP shape is accepted; extra MCP fields are rejected or deferred | Local contract tests exist | Responses official extras `server_label`, `server_url`, `allowed_tools`, `headers`, `require_approval` are locally validated; unknown extras are rejected and headers are redacted | Keep production MCP stability caveated |
| Responses | image / multimodal inputs | `post-responses.md`, `response-api-guide.md` | Official `input_image` string is accepted; Fireworks image object is an extension shape | Validation tests exist | Live smoked with Kimi using message input content part type `image` and `image_url` object `{url, detail}` | Keep Responses image coverage conservative |
| Responses | `reasoning` / reasoning events | `post-responses.md`, `response-api-guide.md` | Typed SSE usage is supported; reasoning-related responses are handled conservatively | Stream/event tests exist | Live smoked as part of the documented responses stream coverage | Expand model-specific reasoning coverage if required |
| Responses | stream events | `post-responses.md` | Typed SSE events preserved through the adapter | Stream tests exist | Typed stream passthrough locally contract-tested | Keep event passthrough stable |
| Anthropic Messages | `model`, `messages`, `max_tokens`, `thinking`, `tools`, `tool_choice`, image content blocks, `anthropic-version`, `anthropic-beta` | `anthropic-messages.md` | Implemented through Anthropic adapter to Fireworks `/inference/v1/messages`; `anthropic-version` is required/non-empty, `anthropic-beta` is accepted and dropped | Adapter tests exist | Live smoked | Keep behavior aligned with the Fireworks Anthropic docs |
| Anthropic Messages | `tool_use`, `tool_result` | `anthropic-messages.md` | Official validation is enforced | Adapter tests exist | Local round-trip contract tested | Keep official tool block validation strict |
| Anthropic Messages | stream tool events | `anthropic-messages.md` | Fireworks stream tool events are passed through | Adapter tests exist | Local contract tested | Keep stream event passthrough stable |
| Anthropic Messages | `output_config`, `raw_output`, `service_tier` | `anthropic-messages.md` | Fireworks extensions | Adapter tests exist | Live smoked as documented in the advanced smoke coverage | Keep extension boundary explicit |
| Anthropic Messages | cache / usage fields | `anthropic-messages.md` | Cache/read token fields are surfaced when present | Usage tests exist | Live observed in current docs | Maintain telemetry extraction |
| Embeddings | `model`, `input`, `prompt_template`, `dimensions`, `return_logits`, `normalize` | `create-embeddings.md` | Implemented via `/v1/embeddings` → `/inference/v1/embeddings`; `prompt_template`, `normalize`, and `return_logits` are Fireworks extensions | Validation tests exist | Skipped unless an embeddings alias is configured | Keep skipped until an embeddings alias is configured |
| Embeddings | `encoding_format=float`, `user` | `create-embeddings.md` | Accepted and dropped as harmless official fields | Rejection/accept-drop tests exist | Skipped | No action unless Fireworks docs change |
| Embeddings | `encoding_format=base64` | `create-embeddings.md` | Rejected | Rejection tests exist | Skipped | No action unless Fireworks docs change |
| Rerank | `model`, `query`, `documents`, `top_n`, `return_documents`, `task` | `rerank-documents.md` | Implemented via `/v1/rerank` → `/inference/v1/rerank` | Validation tests exist | Skipped unless a rerank alias is configured | Keep skipped until a rerank alias is configured |

## Project smoke env vars vs official Fireworks fields

These are **project controls only** and should not be confused with Fireworks API request/response fields:

| Variable | Purpose | Notes |
| --- | --- | --- |
| `FIREWORKS2API_SMOKE_DELETE_RESPONSE=true` | Opt-in smoke toggle for response deletion | Controls local smoke behavior only |
| `FIREWORKS2API_SMOKE_STREAM` | Enables stream smoke cases | Project smoke control |
| `FIREWORKS2API_SMOKE_ERRORS` | Enables error-path smoke cases | Project smoke control |
| `FIREWORKS2API_SMOKE_ADVANCED` | Enables advanced smoke cases | Project smoke control |
| `FIREWORKS2API_SMOKE_TOOLS` | Enables tools smoke cases | Project smoke control |
| `FIREWORKS2API_SMOKE_MCP` | Enables MCP smoke cases | Project smoke control |
| `FIREWORKS2API_MCP_SERVER_URL` | MCP server URL used for smoke | Required public server URL when MCP smoke is enabled; Fireworks docs backup example `https://mcp.deepwiki.com/mcp` is documented but not separately run here |
| `FIREWORKS2API_SMOKE_IMAGES` | Enables image smoke cases | Project smoke control |
| `FIREWORKS2API_VISION_MODEL` | Vision model alias for image smoke | Required when image smoke is enabled |
| `FIREWORKS2API_IMAGE_URL` | Image URL used for image smoke | Project smoke input |
| `FIREWORKS2API_SMOKE_REASONING` | Enables reasoning smoke cases | Project smoke control |
| `FIREWORKS2API_REASONING_MODEL` | Reasoning model alias for reasoning smoke | Required when reasoning smoke is enabled |
| `FIREWORKS2API_EMBEDDINGS_MODEL` | Embeddings model alias for embeddings smoke | Used only when embeddings smoke is configured |
| `FIREWORKS2API_RERANK_MODEL` | Rerank model alias for rerank smoke | Used only when rerank smoke is configured |
| `FIREWORKS2API_SMOKE_VERBOSE` | Verbose smoke logging | Project smoke control |
| `ENABLE_ADMIN_STATIC=true` | Enables the admin static frontend | Deployment/runtime toggle, not Fireworks-native |
| `ADMIN_TOKEN` | Admin auth bootstrap/config | Project secret/config |
| `PROXY_API_KEYS` | Proxy bootstrap keys | Project runtime config |
| `AFFINITY_HASH_SECRET` | Stable routing affinity secret | Project routing config |
| `LOG_HASH_SECRET` | Hashing/redaction secret for logs | Project logging config |
| `UPSTREAM_BASE_URL` | Upstream base URL override | Project runtime config |
| `SYNC_ENV_KEYS_ON_STARTUP` | Sync env keys on startup | Project bootstrap behavior |

## Notes

- This crosswalk is meant to stay in sync with `docs/fireworks-inference-matrix.md` without duplicating implementation details.
- If a future smoke is added, mark it here only when the existing docs/session explicitly report it as live-smoked.
- Latest all-enabled smoke note: Responses function tool loop passed with canonical flat function tool, `tool_choice='required'`, emitted tool/function call, and continuation passed using `tool_output` / `tool_call_id` with calculator output `42`; Responses MCP/SSE continuation passed with `https://mcp.deepwiki.com/mcp` while `gitmcp` later timed out and should be treated as unstable example connectivity; Responses image passed with Kimi using `image` content part and `image_url` object `{url, detail}`; Chat image already passed; Anthropic image URL and base64 both passed with Kimi; reasoning passed separately with Kimi; `/v1/responses` priority fallback is cross-endpoint Chat-backed, simple-text-only, non-stream only, returns `resp_fallback_chatcmpl-*`, and remains outside documented Fireworks Responses priority surface; embeddings/rerank remain skipped unless aliases are configured; SDK-shaped fixtures exist locally, but real SDK live smoke stays optional/skipped when packages are absent.
