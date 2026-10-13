# changelog.d — one file per change, so no two pull requests conflict

**Add a fragment here instead of editing `CHANGELOG.md`.**

`CHANGELOG.md` used to be the file every pull request fought over. Every
`[Unreleased]` bullet was appended at the same spot, so any two pull requests
in flight conflicted there *by construction* — five times in one session, three
of them on first-time contributors' branches whose changes had nothing to do
with each other. The resolution was always "keep both, either order", which is
the definition of a conflict that should not exist (#243).

A fragment is a new file with a name nobody else picks, so it cannot conflict.
The release script assembles every fragment into `CHANGELOG.md` under the
version heading, and deletes them.

## Writing one

Create `changelog.d/<slug>.<section>.md`:

```
changelog.d/changelog-fragments.changed.md
changelog.d/mcp-private-kb-scoping.security.md
```

- **`<slug>`** — a few words naming *your change*, hyphenated. Your branch name
  works. It only has to be unlikely to collide with someone else's, and it
  never appears in the release notes.
- **`<section>`** — one of the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
  sections, which is the format `CHANGELOG.md` uses:

  | Section | For |
  |---|---|
  | `added` | a new feature |
  | `changed` | a change to behaviour that already existed |
  | `deprecated` | something now discouraged, still working |
  | `removed` | something gone |
  | `fixed` | a bug fix |
  | `security` | a vulnerability, and what an operator should do |

  A section outside that list **fails the release** rather than being skipped:
  an entry silently dropped from the notes is how a security fix goes
  unannounced.

The body is the changelog entry itself: markdown bullets, exactly as they
should read in `CHANGELOG.md`, with no `###` heading — the release script adds
the heading.

```markdown
- **`pyrite create -t <type>` no longer files a different type (#197).** Core
  types were exempt from the CLI's write-side refusal, so `-t note` against a
  KB that does not declare it was promoted to an ADR.
```

Write it for someone upgrading who does not know the codebase: what changed,
what they should do about it, and the issue or PR number.

One change can have several fragments when it belongs in several sections —
`my-theme.added.md` and `my-theme.fixed.md` is fine.

## What happens to it

At release time `scripts/release.py`:

1. reads every fragment (a bad section or an empty file stops the release);
2. groups them by section, in the order of the table above, and sorts by slug,
   so the output is identical on any machine;
3. puts the assembled sections in the GitHub release notes and writes them into
   `CHANGELOG.md` under `## [<version>]`;
4. deletes the fragments in the same commit that adds them to `CHANGELOG.md`.

`scripts/release.py <version> --dry-run` prints the assembled section without
changing anything, which is the way to check how your entry will read.

## Not here

- **`CHANGELOG.md` itself** — `## [Unreleased]` stays empty on `dev`, and
  `tests/test_changelog_fragments.py` asserts it. Editing the released sections
  is a separate thing (a typo fix), not how a change is recorded.
- **Changes nobody using Pyrite would notice** — a refactor with no behaviour
  change, a test-only change, a tweak to CI. No fragment needed.

  CI prints a **warning** (never a failure) when a pull request touches
  `pyrite/` or `extensions/` and adds no fragment — a missing entry is
  otherwise silent, which is how #173's fix came to be absent from the release
  notes with nothing complaining (#278). It is advisory because whether a
  change is user-visible is your call, not a script's. If it is a refactor
  with no behaviour change, put a line in the pull-request description and the
  warning goes away:

  ```
  Changelog: none
  ```

  There is deliberately **no `process` or `docs` section** (maintainer,
  2026-09-21). The six sections above are the Keep a Changelog set, which is
  the format `CHANGELOG.md` declares at the top of the file; a `### Process`
  heading there would belong to no format and would put the loop's own
  bookkeeping in front of people reading release notes to find out what
  changed for *them*. Process work is recorded in the KB and in GitHub issues
  (ADR-0033), which is where the retro looks for it.

  If a process change *does* alter something a user sees — a CLI flag, an
  error message, a documented workflow — that is not a process change for this
  purpose. Write the fragment under the section describing what the user
  experiences.

A test must not read a fragment by name; fragments are deleted at release.
