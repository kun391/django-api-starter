# AI Engineering Rules

This file is the source of truth for coding-agent behavior in this repository.
Keep it short. Architectural rationale belongs in `ARCHITECTURE.md`.

## Priorities

1. Correctness and security.
2. Preserve architecture boundaries.
3. Follow the existing module convention.
4. Reuse existing project primitives.
5. Prefer Python/Django capabilities and existing dependencies.
6. Make the smallest correct change.
7. Introduce abstractions only when justified.

## Django

Use Django directly where it provides value. Do not wrap Django merely to hide
Django.

Simple CRUD may use the ORM directly.

Extract services when business mutations become meaningful. Use selectors when
query composition deserves a named boundary.

Use ports/adapters only for volatile boundaries such as external APIs, object
storage, payments, identity providers, message brokers, or external
notification providers.

## Changes

Do not invent a second pattern when one already exists.

Keep changes local to the owning module.

Background tasks call application/service code rather than duplicating business
logic.

Behavior changes require tests.

Prefer composition and reuse over generation from scratch.
