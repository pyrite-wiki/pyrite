## 2026-10-05 · #749 apply() limits (tagged keys, NaN, NFD keys): a fix theme in a worktree (pyrite-dev skill, verify-red, a cold read's probes) · claude-sonnet-5-5

Closed the limits a cold read left on `apply()` before it is wired in: every input now edits narrowly or raises `OperationRefusedError` with a true reason.

**Friction 1: a probe I read wrongly sent me to the wrong test. Severity: wasted a test round.** I printed `{repr(k): v}` for a ruamel load and read `'1'` as a string key; it was `repr(1)` quoted by the dict's own repr. My first test for `!!int 1: a` asserted a string key and failed. Printing `type(k).__name__` next to `k` would have shown it. The probe that mattered was `!!float 1: a` (ruamel: float 1.0, plain reading: int 1; path `1` raised `KeyError`).

**Friction 2: the ticket's item 5 did not reproduce. Severity: had to figure out.** "Unsetting a list's last item drops the comment lines between its items" has no probe in `probe732c/`. About 16000 generated layouts (indents, tabs, blank lines, block scalars, nested lists, tails) lost nothing. I pinned the behaviour with a control test and removed the line from Limits. A ticket that names a loss should carry the input that shows it.

**Friction 3: guard-deletion loops hang on macOS. Severity: lost about 40 minutes.** `subprocess.run(..., timeout=)` does not return while pytest-xdist workers hold the pipe, there is no `timeout(1)`, and a script's prints arrive only at exit when redirected. What worked: no `-n`, a `-k` of just the new tests, and `-x`; each mutation fails in 3 seconds. **Would have helped:** a `scripts/guard-delete` helper that takes a file, a string and a replacement.

**Friction 4: the module's own NaN refusal ("cannot write NaN: it never equals itself") was a second guard for the same cause. Severity: nearly missed.** Making NaN equal left it in place, so `Set(n, nan)` on an existing `.nan` still raised. Grep for the symptom's words ("never equals", "NaN") before declaring a root cause done.

**Worked well:**
- Recording what ruamel loads for each input first made every expected value come from the reference, not from the module.
- A fuzz that already existed took six new texts and five new path pieces and found the second NaN guard on its first run.
