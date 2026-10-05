# Architecture

## Goal

This project is a Django-first modular monolith designed for long-term
maintainability and AI-assisted development.

The architecture deliberately preserves Django's productivity. Framework
abstraction is introduced only where complexity or dependency volatility
justifies it.

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
