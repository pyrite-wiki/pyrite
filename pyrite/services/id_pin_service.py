"""List the files with no ``id:`` and pin each one's current id (#700).

ADR-0042 decision 5, "Two commands ship one release before the switch": an
operator pins ids while the old (title-derived) rule is still in force, so
the switch to path ids loses no identity.

The pin is a **textual insertion**: one line, ``id: <id>``, added as the last
line of the frontmatter (ADR-0042 decision 6: "a new key goes on the last
line of the frontmatter, block style"), ending like the frontmatter line
before it. It
never goes through ``KBService.update`` or a model's ``to_frontmatter``:
those rewrite the whole file. Every other byte stays as it was, and the
result is re-parsed before it is written; a file where one more line would
change anything else is refused and reported, never repaired.

The id is the one the file holds today, from the code that derives it
(``KBRepository.id_of_file`` -> ``read_entry_id``), not re-implemented here.
A collision (two files holding one id) is never resolved silently: the
operator chooses with ``renames``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import KBConfig, PyriteConfig, load_config
from ..exceptions import ValidationError
from ..models.core_types import _frontmatter_of, explicit_entry_id, id_text, read_entry_id
from ..storage.repository import KBRepository
from ..utils.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)

# A plain YAML scalar that reads back as this exact string, under YAML 1.2
# and 1.1 readers alike; anything else is single-quoted.
_PLAIN_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
_YAML11_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}

_OPENING = re.compile(r"\A(﻿?)---(\r?\n)")
# The loader's closing delimiter, the same pattern ``_frontmatter_of`` splits
# on, searched from the same place (after the opening line).
_CLOSING = re.compile(r"^---\s*$", re.MULTILINE)


class PinRefusedError(Exception):
    """One file cannot take the pin as a single added line; the reason."""


@dataclass
class IdScan:
    """What ``scan`` found in one KB. Paths are KB-relative, POSIX."""

    kb: str
    root: Path
    missing: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    collisions: list[dict[str, Any]] = field(default_factory=list)
    # every id a file holds now (explicit or derived), for --rename checks
    taken: set[str] = field(default_factory=set)
    # sha256 of each id-less file's bytes as scanned: a pin is written only
    # to a file that still holds exactly these bytes (so still this id)
    digests: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb": self.kb,
            "missing": self.missing,
            "collisions": self.collisions,
            "skipped": self.skipped,
            "count": len(self.missing),
        }

    @property
    def outside_contract(self) -> bool:
        return bool(self.missing or self.skipped)


# --- the textual insertion -------------------------------------------------


def id_line(entry_id: str, eol: str = "\n") -> str:
    """The line the pin adds, quoted when a plain scalar would read back as
    something else (``123``, ``1e3``, ``yes``, ``null``)."""
    if _PLAIN_ID.fullmatch(entry_id) and entry_id.lower() not in _YAML11_WORDS:
        value = entry_id
    else:
        value = "'" + entry_id.replace("'", "''") + "'"
    return f"id: {value}{eol}"


def insert_id_line(text: str, entry_id: str) -> str:
    """``text`` with ``id: <entry_id>`` added as the last frontmatter line.

    ``text`` is the file decoded as is (no newline translation, BOM kept).
    Raises ``PinRefusedError`` unless the result's frontmatter is the old one plus
    ``id`` and nothing else, with the body unchanged and ``read_entry_id``
    reading back ``entry_id``.
    """
    opening = _OPENING.match(text)
    if not opening:
        raise PinRefusedError("no YAML frontmatter (the file does not start with ---)")
    closing = _CLOSING.search(text, opening.end())
    if not closing:
        raise PinRefusedError("no closing --- for the frontmatter")
    at = closing.start()
    # The new line goes between the frontmatter's last line and the closing
    # `---`, so it takes that last line's ending (the opening line's, when the
    # frontmatter is empty): in a file with mixed endings it matches its
    # neighbour. `at` starts a line, so text[:at] always ends in "\n".
    eol = "\r\n" if text[:at].endswith("\r\n") else "\n"
    new_text = text[:at] + id_line(entry_id, eol) + text[at:]

    try:
        before = _frontmatter_of(text)
        after = _frontmatter_of(new_text)
    except Exception as e:  # FrontmatterError: the added line broke the YAML
        raise PinRefusedError(
            f"one added line would not parse here ({type(e).__name__}); add the id by hand"
        ) from e
    if before is None or after is None:
        raise PinRefusedError("frontmatter could not be parsed")
    old_meta, old_body = before
    new_meta, new_body = after
    expected = dict(old_meta)
    expected["id"] = entry_id
    if (
        dict(new_meta) != expected
        or list(new_meta)[:-1] != list(old_meta)
        or new_body != old_body
        or read_entry_id(new_text) != entry_id
    ):
        raise PinRefusedError(
            "one added line would change more than the id here "
            "(flow-mapping or indented frontmatter?); add the id by hand"
        )
    return new_text


def write_refusal(root: Path, path: Path) -> str | None:
    """Why ``path`` must not be written by a pin, or None.

    A pin writes only a regular file inside the KB root, reached without a
    symbolic link: ``atomic_write_text`` follows a link (a ``link.md`` to
    ``../outside.md`` would write outside the KB) and writes a file with
    several hard links in place (changing every name, wherever it is). Each
    component under the root is checked with ``lstat``, so a path through a
    symlinked directory is refused too (``list_files``' ``rglob`` does not
    descend into one today; this does not rely on that). The KB root itself
    may be a link.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "outside the KB"
    if not rel.parts:
        return "not a file"
    current = root
    st = None
    try:
        for i, part in enumerate(rel.parts):
            current = current / part
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                if i == len(rel.parts) - 1:
                    return "a symbolic link; pin writes only regular files inside the KB"
                where = current.relative_to(root).as_posix()
                return f"inside a symlinked directory ({where}); pin does not write through links"
    except OSError as e:
        return f"cannot be inspected: {e.strerror or e}"
    if st is None or not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if st.st_nlink > 1:
        return f"has {st.st_nlink} hard links; writing it would change every name"
    if not path.resolve().is_relative_to(root.resolve()):
        return "resolves outside the KB"
    return None


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_pin(root: Path, path: Path, planned_original: bytes, new_text: str) -> None:
    """Write ``new_text`` over ``path``, checked again at the last moment: the
    path is still a regular file inside the KB, and its bytes are still the
    ones the pin was computed from (an edit made in between wins: Pyrite is a
    guest)."""
    refusal = write_refusal(root, path)
    if refusal:
        raise PinRefusedError(refusal)
    if path.read_bytes() != planned_original:
        raise PinRefusedError("the file changed after it was read; run the command again")
    atomic_write_text(path, new_text)


