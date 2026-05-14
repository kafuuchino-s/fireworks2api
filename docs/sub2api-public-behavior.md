# sub2api public behavior checklist

fireworks2api treats sub2api's public behavior as the practical compatibility target for Claude Code-style chains:

```text
Claude Code -> sub2api /v1/messages -> fireworks2api /v1/responses -> Fireworks /v1/responses
```

The goal is not to copy sub2api internals. The goal is to make fireworks2api's public surfaces behave like the upstream that sub2api expects.

## `/v1/responses` behavior

This is the critical path for the current deployed chain.

- Always return Responses SSE that sub2api can convert back into Anthropic content blocks.
- Every `data:` payload must carry a usable `type`; if Fireworks only sends the SSE `event:` name, mirror it into `data.type`.
- Terminal direct response payloads are wrapped as `{"type":"response.completed","response":{...}}` / equivalent terminal type.
- Function-call streams must expose a stable block lifecycle:
  - `response.output_item.added` for `function_call` before argument events.
  - `response.function_call_arguments.done` carries the complete arguments when deltas were buffered.
  - early function `response.output_item.done` is suppressed until arguments are complete.
- Message/content-part done events that can close the wrong downstream Anthropic block are not forwarded on the sub2api-facing stream.
- Reasoning summary stream events from Fireworks are suppressed for sub2api bridge-shaped requests because sub2api/Claude Code can treat them as extra block pressure.
- Streaming tool or continuation requests force `store=true`, so Fireworks `previous_response_id` continuations remain available.
- With `previous_response_id`, replayed `function_call` items immediately followed by their `function_call_output` are reduced to the tool output.
- If Fireworks reports `previous_response_id` not found before any client-visible output, delete the stale local binding and retry once without `previous_response_id`.
- Codex-style tool calls in outbound Responses SSE are corrected to the OpenCode/sub2api names expected by Claude Code bridges:
  - `apply_patch` / `applyPatch` -> `edit`
  - `update_plan` / `updatePlan` -> `todowrite`
  - `read_plan` / `readPlan` -> `todoread`
  - `search_files` / `searchFiles` -> `grep`
  - `list_files` / `listFiles` -> `glob`
  - `read_file` / `readFile` -> `read`
  - `write_file` / `writeFile` -> `write`
  - `execute_bash` / `executeBash` / `exec_bash` / `execBash` -> `bash`
  - `fetch` / `web_fetch` / `webFetch` -> `webfetch`
- Tool argument names are corrected for the known hot paths:
  - `bash.work_dir` -> `bash.workdir`
  - `edit.file_path` / `edit.path` / `edit.file` -> `edit.filePath`
  - `edit.old_string` -> `edit.oldString`
  - `edit.new_string` -> `edit.newString`
  - `edit.replace_all` -> `edit.replaceAll`
- Prompt-cache fields are preserved and forwarded:
  - `prompt_cache_key`
  - `prompt_cache_isolation_key`
  - `perf_metrics_in_response`

## Sticky routing and cache affinity

Stable routing prioritizes cache identity over continuation identity:

```text
prompt_cache_key -> user -> affinity headers -> previous_response_id -> fallback
```

This keeps sub2api prompt-cache affinity stable while still allowing Fireworks response lifecycle routing to reuse the key that created a stored response.

## `/v1/messages` behavior

The current Claude Code chain does not call fireworks2api `/v1/messages` directly. It calls sub2api `/v1/messages`, and sub2api calls fireworks2api `/v1/responses`.

Keep fireworks2api `/v1/messages` simple and Fireworks-native for now. If fireworks2api later needs to replace sub2api directly, implement sub2api-style Messages behavior as a separate product pass:

- Anthropic Messages request -> Responses request.
- Responses SSE -> Anthropic Messages SSE.
- Session binding from prompt-cache identity to `previous_response_id`.
- Automatic retry without `previous_response_id` when upstream reports previous response not found.

## Error/retry behavior to mirror next

Low-risk items to align later:

- Responses stream errors must be protocol-shaped, not custom free-form SSE.
- Upstream failures before any client-visible output can fail over.
- Upstream failures after output starts should not create invalid downstream event sequences.
- Retry/correction behavior after client-visible stream output has started remains intentionally conservative.

Avoid copying sub2api account/provider internals, OAuth flows, WebSocket gateway complexity, or admin UI behavior.
