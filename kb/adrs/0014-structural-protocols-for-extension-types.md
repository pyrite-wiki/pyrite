---
id: adr-0014
type: adr
title: "Structural Protocols for Extension Type Interoperability"
adr_number: 14
status: accepted
deciders: ["markr"]
date: "2026-03-01"
tags: [architecture, extensions, protocols, type-system]
links:
- extension-type-protocols
- intent-layer-guidelines-and-goals
- bhag-self-configuring-knowledge-infrastructure
---

# ADR-0014: Structural Protocols for Extension Type Interoperability

## Context

Pyrite's extension ecosystem needs a way for plugins to interoperate without hard dependencies on each other. The journalism plugin needs "claimable" behavior for leads. The software plugin needs "evidence-linked" behavior for QA. Without a dependency mechanism, every extension that needs another extension's behavior must either duplicate it or declare a hard dependency — creating a brittle graph where you can't install journalism without task, versions must match, and the ecosystem fragments.

### Approaches considered

**1. npm/pip-style dependency trees.** Extensions declare `depends: [pyrite-task>=1.0]`. Install resolves the chain. Problem: couples extensions to specific implementations, not behaviors. The journalism plugin doesn't need *the task plugin* — it needs *something claimable*. Dependency trees also create version hell as the ecosystem grows.

**2. Java/TypeScript-style interfaces.** Define `IClaimable` as a nominal interface. Extensions explicitly implement it. Problem: nominal typing requires inheritance or declaration — a type that happens to have the right fields and behavior doesn't satisfy the interface unless it says `implements IClaimable`. This is bureaucratic and fragile.

