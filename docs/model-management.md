# Model management

This document summarizes the completed model-management refactor and the intended Admin/public boundaries.

## Source of truth

- The built-in Fireworks official model registry is the primary Admin model catalog.
- The registry lives in `app/control/fireworks_model_registry.py`.
- Discovery endpoints are not the primary metadata source anymore.

## Admin catalog behavior

`/admin/fireworks/models` is registry-first:

- default `source=official`;
- does not require a Fireworks key;
- shows the official registry as the default browse path.

`source=inference` and `source=account` are advanced discovery/import helpers:

- use them when you need to inspect live Fireworks inference/account state;
- do not treat them as the authoritative catalog.

## Import and manual mapping

Manual model creation is explicit mapping:

- `alias -> upstream_model`
- or `alias -> upstream_router`

The import flow is intentionally strict:

- `/admin/models/import` requires explicit `alias` or `aliases`;
- it no longer guesses basename aliases;
- it no longer invents suggested aliases;
- operators must choose the mapping name intentionally.

## Public model list

`GET /v1/models` remains a local-only view:

- it returns only enabled local mappings;
- it does not expose the Fireworks registry directly;
- it is not a live discovery endpoint.

## Pricing reference

For pricing-related model metadata, use the official Fireworks pricing page:

- `https://docs.fireworks.ai/serverless/pricing`

## Operator guidance

- Prefer the official registry for browsing and selecting models.
- Use inference/account discovery only for advanced inspection or import assistance.
- Keep manual aliases explicit and stable.
- Update docs whenever model-management behavior changes.
