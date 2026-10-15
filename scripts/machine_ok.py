#!/usr/bin/env python3
"""Is this machine free enough to start something heavy? (#168)

The maintainer's orchestration loop (outside this repo) runs this before it
starts a worker, a review suite or a Playwright run, and starts nothing heavy
when it exits 1. On
2026-09-18 a tick recorded a load average of 14 and started anyway; the
machine then ran out of memory. The rule is a command, not a judgement.

  scripts/machine_ok.sh

Prints the two numbers and the limits on one line. Exit 0: ok. Exit 1: busy
(1-minute load above the limit, or available memory below it). A number it
cannot read is not a reason to stop a tick: it says so and exits 0.

Limits (defaults match the maintainer's loop: load 8 and 30%
free on the maintainer's 10-core / 16 GB laptop):
  PYRITE_MACHINE_MAX_LOAD       1-minute load average (default 0.8 x cores)
  PYRITE_MACHINE_MIN_FREE_PCT   available memory, % of total (default 30)

Available memory: free + inactive + speculative pages on macOS (vm_stat,
sysctl hw.memsize); MemAvailable on Linux (/proc/meminfo, or the file
PYRITE_MACHINE_MEMINFO names).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

GB = 1024**3


def parse_meminfo(text: str) -> tuple[int, int] | None:
    """(available, total) bytes from /proc/meminfo text."""
    values = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return values["MemAvailable"], values["MemTotal"]


def parse_vm_stat(text: str) -> int | None:
    """Available bytes (free + inactive + speculative) from `vm_stat`."""
    size = re.search(r"page size of (\d+) bytes", text)
    if not size:
        return None
    pages = 0
    found = False
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"{key}:\s+(\d+)", text)
        if m:
            pages += int(m.group(1))
            found = True
    return pages * int(size.group(1)) if found else None


def _out(*cmd: str) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def memory() -> tuple[int, int] | None:
    """(available, total) bytes, or None when they cannot be read."""
    override = os.environ.get("PYRITE_MACHINE_MEMINFO")
    meminfo = Path(override) if override else Path("/proc/meminfo")
    if override or meminfo.exists():
        try:
            return parse_meminfo(meminfo.read_text())
        except OSError:
            return None
    vm = _out("vm_stat")
    total = _out("sysctl", "-n", "hw.memsize")
    available = parse_vm_stat(vm) if vm else None
    if available is None or not total or not total.strip().isdigit():
        return None
    return available, int(total.strip())


def _limit(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        print(f"machine-ok: {name}={raw!r} is not a number; using {default:g}", file=sys.stderr)
        return default


def main() -> int:
    cores = os.cpu_count() or 1
    max_load = _limit("PYRITE_MACHINE_MAX_LOAD", 0.8 * cores)
    min_pct = _limit("PYRITE_MACHINE_MIN_FREE_PCT", 30.0)
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    mem = memory()
    parts, busy, unknown = [], [], []
    if load is None:
        unknown.append("load average")
    else:
        parts.append(f"load {load:.1f} (limit {max_load:g} on {cores} cores)")
        if load > max_load:
            busy.append("load")
    if mem is None:
        unknown.append("available memory")
    else:
        available, total = mem
        need = total * min_pct / 100
        parts.append(
            f"available memory {available / GB:.1f} GB of {total / GB:.1f} GB "
            f"(limit {min_pct:g}% = {need / GB:.1f} GB)"
        )
        if available < need:
            busy.append("memory")
    line = "machine-ok: " + "; ".join(parts) if parts else "machine-ok:"
    if unknown:
        line += f"; could not read {' or '.join(unknown)} (not a reason to stop)"
    if busy:
        print(f"{line}: BUSY ({', '.join(busy)}) -- start nothing heavy")
        return 1
    print(f"{line}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
