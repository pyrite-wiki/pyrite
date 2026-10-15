- **Breaking: a shortname is link syntax only.** `[[short:id]]` keeps working,
  but `-k <shortname>`, REST `kb=<shortname>` (and `/api/kbs/<shortname>/...`)
  and MCP `kb_name=<shortname>` are refused with `KB_NOT_FOUND`; the refusal
  names the full name in `did_you_mean` when you can read that KB. Use the full
  name. This reverses the REST shortname mapping 0.25.7 added (#912).
  One resolver now answers "which KB" on every surface (#952): an unknown
  name is refused everywhere, for an operator key and auth-disabled too (REST
  and MCP used to let an unscoped caller's unknown `kb` through to a handler
  that answered an empty result), the permissions and worktree routes
  included; a KB you cannot read answers exactly like an unknown one; a KB
  registered after a server started is found (#925); the refusal carries
  `did_you_mean` (near matches from the KBs you can read) and one hint on
  every surface. A blank KB name names no KB (never "not found"); a command
  that needs one answers `VALIDATION_FAILED`. REST's `KB_NOT_FOUND` body is the
  central handler's (`retryable`, `hint`, `did_you_mean`), and MCP's
  `legacy_error_code` for it is `NOT_FOUND` on every tool, `kb_orient` included.
