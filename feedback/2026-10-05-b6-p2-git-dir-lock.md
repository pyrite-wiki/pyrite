## 2026-10-05 · B6 P2 rebuilt: the momentary lock in the git dir (PR #752) from a new-primitive brief · claude-opus-5-5

A worker theme in a worktree. The brief named the reference behaviour (git) and nine adversary classes, and #733's closed branch supplied the lock, the compare and the process tests to carry over. Rebuilding the lock-dir derivation took about one session; four places cost time.

**Friction 1: the brief's reference contradicted the property. Severity: had to figure out.** "Agree with `git rev-parse` on ... a `GIT_DIR` override" cannot hold together with "one file, one lock dir": with `GIT_DIR` exported, git names the other repository for the same file. One shell probe settled it. The lookup reads no environment, and the tests compare with git run on a cleared environment. **Would have helped:** a brief that names the reference's environment as well as the command.

**Friction 2: `git rev-parse` costs 22-27 ms a call on macOS. Severity: slowed (design).** `/usr/bin/git` is the xcrun shim. With decision 12 putting every write through this path, I chose the pure lookup the brief allowed, and checked it with a 13-layout probe against git (detached and sha256 HEADs, `--separate-git-dir`, a pruned worktree, a CRLF gitfile, garbage HEAD). **Would have helped:** the cost of the reference measured in the groom.

**Friction 3: mutation testing by hand again. Severity: slowed.** verify-red labels every test of a new module import-only, so the guard table came from a throwaway script that applies each mutation, runs the file and restores it (33 mutations, about 3 minutes at -n 4). The B6 P1 and #733 entries say the same. **Would have helped:** `scripts/verify-red.sh --mutations <file>` reading a table of (label, old, new).

**Friction 4: `git checkout <branch> -- file` followed by `git reset` left the old content in the working tree.** My first red run imported #733's `atomic_write.py`, not dev's. **Would have helped:** nothing in Pyrite; noted for the next worker who carries files over from a closed branch.

**Worked well:** the dispatch.md "new primitive" lines. Naming the reference and the adversaries before code meant the riskiest assumption was a one-line probe, not a cold-read finding.
