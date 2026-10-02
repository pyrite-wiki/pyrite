- **`kb_orient` / `pyrite orient` is true as the first call** (#66, and the
  declared-types part of #232).
  - **Public shape change, MCP error codes.** A KB name that is not a KB, or
    that the caller may not read, now answers `KB_NOT_FOUND` over MCP, the
    code REST and the CLI already used (ADR-0037). Two older codes are
    replaced: the `NOT_FOUND` every MCP tool, prompt and resource gave a
    caller with per-KB scoping for a KB outside its readable set, and the
    `OPERATION_FAILED` `kb_orient` gave everyone else. The old code is
    carried in `legacy_error_code` for this release only and is removed in
    the next; match on `error_code`. Entry-level `NOT_FOUND` answers and the
    other tools' own not-found answers are unchanged.
  - `kb_orient` with no `kb_name` (and `pyrite orient` with no `-k`, which
    used to be a usage error) lists the knowledge bases the caller may read,
    with the operational contracts and what to call next.
  - A bad name on `kb_orient` and `pyrite orient` carries `did_you_mean`
    (up to three near matches, an empty list when there are none) and a
    `suggestion`; names come only from the KBs the caller may read, never
    from a KB the caller may not read. A scoped caller's refusal (above)
    carries a static hint instead, "Call kb_orient with no kb_name to list the
    KBs you can read.", the same for an absent and an unreadable name, with no
    near matches. A `kb_name` that is not a string is `VALIDATION_FAILED`;
    `null` is accepted for `kb_name` and `detail` and means "not given".
  - New `detail` argument (`--detail`, `?detail=`): `brief` omits
    `ai_instructions`, `evaluation_rubric`, `guidelines` and `goals` from
    `schema` (and the top-level `guidelines`), keeps `relationship_types`, and
    says where to get them (`pyrite orient -k <kb> --detail full`, `kb_schema`); `full`
    is the default and is what a call without `detail` returned before. Any
    other value is `VALIDATION_FAILED` (422 over REST). Blocks a plugin adds
    to orient are returned whole under `brief`, so `brief` does not bound the
    response of a KB whose plugin supplement is large.
  - For a KB whose `kb.yaml` declares types, orient, `kb_schema`,
    `GET /api/kbs/{kb}/schema` and `pyrite-admin schema` list only those
    types: the ones `create` accepts. They used to list every core type as
    well, so a software KB offered `note` and then refused it as
    `UNDECLARED_TYPE`. A KB that declares no types is unchanged.
