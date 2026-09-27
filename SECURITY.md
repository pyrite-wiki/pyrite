# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.25.x  | :white_check_mark: |
| < 0.25  | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in Pyrite, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, use GitHub's private vulnerability reporting feature:

1. Go to the [Security tab](https://github.com/pyrite-wiki/pyrite/security) of this repository
2. Click "Report a vulnerability"
3. Provide a detailed description of the vulnerability

We will acknowledge receipt within 48 hours. For the supported configurations below, we aim to fix critical issues within 7 days.

## Multi-user is experimental

Pyrite's supported security boundary today is **one operator**: you, your agents (with the MCP read/write/admin tiers), and people you trust with everything on the instance.

Running Pyrite for people who should not see each other's data — accounts with per-KB permissions, anonymous readers, the public `/site`, a shared server on the internet — is **experimental**. Those features exist and are being hardened release by release, and there are known open issues in how access between users and KBs is enforced. Until this notice is removed:

- Treat every KB on a shared instance as readable by every user of that instance.
- Keep genuinely private KBs on a separate instance, or local.
- Reports about isolation between users or KBs are welcome through the private channel above. They are fixed in regular releases, batched, rather than as emergency patch releases.

## Scope

This policy applies to the `pyrite` Python package and its server components (REST API, MCP server).

## Best Practices for Users

- Never commit your `.env` file or API keys to version control
- Use read-only KB configurations for untrusted data sources
- Run the REST API behind a reverse proxy in production
- Keep dependencies up to date
