"""
Repo Service — High-level repository management.

Orchestrates GitService + DB + Config to implement subscribe, fork, sync,
and unsubscribe workflows. This is the main entry point for collaboration
operations.
"""

import logging
import shutil
from pathlib import Path

from pyrite.utils.yaml import load_yaml_file

from ..config import (
    ConfigError,
    KBConfig,
    KBType,
    PyriteConfig,
    auto_discover_kbs,
    check_config_save,
    save_config,
)
from ..github_auth import get_github_token
from ..storage.database import PyriteDB
from ..storage.index import IndexManager
from .credential_events import announce_kb_policy_change
from .git_service import GitService
from .kb_names import PLAIN_KB_NAME_RULE, is_plain_kb_name
from .user_service import UserService

logger = logging.getLogger(__name__)


class RepoService:
    """High-level repo operations for collaboration workflows."""

    def __init__(
        self,
        config: PyriteConfig,
        db: PyriteDB,
        git_service: GitService | None = None,
        user_service: UserService | None = None,
        github_token: str | None = None,
    ):
        self.config = config
        self.db = db
        self.git = git_service or GitService()
        self.user_service = user_service or UserService(db)
        self._github_token = github_token

    def _get_token(self) -> str | None:
        """Get GitHub token: injected token first, then CLI fallback."""
        if self._github_token:
            return self._github_token
        return get_github_token()

    def subscribe(
        self,
        remote_url: str,
        name: str | None = None,
        branch: str = "main",
    ) -> dict:
        """
        Subscribe to a remote repo: clone, discover KBs, index with attribution.

        Returns dict with repo info and discovered KBs.
        """
        parsed = GitService.parse_github_url(remote_url)
        if not parsed:
            return {"success": False, "error": "Could not parse GitHub URL"}

        owner, repo_name = parsed
        full_name = name or f"{owner}/{repo_name}"
        refusal = self._refuse_repo_name(full_name)
        if refusal:
            return refusal
        # Before the clone and the index rows (#377).
        check_config_save(self.config)

        # Determine workspace path
        workspace_path = self.config.settings.workspace_path / owner / repo_name
        if workspace_path.exists():
            # The absolute path is for the operator only (CodeQL #51): a caller
            # who can name owner/repo learns nothing from "already subscribed".
            logger.warning("Subscribe refused: %s already exists", workspace_path)
            return {
                "success": False,
                "error_code": "PATH_EXISTS",
                "error": f"'{owner}/{repo_name}' is already present in the workspace",
            }

        workspace_path.parent.mkdir(parents=True, exist_ok=True)

        # Clone (shallow for subscriptions)
        token = self._get_token()
        success, code, msg = GitService.clone_with_code(
            remote_url, workspace_path, branch=branch, depth=1, token=token
        )
        if not success:
            return {"success": False, "error_code": code, "error": msg}

        registered = self._register_clone(
            workspace_path,
            full_name=full_name,
            remote_url=remote_url,
            owner=owner,
            branch=branch,
            read_only=True,  # Subscriptions are read-only
            workspace_role="subscriber",
        )
        if not registered["success"]:
            return registered
        kb_names = registered["kbs"]

        return {
            "success": True,
            "repo": full_name,
            "path": str(workspace_path),
            "kbs": kb_names,
            "kb_default_role": None,
            "entries_indexed": sum(self.db.count_entries(kb) for kb in kb_names),
        }

    def fork_and_subscribe(self, remote_url: str) -> dict:
        """
        Fork a repo on GitHub, then subscribe to the fork.

        Returns dict with fork info.
        """
        parsed = GitService.parse_github_url(remote_url)
        if not parsed:
            return {"success": False, "error": "Could not parse GitHub URL"}

        owner, repo_name = parsed
        token = self._get_token()
        if not token:
            return {"success": False, "error": "GitHub authentication required for forking"}
        # Before the fork on GitHub, the clone and the index rows (#377).
        check_config_save(self.config)

        # Fork on GitHub
        success, fork_data = GitService.fork_repo(owner, repo_name, token)
        if not success:
            return {"success": False, "error": fork_data.get("error", "Fork failed")}

        fork_clone_url = fork_data.get("clone_url", "")
        fork_full_name = fork_data.get("full_name", "")

        if not fork_clone_url:
            return {"success": False, "error": "Fork created but no clone URL returned"}

        # Subscribe to our fork (full clone, not shallow — we need history)
        result = self._clone_and_register(fork_clone_url, fork_full_name, depth=None)
        if not result["success"]:
            return result

        # Set upstream relationship
        upstream_repo = self.db.get_repo(name=f"{owner}/{repo_name}")
        fork_repo = self.db.get_repo(name=fork_full_name)
        if upstream_repo and fork_repo:
            self.db.set_repo_upstream(fork_repo["id"], upstream_repo["id"])

        # Add upstream remote
        workspace_path = Path(result["path"])
        GitService.add_remote(workspace_path, "upstream", remote_url)

        # Update workspace role
        user = self.user_service.get_current_user()
        if fork_repo:
            self.db.update_workspace_role(user["id"], fork_repo["id"], "contributor")

        result["is_fork"] = True
        result["upstream"] = f"{owner}/{repo_name}"
        return result

    def sync(self, repo_name: str | None = None) -> dict:
        """
        Sync repo(s): pull, detect changes, re-index changed files with attribution.

        ``repo_name=None`` -- and only None -- syncs every repo (the CLI's
        "all if omitted"). Any string, the empty one included, names one
        repo: an empty name from a request path is an unknown repo, never a
        request to sync all of them (P-R7).

        Returns dict with sync results.
        """
        if repo_name is not None:
            repos = [self.db.get_repo(name=repo_name)]
            repos = [r for r in repos if r]
        else:
            repos = self.db.list_repos()

        if not repos:
            return {"success": False, "error": "No repos found"}

        results = {}
        token = self._get_token()

        for repo in repos:
            name = repo["name"]
            local_path = Path(repo["local_path"])

            if not local_path.exists():
                results[name] = {"success": False, "error": "Path does not exist"}
                continue

            old_head = repo.get("last_synced_commit") or GitService.get_head_commit(local_path)

            # Pull
            success, msg = GitService.pull(local_path, token=token)
            if not success:
                results[name] = {"success": False, "error": msg}
                continue

            new_head = GitService.get_head_commit(local_path)

            if old_head == new_head:
                results[name] = {"success": True, "message": "Already up to date", "changes": 0}
                continue

            # Get changed files
            changed = GitService.get_changed_files(local_path, since_commit=old_head)

            # Re-index changed files per KB
            index_mgr = IndexManager(self.db, self.config)
            total_reindexed = 0

            # Find KBs in this repo
            kb_rows = self.db.get_kbs_for_repo(repo["id"])

            for kb_row in kb_rows:
                kb_name = kb_row["name"]
                kb_config = self.config.get_kb(kb_name)
                if not kb_config:
                    continue

                # Filter changed files that belong to this KB's subpath
                subpath = kb_row["repo_subpath"] or ""
                kb_changed = [f for f in changed if f.startswith(subpath)] if subpath else changed

                if kb_changed:
                    count = index_mgr.index_with_attribution(
                        kb_name, self.git, since_commit=old_head
                    )
                    total_reindexed += count

            # Update synced state
            self.db.update_repo_synced(name, new_head)

            results[name] = {
                "success": True,
                "message": msg,
                "changes": len(changed),
                "reindexed": total_reindexed,
            }

        return {"success": True, "repos": results}

    def unsubscribe(self, repo_name: str, delete_files: bool = False) -> dict:
        """Remove a repo from the workspace."""
        repo = self.db.get_repo(name=repo_name)
        if not repo:
            return {"success": False, "error": f"Repo '{repo_name}' not found"}

        # Remove KBs associated with this repo -- but first make sure the
        # config save at the end will go through, before any row or clone is
        # deleted (#377).
        kb_rows = self.db.get_kbs_for_repo(repo["id"])
        removed = [kb_row["name"] for kb_row in kb_rows]
        check_config_save(self.config, removed=removed)
        for kb_row in kb_rows:
            self.config.remove_kb(kb_row["name"])
            self.config.forget_db_kb(kb_row["name"])
            self.db.unregister_kb(kb_row["name"])
            announce_kb_policy_change(kb_row["name"])

        # Remove workspace membership
        user = self.user_service.get_current_user()
        self.db.remove_workspace_repo(user["id"], repo["id"])

        # Remove repo from DB
        self.db.delete_repo(repo_name)

        # Optionally delete files
        if delete_files:
            import shutil

            local_path = Path(repo["local_path"])
            if local_path.exists():
                shutil.rmtree(local_path)

        save_config(self.config, removed=removed)

        return {
            "success": True,
            "repo": repo_name,
            "kbs_removed": [r["name"] for r in kb_rows],
            "files_deleted": delete_files,
        }

    def create_pr(
        self,
        repo_name: str,
        title: str,
        body: str = "",
        branch: str | None = None,
    ) -> dict:
        """Create a pull request from a fork to its upstream.

        Requires the repo to be a fork with a recorded upstream.
        """
        repo = self.db.get_repo(name=repo_name)
        if not repo:
            return {"success": False, "error": f"Repo '{repo_name}' not found"}

        if not repo.get("upstream_repo_id"):
            return {"success": False, "error": "Repo is not a fork (no upstream)"}

        upstream = self.db.get_repo(repo_id=repo["upstream_repo_id"])
        if not upstream:
            return {"success": False, "error": "Upstream repo not found in DB"}

        # Parse upstream remote URL to get owner/repo
        upstream_url = upstream.get("remote_url", "")
        parsed = GitService.parse_github_url(upstream_url)
        if not parsed:
            return {"success": False, "error": "Cannot parse upstream URL"}

        upstream_owner, upstream_repo = parsed

        # Determine head branch (fork_owner:branch)
        fork_owner = repo.get("owner", "")
        local_path = Path(repo["local_path"])
        if branch is None:
            branch = GitService.get_current_branch(local_path) if local_path.exists() else "main"

        head = f"{fork_owner}:{branch}"
        base = upstream.get("default_branch", "main")

        token = self._get_token()
        if not token:
            return {"success": False, "error": "GitHub authentication required for PR creation"}

        success, result = GitService.create_pull_request(
            upstream_owner, upstream_repo, title, body, head, base, token
        )
        if success:
            return {"success": True, **result}
        return {"success": False, "error": result.get("error", "PR creation failed")}

    def list_repos(self, user_id: int | None = None) -> list[dict]:
        """List repos, optionally filtered by user workspace."""
        if user_id is not None:
            return self.db.get_workspace_repos(user_id)
        return self.db.list_repos()

    def get_repo(self, name: str) -> dict | None:
        """The repo row named ``name``, or ``None``."""
        return self.db.get_repo(name=name)

    def repo_kb_names(self, repo_id: int) -> list[str]:
        """The KBs a repository holds, as recorded when it was subscribed."""
        return [row["name"] for row in self.db.get_kbs_for_repo(repo_id)]

    def kb_entry_count(self, kb_name: str) -> int:
        """How many indexed entries ``kb_name`` has."""
        return self.db.count_entries(kb_name)

    def get_repo_status(self, repo_name: str) -> dict:
        """Get detailed status for a repo."""
        repo = self.db.get_repo(name=repo_name)
        if not repo:
            return {"success": False, "error": f"Repo '{repo_name}' not found"}

        local_path = Path(repo["local_path"])
        status = dict(repo)

        if local_path.exists():
            status["current_branch"] = GitService.get_current_branch(local_path)
            status["head_commit"] = GitService.get_head_commit(local_path)
            status["is_git_repo"] = GitService.is_git_repo(local_path)
        else:
            status["path_exists"] = False

        # Count KBs and entries
        kb_rows = self.db.get_kbs_for_repo(repo["id"])
        status["kb_count"] = len(kb_rows)
        status["kb_names"] = [r["name"] for r in kb_rows]

        total_entries = sum(self.db.count_entries(r["name"]) for r in kb_rows)
        status["total_entries"] = total_entries

        # Contributors
        contributors = []
        for kb_row in kb_rows:
            contributors.extend(self.db.get_contributors(kb_row["name"]))
        status["contributors"] = contributors

        return status

    def discover_kbs(self, repo_path: Path) -> list[KBConfig]:
        """Discover KBs in a repository path."""
        # First try auto_discover_kbs (looks for kb.yaml)
        discovered = auto_discover_kbs([repo_path])

        if not discovered:
            # Fallback: check if the repo root itself looks like a KB
            kb_yaml = repo_path / "kb.yaml"
            if kb_yaml.exists():
                try:
                    data = load_yaml_file(kb_yaml)
                    kb = KBConfig(
                        name=data.get("name", repo_path.name),
                        path=repo_path,
                        kb_type=KBType(data.get("kb_type", "research")),
                        description=data.get("description", ""),
                    )
                    kb.load_kb_yaml()
                    discovered.append(kb)
                except Exception as e:
                    logger.warning("Could not parse %s: %s", kb_yaml, e)

        return discovered

    def _clone_and_register(
        self,
        clone_url: str,
        full_name: str,
        depth: int | None = 1,
        branch: str = "main",
    ) -> dict:
        """Internal helper: clone and register a repo + KBs."""
        parsed = GitService.parse_github_url(clone_url)
        if not parsed:
            return {"success": False, "error": "Could not parse clone URL"}

        owner, repo_name = parsed
        refusal = self._refuse_repo_name(full_name)
        if refusal:
            return refusal
        workspace_path = self.config.settings.workspace_path / owner / repo_name

        if workspace_path.exists():
            logger.warning("Clone refused: %s already exists", workspace_path)
            return {
                "success": False,
                "error_code": "PATH_EXISTS",
                "error": f"'{owner}/{repo_name}' is already present in the workspace",
            }

        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        token = self._get_token()

        success, code, msg = GitService.clone_with_code(
            clone_url, workspace_path, branch=branch, depth=depth, token=token
        )
        if not success:
            return {"success": False, "error_code": code, "error": msg}

        registered = self._register_clone(
            workspace_path,
            full_name=full_name,
            remote_url=clone_url,
            owner=owner,
            branch=branch,
            read_only=False,
            workspace_role="contributor",
        )
        if not registered["success"]:
            return registered

        return {
            "success": True,
            "repo": full_name,
            "path": str(workspace_path),
            "kbs": registered["kbs"],
            "kb_default_role": None,
        }

    # =========================================================================
    # Registering a fresh clone
    # =========================================================================
    #
    # A KB's name in a cloned repository comes from that repository's own
    # kb.yaml, which whoever controls the repository chooses. Registering a
    # clone therefore never adopts, re-points or re-links a KB or repository
    # that already exists: a collision refuses the whole operation, the clone
    # directory is removed, and nothing is left half-registered.
    #
    # KBs registered from a clone have no default_role: every user reaches
    # them at their global role (subscriptions are also read-only).

    def _refuse_repo_name(self, full_name: str) -> dict | None:
        """A refusal result when a repository row already has this name."""
        if self.db.get_repo(name=full_name) is not None:
            return {
                "success": False,
                "error_code": "REPO_NAME_CONFLICT",
                "error": f"A repository named '{full_name}' is already registered",
            }
        return None

    @staticmethod
    def _refuse_invalid_kb_names(kbs: list[KBConfig]) -> dict | None:
        """A refusal result unless every discovered KB name is a plain name."""
        for kb in kbs:
            if not is_plain_kb_name(kb.name):
                return {
                    "success": False,
                    "error_code": "INVALID_KB_NAME",
                    "error": f"The repository names a KB that is not a valid KB name: "
                    f"use {PLAIN_KB_NAME_RULE}",
                }
        return None

    @staticmethod
    def _kb_name_conflict(name: str) -> dict:
        return {
            "success": False,
            "error_code": "KB_NAME_CONFLICT",
            "error": f"The repository names a KB '{name}', which is already registered "
            "or named twice; nothing was subscribed",
        }

    def _register_clone(
        self,
        workspace_path: Path,
        *,
        full_name: str,
        remote_url: str,
        owner: str,
        branch: str,
        read_only: bool,
        workspace_role: str,
    ) -> dict:
        """Register a fresh clone's repository row and KBs, all or nothing."""
        # Sorted, so a partial registration unwinds in a predictable order.
        discovered_kbs = sorted(self.discover_kbs(workspace_path), key=lambda k: str(k.path))
        refusal = self._refuse_invalid_kb_names(discovered_kbs)
        if refusal:
            shutil.rmtree(workspace_path, ignore_errors=True)
            return refusal

        head = GitService.get_head_commit(workspace_path)
        repo_row = self.db.register_repo(
            name=full_name,
            local_path=str(workspace_path),
            remote_url=remote_url,
            owner=owner,
            visibility="public",
            default_branch=branch,
        )
        self.db.update_repo_synced(full_name, head)

        index_mgr = IndexManager(self.db, self.config)
        registered: list[str] = []
        for kb_config in discovered_kbs:
            kb_config.repo = full_name
            kb_config.repo_subpath = str(kb_config.path.relative_to(workspace_path))
            if read_only:
                kb_config.read_only = True
            # A name is claimed in both stores insert-only, which is the
            # collision check: a KB already in config, in the registry (from
            # any process), or named twice in this repository is refused and
            # left untouched, and everything this clone registered unwinds.
            try:
                self.config.add_kb(kb_config)
            except ConfigError:
                self._unwind_clone(registered, full_name, workspace_path)
                return self._kb_name_conflict(kb_config.name)
            if not self.db.insert_new_kb(
                name=kb_config.name,
                kb_type=kb_config.kb_type,
                path=str(kb_config.path),
                description=kb_config.description,
            ):
                self.config.remove_kb(kb_config.name)
                self._unwind_clone(registered, full_name, workspace_path)
                return self._kb_name_conflict(kb_config.name)
            registered.append(kb_config.name)
            if repo_row.get("id"):
                self.db.link_kb_to_repo(kb_config.name, repo_row["id"], kb_config.repo_subpath)
            index_mgr.index_with_attribution(kb_config.name, self.git)

        user = self.user_service.get_current_user()
        if repo_row.get("id"):
            self.db.add_workspace_repo(user["id"], repo_row["id"], workspace_role)

        save_config(self.config)
        return {"success": True, "kbs": registered}

    def _unwind_clone(self, registered: list[str], full_name: str, workspace_path: Path) -> None:
        """Remove what `_register_clone` registered for this clone, and the clone."""
        for name in registered:
            self.config.remove_kb(name)
            self.db.unregister_kb(name)
        self.db.delete_repo(full_name)
        shutil.rmtree(workspace_path, ignore_errors=True)
