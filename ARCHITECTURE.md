# Architecture

## Goal

This project is a Django-first modular monolith designed for long-term
maintainability and AI-assisted development.

The architecture deliberately preserves Django's productivity. Framework
abstraction is introduced only where complexity or dependency volatility
justifies it.

## Module metadata contract

Every non-core module declares two **independent** fields in `module.yaml`:

- `kind` describes responsibility: `business`, `integration`, or `support`.
- `architecture` describes the current structural level: `simple`, `medium`,
  or `complex`. This is not a ranking of business importance.

For example, a module can be an `integration` with `medium` architecture.
The metadata `type` field is retired because it mixed responsibilities with
complexity. Use `--preset` to select a scaffold independently of the module metadata.
The old `--type` option is a deprecated, one-template-release alias; it is
**not** a module kind or complexity level. Presets create only flat files and
necessary Django packages, never speculative `domain/`, `ports/` or
`adapters/` directories.

```yaml
name: tickets
kind: business
architecture: medium
depends_on:
  - files
  - organizations
emits:
  - tickets.ticket-created
consumes: []
```

`scripts/check_architecture.py` enforces valid values, declared import
boundaries, no duplicate/unknown dependencies and an acyclic `depends_on`
graph. A `simple` module must not have `domain/`, `ports/` or `adapters/`
directories. It deliberately does **not** infer `medium` or `complex`
from a `services.py` file: complexity is a design choice and deeper checks
require behavior-based tests. `depends_on` represents **Python code imports**,
not event topics consumed at runtime. Producer/subscriber contracts are a
separate concern for the outbox hardening PRs.

## Module maturity

A module grows only as needed.

### Simple

```text
API -> ORM
```

Use this for straightforward CRUD and framework-native behavior.

### Medium

```text
API -> Service -> ORM
          |
       Selector
```

Use services for meaningful business mutations and selectors for reusable or
complex reads.

### Complex

```text
API -> Service -> Domain -> Port -> Adapter
               \-------> ORM
```

Introduce domain objects and ports/adapters only when business complexity or a
volatile external dependency warrants the extra boundary.

## Dependency principles

- Keep code local to the module that owns the behavior.
- Django/DRF are first-class tools, not dependencies to hide by default.
- External providers should enter through small, stable boundaries.
- Do not expose vendor SDK objects throughout business code.
- Prefer existing shared primitives over module-specific duplicates.
- Do not create shared abstractions until multiple modules genuinely need them.

## AI-first design

Coding agents should need local context, not the whole repository.

Each future business module may expose a small `module.yaml` and concise
README describing ownership, dependencies, events, and invariants. These files
must complement code rather than duplicate global rules from `AGENTS.md`.

The preferred agent behavior is:

```text
reuse -> compose -> customize
```

not:

```text
rediscover -> reinvent -> re-architect
```

## Evolution

Start Django-native. Extract boundaries when complexity appears. Keep framework
and package upgrades concentrated at interfaces that actually depend on them,
without paying abstraction cost everywhere.