# --- scan ------------------------------------------------------------------


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def scan(kb_config: KBConfig) -> IdScan:
    """Every file ``list_files`` walks (the files sync indexes) with no
    ``id:``, the id each holds today, the collisions among them and the
    files that could not be read."""
    repo = KBRepository(kb_config)
    root = repo.path
    result = IdScan(kb=kb_config.name, root=root)
    holders: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in sorted(repo.list_files()):
        rel = _rel(root, path)
        refusal = write_refusal(root, path)
        if refusal:
            # Not a file pin may write. Never read before this check: a FIFO
            # named `x.md` would block the read. Its id (read through the
            # link, as the index reads it) still counts as taken, but it is
            # no claimant: a link to a file in the KB would otherwise collide
            # with its own target.
            if ("symbolic link" in refusal or "hard links" in refusal) and path.is_file():
                current = repo.id_of_file(path)
                if current:
                    result.taken.add(current)
                    try:
                        has_id = explicit_entry_id(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError):
                        has_id = None
                    if has_id:
                        continue
                    refusal += f"; its id today is {current!r}: add the line by hand"
            result.skipped.append({"path": rel, "reason": refusal})
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            result.skipped.append({"path": rel, "reason": f"not readable as UTF-8: {e}"})
            continue
        try:
            parsed = _frontmatter_of(text)
        except Exception as e:  # what the loader would raise: report, never repair
            result.skipped.append({"path": rel, "reason": f"invalid frontmatter: {e}"})
            continue
        if parsed is None:
            result.skipped.append({"path": rel, "reason": "no YAML frontmatter"})
            continue
        meta = parsed[0]
        current = repo.id_of_file(path)
        if current is None:
            result.skipped.append({"path": rel, "reason": "Pyrite cannot read an id from it"})
            continue
        explicit = id_text(meta.get("id")) is not None
        if not explicit:
            status = "empty_id" if "id" in meta else "missing"
            result.missing.append({"path": rel, "id": current, "status": status})
            result.digests[rel] = _digest(raw)
        holders[current].append({"path": rel, "explicit": explicit})

    # __collection.yaml files hold ids too (``collection-<folder>``)
    for path in sorted(repo.path.rglob("__collection.yaml")):
        current = repo.id_of_file(path)
        if current and not any(part.startswith(".") for part in path.parts):
            holders[current].append({"path": _rel(root, path), "explicit": True})

    result.taken |= set(holders)
    for entry_id, claimants in sorted(holders.items()):
        if len(claimants) > 1 and not all(c["explicit"] for c in claimants):
            result.collisions.append({"id": entry_id, "claimants": claimants})
    return result


