# Public API roadmap

> Scope: docs only. Keep this aligned with `docs/public-api.md` and `docs/public-to-fireworks-adapter-matrix.md`.

## P0 done

- Public `/v1` routes and adapter boundaries are documented.
- OpenAI Chat, Responses, Embeddings, and Anthropic Messages P0 shape handling is recorded.
- Current conservative validation status is documented in the adapter matrix and release checkpoint.

## P1 public adapter gaps

- Responses MCP: official extras are now locally validated; keep production MCP SLA caveated and document the minimal core separately from Fireworks-only fields.
- OpenAI Responses stream shape: typed stream passthrough is locally contract-tested, including terminal usage handling.
- Anthropic stream/tool round-trip: tool_use/tool_result and stream tool events are locally contract-tested; keep broader model-by-model behavior conservative.
- Official SDK live smoke: real SDK smoke has now passed with `openai 2.35.1` and `anthropic 0.100.0` against local `127.0.0.1:8000`.
- Embeddings / rerank live smoke: intentionally skipped per user; keep that caveat explicit.

## P2 broader surface coverage

- Expand the all-model matrix beyond the current core routes.
- Add optional surfaces only when they are officially documented and smoke-verified.
- Keep new extensions clearly separated from the official public contract.
