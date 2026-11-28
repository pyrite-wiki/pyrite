---
id: hosting-security-hardening
title: "Hosting security hardening for journalist deployments"
type: backlog_item
tags: [security, privacy, deployment, journalism]
links:
- target: hosting-security-requirements
  relation: implements
  kb: pyrite
importance: 5
kind: epic
status: retired
priority: high
effort: XL
rank: 1800
lifecycle: archived
---

## Problem

Pyrite targets investigative journalists as a key audience, but the current deployment stack has not been audited against the threat model of legal compulsion against service providers. The hosting security requirements document ([[hosting-security-requirements]]) defines 8 requirement areas (REQ-1 through REQ-8) with concrete audit checklists.

## Scope

This epic covers the implementation and audit work across all 8 requirement areas. Work should be broken into phases — the static analysis audit (Phase 1) first to understand the current gap, then implementation work to close gaps, then runtime/adversarial verification.

### Phase 1: Static Analysis Audit (S)
- Run REQ-1 through REQ-6 audit checklists against current codebase
- Grep for IP address handling, logging patterns, metadata storage
- Review all database schemas for stored fields
- Review Dockerfile and container configuration
- Produce a gap analysis document

### Phase 2: Application-Level Hardening (M)
- REQ-1: Strip IP address logging from application code, error handlers, and request middleware
- REQ-2: Audit account model, reduce metadata precision, remove session history
- REQ-6: Verify MCP access controls, strip query/result logging

### Phase 3: Container Hardening (M)
- REQ-5: Non-root user, minimal base image, read-only filesystem, network minimization
- REQ-1.2: Hardened web server log configuration in default container
- REQ-4: Git metadata leakage review and mitigation

### Phase 4: Encryption at Rest (L)
- REQ-3: Evaluate and implement encryption at rest for private KBs
- REQ-3.2: Key management architecture (client-side vs server-side tradeoffs)
- REQ-6.2: Document MCP/zero-knowledge tension and chosen architecture

### Phase 5: Threat Model & Documentation (M)
- REQ-8: Publish threat model document per deployment tier
- REQ-3.3: Document court order disclosure surface
- REQ-7: Document managed hosting operator requirements (for TCP)

### Phase 6: Runtime & Adversarial Verification (M)
- Deploy test instance, perform typical operations, examine all artifacts
- Simulate court order scenario, document complete disclosure surface
- Compare against published threat model

## Success Criteria

- All REQ audit checklists pass
- Published threat model document is accurate and matches system behavior
- A journalist can evaluate the security posture of each deployment tier from published documentation
- No IP addresses persist anywhere in a running Tier 1 or Tier 2 deployment