# --- plan and apply --------------------------------------------------------


def validate_new_id(entry_id: str) -> None:
    """Today's id rule (a plain filename stem) plus ADR-0042's segment rules,
    so a pinned id is valid before and after the switch."""
    KBRepository._validate_entry_id(entry_id)
    if entry_id in ("-", ".", "..") or entry_id != entry_id.strip():
        raise ValidationError(f"Invalid entry id {entry_id!r}")


def parse_renames(values: list[str]) -> dict[str, str]:
    renames: dict[str, str] = {}
    for value in values:
        path, sep, new_id = value.partition("=")
        if not sep or not path:
            raise ValidationError(f"Invalid --rename {value!r}: expected <path>=<id>")
        key = PurePosixPath(path).as_posix()
        if key in renames:
            raise ValidationError(f"--rename names {key} twice")
        renames[key] = new_id
    return renames


def plan(found: IdScan, renames: dict[str, str]) -> dict[str, Any]:
    """What a pin would do. Raises ``ValidationError`` for a ``--rename``
    that is not a valid choice, before anything is written."""
    in_group: dict[str, dict[str, Any]] = {}
    for group in found.collisions:
        for c in group["claimants"]:
            if not c["explicit"]:
                in_group[c["path"]] = group

    new_ids: dict[str, str] = {}
    for path, new_id in renames.items():
        if path not in in_group:
            raise ValidationError(
                f"--rename {path}: not in a collision group; pinning keeps a file's "
                "current id (only a file that shares its id with another is renamed)"
            )
        try:
            validate_new_id(new_id)
        except ValidationError as e:
            raise ValidationError(f"--rename {path}: invalid id: {e}") from e
        if new_id in found.taken:
            raise ValidationError(f"--rename {path}: id {new_id!r} is taken by a file")
        if new_id in new_ids.values():
            raise ValidationError(f"--rename {path}: id {new_id!r} is taken by another --rename")
        new_ids[path] = new_id

    pinned: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = list(found.skipped)
    refused: list[dict[str, Any]] = []
    refused_paths: set[str] = set()
    for group in found.collisions:
        explicit = [c["path"] for c in group["claimants"] if c["explicit"]]
        idless = [c["path"] for c in group["claimants"] if not c["explicit"]]
        unrenamed = [p for p in idless if p not in new_ids]
        if len(explicit) + len(unrenamed) > 1:
            refused.append(
                {
                    "id": group["id"],
                    "claimants": group["claimants"],
                    "unrenamed": unrenamed,
                    "reason": (
                        "more than one file would hold this id; keep one and "
                        "--rename <path>=<id> the others"
                    ),
                }
            )
            refused_paths.update(idless)

    for row in found.missing:
        path = row["path"]
        if path in refused_paths:
            continue
        if row["status"] == "empty_id":
            skipped.append(
                {
                    "path": path,
                    "reason": "has an empty id: line; fill it in by hand "
                    f"(its id today is {row['id']!r})",
                }
            )
            continue
        item = {"path": path, "id": new_ids.get(path, row["id"])}
        if path in new_ids:
            item["renamed_from"] = row["id"]
        pinned.append(item)

    return {"kb": found.kb, "pinned": pinned, "refused": refused, "skipped": skipped}


