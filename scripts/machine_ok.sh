#!/usr/bin/env bash
# Exit 1 when the machine is too busy to start anything heavy (#168); the
# the maintainer's orchestration loop (outside this repo) runs it before a worker, review suite or
# Playwright run. Details and limits: scripts/machine_ok.py.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/machine_ok.py" "$@"
