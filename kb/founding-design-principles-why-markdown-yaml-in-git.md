---
id: founding-design-principles-why-markdown-yaml-in-git
title: 'Founding Design Principles: Why Markdown+YAML in Git'
type: design_doc
tags:
- architecture
- philosophy
- design-principles
- git
- markdown
- trust
status: active
author: markr
date: '2025-06-01'
---

# Founding Design Principles: Why Markdown+YAML in Git

These principles predate the ADR process. They are the foundational reasoning that motivated Pyrite's architecture — the "why behind the why" of ADR-0001 (git-native storage), ADR-0008 (structured data and schema), and the broader Knowledge-as-Code vision. They were established through design discussions before the project had a formal decision-recording process.

---

## Principle 1: Hugo-Style Markdown+YAML Is the Ideal Format for Human-AI Collaboration

The choice of markdown files with YAML frontmatter wasn't arbitrary — it sits at a unique intersection of three properties that no other format achieves simultaneously:

**Human readable.** A person can open any entry in any text editor and immediately understand both its structure (frontmatter) and its content (body). No binary format to decode, no database to query, no specialized viewer required. This matters because knowledge that humans can't read is knowledge humans can't trust.

**Machine parsable.** YAML frontmatter is trivially parsable by any programming language. The format is well-specified, has mature libraries everywhere, and produces clean data structures. An agent doesn't need a specialized SDK or API client to read or write entries — standard YAML and markdown parsers suffice. This makes integration with any agent runtime (OpenClaw, Claude Code, Codex, custom tooling) straightforward.

**AI friendly.** LLMs are natively fluent in both markdown and YAML. They encounter these formats constantly in training data. An AI agent can generate valid entries without special prompting, read entries without format-specific instructions, and reason about the structure of frontmatter fields naturally. The format is within the model's comfort zone in a way that, say, Protocol Buffers or XML would not be.

Hugo (the static site generator) proved this format at scale for content management. Jekyll, Eleventy, and dozens of other tools adopted it. The format has been battle-tested for a decade. Pyrite extends it from content management to knowledge management — same format, richer schemas, AI-native tooling.

The compromise this represents is deliberate: YAML frontmatter is less expressive than a full database schema, and markdown bodies are less structured than XML. But the combination hits a sweet spot where humans can author comfortably, machines can parse reliably, and AI agents can work fluently. Over-optimizing for any one of these three properties would sacrifice the others.

## Principle 2: Git Is the Central Versioning System of the Modern World

The decision to make git the versioning layer wasn't just "we need version control." It was a strategic bet on the most successful distributed infrastructure system ever built.

**Git already won.** From source code to GitOps to Infrastructure-as-Code, git has become the universal version control layer. Billions of dollars of infrastructure — GitHub, GitLab, Bitbucket, CI/CD pipelines, code review tools, deployment systems — exist because git won. Knowledge-as-Code means leveraging all of that infrastructure for free: pull requests for knowledge review, CI for schema validation, branching for experimental knowledge, blame for provenance, diff for change tracking.

**The infrastructure is already built.** By choosing git, Pyrite inherits: hosting (GitHub, GitLab, self-hosted), access control (SSH keys, deploy tokens, org permissions), collaboration workflows (fork, branch, PR, review, merge), automation (GitHub Actions, webhooks, CI/CD), discovery (GitHub search, stars, topics), and distribution (clone, fork, mirror). Building any of this from scratch would be years of work. Choosing git gets it immediately.

**Git is distributed by design.** This isn't just a technical property — it's a resilience property. A Pyrite KB is a git repo. Clone it and you have the complete history. Fork it and you have an independent copy. Mirror it across providers and you have redundancy. No single server failure can destroy the knowledge. No vendor lock-in can hold it hostage. The distributed nature of git means Knowledge-as-Code inherits the same resilience properties that make distributed source control superior to centralized systems.

**Knowledge-as-Code parallels Infrastructure-as-Code.** The IaC revolution happened when teams realized that treating infrastructure definitions as code — versioned, reviewed, tested, deployed through pipelines — produced dramatically better outcomes than clicking through web consoles. Knowledge-as-Code is the same insight applied to knowledge: treat knowledge definitions as code, version them in git, review changes through PRs, validate with CI, and deploy through pipelines. The tooling patterns are identical because the underlying insight is identical.

## Principle 3: Trust Is Credentials + Commits (+ Signatures)

In a world where AI agents produce knowledge, trust becomes the central problem. Pyrite's trust model builds directly on git's existing mechanisms:

**Trust is based on credentials.** Who has push access to the repository? Who has write access to the MCP server? The identity behind an action is the first layer of trust. Git's authentication model (SSH keys, GPG keys, tokens) provides this. Organizational access controls (GitHub teams, branch protection rules) add granularity. This is the same trust model that governs source code — and it works because it's been hardened by millions of organizations over decades.

**Trust is based on the actions taken with those credentials.** Every change to a Pyrite KB is a git commit. Every commit has an author, a timestamp, a diff, and a message. The complete history of who changed what, when, and why is permanently recorded. This audit trail is not a feature Pyrite had to build — it's inherent in the storage format. When an AI agent creates 500 entries overnight, every single one is traceable to a specific commit by a specific credential.

**Signed commits add cryptographic trust infrastructure.** If all commits are signed (GPG or SSH signatures), the trust model strengthens further: you can verify not just that a commit claims to be from a particular author, but that it cryptographically *is* from that author. This enables scenarios like: only human-signed commits are trusted for schema changes; agent-signed commits require human review before merging to main; unsigned commits are rejected at the repository level. Git's existing signature verification infrastructure supports all of this without Pyrite building anything custom.

**The trust model scales with the threat model.** A personal KB might need nothing beyond filesystem permissions. A team KB uses branch protection and PR review. A public KB with agent contributors uses signed commits, CI validation, and tiered merge permissions. The same git-native primitives support all three levels. Pyrite doesn't need its own permissions system for versioned content — it inherits git's, which is already more sophisticated and battle-tested than anything we could build.

---

## How These Principles Connect

These three principles are mutually reinforcing:

- The format (markdown+YAML) is what makes git diffs meaningful — binary formats would make git history useless
- Git's distributed nature is what makes the format portable — clone a repo and you have working knowledge, not a database dump that needs a server
- The trust model only works because git records every change as a discrete, attributable commit — and that only works because each change is a human-readable diff of a text file

Changing any one of these choices would undermine the others. A database backend would break git-native versioning. A binary format would break human readability and meaningful diffs. A custom trust system would abandon decades of hardened infrastructure.

This is why these are *principles*, not just decisions. They constrain the solution space in ways that keep the architecture coherent as the system grows.

## Relationship to the OpenClaw Ecosystem

The OpenClaw explosion of early 2026 validated these principles from the opposite direction. OpenClaw agents operate with system-level access but no structured knowledge layer, no version-controlled memory, and a trust model based on... messaging platform permissions. The result: 386 malicious skills on ClawHub, prompt injection via Moltbook, and fundamentally unauditable agent behavior.

Pyrite's founding principles directly address every OpenClaw weakness:
- **Format**: Agents produce human-readable, schema-validated entries — not opaque state mutations
- **Git**: Every agent action is a reviewable commit — not a fire-and-forget API call
- **Trust**: Credentials, commit signatures, and branch protection — not "whoever has the bot token"

The Knowledge-as-Code vision — awesome lists of subject-matter KBs, forkable and extendable, with MCP servers for agent access and web UIs for human review — is what the agent ecosystem needs but hasn't built. These founding principles are why Pyrite can provide it.
