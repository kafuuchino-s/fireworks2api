# Public-to-Fireworks Adapter Matrix

> Scope: docs only. This matrix records the current **P0 completed state** for the public OpenAI / Anthropic surfaces and how they map to Fireworks-native internals.
>
> Rule of thumb: **official public shapes stay separate from Fireworks-native internals**. Public-compatible fields may be mapped, dropped, rejected, or explicitly treated as Fireworks extensions.

## Taxonomy / 分类

| Term | Meaning |
| --- | --- |
| official | Public OpenAI / Anthropic field or behavior. |
| extension | Fireworks-specific field or behavior. |
| map | Convert the public field into a Fireworks-native shape. |
| drop | Accept the public field but remove it before upstream send. |
| reject | Reject the field or request. |
| validate | Enforce the public contract before mapping or forwarding. |

## Status summary

- **P0 completed**: official public shapes are separated from Fireworks-native internals.
- **Validation**: **374 passed, compileall passed, LSP 0 diagnostics**.

## Public / `/v1` route coverage

| Route | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| `GET /v1/models` | official | map | OpenAI model list shape | P0 done |
| `GET /v1/models/{model}` | official | map | OpenAI model object shape | P0 done |
| `POST /v1/chat/completions` | official | map | Fireworks chat completion endpoint | P0 done |
| `POST /v1/completions` | official | map | Fireworks completions endpoint | P0 done |
| `POST /v1/responses` | official | map / fallback | Fireworks responses endpoint; simple non-stream priority text cross-endpoint falls back through Chat Completions priority and synthesizes a Responses-shaped response (`resp_fallback_chatcmpl-*`) | P0 done |
| `GET /v1/responses` | extension | extension | Fireworks local lifecycle/list support | P0 done |
| `GET /v1/responses/{id}` | official | map | Fireworks responses retrieve endpoint | P0 done |
| `DELETE /v1/responses/{id}` | official | map | Fireworks responses delete endpoint | P0 done |
| `POST /v1/embeddings` | official | map | Fireworks embeddings endpoint | P0 done |
| `POST /v1/messages` | official | map | Fireworks Anthropic Messages endpoint | P0 done |
| `POST /v1/rerank` | extension | extension | Fireworks rerank endpoint | P0 done; live smoke intentionally skipped per user |

## OpenAI Chat

| Public field / feature | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| `role=tool` | official | map | Fireworks chat tool-role message shape | P0 done |
| `stream_options.include_usage` | official | map | preserve stream usage passthrough / terminal usage extraction | P0 done |
| `user` string | official harmless field | validate + drop | no internal equivalent needed | P0 done |
| `tool_choice='any'` | official-like but unsupported | reject | no Fireworks equivalent in current adapter policy | P0 done |

## OpenAI Responses

| Public field / feature | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| `input_image` | official | map | Fireworks image input shape | P0 done |
| `function_call_output` | official | map | Fireworks `tool_output` / continuation shape | P0 done |
| `user` string | official harmless field | validate + drop | no internal equivalent needed | P0 done |
| `service_tier` | official-like but unsupported | fallback on simple non-stream text; otherwise reject | simple priority text fallback uses Chat Completions priority; complex/stream/tool/MCP/image/reasoning/lifecycle priority Responses requests remain rejected | P0 done |
| MCP extras beyond the minimal accepted shape | extension / unsupported | reject | only the documented minimal MCP core is accepted; official extras are locally validated and headers are redacted | P1 implemented + tested |

## Embeddings

| Public field / feature | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| `encoding_format=float` | official harmless field | drop | no internal equivalent needed | P0 done |
| `encoding_format=base64` | unsupported | reject | no internal equivalent | P0 done |
| `user` | official harmless field | drop | no internal equivalent needed | P0 done |

## Anthropic Messages

| Public field / feature | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| `anthropic-version` | required official header | validate | required for public Anthropic surface | P0 done |
| `anthropic-beta` | official harmless header | drop | no internal equivalent needed | P0 done |
| `tool_use` / `tool_result` | official | validate | Fireworks Anthropic tool-message shapes | P0 done; live tool round-trip returned `42` |
| `tool_choice` | official | validate | mapped into Fireworks Anthropic request shape | P0 done |
| `output_config` | extension | extension | Fireworks-only surface | P0 done |
| `raw_output` | extension | extension | Fireworks-only surface | P0 done |
| `service_tier` | extension | extension | Fireworks-only surface; Responses priority is only a documented cross-endpoint fallback, not a Fireworks-native Responses contract | P0 done |

## Auth / error envelope notes

| Area | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| OpenAI auth | official | validate | `Authorization: Bearer <api_key>` | P0 done |
| Anthropic auth | official | validate | `x-api-key` or `Authorization: Bearer <api_key>` plus required `anthropic-version` | P0 done |
| OpenAI error envelope | official | validate | OpenAI-style `error` object envelope | P0 done |
| Anthropic error envelope | official | validate | Anthropic-style error envelope | P0 done |

## Extensions / Rejects

| Public field / feature | Public classification | Adapter action | Fireworks / internal shape | Tests / status |
| --- | --- | --- | --- | --- |
| Fireworks-only request/response shapes | extension | extension | kept outside official public contract | P0 done |
| Unsupported official values / extras | unsupported | reject | no internal mapping unless explicitly added | P0 done |
| Harmless official fields with no internal need | official harmless | drop | intentionally omitted from Fireworks payload | P0 done |

## P1 / P2 roadmap

| Priority | Item | Suggested direction | Owner | Tests / status |
| --- | --- | --- | --- | --- |
| P1 | Broader Responses lifecycle coverage | keep official shape separate while expanding retrieve/list/delete parity | adapter + tests | see `docs/public-api-roadmap.md` |
| P1 | More structured MCP support | accept only documented core fields, then expand cautiously | adapter + tests | see `docs/public-api-roadmap.md` |
| P1 | More tool-loop end-to-end cases | add model-by-model tool continuation coverage | adapter + tests | see `docs/public-api-roadmap.md` |
| P2 | Additional embeddings/rerank smoke coverage | currently intentionally skipped per user; keep live-smoke caveat explicit | smoke + docs | see `docs/public-api-roadmap.md` |
| P2 | More Anthropic compatibility edges | extend validation only after official/documented need is proven | adapter + tests | see `docs/public-api-roadmap.md` |
| P2 | Route/document parity audit | keep this matrix aligned with `docs/public-api.md` | docs | see `docs/public-api-roadmap.md` |

## Notes

- This file is intentionally narrower than the broader inference matrices: it focuses on **public surface → adapter → Fireworks-native** mapping.
- The key design rule is to keep **official public shapes**, **adapter policy**, and **Fireworks-native internals** explicitly separated so docs do not imply unsupported cross-surface equivalence.
- `/v1/responses` priority fallback is intentionally narrow: simple non-stream text only, via Chat Completions priority, with synthesized Responses-shaped output and no lifecycle binding.
- No live-smoke or SDK compatibility is overclaimed here; the table reflects the current documented P0 adapter state only.
