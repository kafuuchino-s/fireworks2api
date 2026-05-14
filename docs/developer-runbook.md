# Developer Runbook

This runbook is for implementation work in `fireworks2api`. It focuses on test/compile checks, docs-backed field handling, route tracing, and safe logging.

## 1) Test commands

Use the project virtualenv for validation:

```powershell
.venv\Scripts\python.exe -m pytest
```

For a compile-only check:

```powershell
.venv\Scripts\python.exe -m compileall app tests
```

For inference smoke coverage:

```powershell
.venv\Scripts\python.exe scripts\fireworks_inference_smoke.py
```

Guidance:

- Run the smallest command that validates the change.
- Do not claim a smoke result unless the script was actually executed.
- Keep smoke notes conservative when aliases or optional features are absent.

## 2) Compile / LSP hygiene

Before considering a change done:

- compile the Python sources;
- check for diagnostics if the change touches request validation, adapters, or public route code;
- keep route/module boundaries clean so LSP errors do not hide adapter regressions.

If a change introduces typing issues, fix them before expanding scope.

## 3) Adding public fields: official vs Fireworks matrix

When adding or changing a public request field, classify it first:

| Class | Meaning | Typical action |
| --- | --- | --- |
| official | Part of the public OpenAI / Anthropic shape | validate, map, or harmlessly drop |
| extension | Fireworks-specific surface | only expose when explicitly documented |
| harmless official | Public field with no upstream need | accept and drop |
| unsupported | No documented contract or mapping | reject |

Working rule:

1. Check the public contract docs first.
2. Decide whether the field is official, harmless official, extension, or unsupported.
3. If it is public/official, keep the public shape separate from the Fireworks-native payload.
4. Update the adapter-matrix docs when behavior changes.

Examples of conservative handling:

- OpenAI Chat `role=tool`: official, mapped.
- OpenAI Embeddings `encoding_format=float`: harmless official, drop.
- OpenAI Embeddings `encoding_format=base64`: unsupported, reject.
- Responses `service_tier`: reject until Fireworks support is explicitly confirmed for that surface.

## 4) Adding Fireworks-native fields from docs

When a new Fireworks-native field is needed:

1. Verify it against the Fireworks official docs cache or fetch the missing page from `https://docs.fireworks.ai/llms.txt`.
2. Add the field only to the Fireworks-native adapter path that needs it.
3. Keep the public route contract unchanged unless the public API explicitly exposes it.
4. Document it as a Fireworks extension if it is not part of the public OpenAI / Anthropic shape.

Do not infer Fireworks-native semantics from OpenAI or Anthropic docs. Use Fireworks docs as the source of truth for native fields and endpoint behavior.

## 5) Model management implementation boundaries

Model management is registry-first now:

- The built-in official Fireworks registry in `app/control/fireworks_model_registry.py` is the primary Admin catalog.
- `/admin/fireworks/models` should default to `source=official` and work without a Fireworks key.
- `source=inference` and `source=account` are discovery/import assistance paths only.
- Manual add should remain explicit `alias -> upstream_model/router` mapping.
- `/admin/models/import` should require explicit alias inputs and should not infer basename or suggested aliases.
- Public `/v1/models` remains a local enabled-mapping view only.

When adjusting model-management behavior, update docs rather than relying on discovery endpoints as the source of truth.

## 6) Route trace expectations

Route traces are a developer aid, not a customer feature.

Expected trace path:

```text
public route -> endpoint adapter -> Fireworks-native route -> Fireworks transport
```

Trace data should help answer:

- which route handled the request;
- which adapter module/function transformed it;
- which Fireworks endpoint/path was selected;
- which fields were mapped, forwarded, omitted, or rejected;
- which routing strategy or key-affinity path was used;
- what status, usage, or cache metadata returned.

Safety boundary:

- do not store full prompts, full bodies, tool args, tool outputs, image URLs, base64 data, API keys, or raw stable keys;
- keep traces structured and minimal;
- use the trace to explain behavior, not to mirror the request payload.

## 7) Safe logging / redaction

Logging should preserve observability without leaking secrets.

Allowed examples:

- request metadata;
- route and adapter names;
- model alias and upstream model;
- key fingerprint;
- stable key hash;
- token/usage/cache counts;
- upstream request id;
- error class/type.

Disallowed examples:

- full Fireworks keys;
- Authorization header values;
- full prompt text;
- full request bodies;
- image URLs or base64 image data;
- tool input/output content;
- raw stable keys or raw route keys.

If a log line needs more context, prefer structured metadata over free-form payload dumps.

## 8) Fixer / Oracle workflow notes

Use the fixer path for small, bounded implementation changes:

- read the exact file(s) before editing;
- change only the requested scope;
- run the smallest relevant validation afterward.

Use Oracle or higher-level review only when the change spans multiple layers, requires policy decisions, or touches a non-trivial adapter boundary.

Good fixer habits:

- keep public route, adapter, and dataplane concerns separate;
- update docs alongside behavior changes when the public contract changes;
- avoid broad refactors when the task is about one field or one route.

## 9) Implementation reminders

- Public routes are thin; adapters own field policy.
- Fireworks-native transport should stay product-neutral.
- Do not overclaim live smoke in docs, tests, or commit messages.
- If a route is intentionally skipped or partial, say so plainly.