**3. Structural subtyping via protocols (Python's `typing.Protocol`).** Define behavioral contracts as field + behavior requirements. Any type satisfying the contract is compatible, regardless of whether it declares anything. Problem: can become a mess of micro-contracts if not carefully scoped.

**4. No formal system.** Document conventions, let developers figure it out. Problem: doesn't scale, agents can't verify compatibility programmatically.

### Why protocols win for Pyrite

The critical insight: Pyrite's primary consumers are AI agents, not human developers. The traditional objection to fine-grained protocols — "too many contracts for developers to track" — doesn't apply when agents can search a KB of protocol definitions and hold many contracts in working context simultaneously. Protocols are KB entries, searchable and documented via the intent layer.

Additionally, Pyrite's existing infrastructure already operates on structural checks: QA validation checks field presence, before_save hooks check status transitions, schema validation checks field types. The platform already does structural typing — protocols formalize what's already happening.

## Decision

**Pyrite uses structural subtyping via protocols for extension type interoperability, modeled on Python's `typing.Protocol` (PEP 544).**

### Core principles

**Structural, not nominal.** A type satisfies a protocol if it has the required fields and behaviors. No inheritance or explicit declaration needed. If it has `status`, `assignee`, and a claim workflow, it's claimable — regardless of what it calls itself.

**Small primitives that compose.** Following Python's design philosophy, protocols are small behavioral contracts (like `__iter__`, `__len__`). Larger behavioral bundles are compositions of primitives. A few well-designed contracts go a long way.

**Platform-backed behavior.** Protocols that matter are ones the platform runtime knows how to use — analogous to Python's dunder methods that the interpreter calls. A protocol without platform support is just documentation. A protocol with platform support is an extension point.

### The Python analogy

This mapping is deliberate and serves as the conceptual framework:

| Python | Pyrite |
|--------|--------|
| Class | Entry type (data + behavior) |
| `typing.Protocol` | Knowledge protocol (structural contract) |
| Dunder methods (`__iter__`, `__len__`) | Platform operations (`before_save`, `validate`, `claim`) |
| Type annotations | Schema definitions |
| `mypy` | `pyrite ci` / `pyrite protocol check` |
| Module | KB (namespace with exports and docstrings) |
| `ABC` | Explicit type extension (`ADREntry(NoteEntry)`) |

### Primitive protocols

The platform defines a small set of primitive protocols — each maps to a platform operation the runtime knows how to perform:

| Primitive | Data Contract | Platform Operation |
|-----------|--------------|-------------------|
| `has_status` | `status` field with defined values | Platform can query/filter by status |
| `has_assignee` | `assignee` field | Platform can query/filter by assignee |
| `has_evidence` | `evidence` links + `confidence` number | QA can follow evidence chains |
| `has_parent` | `parent` reference field | Platform can build hierarchies |
| `workflow` | Valid transitions defined in schema | `before_save` enforces transitions |
| `atomic_claim` | CAS operation on status + assignee | Platform provides atomic claim |
| `rollup` | After-save sibling check + parent update | Platform provides auto-rollup |
| `source_chain` | QA follows evidence links to verify targets | QA validates completeness |

### Composed bundles

Common combinations get named for convenience:

| Bundle | Composed of | Use case |
|--------|-------------|----------|
| `claimable` | `has_status` + `has_assignee` + `workflow` + `atomic_claim` | Agent coordination |
| `decomposable` | `has_parent` + `rollup` | Task/investigation hierarchies |
| `verifiable` | `has_evidence` + `source_chain` | Research, QA |

Bundles are shorthand. The primitives are what the platform checks. An agent building an extension thinks in bundles. The platform operates on primitives.

### Extension dependency via protocols

Instead of depending on specific extensions:

```yaml
# Extension manifest
requires_protocols:
  - claimable    # I need something claimable in the KB
provides_protocols:
  - claimable    # My lead type is claimable
  - verifiable   # My finding type is verifiable
```

`pyrite extension install` checks that the target KB has types satisfying required protocols. It doesn't care which extension provides them.

### Schema-as-implementation

When a type's schema defines the right fields and transitions, the platform can provide default behavior — no plugin code needed. A type definition in kb.yaml that has `status` with valid transitions and an `assignee` field automatically gets workflow enforcement and claim support. The schema is the program.

This is the path toward the BHAG: an agent defines a schema, and the schema generates infrastructure (validation, search, workflow enforcement, agent coordination) without writing a plugin.

## Consequences

### Positive

- **Extensions decoupled.** Journalism doesn't depend on task. It depends on `claimable`. Any provider works.
- **Agent-friendly.** Protocol definitions are KB entries — searchable, documented, consumable by agents building extensions.
- **Platform behavior for free.** Define the right fields in your schema, get workflow enforcement, QA validation, and coordination without writing plugin code.
- **Composable.** Small protocols compose into rich behaviors. New bundles emerge from existing primitives.
- **Backwards compatible.** Existing extensions continue to work. Protocols formalize existing structural checks.

### Negative

- **Learning curve.** Developers new to structural typing may find it unfamiliar. Mitigated by: the Python analogy, the pyrite-dev skill encoding the patterns, and the fact that most users never interact with protocols directly.
- **Implicit satisfaction risks.** A type might accidentally satisfy a protocol it didn't intend to. Mitigated by: optional explicit `satisfies` declarations, and protocol names that are specific enough to avoid false matches.
- **Protocol evolution.** Changing a protocol's requirements can break existing satisfying types. Mitigated by: versioned protocols (`claimable/1.0`), additive-only changes within a version.

### Implementation phases

1. **Protocol definitions** [S]: `protocol` entry type, core primitives and bundles as KB entries, `pyrite protocol list/show`. No automated checking — protocols are documentation.
2. **Satisfaction checking** [M]: `pyrite protocol check`, extension manifest `requires_protocols`/`provides_protocols`, install-time validation.
3. **Platform-backed default behavior** [L]: Schema-defined fields automatically get platform behavior (workflow enforcement, claim, rollup) without plugin code. This is the BHAG convergence point.

## Related

- [Extension Type Protocols design doc](../documents/extension-type-protocols.md) — full design with examples
- [Intent Layer design doc](../designs/intent-layer-guidelines-and-goals.md) — protocols need guidelines and evaluation rubrics
- [BHAG: Self-Configuring Knowledge Infrastructure](../designs/bhag-self-configuring-knowledge-infrastructure.md) — protocols enable schema-as-program
- [ADR-0008: Structured Data and Schema](0008-structured-data-and-schema.md) — schema-as-config foundation
- [ADR-0009: Type Metadata and Plugin Documentation](0009-type-metadata-and-plugin-documentation.md) — type metadata resolution system
