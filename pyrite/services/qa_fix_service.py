"""
QA Fix Service — auto-fix for safe structural issues in knowledge bases.

Extracted from QAService. Handles date normalisation, missing field defaults,
broken wikilink fixes by edit distance, and tag normalisation.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from ..config import PyriteConfig
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)


class QAFixService:
    """Auto-fix safe structural issues in knowledge bases."""

    # Rules that can be safely auto-fixed.
    FIXABLE_RULES = frozenset(
        {
            "invalid_date",
            "schema_violation",  # only missing-field sub-cases
            "broken_link",
        }
    )

    #: Every kind of fix `fix_kb` dispatches: the validation rules above, plus
    #: `tag_case`, which comes from the tag index rather than an issue. A test
    #: runs each through a write that lands, raises, and finds no file
    #: (tests/test_qa_records_only_what_ran.py); add a kind here and there.
    FIX_KINDS = ("invalid_date", "schema_violation", "broken_link", "tag_case")

    def __init__(self, config: PyriteConfig, db: PyriteDB, kb_svc=None):
        self.config = config
        self.db = db
        self._kb_svc = kb_svc

    def fix_kb(
        self,
        kb_name: str,
        *,
        dry_run: bool = True,
        fix_rules: list[str] | None = None,
        validate_kb_fn=None,
    ) -> dict[str, Any]:
        """Auto-fix safe structural issues in a KB.

        Runs validation first, then applies mechanical fixes where
        unambiguous.  Returns a report of what was fixed, what was skipped,
        what still needs manual attention, and what failed.

        ``fixed`` holds a fix only once its write returned (#788): a write
        that raised, an entry whose file is gone, or a link rewrite that
        found nothing to change goes to ``failed`` with its ``reason``, and
        one failing entry does not stop the rest. ``manual`` is only for
        issues a person must decide. With ``dry_run`` nothing is written, so
        ``fixed`` lists the fixes that would be attempted and nothing fails.

        Args:
            kb_name: KB to fix.
            dry_run: If True, report planned fixes without writing.
            fix_rules: Optional list of rule names to restrict fixes to.
            validate_kb_fn: Callable to run validation (injected by QAService).

        Returns:
            Dict with fixed, skipped, manual, failed lists plus summary counts.
        """
        if self._kb_svc is not None:
            kb_svc = self._kb_svc
        else:
            from .kb_service import KBService

            kb_svc = KBService(self.config, self.db)

        # Run validation to discover issues
        result = validate_kb_fn(kb_name)
        issues = result["issues"]

        fixed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        manual: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        def attempt(fixes: list[dict[str, Any]], write) -> None:
            """Run one write for ``fixes``: fixed if it returned, else failed."""
            if dry_run:
                fixed.extend(fixes)
                return
            try:
                not_applied = write()
            except Exception as e:
                logger.warning("Failed to apply a fix to %s: %s", fixes[0]["entry_id"], e)
                not_applied = f"update_failed: {type(e).__name__}: {e}"
            if not_applied:
                failed.extend({**fix, "reason": not_applied} for fix in fixes)
            else:
                fixed.extend(fixes)

        # Collect all entry IDs in this KB for wikilink matching
        all_entry_ids = self._get_all_entry_ids(kb_name)

        # Group issues by entry_id for batch updates
        issues_by_entry: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            eid = issue.get("entry_id", "")
            if eid:
                issues_by_entry.setdefault(eid, []).append(issue)

        # Also run tag normalisation pass
        tag_fixes = self._find_tag_normalisation_fixes(kb_name)

        # Apply fix_rules filter if provided
        active_fix_rules = set(fix_rules) if fix_rules else None

        for entry_id, entry_issues in issues_by_entry.items():
            updates: dict[str, Any] = {}
            field_fixes: list[dict[str, Any]] = []

            for issue in entry_issues:
                rule = issue.get("rule", "")

                # Filter by fix_rules if specified
                if active_fix_rules and rule not in active_fix_rules:
                    if rule not in active_fix_rules and "tag_case" not in active_fix_rules:
                        skipped.append({**issue, "reason": "filtered_by_fix_rule"})
                        continue

                if rule == "invalid_date":
                    fix = self._fix_invalid_date(issue)
                    if fix:
                        updates["date"] = fix["new_value"]
                        field_fixes.append(fix)
                    else:
                        manual.append({**issue, "reason": "cannot_normalise_date"})

                elif rule == "schema_violation" and "missing" in issue.get("message", "").lower():
                    # Only reached by an issue whose message says "missing":
                    # the KB schema's required-field errors say "required",
                    # so no validator yields one today (#795 Learned).
                    fix = self._fix_missing_field(issue)
                    if fix:
                        # _fix_missing_field's only default is `importance`,
                        # a top-level field. A metadata default would have to
                        # send only its own key (update_entry merges metadata
                        # into the file's), never a dict read from the index.
                        updates[fix["field"]] = fix["new_value"]
                        field_fixes.append(fix)
                    else:
                        manual.append({**issue, "reason": "no_safe_default"})

                elif rule == "broken_link":
                    fix = self._fix_broken_link(issue, all_entry_ids)
                    if fix:
                        attempt(
                            [fix],
                            lambda fix=fix, issue=issue: self._apply_link_fix(
                                issue["entry_id"],
                                issue["kb_name"],
                                fix["old_target"],
                                fix["new_value"],
                                kb_svc,
                            ),
                        )
                    else:
                        manual.append({**issue, "reason": "no_close_match"})
                else:
                    manual.append({**issue, "reason": "not_auto_fixable"})

            # Apply accumulated field updates for this entry: one write, so
            # its fixes land or fail together.
            if field_fixes:
                attempt(
                    field_fixes,
                    lambda entry_id=entry_id, updates=updates: self._apply_field_updates(
                        entry_id, kb_name, updates, kb_svc
                    ),
                )

        # Tag normalisation
        for tag_fix in tag_fixes:
            if active_fix_rules and "tag_case" not in active_fix_rules:
                skipped.append({**tag_fix, "reason": "filtered_by_fix_rule"})
                continue
            attempt(
                [tag_fix],
                lambda tag_fix=tag_fix: self._apply_tag_normalisation(
                    tag_fix["entry_id"],
                    kb_name,
                    tag_fix["old_value"],
                    tag_fix["new_value"],
                    kb_svc,
                ),
            )

        return {
            "kb_name": kb_name,
            "dry_run": dry_run,
            "fixed_count": len(fixed),
            "skipped_count": len(skipped),
            "manual_count": len(manual),
            "failed_count": len(failed),
            "fixed": fixed,
            "skipped": skipped,
            "manual": manual,
            "failed": failed,
        }

    @staticmethod
    def _apply_field_updates(entry_id: str, kb_name: str, updates: dict[str, Any], kb_svc) -> None:
        """Write one entry's field fixes in one update. A missing file raises
        from ``update_entry`` (``EntryNotFoundError``), so it is reported as
        ``update_failed`` like any other write that raised."""
        kb_svc.update_entry(entry_id, kb_name, **updates)

    def _load(self, entry_id: str, kb_name: str):
        """The entry as its file has it, or ``None`` when there is no file."""
        from ..storage.repository import KBRepository

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return None
        return KBRepository(kb_config).load(entry_id)

    def _get_all_entry_ids(self, kb_name: str) -> list[str]:
        """Get all entry IDs in a KB for edit-distance matching."""
        rows = self.db.execute_sql(
            "SELECT id FROM entry WHERE kb_name = :kb_name",
            {"kb_name": kb_name},
        )
        return [row["id"] for row in rows]

    @staticmethod
    def _normalise_date(raw: str) -> str | None:
        """Try to normalise a date string to YYYY-MM-DD.

        Handles: YYYY, YYYY-MM, MM/DD/YYYY, DD/MM/YYYY (if unambiguous),
        and ISO datetime strings.

        Returns normalised string or None if not parseable.
        """
        raw = raw.strip().strip("'\"")

        # Already valid YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return raw

        # Just a year: YYYY -> YYYY-01-01
        if re.match(r"^\d{4}$", raw):
            return f"{raw}-01-01"

        # Year-month: YYYY-MM -> YYYY-MM-01
        m = re.match(r"^(\d{4})-(\d{1,2})$", raw)
        if m:
            year, month = m.group(1), m.group(2).zfill(2)
            return f"{year}-{month}-01"

        # ISO datetime: YYYY-MM-DDTHH:MM:SS... -> YYYY-MM-DD
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T", raw)
        if m:
            return m.group(1)

        # US-style: MM/DD/YYYY
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
        if m:
            month, day, year = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
            try:
                datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
                return f"{year}-{month}-{day}"
            except ValueError:
                return None

        return None

    def _fix_invalid_date(self, issue: dict[str, Any]) -> dict[str, Any] | None:
        """Attempt to fix an invalid date issue. Returns fix dict or None."""
        msg = issue.get("message", "")
        # Extract the raw date from the message (after "invalid date: '...'")
        m = re.search(r"invalid date:\s*'([^']*)'", msg)
        if not m:
            return None

        raw_date = m.group(1)
        normalised = self._normalise_date(raw_date)
        if normalised is None:
            return None

        return {
            "entry_id": issue["entry_id"],
            "kb_name": issue["kb_name"],
            "rule": "invalid_date",
            "field": "date",
            "old_value": raw_date,
            "new_value": normalised,
            "message": f"Normalised date '{raw_date}' -> '{normalised}'",
        }

    @staticmethod
    def _fix_missing_field(issue: dict[str, Any]) -> dict[str, Any] | None:
        """Provide a type-appropriate default for a missing required field.

        Only handles fields where a safe default exists.
        Returns fix dict or None.
        """
        field = issue.get("field", "")

        # Map of field names to safe defaults
        safe_defaults: dict[str, Any] = {
            "importance": 5,
        }

        default_val = safe_defaults.get(field)
        if default_val is None:
            return None

        return {
            "entry_id": issue["entry_id"],
            "kb_name": issue["kb_name"],
            "rule": "schema_violation",
            "field": field,
            "old_value": None,
            "new_value": default_val,
            "message": f"Added default {field}={default_val}",
        }

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        """Compute Levenshtein edit distance between two strings."""
        if len(a) < len(b):
            return QAFixService._edit_distance(b, a)

        if len(b) == 0:
            return len(a)

        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                cost = 0 if ca == cb else 1
                curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
            prev = curr

        return prev[len(b)]

    def _fix_broken_link(
        self,
        issue: dict[str, Any],
        all_entry_ids: list[str],
    ) -> dict[str, Any] | None:
        """Fix broken link by finding closest entry ID by edit distance.

        Only fixes if the best match is within 40% edit distance of the
        target ID length (i.e. a plausible typo, not a completely
        different ID).

        Returns fix dict or None.
        """
        msg = issue.get("message", "")
        # Extract target_id from message: "links to non-existent 'xxx' in 'yyy'"
        m = re.search(r"links to non-existent '([^']*)'", msg)
        if not m:
            return None

        broken_target = m.group(1)

        if not all_entry_ids:
            return None

        # Find closest match
        best_id = None
        best_dist = float("inf")
        for eid in all_entry_ids:
            d = self._edit_distance(broken_target, eid)
            if d < best_dist:
                best_dist = d
                best_id = eid

        # Only fix if within reasonable edit distance (40% of target length)
        max_allowed = max(2, int(len(broken_target) * 0.4))
        if best_dist > max_allowed or best_id is None:
            return None

        return {
            "entry_id": issue["entry_id"],
            "kb_name": issue["kb_name"],
            "rule": "broken_link",
            "field": "links",
            "old_target": broken_target,
            "old_value": broken_target,
            "new_value": best_id,
            "edit_distance": best_dist,
            "message": f"Fixed broken link '{broken_target}' -> '{best_id}' (distance={best_dist})",
        }

    def _apply_link_fix(
        self,
        entry_id: str,
        kb_name: str,
        old_target: str,
        new_target: str,
        kb_svc,
    ) -> str | None:
        """Update a broken link target in the entry's link list and re-save.

        Returns ``None`` once the write returned, else why nothing was
        written: the entry's file is gone, or the target is not in its
        ``links`` (a ``[[wikilink]]`` in the body is not rewritten).
        """
        entry = self._load(entry_id, kb_name)
        if not entry:
            return "entry_not_found: the entry's file is gone"

        # Update the link target in the entry's links list
        changed = False
        for link in entry.links:
            if link.target == old_target:
                link.target = new_target
                changed = True

        if not changed:
            return (
                f"link_not_rewritten: '{old_target}' is not in the entry's links "
                "(a link in the body is not rewritten)"
            )
        kb_svc.update_entry(entry_id, kb_name, links=entry.links)
        return None

    def _find_tag_normalisation_fixes(self, kb_name: str) -> list[dict[str, Any]]:
        """Find tags with mixed case that should be lowercased.

        Returns list of fix dicts (one per entry+tag combination).
        """
        rows = self.db.execute_sql(
            "SELECT et.entry_id, t.name AS tag_name FROM entry_tag et "
            "JOIN tag t ON et.tag_id = t.id "
            "WHERE et.kb_name = :kb_name AND t.name != LOWER(t.name)",
            {"kb_name": kb_name},
        )

        fixes = []
        for row in rows:
            tag_name = row["tag_name"]
            fixes.append(
                {
                    "entry_id": row["entry_id"],
                    "kb_name": kb_name,
                    "rule": "tag_case",
                    "field": "tags",
                    "old_value": tag_name,
                    "new_value": tag_name.lower(),
                    "message": f"Normalised tag '{tag_name}' -> '{tag_name.lower()}'",
                }
            )

        return fixes

    def _apply_tag_normalisation(
        self,
        entry_id: str,
        kb_name: str,
        old_tag: str,
        new_tag: str,
        kb_svc,
    ) -> str | None:
        """Normalise a single tag on an entry by updating its tag list.

        Returns ``None`` once the write returned, else why nothing was written.
        """
        entry = self._load(entry_id, kb_name)
        if not entry:
            return "entry_not_found: the entry's file is gone"

        new_tags = [new_tag if t == old_tag else t for t in entry.tags]
        kb_svc.update_entry(entry_id, kb_name, tags=new_tags)
        return None
