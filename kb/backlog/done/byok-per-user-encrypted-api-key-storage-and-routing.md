---
id: byok-per-user-encrypted-api-key-storage-and-routing
title: 'BYOK: per-user encrypted API key storage and routing'
type: backlog_item
tags:
- ai
- auth
- multi-user
- byok
importance: 5
kind: feature
status: done
priority: high
effort: M
rank: 0
---

Each user stores their own Anthropic/OpenAI API key, encrypted with Fernet (infrastructure exists in AuthService). LLMService resolves the requesting user's key per-request via DI. UI: 'My API Keys' section in user profile/settings. Clear error when user has no key configured.

Cross-ref: byok-api-key-management-for-multi-user-deployments
