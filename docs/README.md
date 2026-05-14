# Documentation index

This directory contains the project docs for setup, public API contracts, routing, Fireworks-native behavior, operational runbooks, and release checkpoints.

## Quick start

- `../README.md` — fast setup, public routes, smoke commands, and trace command

## Public API contract

- `public-api.md` — public contract summary and route table

## Adapter / routing

- `public-to-fireworks-adapter-matrix.md` — public field -> adapter -> Fireworks-native mapping
- `inference-routing.md` — public route -> adapter -> Fireworks-native route mapping
- `fireworks-field-crosswalk.md` — field-by-field Fireworks/public crosswalk
- `route-transform-trace.md` — route trace and transform-debug workflow

## Fireworks-native status

- `fireworks-native-capabilities.md` — verified live smoke scope and capability notes
- `fireworks-inference-matrix.md` — inference capability matrix and caveats

## Operations / runbooks

- `operator-runbook.md` — startup, Admin key onboarding, smoke, trace, and troubleshooting
- `developer-runbook.md` — tests, adapter workflow, public-vs-native field policy, and redaction rules
- `route-transform-trace.md` — admin trace/debug runbook for request routing and transforms

## Release / checkpoint

- `release-checkpoint.md` — current release checkpoint, skipped/partial items, and validation commands
- `public-api-roadmap.md` — P1/P2 public adapter and smoke roadmap

## Local Fireworks docs cache

- `fireworks/` — gitignored local cache of Fireworks docs downloaded from `https://docs.fireworks.ai/llms.txt`

The `docs/fireworks/` cache is for maintainers and automation only. It is not required for users of the project.
