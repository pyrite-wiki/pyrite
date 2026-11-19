#!/usr/bin/env python3
"""Fail before testing a different checkout through shared editable installs."""

import argparse
import importlib
import sys
from pathlib import Path


def module_roots(root):
    yield "pyrite", root / "pyrite"
    for source in sorted((root / "extensions").glob("*/src")):
        for package in sorted(source.iterdir()):
            if package.is_dir() and (package / "__init__.py").is_file():
                yield package.name, package


def check(root):
    failures = []
    for name, expected in module_roots(root):
        try:
            module = importlib.import_module(name)
            actual = Path(module.__file__).resolve()
            if not actual.is_relative_to(expected.resolve()):
                failures.append(f"{name}: imported {actual}; expected under {expected}")
        except (ImportError, TypeError) as error:
            failures.append(f"{name}: cannot import from {expected}: {error}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    failures = check(args.root.resolve())
    if failures:
        print("Checkout import isolation failed:\n" + "\n".join(failures), file=sys.stderr)
        print(
            "Run scripts/setup-checkout.sh for this checkout; do not share its editable venv.",
            file=sys.stderr,
        )
        return 1
    print(f"Core and every extension import from {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
