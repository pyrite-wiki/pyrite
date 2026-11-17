"""Every read, probe, listing and create of a file is classified: a ratchet (ADR-0052 N1).

``kb/design.md`` principles 7 and 8, ADR-0052 decision 3: bytes, and any fact
derived from bytes (an id, existence, a hint, a hash), reach a network caller
only from files ``KBFiles`` reads inside a KB root, and every name a
network-reachable path lists, probes, creates or stages has its answer
decided inside the root, or ends in the same ``KBFiles`` refusal whatever lies
outside. Nothing that answers a caller has a raw read mode.

This test walks every module the server can load (``pyrite/`` and
``extensions/*/src``, less the CLI's own modules: see "The CLI exemption"
below) and finds each call of one of these kinds, keyed by what it is called
on:

- through ``KBFiles`` (a ``KBFiles(...)`` receiver, or one bound as ``inside``):
  ``KBFiles.read_bytes``, ``.present``, ``.contains``, ``.make_dirs``,
  ``.require``, ``.check``; the repository's readers and probes built on it
  (``repo._read_inside``, ``._inside_bytes``, ``._inside_text``, ``.present``,
  ``._named``, ``KBRepository(...).exists``) and ``KBConfig.kb_yaml_present``;
- on any other receiver (a ``Path``): reads (``read_text``, ``read_bytes``,
  ``open``), probes (``exists``, ``is_file``, ``is_dir``, ``is_symlink``,
  ``stat``, ``lstat``, ``samefile``), listings (``iterdir``, ``glob``,
  ``rglob``, ``walk``; ``glob`` and ``rglob`` with a literal name are their own
  kind, since Python 3.11 checks a literal name's target) and creates
  (``mkdir``, ``touch``, ``symlink_to``, ``hardlink_to``);
- the ``os``, ``os.path``, ``io`` and ``glob`` functions of those kinds
  (``os.open``, ``os.stat``, ``os.scandir``, ``os.listdir``, ``os.walk``,
  ``os.mkdir``, ``os.makedirs``, ``os.path.exists`` ...), ``open``, ``FileIO``,
  and ``getattr(x, "read_text")`` and the like;
- ``load_yaml_file`` as two kinds: ``(kb_root)``, which is
  ``KBFiles.read_bytes``, and ``(raw)``, with no ``kb_root`` or the literal
  ``None``;
- the raw helpers the lists below rely on, each its own kind:
  ``_is_regular_file``, ``_hard_link_fault``.

Each (module, function) holding such calls is in exactly one list, with the
count of each kind:

- ``DECIDED_INSIDE``: the answer comes from ``KBFiles`` (or the repository's
  readers on it), or from names under the root alone.
- ``SAME_ANSWER``: a ``Path`` probe or listing whose two outcomes give the
  caller the same answer, because what follows is a ``KBFiles`` read that
  refuses a link out, or a skip either way; the reason says which.
- ``NOT_A_KB_FILE``: operator configuration, the index, caches, locks, static
  assets, package metadata, the KB's own folder (the operator's registry
  entry), an export destination; each with the reason.
- ``LOCAL_ONLY``: reached only by the local process, with the guard that
  proves it (checked below).

A function in no list fails, and so does any change in a listed function's
counts: swapping ``KBFiles(root).present(p)`` for ``p.exists()``, or a probe
for a raw read, changes two counts. Each list's size is pinned, and the
mutation tests below revert this theme's own fixes in memory and show that
each one fails.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOTS = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]

#: The CLI's own modules: the local process (its root binds
#: ``is_local_process``), which reads what the operator points it at (ADR-0052
#: decision 5). Exempt only while every import of one from a scanned module is
#: listed in ``CLI_IMPORTS`` and made inside the function named there
#: (``test_the_cli_exemption_is_proved_by_imports``).
CLI_ENTRY = ("pyrite/cli/", "pyrite/admin_cli.py", "pyrite/read_cli.py")

Site = tuple[str, str]  # (module relative to the repo, enclosing function)
Entry = tuple[dict[str, int], str]  # ({kind: count}, reason)

#: Modules listed whole, with the counts of the whole module.
WHOLE_MODULES = frozenset(
    {
        "pyrite/utils/kb_files.py",
        "pyrite/utils/file_lock.py",
        "pyrite/utils/atomic_write.py",
        "pyrite/utils/private_files.py",
    }
)

DECIDED_INSIDE: dict[Site, Entry] = {
    ("extensions/software-kb/src/pyrite_software_kb/adr_listing.py", "adr_numbers_in_files"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/board.py", "load_board_config"): (
        {"KBFiles.present": 1, "load_yaml_file(kb_root)": 1},
        "KBFiles.present, then load_yaml_file(kb_root=)",
    ),
    ("pyrite/config.py", "KBConfig._read_kb_yaml_or_raise"): (
        {"os.scandir": 1},
        "the KB's folder listed (operator config); kb.yaml through read_kb_yaml, KBFiles (#1542, #1547)",
    ),
    ("pyrite/config.py", "KBConfig.kb_yaml_present"): (
        {"KBFiles.present": 1},
        "KBFiles.present for kb.yaml",
    ),
    ("pyrite/config.py", "KBConfig.load_kb_yaml"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present; kb.yaml through read_kb_yaml (#1547)",
    ),
    ("pyrite/services/export_service.py", "ExportService.export_kb_to_directory"): (
        {"KBFiles.read_bytes": 1, "path.mkdir": 2},
        "kb.yaml read through KBFiles; the mkdirs make the export destination, not a KB path",
    ),
    ("pyrite/services/file_search.py", "search_kb_files"): (
        {"path.exists": 1, "repo._read_inside": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it; files through _read_inside",
    ),
    ("pyrite/services/kb_registry_service.py", "KBRegistryService._apply_preset"): (
        {"KBFiles.contains": 1, "KBFiles.make_dirs": 1, "KBFiles.present": 2},
        "KBFiles.contains for every planned path before any write, then KBFiles.write_bytes, make_dirs and present (fixed here)",
    ),
    ("pyrite/services/kb_service.py", "KBService._entry_file"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("pyrite/services/kb_service.py", "KBService._parse_change_entry"): (
        {"KBFiles.present": 1, "KBFiles.read_bytes": 1},
        "KBFiles.present, then KBFiles.read_bytes",
    ),
    ("pyrite/services/kb_service.py", "KBService._prepare"): (
        {"KBFiles.contains": 1, "repo.exists": 1, "repo.present": 1},
        "KBRepository.exists (find_file); KBFiles.contains refuses a path out as the write does (fixed here); repo.present",
    ),
    ("pyrite/services/kb_service.py", "KBService._prepare_and_save"): (
        {"repo.exists": 1},
        "KBRepository.exists (find_file)",
    ),
    ("pyrite/services/kb_service.py", "KBService._read_overlay_file"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes of a worktree overlay's file",
    ),
    ("pyrite/services/kb_service.py", "KBService.get_pending_changes"): (
        {"KBFiles.present": 2},
        "KBFiles.present, bound once",
    ),
    ("pyrite/services/kb_service.py", "_read_kb_file"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes",
    ),
    ("pyrite/services/qa_service.py", "QAService._check_file_values"): (
        {"KBFiles.present": 1},
        "KBFiles.present of the indexed path, then the repository's load",
    ),
    ("pyrite/services/qa_service.py", "QAService._check_schema_all"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present",
    ),
    ("pyrite/services/qa_service.py", "QAService._check_schema_retypes_core_fields"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present",
    ),
    ("pyrite/services/qa_service.py", "QAService._check_schema_validation"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present",
    ),
    ("pyrite/services/qa_service.py", "QAService._kb_folder_problem"): (
        {"path.exists": 1, "path.is_dir": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it (exists, is_dir); the rest by KBConfig.check_readable (#1542)",
    ),
    ("pyrite/services/review_service.py", "_read_entry_file"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes",
    ),
    ("pyrite/services/schema_service.py", "SchemaService.migrate_entries"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("pyrite/services/template_service.py", "TemplateService._parse_template_file"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes",
    ),
    ("pyrite/services/template_service.py", "TemplateService.get_template"): (
        {"KBFiles.present": 1},
        "KBFiles.present, then read_bytes",
    ),
    ("pyrite/storage/index.py", "IndexManager._load_schemas"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present",
    ),
    ("pyrite/storage/index.py", "IndexManager.check_health"): (
        {"KBFiles.present": 1, "path.exists": 4},
        "KBFiles.present of an indexed path; the KB's folder (operator config); kb.yaml only through _load_schemas (#1542)",
    ),
    ("pyrite/storage/index.py", "IndexManager.index_with_attribution"): (
        {"KBFiles.present": 1},
        "KBFiles.present of a file git names",
    ),
    ("pyrite/storage/index.py", "_hash_file"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes",
    ),
    ("pyrite/storage/repository.py", "KBRepository._contained"): (
        {"KBFiles.require": 1},
        "KBFiles.require",
    ),
    ("pyrite/storage/repository.py", "KBRepository._filename_candidates"): (
        {"repo._named": 2, "repo.present": 2},
        "present and _named (fixed here: 3.11's literal rglob and is_dir/exists followed links)",
    ),
    ("pyrite/storage/repository.py", "KBRepository._files_holding"): (
        {"repo._inside_text": 1},
        "_inside_text",
    ),
    ("pyrite/storage/repository.py", "KBRepository._free_default_path"): (
        {"repo.present": 2},
        "present: a link out is taken whatever it points at",
    ),
    ("pyrite/storage/repository.py", "KBRepository._holds"): (
        {"repo._inside_text": 1},
        "_inside_text",
    ),
    ("pyrite/storage/repository.py", "KBRepository._inside_bytes"): (
        {"KBFiles.read_bytes": 1},
        "KBFiles.read_bytes for the KB: every entry load, id, hint and rename",
    ),
    ("pyrite/storage/repository.py", "KBRepository._inside_text"): (
        {"repo._inside_bytes": 1},
        "_inside_bytes, decoded",
    ),
    ("pyrite/storage/repository.py", "KBRepository._load_collection"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("pyrite/storage/repository.py", "KBRepository._load_entry"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("pyrite/storage/repository.py", "KBRepository._maybe_migrate"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present (KBFiles.present)",
    ),
    ("pyrite/storage/repository.py", "KBRepository._migrations_apply"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present (KBFiles.present)",
    ),
    ("pyrite/storage/repository.py", "KBRepository._named"): (
        {"os.walk": 1},
        "names under the root by os.walk: a link is listed as its name, never followed or checked, and a folder link is not walked into, on every Python (fixed here)",
    ),
    ("pyrite/storage/repository.py", "KBRepository._publish_rewrite"): (
        {"repo._read_inside": 1},
        "_read_inside",
    ),
    ("pyrite/storage/repository.py", "KBRepository._read_inside"): (
        {"repo._inside_bytes": 1},
        "_inside_bytes, absent as FileNotFoundError",
    ),
    ("pyrite/storage/repository.py", "KBRepository.count"): (
        {"repo._named": 1},
        "_named (fixed here: 3.11's literal rglob listed a link only when its target existed)",
    ),
    ("pyrite/storage/repository.py", "KBRepository.holder_of_path"): (
        {"repo.present": 1},
        "present, then read_id",
    ),
    ("pyrite/storage/repository.py", "KBRepository._in_kb_link"): (
        {"KBFiles.resolved": 1, "os.path.islink": 1},
        "where an in-KB link leads (#1262): the name is decided under the root lexically first; islink is the lstat of that name itself, never followed; where it leads, and whether that is out of the KB, is KBFiles.resolved (the rule behind contains). Nothing is read",
    ),
    ("pyrite/storage/repository.py", "KBRepository.alias_target"): (
        {"_is_regular_file": 1},
        "_in_kb_link (KBFiles.resolved), then a stat of a target KBFiles placed inside: is it a regular file. Nothing is read: the entry is read at the target through KBFiles",
    ),
    ("pyrite/storage/repository.py", "KBRepository.dangling_target"): (
        {"os.path.lexists": 1},
        "_in_kb_link (KBFiles.resolved), then lexists of the name KBFiles placed inside the root: is anything there. A link out is never asked (None first), so no answer depends on outside",
    ),
    ("pyrite/storage/repository.py", "KBRepository.list_collection_files"): (
        {"repo._named": 1},
        "_named (fixed here: as count; moved out of list_all_files by #1262)",
    ),
    ("pyrite/storage/repository.py", "KBRepository.present"): (
        {"KBFiles.present": 1},
        "KBFiles.present: a link out is taken whatever it points at",
    ),
    ("pyrite/storage/repository.py", "KBRepository.read_id"): (
        {"repo._inside_bytes": 1},
        "the one read path of the id locators (_inside_bytes)",
    ),
    ("pyrite/storage/repository.py", "KBRepository.save"): (
        {"KBConfig.kb_yaml_present": 1},
        "kb_yaml_present; the raw mkdir is gone (fixed here): the create makes folders beneath the root",
    ),
    ("pyrite/utils/containment.py", "resolves_inside"): (
        {"os.path.exists": 1, "os.path.isdir": 1, "os.path.samefile": 1},
        "the containment decision KBFiles' callers share: the resolved root and a parent's identity, never a file's content",
    ),
    ("pyrite/utils/kb_files.py", "*"): (
        {
            "os.access": 2,
            "os.lstat": 1,
            "os.mkdir": 1,
            "os.open": 7,
            "os.path.exists": 1,
            "os.path.isdir": 1,
            "os.path.samefile": 1,
            "os.stat": 3,
            "path.lstat": 8,
            "path.mkdir": 2,
        },
        "KBFiles itself: the root resolved once, folders opened beneath it O_NOFOLLOW, the file read by descriptor with one name; its probes decide containment",
    ),
    ("pyrite/utils/yaml.py", "load_yaml_file"): (
        {"KBFiles.read_bytes": 1, "open": 1},
        "with kb_root, KBFiles.read_bytes; the raw `open` branch is operator config, and every raw caller is counted as load_yaml_file(raw) at its own site",
    ),
    ("pyrite/utils/yaml.py", "read_kb_yaml"): (
        {"KBFiles.present": 2, "load_yaml_file(kb_root)": 1},
        "the one kb.yaml loader (#1547): KBFiles.present, then load_yaml_file(kb_root=); present again when the read finds no regular file (a directory, a FIFO, or gone)",
    ),
}

SAME_ANSWER: dict[Site, Entry] = {
    ("pyrite/config.py", "KBConfig._kb_yaml_stamp"): (
        {"os.stat": 1},
        "the schema cache's key only (#1547): any change reads kb.yaml again through read_kb_yaml (KBFiles), whose answer, a link out refused, is the same either way",
    ),
    ("pyrite/services/kb_registry_service.py", "KBRegistryService._health_kb"): (
        {"path.exists": 1, "path.rglob": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it; rglob('*.md') counts names, a link by its name on every Python",
    ),
    ("pyrite/services/kb_service.py", "files_the_index_may_miss"): (
        {"KBFiles.contains": 1, "path.stat": 1},
        "the stat of a file after KBFiles.contains: a link out orders nothing",
    ),
    ("pyrite/services/qa_service.py", "QAService._check_non_string_titles"): (
        {"_is_regular_file": 1, "repo._read_inside": 1},
        "_is_regular_file skips; a link out that is a file is refused by _read_inside and skipped",
    ),
    ("pyrite/services/template_service.py", "TemplateService.list_templates"): (
        {"path.glob": 1, "path.is_dir": 1},
        "is_dir and glob('*.md') of _templates: each template is read through KBFiles and a refused one is skipped, so a folder link out lists nothing either way",
    ),
    ("pyrite/storage/document_manager.py", "DocumentManager._remove_old_file"): (
        {"path.iterdir": 1},
        "iterdir of the emptied folders above a file find_file found (read through KBFiles), up to the resolved root; a folder that is not empty or not a folder stops it either way",
    ),
    ("pyrite/storage/document_manager.py", "DocumentManager.save_entry"): (
        {"path.exists": 1},
        "old_path came from find_file (read through KBFiles)",
    ),
    ("pyrite/storage/index.py", "IndexManager._entry_to_dict"): (
        {"KBConfig.kb_yaml_present": 2, "path.stat": 1},
        "the stat of a file loaded through KBFiles (a link out never loads); kb_yaml_present",
    ),
    ("pyrite/storage/index.py", "IndexManager.check_staleness"): (
        {"KBFiles.contains": 1, "path.exists": 1, "path.stat": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it; file times after KBFiles.contains",
    ),
    ("pyrite/storage/index.py", "IndexManager.index_row"): (
        {"path.stat": 1},
        "the stat of a file the reconcile just read through KBFiles",
    ),
    ("pyrite/storage/index.py", "IndexManager.plan_reconcile"): (
        {"KBFiles.contains": 1, "path.stat": 1},
        "after KBFiles.contains: a link out is skipped before the stat",
    ),
    ("pyrite/storage/index.py", "unmodellable_files"): (
        {"path.stat": 1, "repo._read_inside": 1},
        "skips what is not a regular file; a link out that is one is refused by _read_inside and skipped",
    ),
    ("pyrite/storage/repository.py", "KBRepository.list_entries"): (
        {"_is_regular_file": 1, "repo._named": 1},
        "_named for collections; _is_regular_file skips a non-file, and a link out that is a file is refused by the load and skipped: the same answer",
    ),
    ("pyrite/storage/repository.py", "KBRepository.walk_files"): (
        {"path.rglob": 1},
        "rglob('*.md'): a wildcard lists a link by its name on every Python and walks into no folder link; each file is then read through KBFiles (list_files filters this walk, #1262)",
    ),
    ("pyrite/storage/repository.py", "KBRepository.read_for_write"): (
        {"path.stat": 1, "repo._read_inside": 1},
        "stat(follow_symlinks=False) of the name, after KBFiles refused it: tells a hard link from a link out, never reads the target",
    ),
    ("pyrite/storage/repository.py", "KBRepository.rename"): (
        {"_hard_link_fault": 1, "path.exists": 1, "repo._inside_bytes": 1, "repo._read_inside": 1},
        "src.exists(): src came from find_file (read through KBFiles); its bytes then read once through _read_inside; linkers through _inside_bytes; _hard_link_fault counts the name's own links",
    ),
    ("pyrite/storage/repository.py", "KBRepository.search_files"): (
        {"_is_regular_file": 1, "repo._read_inside": 1},
        "_is_regular_file skips; a link out that is a file is refused by _read_inside and skipped: the same answer",
    ),
    ("pyrite/storage/repository.py", "_hard_link_fault"): (
        {"path.lstat": 1},
        "lstat: the name's own link count (fixed here: stat followed a link out and reported the outside inode)",
    ),
    ("pyrite/storage/repository.py", "_is_regular_file"): (
        {"path.stat": 1},
        "stat follows a link: every caller skips on either outcome, and a link out that is a file is refused by its KBFiles read (callers counted as _is_regular_file)",
    ),
    ("pyrite/storage/repository.py", "_read_fault_reason"): (
        {"_hard_link_fault": 1},
        "_hard_link_fault (lstat) for a linker KBFiles refused",
    ),
}

NOT_A_KB_FILE: dict[Site, Entry] = {
    ("pyrite/__init__.py", "_read_version"): (
        {"path.is_file": 1, "path.read_text": 1},
        "the source checkout's pyproject.toml",
    ),
    ("pyrite/config.py", "GitCredential.token"): (
        {"path.read_text": 1},
        "the operator's git_credentials token_file",
    ),
    ("pyrite/config.py", "GitHubAuth.to_dict"): (
        {"path.exists": 1},
        "the operator's configured key path",
    ),
    ("pyrite/config.py", "KBConfig.local_db_path"): (
        {"path.mkdir": 1},
        "legacy: its only caller, get_db_path, has no caller",
    ),
    ("pyrite/config.py", "KBConfig.validate"): (
        {"path.exists": 1, "path.is_dir": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/config.py", "PyriteConfig.from_dict"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "the GitHub auth file: operator config",
    ),
    ("pyrite/config.py", "Repository.validate"): (
        {"path.exists": 1},
        "a repository folder: operator config",
    ),
    ("pyrite/config.py", "_in_container"): (
        {"open": 1, "os.path.exists": 2},
        "/.dockerenv, /proc/1/cgroup",
    ),
    ("pyrite/config.py", "_kb_names_on_disk"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "config.yaml: operator config",
    ),
    ("pyrite/config.py", "_read_config_mapping"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "config.yaml: operator config",
    ),
    ("pyrite/config.py", "_untrusted_save_data"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "config.yaml: operator config",
    ),
    ("pyrite/config.py", "_write_config"): (
        {"path.mkdir": 1},
        "config.yaml's folder: operator config",
    ),
    ("pyrite/config.py", "auto_discover_kbs"): (
        {"os.walk": 1, "path.exists": 1},
        "folders the operator names, or settings.kb_roots for a network caller (MCP discover, fixed here); kb.yaml found by name with os.walk (fixed here) and read with read_kb_yaml, KBFiles (#1547)",
    ),
    ("pyrite/config.py", "ensure_config_dir"): (
        {"path.mkdir": 1},
        "the config folder: operator config",
    ),
    ("pyrite/config.py", "load_config"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "config.yaml: operator config",
    ),
    ("pyrite/config.py", "resolve_config_source"): (
        {"path.is_file": 1},
        "config.yaml: operator config",
    ),
    ("pyrite/github_auth.py", "clear_github_auth"): (
        {"path.exists": 1},
        "the GitHub token file",
    ),
    ("pyrite/github_auth.py", "load_github_auth"): (
        {"load_yaml_file(raw)": 1, "path.exists": 1},
        "the GitHub token file",
    ),
    ("pyrite/github_auth.py", "start_oauth_flow"): (
        {"path.open": 1},
        "webbrowser.open, not a file",
    ),
    ("pyrite/renderers/quartz.py", "export_site"): (
        {"path.mkdir": 2},
        "the site export's output folder",
    ),
    ("pyrite/renderers/quartz.py", "scaffold_quartz_project"): (
        {"path.mkdir": 2},
        "the Quartz project folder being made",
    ),
    ("pyrite/server/api.py", "create_app"): (
        {"path.is_dir": 1},
        "the built web UI's folder",
    ),
    ("pyrite/server/static.py", "mount_static"): (
        {"path.exists": 1, "path.is_dir": 1, "path.read_text": 1},
        "the built web UI: index.html, assets",
    ),
    ("pyrite/server/static.py", "mount_static.favicon"): (
        {"path.exists": 1},
        "the built web UI's favicon",
    ),
    ("pyrite/server/static.py", "mount_static.spa_fallback"): (
        {"path.is_file": 1},
        "a file of the built web UI, inside its folder",
    ),
    ("pyrite/services/branding_service.py", "BrandingService._load"): (
        {"path.is_dir": 1, "path.is_file": 1, "path.stat": 1},
        "the operator's branding folder and branding.yaml",
    ),
    ("pyrite/services/branding_service.py", "BrandingService._load_raw"): (
        {"load_yaml_file(raw)": 1},
        "the operator's branding.yaml",
    ),
    ("pyrite/services/branding_service.py", "BrandingService.resolve_asset"): (
        {"path.is_dir": 1, "path.is_file": 1},
        "an asset in the operator's branding folder",
    ),
    ("pyrite/services/embedding_service.py", "_check_model_source"): (
        {"os.path.lexists": 1, "path.is_file": 1, "path.read_text": 1},
        "the embedding model's files, named by the operator",
    ),
    ("pyrite/services/ephemeral_service.py", "EphemeralKBService._remove_dir"): (
        {"path.exists": 1},
        "an ephemeral KB's folder, which Pyrite made",
    ),
    ("pyrite/services/ephemeral_service.py", "EphemeralKBService.create_ephemeral_kb"): (
        {"path.mkdir": 2},
        "the ephemeral root and a new folder in it, both Pyrite's",
    ),
    ("pyrite/services/export_service.py", "ExportService.export_collection_entries"): (
        {"path.mkdir": 1},
        "the export destination",
    ),
    ("pyrite/services/export_service.py", "ExportService.export_kb_to_repo"): (
        {"path.mkdir": 1},
        "the export destination",
    ),
    ("pyrite/services/git_service.py", "GitService._is_repository_root"): (
        {"os.path.samefile": 1, "path.exists": 1},
        "a registered repository's .git",
    ),
    ("pyrite/services/git_service.py", "GitService._network_plan"): (
        {"path.is_file": 1, "os.access": 1},
        "the operator's git_credentials ssh_key, probed before it is used",
    ),
    ("pyrite/services/git_service.py", "GitService.worktree_add"): (
        {"path.mkdir": 1},
        "a git worktree's folder",
    ),
    ("pyrite/services/index_backup.py", "backup_index"): (
        {"path.exists": 1, "path.is_dir": 1},
        "the index database",
    ),
    ("pyrite/services/kb_registry_service.py", "KBRegistryService.add_kb"): (
        {"path.mkdir": 1},
        "the new KB's folder, under kb_roots for a network caller",
    ),
    ("pyrite/services/kb_service.py", "KBService.list_entry_files"): (
        {"path.exists": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/services/repo_service.py", "RepoService._clone_and_register"): (
        {"path.exists": 1, "path.mkdir": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.create_pr"): (
        {"path.exists": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.discover_kbs"): (
        {"path.exists": 1},
        "a cloned repository's kb.yaml, read with read_kb_yaml (KBFiles) before it is registered (#1547)",
    ),
    ("pyrite/services/repo_service.py", "RepoService.get_repo_status"): (
        {"path.exists": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.scrub_workspace_remotes"): (
        {"path.exists": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.subscribe"): (
        {"path.exists": 1, "path.mkdir": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.sync"): (
        {"path.exists": 1},
        "the repo workspace",
    ),
    ("pyrite/services/repo_service.py", "RepoService.unsubscribe"): (
        {"path.exists": 1},
        "the repo workspace",
    ),
    ("pyrite/services/schema_service.py", "SchemaService._require_kb_directory"): (
        {"path.is_dir": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/services/url_checker.py", "URLChecker._urlopen"): (
        {"path.open": 1},
        "an HTTP opener, not a file",
    ),
    ("pyrite/services/url_checker.py", "URLChecker.load_cache"): (
        {"path.exists": 1, "path.read_text": 1},
        "the URL cache",
    ),
    ("pyrite/services/url_checker.py", "URLChecker.save_cache"): (
        {"path.mkdir": 1},
        "the URL cache's folder",
    ),
    ("pyrite/services/worktree_service.py", "WorktreeService._find_repo_root"): (
        {"path.exists": 1},
        "the repository's .git",
    ),
    ("pyrite/services/worktree_service.py", "WorktreeService.delete_worktree"): (
        {"path.exists": 1},
        "a worktree's folder",
    ),
    ("pyrite/services/worktree_service.py", "WorktreeService.ensure_worktree"): (
        {"path.mkdir": 1},
        "a worktree's overlay folder",
    ),
    ("pyrite/services/worktree_service.py", "WorktreeService.merge"): (
        {"path.exists": 1},
        "a worktree's overlay database",
    ),
    ("pyrite/services/worktree_service.py", "WorktreeService.reset_to_main"): (
        {"path.exists": 1},
        "a worktree's overlay database",
    ),
    ("pyrite/storage/connection.py", "ConnectionMixin._init_connection"): (
        {"path.is_dir": 1},
        "the index database",
    ),
    ("pyrite/storage/connection.py", "index_file_problem"): (
        {"path.exists": 2, "path.open": 1, "path.stat": 2},
        "the index database",
    ),
    ("pyrite/storage/index.py", "IndexManager.index_all"): (
        {"path.exists": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/storage/index.py", "IndexManager.reconcile_kb"): (
        {"path.exists": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/storage/index.py", "IndexManager.sync_incremental"): (
        {"path.exists": 1},
        "the KB's folder itself: the operator's registry entry, not a file in it",
    ),
    ("pyrite/storage/schema_repair.py", "schema_lock"): (
        {"os.open": 1},
        "the schema repair lock",
    ),
    ("pyrite/utils/atomic_write.py", "*"): (
        {"open": 1, "os.access": 1, "os.open": 2, "os.stat": 2, "path.read_bytes": 1},
        "the replace for operator files (config.yaml, an MCP client's config); KB files go through KBFiles",
    ),
    ("pyrite/utils/file_lock.py", "*"): (
        {
            "open": 3,
            "os.access": 3,
            "os.mkdir": 1,
            "os.open": 4,
            "os.path.isdir": 2,
            "os.path.isfile": 1,
            "os.path.islink": 1,
            "os.path.samefile": 1,
            "os.stat": 3,
        },
        "the lock beside a KB's git directory: .git, HEAD, commondir, the stripes",
    ),
    ("pyrite/utils/git_hardening.py", "network_home"): (
        {"os.path.isdir": 1, "os.open": 1},
        "the private HOME and global git config Pyrite makes for network git",
    ),
    ("pyrite/utils/install_hints.py", "extra_install_command"): (
        {"path.read_text": 1},
        "the installed package's direct_url.json",
    ),
    ("pyrite/utils/private_files.py", "*"): (
        {"os.mkdir": 1, "os.open": 2, "os.stat": 1, "path.exists": 1},
        "the GitHub token file, mode 0600",
    ),
    ("pyrite/utils/yaml.py", "dump_yaml_file"): (
        {"open": 1},
        "writes operator config",
    ),
}

LOCAL_ONLY: dict[Site, Entry] = {
    ("extensions/software-kb/src/pyrite_software_kb/validators.py", "_validate_component"): (
        {"path.exists": 1},
        "is_local_process(): a component's path: names any place on disk",
    ),
    ("pyrite/services/id_pin_service.py", "_prepared"): (
        {"KBFiles.read_bytes": 1},
        "module imported only by the CLI",
    ),
    ("pyrite/services/id_pin_service.py", "_registered_kb"): (
        {"path.is_file": 1},
        "module imported only by the CLI",
    ),
    ("pyrite/services/id_pin_service.py", "_write_pin"): (
        {"os.lstat": 1},
        "module imported only by the CLI",
    ),
    ("pyrite/services/id_pin_service.py", "scan"): (
        {"KBFiles.read_bytes": 1, "path.is_file": 1, "repo._inside_text": 1, "repo._named": 1},
        "module imported only by the CLI",
    ),
    ("pyrite/services/id_pin_service.py", "write_refusal"): (
        {"os.lstat": 1},
        "module imported only by the CLI",
    ),
    ("pyrite/services/kb_service.py", "KBService.add_entry_from_file"): (
        {"path.read_text": 1},
        "is_local_process(): `pyrite add` reads the path it is given",
    ),
}

SIZES = {
    "DECIDED_INSIDE": 55,
    "SAME_ANSWER": 20,
    "NOT_A_KB_FILE": 68,
    "LOCAL_ONLY": 7,
}

LISTS = {
    "DECIDED_INSIDE": DECIDED_INSIDE,
    "SAME_ANSWER": SAME_ANSWER,
    "NOT_A_KB_FILE": NOT_A_KB_FILE,
    "LOCAL_ONLY": LOCAL_ONLY,
}

#: Modules whose LOCAL_ONLY entries rest on only CLI modules importing them.
CLI_ONLY_MODULES = {"pyrite/services/id_pin_service.py"}

#: Every import of a CLI module from a scanned module: (importer, imported)
#: -> (the one function the import is made in, why the CLI module stays
#: exempt). The imported module's body must hold no read, probe, listing or
#: create (its functions run only under the CLI); with "*" (imported from
#: several places), no function of the imported module may hold one either.
CLI_IMPORTS: dict[tuple[str, str], tuple[str, str]] = {
    **{
        (
            f"extensions/{ext}/src/{pkg}/plugin.py",
            f"extensions/{ext}/src/{pkg}/cli.py",
        ): (
            f"{cls}.get_cli_commands",
            "returns the typer app (the admin plugin list asks only whether there is "
            "one); its commands run only under the CLI root",
        )
        for ext, pkg, cls in [
            ("encyclopedia", "pyrite_encyclopedia", "EncyclopediaPlugin"),
            (
                "journalism-investigation",
                "pyrite_journalism_investigation",
                "JournalismInvestigationPlugin",
            ),
            ("social", "pyrite_social", "SocialPlugin"),
            ("software-kb", "pyrite_software_kb", "SoftwareKBPlugin"),
            ("zettelkasten", "pyrite_zettelkasten", "ZettelkastenPlugin"),
        ]
    },
    ("pyrite/utils/errors.py", "pyrite/cli/output.py"): (
        "*",
        "output formats, the console and format flags for the shared error shape; the "
        "module holds no read, probe, listing or create at all",
    ),
    ("pyrite/utils/errors.py", "pyrite/cli/outcome.py"): (
        "*",
        "the REFUSED outcome constant; the module holds no read, probe, listing or create",
    ),
}


# -- the walk --------------------------------------------------------------------

_READS = {"read_text", "read_bytes", "open"}
_PROBES = {"exists", "is_file", "is_dir", "is_symlink", "stat", "lstat", "samefile"}
_LISTS = {"iterdir", "glob", "rglob", "walk"}
_CREATES = {"mkdir", "touch", "symlink_to", "hardlink_to"}
_PATH_ATTRS = _READS | _PROBES | _LISTS | _CREATES
_OS = {"open", "stat", "lstat", "access", "scandir", "listdir", "walk", "mkdir", "makedirs"}
_OS_PATH = {"exists", "isfile", "isdir", "islink", "lexists", "getsize", "getmtime", "samefile"}
_IO = {"open", "FileIO"}
_GLOB = {"glob", "iglob"}
_KBFILES = {"read_bytes", "present", "contains", "resolved", "make_dirs", "check", "require"}
_KBFILES_NAMES = {"inside", "kbf", "files"}
_REPO = {"_read_inside", "_inside_bytes", "_inside_text", "present", "_named"}
_HELPERS = {"_is_regular_file", "_hard_link_fault", "FileIO", "open"}
_TRACKED_ATTR_LOADS = {"kb_yaml_present"}


def _literal(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and not any(c in node.value for c in "*?[")
    )


def _kind(node):
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "load_yaml_file":
            kb_root = next((k.value for k in node.keywords if k.arg == "kb_root"), None)
            raw = kb_root is None or (isinstance(kb_root, ast.Constant) and kb_root.value is None)
            return "load_yaml_file(raw)" if raw else "load_yaml_file(kb_root)"
        if func.id == "open":
            return "open"
        if func.id == "FileIO":
            return "io.FileIO"
        if func.id in {"_is_regular_file", "_hard_link_fault"}:
            return func.id
        if func.id == "present":
            return "KBFiles.present"
        if func.id == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            name = node.args[1].value
            if name in _TRACKED_ATTR_LOADS:
                return f"KBConfig.{name}"
            if isinstance(name, str) and (name in _PATH_ATTRS or name in _KBFILES):
                return f"getattr.{name}"
        return None
    if not isinstance(func, ast.Attribute):
        return None
    attr, value = func.attr, func.value
    base = ast.unparse(value) if isinstance(value, (ast.Name, ast.Attribute)) else None
    if base == "os.path":
        return f"os.path.{attr}" if attr in _OS_PATH else None
    if base == "os":
        return f"os.{attr}" if attr in _OS else None
    if base == "io":
        return f"io.{attr}" if attr in _IO else None
    if base == "glob":
        return f"glob.{attr}" if attr in _GLOB else None
    kbfiles = (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "KBFiles"
    ) or (isinstance(value, ast.Name) and value.id in _KBFILES_NAMES)
    if kbfiles and attr in _KBFILES:
        return f"KBFiles.{attr}"
    repo_receiver = (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "KBRepository"
    ) or (isinstance(value, ast.Name) and value.id in {"repo", "target_repo"})
    if attr in _REPO or (repo_receiver and attr == "exists"):
        return f"repo.{attr}"
    if attr in _PATH_ATTRS:
        if attr in {"glob", "rglob"} and node.args and _literal(node.args[0]):
            return f"path.{attr}(literal)"
        return f"path.{attr}"
    return None


class _Walker(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.found = []

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = _scoped  # noqa: N815
    visit_FunctionDef = _scoped  # noqa: N815
    visit_AsyncFunctionDef = _scoped  # noqa: N815

    def _here(self):
        return ".".join(self.stack) or "<module>"

    def visit_Call(self, node):
        kind = _kind(node)
        if kind is not None:
            self.found.append((self._here(), kind))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr in _TRACKED_ATTR_LOADS and isinstance(node.ctx, ast.Load):
            self.found.append((self._here(), f"KBConfig.{node.attr}"))
        self.generic_visit(node)


def calls_in(source):
    w = _Walker()
    w.visit(ast.parse(source))
    return w.found


def _dotted(rel):
    parts = list(Path(rel).with_suffix("").parts)
    if parts[0] == "extensions":
        parts = parts[3:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _modules():
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            yield str(path.relative_to(REPO)), path


def _is_cli_entry(rel):
    return rel.startswith(CLI_ENTRY) or rel.endswith("/cli.py")


def _imports(rel, tree):
    me = _dotted(rel).split(".")
    pkg = me if rel.endswith("__init__.py") else me[:-1]
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = pkg[: len(pkg) - (node.level - 1)] if node.level else []
            mod = ".".join([*base, *(node.module.split(".") if node.module else [])])
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}
    return out


def import_graph():
    """{importer: {imported module}} over every module, lazy imports and
    ``import_module(".name", __package__)`` strings included."""
    trees = {rel: ast.parse(p.read_text(encoding="utf-8")) for rel, p in _modules()}
    dotted = {_dotted(rel): rel for rel in trees}
    edges = {}
    for rel, tree in trees.items():
        names = _imports(rel, tree)
        me = _dotted(rel).split(".")
        pkg = me if rel.endswith("__init__.py") else me[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if v.startswith(".") and v[1:].replace("_", "a").isalnum():
                    names.add(".".join([*pkg, v[1:]]))
        edges[rel] = {dotted[n] for n in names if n in dotted and dotted[n] != rel}
    return trees, edges


def _scope() -> tuple[set[str], set[str], dict[str, ast.Module]]:
    trees, _ = import_graph()
    exempt = {rel for rel in trees if _is_cli_entry(rel)}
    return set(trees) - exempt, exempt, trees


def _counts(rel: str, tree: ast.AST) -> dict[Site, Counter]:
    walker = _Walker()
    walker.visit(tree)
    out: dict[Site, Counter] = {}
    for function, kind in walker.found:
        key = (rel, "*") if rel in WHOLE_MODULES else (rel, function)
        out.setdefault(key, Counter())[kind] += 1
    return out


def _sites() -> dict[Site, Counter]:
    in_scope, _, trees = _scope()
    out: dict[Site, Counter] = {}
    for rel in sorted(in_scope):
        out.update(_counts(rel, trees[rel]))
    return out


def _classified() -> dict[Site, tuple[str, dict[str, int]]]:
    out: dict[Site, tuple[str, dict[str, int]]] = {}
    for name, lst in LISTS.items():
        for site, (counts, _reason) in lst.items():
            assert site not in out, f"{site} is in {out[site][0]} and {name}"
            out[site] = (name, counts)
    return out


def _mismatches(sites: dict[Site, Counter]) -> list[str]:
    classified = _classified()
    found = []
    for site, counts in sorted(sites.items()):
        if site not in classified:
            found.append(f"  {site[0]}: {site[1]}: unclassified {dict(counts)}")
        elif dict(counts) != classified[site][1]:
            found.append(
                f"  {site[0]}: {site[1]}: listed {classified[site][1]} in "
                f"{classified[site][0]}, found {dict(counts)}"
            )
    return found


# -- the ratchet -------------------------------------------------------------------


def test_every_read_probe_listing_and_create_is_classified() -> None:
    sites = _sites()
    assert len(sites) > 100, "the walk found almost nothing; the scan is broken"
    wrong = _mismatches(sites)
    assert not wrong, (
        "a read, probe, listing or create is unclassified, or a classified function's "
        "calls changed. A KB file that answers a caller goes through KBFiles "
        "(read_bytes, present, make_dirs) or the repository's readers; anything else "
        "is classified in tests/test_kb_reads_are_one_function.py, by hand:\n" + "\n".join(wrong)
    )


def test_every_listed_site_still_exists() -> None:
    sites = _sites()
    gone = sorted(site for site in _classified() if site not in sites)
    assert not gone, f"gone, so delete the line and lower the size: {gone}"


@pytest.mark.control(reason="a check of the ratchet's own lists")
def test_the_list_sizes_are_pinned() -> None:
    assert {name: len(lst) for name, lst in LISTS.items()} == SIZES


@pytest.mark.control(reason="a check of the ratchet's own lists")
def test_every_entry_has_a_reason() -> None:
    for name, lst in LISTS.items():
        for site, (_, reason) in lst.items():
            assert reason.strip(), f"{name}: {site} has no reason"


def _function(tree: ast.AST, qualname: str):
    nodes: list[ast.AST] = [tree]
    for part in qualname.split("."):
        nxt = None
        for parent in nodes:
            for child in ast.iter_child_nodes(parent):
                if (
                    isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                    and child.name == part
                ):
                    nxt = child
        if nxt is None:
            return None
        nodes = [nxt]
    return nodes[0]


def test_a_local_only_guard_is_proved() -> None:
    """A LOCAL_ONLY entry names its guard: ``is_local_process()`` called in the
    function, or a module that only CLI modules import."""
    _, edges = import_graph()
    for (module, function), (_, reason) in LOCAL_ONLY.items():
        if reason.startswith("is_local_process()"):
            tree = ast.parse((REPO / module).read_text(encoding="utf-8"))
            node = _function(tree, function)
            assert node is not None, (module, function)
            calls = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            assert "is_local_process" in calls, f"{module}: {function} has no guard"
        else:
            assert module in CLI_ONLY_MODULES, (module, function)
    for module in CLI_ONLY_MODULES:
        importers = sorted(rel for rel, targets in edges.items() if module in targets)
        assert importers, f"{module} has no importer at all"
        outside = [rel for rel in importers if not _is_cli_entry(rel)]
        assert not outside, f"{module} is imported outside the CLI by {outside}"


def test_the_cli_exemption_is_proved_by_imports() -> None:
    """A CLI module is left out of the walk only while every import of it from
    a scanned module is listed in CLI_IMPORTS, made inside the function named
    there, and the module's own body (outside its functions) holds no read,
    probe, listing or create."""
    in_scope, exempt, trees = _scope()
    _, edges = import_graph()
    found = {(a, b) for a in in_scope for b in edges[a] & exempt}
    assert found == set(CLI_IMPORTS), (
        "an import of a CLI module from a scanned module is unlisted (or a listed one is "
        f"gone): {sorted(found ^ set(CLI_IMPORTS))}"
    )
    for (importer, imported), (function, _reason) in CLI_IMPORTS.items():
        assert (imported, "<module>") not in _counts(imported, trees[imported])
        if function == "*":
            assert not _counts(imported, trees[imported]), (
                f"{imported} holds a read, probe, listing or create, and {importer} "
                "imports it from more than one function"
            )
            continue
        dotted = _dotted(imported)
        for node in ast.walk(trees[importer]):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = _imports(importer, ast.Module(body=[node], type_ignores=[]))
                if dotted in names:
                    host = _function(trees[importer], function)
                    assert host is not None, (importer, function)
                    assert any(n is node for n in ast.walk(host)), (
                        f"{importer} imports {imported} outside {function}"
                    )


# -- planted violations: the walk sees every shape --------------------------------

PLANTED = [
    ("p.read_text()", "path.read_text"),
    ("p.read_bytes()", "path.read_bytes"),
    ("open(p)", "open"),
    ("p.open('rb')", "path.open"),
    ("os.open(p, 0)", "os.open"),
    ("io.open(p)", "io.open"),
    ("io.FileIO(fd)", "io.FileIO"),
    ("FileIO(fd)", "io.FileIO"),
    ("getattr(p, 'read_text')()", "getattr.read_text"),
    ("load_yaml_file(p)", "load_yaml_file(raw)"),
    ("load_yaml_file(p, kb_root=None)", "load_yaml_file(raw)"),
    ("load_yaml_file(p, kb_root=root)", "load_yaml_file(kb_root)"),
    ("p.exists()", "path.exists"),
    ("p.is_file()", "path.is_file"),
    ("p.is_dir()", "path.is_dir"),
    ("p.is_symlink()", "path.is_symlink"),
    ("p.stat()", "path.stat"),
    ("p.lstat()", "path.lstat"),
    ("os.stat(p)", "os.stat"),
    ("os.lstat(p)", "os.lstat"),
    ("os.access(p, 0)", "os.access"),
    ("os.path.exists(p)", "os.path.exists"),
    ("os.path.isfile(p)", "os.path.isfile"),
    ("os.path.isdir(p)", "os.path.isdir"),
    ("os.path.islink(p)", "os.path.islink"),
    ("os.path.lexists(p)", "os.path.lexists"),
    ("os.path.getsize(p)", "os.path.getsize"),
    ("p.iterdir()", "path.iterdir"),
    ("p.glob('*.md')", "path.glob"),
    ("p.rglob('*.md')", "path.rglob"),
    ("p.rglob('__collection.yaml')", "path.rglob(literal)"),
    ("p.walk()", "path.walk"),
    ("os.scandir(p)", "os.scandir"),
    ("os.listdir(p)", "os.listdir"),
    ("os.walk(p)", "os.walk"),
    ("glob.glob(p)", "glob.glob"),
    ("p.mkdir()", "path.mkdir"),
    ("os.mkdir(p)", "os.mkdir"),
    ("os.makedirs(p)", "os.makedirs"),
    ("p.touch()", "path.touch"),
    ("_is_regular_file(p)", "_is_regular_file"),
    ("_hard_link_fault(p)", "_hard_link_fault"),
    ("KBFiles(root).read_bytes(p)", "KBFiles.read_bytes"),
    ("KBFiles(root).present(p)", "KBFiles.present"),
    ("inside.present(p)", "KBFiles.present"),
    ("KBFiles(root).make_dirs(p)", "KBFiles.make_dirs"),
    ("self._read_inside(p)", "repo._read_inside"),
    ("self.present(p)", "repo.present"),
    ("KBRepository(c).exists(i)", "repo.exists"),
]


@pytest.mark.control(reason="a check of the ratchet's walker: every shape is seen")
@pytest.mark.parametrize(("call", "kind"), PLANTED)
def test_a_planted_call_is_found_with_its_kind(call, kind) -> None:
    source = (
        f"class Planted:\n    def reader(self, p, fd, root, c, i, inside):\n        return {call}\n"
    )
    assert calls_in(source) == [("Planted.reader", kind)]
    assert ("pyrite/services/planted.py", "Planted.reader") not in _classified()


# -- mutations: reverting this theme's fixes fails the ratchet -----------------------

#: (module, the text as it is, a revert or swap of it): each must change the
#: counts of a classified function.
MUTATIONS = [
    (
        "extensions/software-kb/src/pyrite_software_kb/board.py",
        "KBFiles(kb_path).present(board_file)",
        "board_file.exists()",
    ),
    (
        "pyrite/services/template_service.py",
        "KBFiles(root).present(path)",
        "path.is_file()",
    ),
    (
        "pyrite/utils/yaml.py",
        "if not inside.present(path):  # nothing there",
        "if not path.exists():  # nothing there",
    ),
    (
        "extensions/software-kb/src/pyrite_software_kb/board.py",
        "kb_root=kb_path",
        "kb_root=None",
    ),
    (
        "pyrite/services/review_service.py",
        "KBFiles(root).read_bytes(Path(file_path))",
        'getattr(Path(file_path), "read_bytes")()',
    ),
    (
        "pyrite/storage/repository.py",
        'for f in self._named("__collection.yaml")',
        'for f in self.path.rglob("__collection.yaml")',
    ),
    (
        "pyrite/storage/repository.py",
        "links = path.lstat().st_nlink",
        "links = path.stat().st_nlink",
    ),
    (
        "pyrite/services/kb_registry_service.py",
        "inside.make_dirs(folder)",
        "folder.mkdir(parents=True, exist_ok=True)",
    ),
    (
        "pyrite/utils/yaml.py",
        "load_yaml_file(path, kb_root=root)",
        "load_yaml_file(path)",
    ),
]


@pytest.mark.parametrize(("module", "now", "mutated"), MUTATIONS)
def test_a_reverted_fix_fails_the_ratchet(module, now, mutated) -> None:
    source = (REPO / module).read_text(encoding="utf-8")
    assert source.count(now) == 1, f"{module} no longer holds {now!r}; update the mutation"
    assert not _mismatches(_counts(module, ast.parse(source)))
    wrong = _mismatches(_counts(module, ast.parse(source.replace(now, mutated))))
    assert wrong, f"replacing {now!r} with {mutated!r} in {module} passes the ratchet"
