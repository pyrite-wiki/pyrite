- **`task claim` decides on the task's file, not on its index row.** A row
  that had fallen behind the file (after a `git pull`, a hand edit, or an
  `index sync` that ran across a claim) said `open` for a task the file said
  was `claimed`, and a second `pyrite task claim` (CLI, MCP `task_claim`,
  REST, and software-kb's `claim_entry` transitions) answered `claimed: true`
  and rewrote the file with the new assignee. A claim now reads the status and
  the assignee from the file and writes only if the file still holds them at
  the instant of the write; a refusal reports the file's status and repairs
  the row. The reverse also holds: a task released in the file is claimable
  though its row still says `claimed`, and a task file with no row yet is
  claimable instead of "not found". When the row was behind, a caller the
  holder rule refuses on the file's assignee now gets `Forbidden` rather than
  a `File update failed` payload.
