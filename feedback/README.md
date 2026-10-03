# Pyrite field feedback

Hallway-testing entries, **one file per entry**: `feedback/YYYY-MM-DD-<slug>.md`,
where the slug names the task or theme (`2026-10-03-b6-p2-file-lock.md`). Use the
entry format from the hallway-agent-testing skill. Commit the entry in its own
commit, naming only that file.

One file per entry means two branches never touch the same file, so landing
one theme no longer makes every other open branch conflict (#727: four hand
rebases for five merges on 2026-10-03).

The rules of `../FEEDBACK.md` still hold. That file keeps the entries written
before 2026-10-03 and is no longer appended to.
- Do not edit someone else's entry; the series is the value.
- Maintainers triage with `[fixed <commit>]`, `[wontfix — reason]` or
  `[tracked]` and leave the text intact.
- Subjects of investigation become stable placeholders (`<person-a>`).