def _prepared(found: IdScan, item: dict[str, str]) -> tuple[Path, bytes, str]:
    """The path, its bytes and the pinned text for one planned pin, refused
    unless the file is still one pin may write and still holds the bytes
    ``scan`` read (so still the id ``scan`` derived)."""
    path = found.root / item["path"]
    refusal = write_refusal(found.root, path)
    if refusal:
        raise PinRefusedError(refusal)
    original = path.read_bytes()
    if _digest(original) != found.digests.get(item["path"]):
        raise PinRefusedError("the file changed after it was scanned; run the command again")
    return path, original, insert_id_line(original.decode("utf-8"), item["id"])


def apply(found: IdScan, the_plan: dict[str, Any]) -> dict[str, Any]:
    """Write each planned pin. A file that cannot take it moves from
    ``pinned`` to ``skipped`` with the reason; nothing else is written."""
    done: list[dict[str, str]] = []
    for item in the_plan["pinned"]:
        try:
            path, original, new_text = _prepared(found, item)
            _write_pin(found.root, path, original, new_text)
        except (PinRefusedError, OSError, UnicodeDecodeError) as e:
            the_plan["skipped"].append({"path": item["path"], "reason": str(e)})
            continue
        done.append(item)
    the_plan["pinned"] = done
    return the_plan


def check(found: IdScan, the_plan: dict[str, Any]) -> dict[str, Any]:
    """A dry run: every planned pin is computed (so a file that would be
    refused shows as skipped), and nothing is written."""
    ok: list[dict[str, str]] = []
    for item in the_plan["pinned"]:
        try:
            _prepared(found, item)
        except (PinRefusedError, OSError, UnicodeDecodeError) as e:
            the_plan["skipped"].append({"path": item["path"], "reason": str(e)})
            continue
        ok.append(item)
    the_plan["pinned"] = ok
    return the_plan


# --- finding the KB without writing anything -------------------------------


def find_kb_without_writes(kb_name: str) -> KBConfig | None:
    """The KB named ``kb_name``, found the way every command finds it
    (config.yaml, then the KBs ``pyrite kb add`` registered in the index),
    without creating or changing a file.

    ``cli_context`` cannot be used for this: it creates the config directory,
    opens the index (creating ``index.db`` and its ``-wal``/``-shm`` files,
    running migrations) and may write a registry row back. Here the config
    directory is not created, and the index is read only if it exists,
    opened ``immutable`` so SQLite creates no ``-wal``/``-shm`` file. The
    cost: a registration still in an uncheckpointed WAL (a running server
    that has not checkpointed) is not seen; the KB is then "not found",
    never a wrong KB.
    """
    config = load_config(create_dir=False)
    kb = config.get_kb(kb_name)
    if kb is not None:
        return kb
    return _registered_kb(config, kb_name)


def _registered_kb(config: PyriteConfig, kb_name: str) -> KBConfig | None:
    index_path = Path(config.settings.index_path).expanduser()
    if not index_path.is_file():
        return None
    uri = f"{index_path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT name, path, kb_type, description, default_role "
                "FROM kb WHERE source = 'user' AND name = ?",
                (kb_name,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning("Could not read registered KBs from %s: %s", index_path, e)
        return None
    for name, path, kb_type, description, default_role in rows:
        kb = config.kb_config_from_registry_row(
            {
                "name": name,
                "path": path,
                "kb_type": kb_type,
                "description": description or "",
                "default_role": default_role,
            }
        )
        if kb is not None:
            kb.load_kb_yaml()
            return kb
    return None
