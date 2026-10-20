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
  every surface, naming `kb_orient` (MCP) and `pyrite orient` (CLI). A blank
  KB name names no KB (never "not found"); a command that needs one answers
  `VALIDATION_FAILED`; a blank KB *path segment* is refused. REST's
  `KB_NOT_FOUND` body is the central handler's (`retryable`, `hint`,
  `did_you_mean`), and MCP's `legacy_error_code` for it is `NOT_FOUND` on
  every tool, `kb_orient` included, except `kb_update` for an unscoped
  caller (an operator key, auth off), whose call reached the old handler and
  its own old code, `UPDATE_FAILED` (#761); a scoped caller was refused
  before the handler, with `NOT_FOUND`, and still is. The admin-only KB routes (`PUT`/`DELETE
  /api/kbs/{name}`, `reindex`, `default-role`, `publish`, `commit`, `push`)
  answer the same refusal. A KB resolved from the index registry carries
  every attribute of its config.yaml entry (`read_only`, `shortname`), and a
  plugin's MCP handlers resolve through the server's config, so no write
  surface lands a write in a read-only KB. The web resolves a `[[short:id]]`
  prefix to the full name before building a link or calling the API (the
  read view's embeds included), so shortname wikilinks keep working there,
  and a link prefix is looked up among your readable KBs only, by full name
  and then by shortname: a private KB that shares the shortname, or whose
  full name is your KB's shortname, never changes the answer.
  `PyriteConfig.kb_name_for` and `get_kb_by_shortname` are gone (they
  mapped across every KB); the one lookup is
  `services/kb_resolver.readable_kb_named`. A refused
  `sw_create_backlog_item` carries `error_code`; a `kb` row with no `source`
  is a user row for the CLI's registry merge, as it is for the resolver.
