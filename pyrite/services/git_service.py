"""
Git Service — Low-level git operations via subprocess.

Pure git plumbing wrapper with no DB or config dependencies.
A token reaches git only through an inline credential helper (never argv, a remote URL or
a config file).
"""

import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, TypedDict
from urllib.parse import urlparse, urlsplit, urlunsplit

from ..config import DEFAULT_GIT_ALLOWED_HOSTS
from ..exceptions import InvalidGitRefError, RemoteNotAllowedError, outside_text
from ..utils.git_hardening import git_env

__all__ = ["GitService", "RemoteNotAllowedError"]

logger = logging.getLogger(__name__)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_SSH_PREFIX = "git@github.com:"

# The environment variable that carries a token to the inline helper.
_TOKEN_ENV = "PYRITE_GIT_TOKEN"
# An inline credential helper: answers ``get`` from the command's environment
# and ignores ``store`` and ``erase``, so a token is never kept anywhere.
_CREDENTIAL_HELPER = (
    '!f() { test "$1" = get && echo username=oauth2 && echo "password=$' + _TOKEN_ENV + '"; }; f'
)


#: What a result says in place of git's stderr or an OS error: that text names
#: server paths and is the operator's (it is logged, ``_log_git_failure``).
_GIT_SEE_LOG = "The server log has git's message."


def _log_git_failure(what: str, where: Path | str, text: str) -> None:
    """Log the text git (or the OS) gave for a failed command, for the operator."""
    logger.warning("%s failed in %s: %s", what, where, (text or "").strip())


_ACCOUNT_HOME: str | None = None


def _account_isolation_env() -> dict[str, str]:
    """For a network caller (not ``acting.is_local_process``): the environment
    that closes every credential source of the server account to a git
    network command and its URL lookups. Global and system config are not
    read (``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` at the null device,
    ``GIT_CONFIG_NOSYSTEM``), so neither their helpers, ``core.askPass``,
    ``http.extraHeader`` nor ``url.*.insteadOf`` apply; ``HOME`` and
    ``XDG_CONFIG_HOME`` are an empty folder of Pyrite's, so no ``~/.netrc`` or
    ``~/.config/git`` is found; ``GIT_ASKPASS`` and ``SSH_ASKPASS`` are empty,
    so no prompt program runs. Empty for the local operator, whose git setup
    is their own. The repository's own config (a workspace Pyrite cloned) is
    still read, as git reads it."""
    from .acting import is_local_process

    if is_local_process():
        return {}
    global _ACCOUNT_HOME
    if _ACCOUNT_HOME is None or not os.path.isdir(_ACCOUNT_HOME):
        _ACCOUNT_HOME = tempfile.mkdtemp(prefix="pyrite-git-home-")
    return {
        "HOME": _ACCOUNT_HOME,
        "XDG_CONFIG_HOME": _ACCOUNT_HOME,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
    }


class _Credentials(NamedTuple):
    """Environment variables and config for one authenticated git command."""

    env: dict[str, str]
    config: tuple[tuple[str, str], ...]


# GitHub owner/repo names: alphanumerics plus . _ - and never a leading "-",
# which git would read as an option, nor a leading ".": the names become
# directories under the workspace root, and "." / ".." / hidden names must
# not.
_GITHUB_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")

# Every git network operation Pyrite runs for a caller (clone, pull, the
# export push) contacts only the host that was checked:
#  * git's own transports are limited to https;
#  * Git LFS transfers are off: only the checked URL is contacted, and Pyrite
#    reads and writes plain files, never LFS objects;
#  * HTTP redirects are not followed (-c http.followRedirects=false, written
#    out in each command): the host answering is the host checked.
_ALLOWED_GIT_PROTOCOLS = "https"
_NETWORK_GUARD_ENV = {
    "GIT_ALLOW_PROTOCOL": _ALLOWED_GIT_PROTOCOLS,
    "GIT_LFS_SKIP_SMUDGE": "1",
    "GIT_LFS_SKIP_PUSH": "1",
    # Credentials come from the inline helper or not at all; git never waits
    # on a terminal.
    "GIT_TERMINAL_PROMPT": "0",
}
# A path segment that is "." or "..", spelled literally or percent-encoded:
# git normalises them away, so the repository fetched would not be the one
# the URL appears to name.
_DOT_SEGMENT_RE = re.compile(r"(?:^|/)(?:\.|%2e){1,2}(?:/|$)", re.IGNORECASE)
_REDIRECT_RE = re.compile(r"The requested URL returned error: 30[0-8]\b")
_REDIRECT_MESSAGE = (
    "The remote answered with a redirect, which Pyrite does not follow; "
    "use the repository's current URL"
)

# --- Error disclosure control (CodeQL py/stack-trace-exposure #51/#52/#53) ---
#
# git's stderr is written for the operator at a terminal, not for a remote
# API caller: its very first line is "Cloning into '<absolute dest>'...",
# which hands a write-tier caller the server's filesystem layout ($HOME, the
# workspace root). `_sanitize_output` already removed tokens; these remove
# paths, and `classify_git_error` keeps the message actionable by mapping the
# three failures a caller can do something about onto stable codes.

# Lines that exist only to narrate local filesystem work.
_PATH_NARRATION_RE = re.compile(
    r"^\s*(Cloning into|Checking out files|Updating files|Resolving deltas|"
    r"Receiving objects|Counting objects|Compressing objects|remote: Enumerating)\b.*$",
    re.MULTILINE,
)
_PATH_PLACEHOLDER = "<path>"

# Absolute filesystem paths, in the three shapes git prints them.
#
# Two things the first pass got wrong, both reproduced by the cold read:
#
#  * A bare `/...` run also matches the path portion of a URL, so
#    `repository 'https://github.com/owner/repo/' not found` became
#    `repository 'http<path>' not found` and `git@github.com:owner/repo.git`
#    became `git@github.com:owner<path>`. Those URLs are the caller's *own*
#    input and disclose nothing; they are also the only actionable part of a
#    fork (#52) or pr (#53) error, which is unclassified and returned as text.
#    `(?<![\w:@.-])` refuses a match that continues a scheme, a host or an
#    scp-style `host:path`.
#
#  * A path containing a space was truncated at the space, leaking the rest
#    of the directory name (`'/Users/alice/My Docs/repos/o/r'` →
#    `'<path> Docs<path>'`). Quoted paths are therefore matched to the closing
#    quote, and unquoted ones may absorb spaces up to a `:` or end of line.
#
# Matched in order; the first alternative that fits wins.
_PATH_BODY = r"(?:[A-Za-z]:[\\/]|~/|/)"
_ABS_PATH_RE = re.compile(
    # Quoted: '/a/b c/d' or "C:\a\b c" — redact everything to the closing quote.
    rf"(?<=')(?<![\w:@.-]')(?!//){_PATH_BODY}[^'\n]*(?=')"
    rf"|(?<=\")(?!//){_PATH_BODY}[^\"\n]*(?=\")"
    # Unquoted: absorb spaces, but stop at a `:` (git's "<path>: reason"),
    # a quote, or end of line.
    rf"|(?<![\w:@.\-/]){_PATH_BODY}(?!/)[^\s'\"<>|:\n]*"
    rf"(?:[ \t]+[^\s'\"<>|:\n]+)*"
)

# Order is significant: the first match wins, and the branch and auth shapes
# are the specific ones ("Remote branch X not found in upstream origin" would
# otherwise be swallowed by a looser "not found" reading).
_GIT_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("REMOTE_REDIRECTED", _REDIRECT_RE, _REDIRECT_MESSAGE),
    (
        "BRANCH_NOT_FOUND",
        re.compile(
            r"Remote branch .* not found|Could not find remote branch|"
            r"couldn't find remote ref",
            re.IGNORECASE,
        ),
        "Branch not found in the remote repository",
    ),
    (
        "AUTH_REQUIRED",
        re.compile(
            r"Authentication failed|could not read Username|could not read Password|"
            r"Invalid username or (token|password)|Permission denied \(publickey\)|"
            r"terminal prompts disabled|HTTP Basic: Access denied",
            re.IGNORECASE,
        ),
        "Authentication required — connect or refresh the GitHub credentials",
    ),
    (
        "REPO_NOT_FOUND",
        # `does not appear to be a git repository` is deliberately NOT here:
        # git says that when the *local* remote is missing (`fatal: 'origin'
        # does not appear to be a git repository`, i.e. a push from a KB with
        # no remote configured), so mapping it to REPO_NOT_FOUND told the user
        # "Repository not found, or the configured credentials cannot see it"
        # — false on both halves.
        re.compile(
            r"repository .* not found|Repository not found|remote: Not Found",
            re.IGNORECASE,
        ),
        "Repository not found, or the configured credentials cannot see it",
    ),
)
_GIT_ERROR_FALLBACK = ("CLONE_FAILED", "Git operation failed — see the server log for details")


def _git_env(
    extra: dict[str, str] | None = None,
    *,
    hooks: bool = False,
    config: Sequence[tuple[str, str]] = (),
) -> dict[str, str]:
    """Environment for a git subprocess: isolated from any parent git
    process's repo-scoped state, and hardened against the repository's own
    config and hooks (see ``pyrite.utils.git_hardening``). Pass `extra` to
    layer in e.g. credential vars."""
    return git_env(extra, hooks=hooks, config=config)


# Verdicts of the version-servability check (#453). REFUSED is a positive
# fact (an unsafe stored path, or a readable file holding another entry) and
# the only verdict attribution deletes on. UNKNOWN is every case where git
# did not hand back readable text: nothing was learned, so nothing is acted on.
SERVABLE = "servable"
REFUSED = "refused"
UNKNOWN = "unknown"

# The one command that reads a file at a commit. `cat-file blob` exits 0 only
# when the commit, its trees and the blob were all read and the path names a
# blob (`git show` exits 0 on a directory and prints a tree listing). Its
# exit code is the fact; stderr is never classified, because git words
# "path absent from a readable commit" and "commit unreadable" alike (#453).
READ_BLOB_ARGV = ("git", "cat-file", "blob", "--end-of-options")


class GitWorkingTreeStatus(TypedDict):
    """Git working tree status shape returned by GitService.get_status (#1047)."""

    clean: bool
    staged: list[str]
    unstaged: list[str]
    untracked: list[str]


class GitService:
    """Low-level git operations via subprocess."""

    # The servability verdicts, reachable through an injected GitService
    # (storage receives the class and does not import this module).
    SERVABLE = SERVABLE
    REFUSED = REFUSED
    UNKNOWN = UNKNOWN

    @staticmethod
    def get_kb_prefix(kb_path: Path) -> str | None:
        """The KB's path relative to its git repo ROOT, e.g. "kbdir/" for a
        KB that is a subdirectory of its repo, or "" when the KB path IS
        the repo root. None when git cannot say.

        `git log`/`git show` report paths relative to the repo root, not to
        `cwd` -- joining such a path directly onto `kb_path` double-prefixes
        a KB in a subdirectory (repo/kbdir/ + "kbdir/a.md" ->
        repo/kbdir/kbdir/a.md). Every caller that turns a repo-relative
        path into a KB-relative one must go through this (#432 cold read).

        Fails closed: "" means "every path in the repo is this KB's", so a
        failure is None, never "", and `_kb_relative_path` refuses every
        path against None. Only git's line terminator is removed -- a
        directory name may begin or end with whitespace, and " kb/" is not
        "kb/".
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-prefix"],
                cwd=str(kb_path),
                capture_output=True,
                text=True,
                timeout=10,
                env=_git_env(),
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get KB prefix for %s", kb_path, exc_info=True)
            return None
        if result.returncode != 0:
            logger.warning("Failed to get KB prefix for %s: %s", kb_path, result.stderr.strip())
            return None
        return result.stdout.rstrip("\n")

    @staticmethod
    def _kb_relative_path(repo_relative_path: str, kb_prefix: str | None) -> str | None:
        """Convert a repo-relative path to KB-relative, or None if the path
        falls outside the KB's own subtree (e.g. another KB in a sibling
        directory, or `--follow` reporting a path from before a move into
        this KB). Never resolves a path outside the prefix -- a caller must
        treat None as "not this KB's file", not fall back to any other
        path (#432 cold read: refuse, don't guess). An unknown prefix
        (None) refuses every path."""
        if kb_prefix is None:
            return None
        if not kb_prefix:
            return repo_relative_path
        if not repo_relative_path.startswith(kb_prefix):
            return None
        rest = repo_relative_path[len(kb_prefix) :]
        return rest or None

    @staticmethod
    def is_safe_kb_relative_path(path: str) -> bool:
        """True for a non-empty relative path with no ".." segment -- the
        only shape a stored KB-relative path may have. An absolute path or
        a ".." segment is refused rather than resolved: git anchors
        "<rev>:./<path>" at the KB, so either would name a file other than
        the one recorded (#432)."""
        if not path or path.startswith("/"):
            return False
        return ".." not in path.split("/")

    @staticmethod
    def show(repo_path: Path, path: str, rev: str = "HEAD") -> str | None:
        """The text of `path` at `rev` in `repo_path`'s tree, or None when the
        path is unsafe or git did not hand back a UTF-8 blob (absent, an
        unreadable object, a timeout, an OSError, a decode error). The one
        version read: never raises for a file it cannot serve.

        `<rev>:<path>` is resolved against the repo root unless anchored, so
        the path is read as `./<path>` from `repo_path`: a KB that is a
        subdirectory of its repo is found (#415), and a caller that already
        ran `git show HEAD:<path>` gets the same text with one home for the
        subprocess (#1049)."""
        if not GitService.is_safe_kb_relative_path(path):
            return None
        try:
            result = subprocess.run(
                [*READ_BLOB_ARGV, f"{rev}:./{path}"],
                cwd=str(repo_path),
                capture_output=True,
                timeout=10,
                env=_git_env(),
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("git cat-file failed for %s", repo_path, exc_info=True)
            return None
        if result.returncode != 0:
            return None
        try:
            text = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("Version of %s at %s is not UTF-8", path, rev)
            return None
        # Universal newlines, as the `text=True` read before #453 returned:
        # "\r\n" and a lone "\r" become "\n", so a CRLF file reads as it did.
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def read_file_at(local_path: Path, commit: str, kb_relative_path: str) -> str | None:
        """The KB-relative spelling of `show`, kept for its callers."""
        return GitService.show(local_path, kb_relative_path, rev=commit)

    @staticmethod
    def version_servability(
        local_path: Path,
        commit: str,
        versioned_path: str | None,
        current_rel_path: str | None,
        entry_id: str,
    ) -> tuple[str, str | None]:
        """`(verdict, text)`: can a recorded version row be served for this
        entry? The one rule for it, used by `VersionService.get_entry_at_version`
        (which serves only SERVABLE) and by attribution (which deletes a row
        only on REFUSED, #453).

        Read at the path that version recorded (`versioned_path`, else the
        entry's current one). REFUSED only on a positive fact: the path is
        unsafe, or the file was read and holds another entry. No path to
        read, or no text from `read_file_at` (absent from the commit, the
        commit or blob unreadable, a shallow boundary, a timeout, a decode
        error): UNKNOWN. A path absent from a readable commit is UNKNOWN too:
        the read refuses it either way, and keeping a row is the safe side.
        """
        from ..models.core_types import entry_id_from_markdown, explicit_entry_id

        rel_path: str | None
        if versioned_path:
            # Stored KB-relative from #432 onward. Tolerate an absolute path
            # under the KB (a dev database may hold rows from before paths
            # were stored relative); any other absolute path is refused,
            # never resolved.
            if Path(versioned_path).is_absolute():
                try:
                    rel_path = str(Path(versioned_path).relative_to(local_path))
                except ValueError:
                    rel_path = versioned_path
            else:
                rel_path = versioned_path
        else:
            rel_path = current_rel_path
        if rel_path is None:
            return UNKNOWN, None  # the entry's indexed path is not under the KB
        if not GitService.is_safe_kb_relative_path(rel_path):
            return REFUSED, None

        text = GitService.read_file_at(local_path, commit, rel_path)
        if text is None:
            return UNKNOWN, None
        if rel_path != current_rel_path:
            # A path other than the current one (a rename): git pairs renames
            # by similarity and entries share boilerplate, so only the id says
            # the file is this entry (#432).
            return (SERVABLE, text) if entry_id_from_markdown(text) == entry_id else (REFUSED, None)
        # The same path can hold a different entry at an older commit. A file
        # stating another id is not this entry's version; one with no explicit
        # id is served (its id comes from the title, which an edit may change).
        stated_id = explicit_entry_id(text)
        if stated_id is not None and stated_id != entry_id:
            return REFUSED, None
        return SERVABLE, text

    @staticmethod
    def _parse_nul_separated_name_status(raw: bytes) -> list[dict]:
        """Parse `git log --name-status -z` / `git show --name-status -z`
        output into per-commit blocks.

        NUL-separated so a quoted path (core.quotePath: unicode, a tab, a
        backslash) comes back as the exact raw bytes git wrote, never
        through the human-quoted plain-output form (#432 cold read; one
        parser, shared by every caller that reads --name-status -z).

        Byte layout per commit (verified against real git output, not
        assumed): "<format-line>\\0\\n<status>\\0<path>\\0[<path2>\\0]",
        repeated. The "\\n" is a PREFIX of the status token, not a suffix
        of the header -- splitting on "\\0" alone and looking for "\\n"
        inside the *header* token finds nothing; it must be looked for at
        the START of the token that follows a header. Positional, not
        content-sniffed: a path or commit message could itself contain
        anything, so the only reliable signal is "this token follows
        either the very start of the stream, or the expected number of
        path tokens for the previous status field."

        Returns a list of dicts: {"header": <the format-string line, str>,
        "statuses": [(status, [paths...]), ...]}.
        """
        text = raw.decode("utf-8", errors="surrogateescape")
        tokens = text.split("\0")
        blocks: list[dict] = []
        i = 0
        n = len(tokens)
        while i < n:
            header = tokens[i]
            i += 1
            if header == "" and i >= n:
                break
            statuses: list[tuple[str, list[str]]] = []
            # Each status token begins with the "\n" left over from the
            # header/status boundary; strip exactly that one leading
            # newline (never anything else -- a status code is never
            # itself empty) to recover the real status field.
            while i < n and tokens[i].startswith("\n"):
                status_field = tokens[i][1:]
                i += 1
                if status_field[:1] in ("R", "C"):
                    old_path = tokens[i] if i < n else ""
                    i += 1
                    new_path = tokens[i] if i < n else ""
                    i += 1
                    statuses.append((status_field, [old_path, new_path]))
                else:
                    path = tokens[i] if i < n else ""
                    i += 1
                    statuses.append((status_field, [path]))
            blocks.append({"header": header, "statuses": statuses})
        return blocks

    @staticmethod
    def subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
        """Public entry point for other modules that shell out to git
        directly (e.g. WorktreeService) to get the same leak-isolated
        environment GitService's own subprocess calls use."""
        return _git_env(extra)

    @staticmethod
    def check_remote_allowed(
        url: object,
        allowed_hosts: Sequence[str] | None = None,
        *,
        allow_userinfo: bool = False,
    ) -> None:
        """Raise RemoteNotAllowedError unless `url` is an https:// URL on a host
        in `allowed_hosts` (the operator's `git_allowed_hosts` setting; None
        means the default, github.com).

        An entry is a hostname, matched case-insensitively against a URL with
        no explicit port, or `host:port` for a URL that names a port. The URL
        must name its host directly: userinfo, a query, a fragment and "." or
        ".." path segments are refused. `allow_userinfo` exists only for
        checking a URL Pyrite built or configured itself (a clone's origin
        carries the token Pyrite injected).
        """
        allowed = GitService._allowed_set(allowed_hosts)
        if GitService._remote_host_key(url, allow_userinfo) not in allowed:
            raise GitService._refusal(allowed)

    @staticmethod
    def _allowed_set(allowed_hosts: Sequence[str] | None) -> set[str]:
        hosts = list(DEFAULT_GIT_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts)
        return {h.strip().lower() for h in hosts if isinstance(h, str) and h.strip()}

    @staticmethod
    def _refusal(allowed: set[str], *, rewritten: bool = False) -> RemoteNotAllowedError:
        """The refusal: names the setting and what to change, never the URL,
        and never the allowed hosts (they can name internal servers; they go
        to the log for the operator)."""
        logger.info("Git remote refused; git_allowed_hosts is %s", sorted(allowed))
        if rewritten:
            return RemoteNotAllowedError(
                "Git remote refused: the git configuration of the account Pyrite "
                "runs as rewrites this URL (a url.<base>.insteadOf rule) to one "
                "that is not https:// on a host in the git_allowed_hosts setting. "
                "An operator removes or changes that rule, or adds the rewritten "
                "host to git_allowed_hosts."
            )
        if not allowed:
            return RemoteNotAllowedError(
                "Git remote refused: the git_allowed_hosts setting is empty, so no "
                "host may be cloned from or pushed to. To allow one, an operator "
                "adds it to git_allowed_hosts in config.yaml or "
                "PYRITE_GIT_ALLOWED_HOSTS."
            )
        return RemoteNotAllowedError(
            "Git remote refused: only https:// URLs on a host listed in the "
            "git_allowed_hosts setting may be cloned from or pushed to. To use "
            "another host, an operator adds it to git_allowed_hosts in "
            "config.yaml or PYRITE_GIT_ALLOWED_HOSTS."
        )

    @staticmethod
    def _remote_host_key(url: object, allow_userinfo: bool) -> str | None:
        """`host` or `host:port` of an acceptable https URL, else None."""
        if not isinstance(url, str) or not url:
            return None
        if any(c.isspace() or ord(c) < 32 or c in "\\?#" for c in url):
            return None
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return None
        if parts.scheme != "https" or not parts.hostname:
            return None
        if not allow_userinfo and (parts.username is not None or parts.password is not None):
            return None
        if _DOT_SEGMENT_RE.search(parts.path):
            return None
        host = parts.hostname.lower()
        return host if port is None else f"{host}:{port}"

    @staticmethod
    def _effective_url(url: str, cwd: Path | None = None) -> str | None:
        """The URL git will actually contact for `url`, after the account's
        url.<base>.insteadOf rules. Local: git does not contact the remote."""
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--get-url", "--", url],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                # The same config the network command will read.
                env=_git_env(_account_isolation_env()),
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Could not expand a git URL", exc_info=True)
            return None
        effective = result.stdout.strip()
        return effective if result.returncode == 0 and effective else None

    @staticmethod
    def _check_remote_urls(
        local_path: Path, remote: str, allowed_hosts: Sequence[str] | None, *, push: bool
    ) -> None:
        """Raise RemoteNotAllowedError unless every URL git would contact for
        `remote` (every push URL, or every fetch URL) is allowed.

        `git remote get-url` reports URLs after insteadOf rewriting, so the
        check is on what git will contact; the configured URLs are read too,
        only to say whether a rewrite is what made the remote unacceptable.
        """
        allowed = GitService._allowed_set(allowed_hosts)
        get_url = ["git", "remote", "get-url", "--all"]
        if push:
            get_url.append("--push")
        configured = [f"remote.{remote}.url"] + ([f"remote.{remote}.pushurl"] if push else [])
        try:
            effective = subprocess.run(
                [*get_url, "--", remote],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(_account_isolation_env()),
                timeout=10,
            )
            raw = [
                subprocess.run(
                    ["git", "config", "--get-all", "--", key],
                    cwd=str(local_path),
                    capture_output=True,
                    text=True,
                    env=_git_env(),
                    timeout=10,
                ).stdout.split()
                for key in configured
            ]
        except (subprocess.SubprocessError, OSError):
            logger.warning("Could not read the URLs of remote %s", remote, exc_info=True)
            raise GitService._refusal(allowed) from None
        urls = effective.stdout.split() if effective.returncode == 0 else []
        ok = bool(urls) and all(GitService._remote_host_key(u, True) in allowed for u in urls)
        if ok:
            return
        # push URLs replace the fetch URL when set; either way the configured
        # set is what git rewrote into `urls`.
        raw_urls = (raw[1] or raw[0]) if push else raw[0]
        rewritten = bool(raw_urls) and all(
            GitService._remote_host_key(u, True) in allowed for u in raw_urls
        )
        raise GitService._refusal(allowed, rewritten=rewritten)

    @staticmethod
    def clone(
        remote_url: str,
        local_path: Path,
        branch: str = "main",
        depth: int | None = 1,
        token: str | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """
        Clone a repository.

        Args:
            remote_url: https:// URL on an allowed host
            local_path: Where to clone to
            branch: Branch to clone
            depth: Shallow clone depth (None for full clone)
            token: GitHub OAuth token for private repos
            allowed_hosts: the operator's `git_allowed_hosts` (None: github.com)

        Returns:
            (success, message) — the message is caller-safe (no tokens, no
            filesystem paths). Callers that need to branch on *why* it failed
            should use `clone_with_code`.
        """
        success, _code, message = GitService.clone_with_code(
            remote_url,
            local_path,
            branch=branch,
            depth=depth,
            token=token,
            allowed_hosts=allowed_hosts,
        )
        return success, message

    @staticmethod
    def clone_with_code(
        remote_url: str,
        local_path: Path,
        branch: str = "main",
        depth: int | None = 1,
        token: str | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[bool, str, str]:
        """Clone a repository, returning `(success, code, message)`.

        The remote must be an https:// URL on a host in `allowed_hosts` (the
        operator's `git_allowed_hosts`; None means github.com), checked before
        git runs, and git itself runs with only the https transport enabled
        and LFS transfers and redirects off.

        `code` is a stable identifier a caller can branch on (REPO_NOT_FOUND /
        AUTH_REQUIRED / BRANCH_NOT_FOUND / CLONE_FAILED / CLONE_TIMEOUT /
        REMOTE_NOT_ALLOWED), and
        `message` never carries a filesystem path or a token. The full,
        unredacted stderr goes to the log at WARNING so the operator loses
        nothing (CodeQL py/stack-trace-exposure #51).
        """
        # A value beginning with "-" would be parsed by git as an option
        # (--upload-pack=<cmd> is code execution). "--" below covers the
        # positionals; --branch's value is an option argument, so refuse it.
        if remote_url.startswith("-") or branch.startswith("-"):
            return False, "INVALID_REQUEST", "Invalid repository URL or branch"
        try:
            GitService.check_remote_allowed(remote_url, allowed_hosts)
            url = remote_url
            # The URL git will really use -- after the account's insteadOf
            # rules -- must pass the same check as the one named.
            effective = GitService._effective_url(url)
            if effective != url:
                try:
                    GitService.check_remote_allowed(effective, allowed_hosts, allow_userinfo=True)
                except RemoteNotAllowedError:
                    raise GitService._refusal(
                        GitService._allowed_set(allowed_hosts), rewritten=effective is not None
                    ) from None
        except RemoteNotAllowedError as e:
            return False, RemoteNotAllowedError.error_code, str(e)

        cmd = ["git", "-c", "http.followRedirects=false", "clone", "--branch", branch]
        if depth is not None:
            cmd.extend(["--depth", str(depth)])
        cmd.extend(["--", url, str(local_path)])

        try:
            creds = GitService._credentials(token, [effective or url])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                env=_git_env({**_NETWORK_GUARD_ENV, **creds.env}, config=creds.config),
            )
            if result.returncode == 0:
                return True, "OK", f"Cloned to {local_path}"
            logger.warning(
                "git clone of %s into %s failed (rc=%s): %s",
                remote_url,
                local_path,
                result.returncode,
                GitService._sanitize_output(result.stderr, token).strip(),
            )
            code, message = GitService.classify_git_error(result.stderr, token)
            return False, code, message
        except subprocess.TimeoutExpired:
            logger.warning("git clone of %s into %s timed out", remote_url, local_path)
            return False, "CLONE_TIMEOUT", "Clone timed out"
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git clone of %s into %s raised", remote_url, local_path, exc_info=True)
            return (
                False,
                "CLONE_FAILED",
                f"Clone failed: {GitService.sanitize_error(outside_text(e), token)}",
            )

    @staticmethod
    def pull(
        local_path: Path,
        token: str | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """Pull latest changes. Returns (success, message).

        Every fetch URL of the remote the pull uses must be https on a host in
        `allowed_hosts` (the operator's `git_allowed_hosts`; None means
        github.com), checked before git fetches, with the same transport, LFS
        and redirect restrictions as a clone. A remote that is not allowed --
        an ssh remote from before the setting existed, or a host since removed
        from it -- is refused with a message naming the setting.
        """
        try:
            remote = GitService._pull_remote(local_path)
            GitService.scrub_remote_credentials(local_path)
            GitService._check_remote_urls(local_path, remote, allowed_hosts, push=False)
        except RemoteNotAllowedError as e:
            return False, f"Pull refused: {e}"

        try:
            creds = GitService._remote_credentials(local_path, remote, token, push=False)
            result = subprocess.run(
                ["git", "-c", "http.followRedirects=false", "pull"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env({**_NETWORK_GUARD_ENV, **creds.env}, config=creds.config),
                timeout=60,
            )
            if result.returncode == 0:
                return True, result.stdout.strip() or "Already up to date"
            logger.warning(
                "git pull in %s failed (rc=%s): %s",
                local_path,
                result.returncode,
                GitService._sanitize_output(result.stderr, token).strip(),
            )
            # Git's own words, token- and path-redacted — NOT classified.
            # `classify_git_error` knows four clone-shaped patterns; routing
            # pull through it answered a merge conflict ("Your local changes
            # ... would be overwritten by merge") with a canned "see the
            # server log", which the CLI user — who *is* the operator, with
            # no server log — cannot act on, and which the web UI renders
            # verbatim (web/src/routes/changes/+page.svelte).
            if _REDIRECT_RE.search(result.stderr):
                return False, f"Pull failed: {_REDIRECT_MESSAGE}"
            message = GitService.sanitize_error(result.stderr, token)
            return False, f"Pull failed: {message}" if message else "Pull failed"
        except subprocess.TimeoutExpired:
            return False, "Pull timed out"
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git pull in %s raised", local_path, exc_info=True)
            return False, f"Pull failed: {GitService.sanitize_error(outside_text(e), token)}"

    @staticmethod
    def _pull_remote(local_path: Path) -> str:
        """The remote a bare `git pull` fetches from: the checked-out branch's
        configured remote, else origin."""
        branch = GitService._checked_out_branch(local_path)
        if not branch:
            return "origin"
        try:
            configured = subprocess.run(
                ["git", "config", "--get", "--", f"branch.{branch}.remote"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
                timeout=10,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            configured = ""
        return configured or "origin"

    @staticmethod
    def get_remote_url(local_path: Path) -> str | None:
        """Get the 'origin' remote URL."""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get remote URL for %s", local_path, exc_info=True)
        return None

    @staticmethod
    def get_current_branch(local_path: Path) -> str:
        """Get the current branch name."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get current branch for %s", local_path, exc_info=True)
        return "main"

    @staticmethod
    def _checked_out_branch(local_path: Path) -> str | None:
        """The checked-out branch's name, or None on a detached HEAD.

        Unlike `get_current_branch`, which answers "HEAD" (detached) or
        "main" (on error), this never invents a name to push.
        """
        try:
            result = subprocess.run(
                ["git", "symbolic-ref", "--short", "-q", "HEAD"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to read the current branch of %s", local_path, exc_info=True)
            return None
        name = result.stdout.strip()
        return name if result.returncode == 0 and name else None

    @staticmethod
    def get_head_commit(local_path: Path) -> str:
        """Get HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get HEAD commit for %s", local_path, exc_info=True)
        return ""

    @staticmethod
    def get_file_log(
        local_path: Path,
        file_path: str,
        since_commit: str | None = None,
    ) -> list[dict]:
        """
        Get git log for a specific file, following renames.

        `local_path` is the KB directory (git's cwd for this call);
        `file_path` is KB-relative. Returns list of dicts with: hash,
        author_name, author_email, date, message, status, file_path --
        `status` is git's name-status code for the file in that commit
        ("A" where it was added, which starts that path's current life);
        `file_path` is the KB-relative path this file had *at that commit* (its tree),
        which for commits before a rename is the old name, not the name
        passed in (#432: reading a pre-rename commit at the current path
        404s, because that path did not exist yet).

        Uses `-z` throughout: `--name-status` alone prints a quoted,
        escaped form for a non-ASCII or whitespace-containing name
        (core.quotePath), which a naive split would return verbatim
        instead of the real filename (#432 cold read).

        Rename detection uses git's default similarity threshold. A lower
        one pairs a deleted entry with an unrelated added one, because
        entries share frontmatter boilerplate. The limit that follows: a
        rename bundled with an edit that drops similarity below the
        default starts the file's history at the rename. Even at the
        default, similarity does not prove two files are one entry --
        callers that record versions also require the entry id to match
        across a rename (#432).

        git reports every path relative to the repo ROOT, not to
        `local_path` -- a KB that is a subdirectory of its repo would
        otherwise get its prefix doubled by a caller that joins the
        reported path onto `local_path` directly (#432 cold read). Any
        commit whose reported path falls outside the KB's own subtree
        (e.g. `--follow` chasing a rename from a sibling KB, or from
        before a move into this KB) is dropped, not guessed at.
        """
        kb_prefix = GitService.get_kb_prefix(local_path)
        cmd = [
            "git",
            "log",
            "--follow",
            "--format=%H|%an|%ae|%aI|%s",
            "--name-status",
            "-z",
            "--",
            file_path,
        ]
        if since_commit:
            cmd.insert(2, f"{since_commit}..HEAD")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(local_path),
                capture_output=True,
                timeout=30,
                env=_git_env(),
            )
            if result.returncode != 0:
                return []

            entries = []
            for block in GitService._parse_nul_separated_name_status(result.stdout):
                header = block["header"]
                parts = header.split("|", 4)
                if len(parts) < 5:
                    continue
                # The status line(s) for this commit: for a plain change,
                # one ("M", [path]); for a rename/copy, ("R100", [old,
                # new]) -- the path that existed in *this* commit's tree is
                # always the last path (the new name on a rename, the only
                # name otherwise).
                repo_relative_path = None
                status = ""
                if block["statuses"]:
                    status, paths = block["statuses"][0]
                    if paths:
                        repo_relative_path = paths[-1]
                if repo_relative_path is None:
                    continue
                kb_relative_path = GitService._kb_relative_path(repo_relative_path, kb_prefix)
                if kb_relative_path is None:
                    # Outside this KB's subtree -- never resolve to it.
                    continue
                entries.append(
                    {
                        "hash": parts[0],
                        "author_name": parts[1],
                        "author_email": parts[2],
                        "date": parts[3],
                        "message": parts[4],
                        "file_path": kb_relative_path,
                        "status": status,
                    }
                )
            return entries
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to parse git log for %s", file_path, exc_info=True)
            return []

    @staticmethod
    def get_commit_info(local_path: Path, commit_hash: str) -> dict | None:
        """Get author/date/message for a single commit.

        Returns None if the commit cannot be read. Used alongside
        `get_commit_file_changes` to record entry_version rows for a commit the
        server just made (#432), without a full `get_file_log` walk.
        """
        try:
            result = subprocess.run(
                [
                    "git",
                    "show",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-patch",
                    "--format=%H|%an|%ae|%aI|%s",
                    "--end-of-options",
                    commit_hash,
                ],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env(),
            )
            if result.returncode != 0:
                return None
            line = result.stdout.strip()
            parts = line.split("|", 4)
            if len(parts) < 5:
                return None
            return {
                "hash": parts[0],
                "author_name": parts[1],
                "author_email": parts[2],
                "date": parts[3],
                "message": parts[4],
            }
        except (subprocess.SubprocessError, OSError):
            logger.warning(
                "Failed to get commit info for %s at %s", local_path, commit_hash, exc_info=True
            )
            return None

    @staticmethod
    def get_commit_file_changes(
        local_path: Path, commit_hash: str
    ) -> list[tuple[str, str, str | None]]:
        """(status, KB-relative path, KB-relative source path) for each file
        a single commit changed. The source path is set only for a rename
        whose source lies inside this KB; it is None otherwise, including a
        rename from outside the KB (confinement: a path outside the KB is
        never resolved). Rename detection is git's default threshold (see
        `get_file_log`).
        """
        # None (git could not say) refuses every path in _kb_relative_path.
        kb_prefix = GitService.get_kb_prefix(local_path)
        try:
            result = subprocess.run(
                [
                    "git",
                    "show",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-status",
                    "--format=",
                    "-z",
                    "--end-of-options",
                    commit_hash,
                ],
                cwd=str(local_path),
                capture_output=True,
                timeout=30,
                env=_git_env(),
            )
            if result.returncode != 0:
                return []
            out: list[tuple[str, str, str | None]] = []
            for status, paths in GitService._parse_nul_separated_name_only_statuses(result.stdout):
                if not paths:
                    continue
                kb_relative_path = GitService._kb_relative_path(paths[-1], kb_prefix)
                if kb_relative_path is None:
                    continue
                source = None
                if len(paths) == 2:
                    source = GitService._kb_relative_path(paths[0], kb_prefix)
                out.append((status, kb_relative_path, source))
            return out
        except (subprocess.SubprocessError, OSError):
            logger.warning(
                "Failed to get commit files for %s at %s", local_path, commit_hash, exc_info=True
            )
            return []

    @staticmethod
    def _parse_nul_separated_name_only_statuses(raw: bytes) -> list[tuple[str, list[str]]]:
        """Parse `git show --name-status -z --format=` output (no header
        line -- just NUL-terminated status/path tokens) into (status,
        [paths]) pairs, sharing the rename/copy two-path handling with
        `_parse_nul_separated_name_status`."""
        text = raw.decode("utf-8", errors="surrogateescape")
        tokens = [t for t in text.split("\0") if t != ""]
        out: list[tuple[str, list[str]]] = []
        i = 0
        n = len(tokens)
        while i < n:
            status_field = tokens[i]
            i += 1
            if status_field and status_field[0] in ("R", "C"):
                old_path = tokens[i] if i < n else ""
                i += 1
                new_path = tokens[i] if i < n else ""
                i += 1
                out.append((status_field, [old_path, new_path]))
            else:
                path = tokens[i] if i < n else ""
                i += 1
                out.append((status_field, [path]))
        return out

    @staticmethod
    def get_changed_files(
        local_path: Path,
        since_commit: str | None = None,
    ) -> list[str]:
        """
        Get list of changed .md files since a commit.

        If since_commit is None, lists all tracked .md files.
        """
        if since_commit:
            cmd = [
                "git",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                f"{since_commit}..HEAD",
                "--",
                "*.md",
            ]
        else:
            cmd = ["git", "ls-files", "*.md"]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(local_path),
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env(),
            )
            if result.returncode == 0:
                return [f for f in result.stdout.strip().split("\n") if f]
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get changed files for %s", local_path, exc_info=True)
        return []

    @staticmethod
    def is_git_repo(path: Path) -> bool:
        """Check if a path is inside a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            logger.debug("Not a git repo: %s", path)
            return False

    @staticmethod
    def add_remote(
        local_path: Path,
        name: str,
        url: str,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """Add a git remote.

        The URL must be https on a host in `allowed_hosts` (the operator's
        `git_allowed_hosts`; None means github.com), so a later fetch from
        this remote can pass the same check; neither value is ever read as an
        option.
        """
        if not isinstance(name, str) or not name or name.startswith("-"):
            return False, "Invalid remote name"
        try:
            GitService.check_remote_allowed(url, allowed_hosts)
        except RemoteNotAllowedError as e:
            return False, str(e)
        try:
            result = subprocess.run(
                ["git", "remote", "add", "--", name, url],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, f"Added remote '{name}'"
            return False, result.stderr.strip()
        except (subprocess.SubprocessError, OSError) as e:
            return False, outside_text(e)

    @staticmethod
    def fork_repo(owner: str, repo: str, token: str) -> tuple[bool, dict]:
        """
        Fork a repo on GitHub via the API.

        Returns (success, response_dict).
        """
        try:
            import httpx
        except ImportError:
            return False, {"error": "httpx required for GitHub API operations"}

        try:
            with httpx.Client() as client:
                response = client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/forks",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=30,
                )
                if response.status_code in (200, 202):
                    return True, response.json()
                return False, {
                    "error": f"GitHub API error: {response.status_code}",
                    "message": response.text,
                }
        except (subprocess.SubprocessError, OSError) as e:
            return False, {"error": outside_text(e)}

    @staticmethod
    def create_pull_request(
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        token: str,
    ) -> tuple[bool, dict]:
        """
        Create a pull request on GitHub via the API.

        Args:
            owner: Repo owner (upstream)
            repo: Repo name (upstream)
            title: PR title
            body: PR body/description
            head: Head branch (e.g. "user:branch" for cross-fork PRs)
            base: Base branch to merge into
            token: GitHub OAuth token

        Returns:
            (success, response_dict)
        """
        try:
            import httpx
        except ImportError:
            return False, {"error": "httpx required for GitHub API operations"}

        try:
            with httpx.Client() as client:
                response = client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls",
                    json={
                        "title": title,
                        "body": body,
                        "head": head,
                        "base": base,
                    },
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                    timeout=30,
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    return True, {
                        "pr_url": data.get("html_url"),
                        "pr_number": data.get("number"),
                    }
                return False, {
                    "error": f"GitHub API error: {response.status_code}",
                    "message": response.text,
                }
        except (subprocess.SubprocessError, OSError) as e:
            return False, {"error": outside_text(e)}

    @staticmethod
    def parse_github_url(url: str) -> tuple[str, str] | None:
        """
        Parse a GitHub URL into (owner, repo).

        Handles:
          https://github.com/owner/repo
          https://github.com/owner/repo.git
          git@github.com:owner/repo.git
        """
        path = GitService._github_repo_path(url)
        if path is None:
            return None
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = path.split("/")
        # Exactly owner/repo: any further segment ("..", or a browser page
        # such as /tree/main) would make the repository git fetches differ
        # from the owner/repo used for the workspace path and the name.
        if len(parts) != 2:
            return None
        owner, repo = parts[0], parts[1]
        if not _GITHUB_NAME_RE.fullmatch(owner) or not _GITHUB_NAME_RE.fullmatch(repo):
            return None
        if repo in (".", ".."):
            return None
        return owner, repo

    @staticmethod
    def _github_repo_path(url: str) -> str | None:
        """Return the path portion of `url` iff its host *is* github.com.

        The host is compared for equality after real URL parsing. Searching
        the string for "github.com" is not a host check: it also matches
        https://evil.example/github.com/x, github.com.evil.example, query
        strings and fragments -- and the token injector trusted that match enough
        to hand over the caller's OAuth token.
        """
        if url.startswith(_GITHUB_SSH_PREFIX):
            return url[len(_GITHUB_SSH_PREFIX) :]
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return None
        # Userinfo is refused outright: "https://github.com@evil.example/" has
        # hostname evil.example, and a legitimate caller never supplies it.
        if parsed.username is not None or parsed.password is not None:
            return None
        if (parsed.hostname or "").lower() not in _GITHUB_HOSTS:
            return None
        return parsed.path

    @staticmethod
    def commit(
        local_path: Path,
        message: str,
        paths: list[str] | None = None,
        sign_off: bool = False,
        run_hooks: bool = False,
    ) -> tuple[bool, dict]:
        """
        Stage and commit changes in a git repository.

        Args:
            local_path: Path to the git repository
            message: Commit message
            paths: Specific file paths to stage (stages all if None)
            sign_off: Add Signed-off-by line
            run_hooks: run the repository's own commit hooks. Off unless the
                caller is the operator's own command: a hook is a program the
                repository supplies.

        Returns:
            (success, result_dict) where result_dict contains
            commit_hash, files_changed, message on success
            or error on failure.
        """
        if not GitService.is_git_repo(local_path):
            return False, {"error": "Not a git repository"}

        try:
            # Stage files
            if paths:
                for p in paths:
                    result = subprocess.run(
                        ["git", "add", "--", p],
                        cwd=str(local_path),
                        capture_output=True,
                        text=True,
                        env=_git_env(hooks=run_hooks),
                    )
                    if result.returncode != 0:
                        _log_git_failure("git add", local_path, result.stderr)
                        return False, {"error": f"Failed to stage {p}. {_GIT_SEE_LOG}"}
            else:
                result = subprocess.run(
                    ["git", "add", "-A"],
                    cwd=str(local_path),
                    capture_output=True,
                    text=True,
                    env=_git_env(hooks=run_hooks),
                )
                if result.returncode != 0:
                    _log_git_failure("git add", local_path, result.stderr)
                    return False, {"error": f"Failed to stage the changes. {_GIT_SEE_LOG}"}

            # Check if there's anything to commit
            status_result = subprocess.run(
                ["git", "diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(hooks=run_hooks),
            )
            staged_files = [f for f in status_result.stdout.strip().split("\n") if f]
            if not staged_files:
                return False, {"error": "No changes to commit"}

            # Build commit command
            cmd = ["git", "commit", "-m", message]
            if sign_off:
                cmd.append("--signoff")

            result = subprocess.run(
                cmd,
                cwd=str(local_path),
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env(hooks=run_hooks),
            )
            if result.returncode != 0:
                _log_git_failure("git commit", local_path, result.stderr)
                return False, {"error": f"Commit failed. {_GIT_SEE_LOG}"}

            # Get the commit hash
            commit_hash = GitService.get_head_commit(local_path)

            return True, {
                "commit_hash": commit_hash,
                "files_changed": len(staged_files),
                "files": staged_files,
                "message": message,
            }
        except subprocess.TimeoutExpired:
            return False, {"error": "Commit timed out"}
        except (subprocess.SubprocessError, OSError) as e:
            _log_git_failure("git commit", local_path, str(e))
            return False, {"error": f"Commit failed. {_GIT_SEE_LOG}"}

    # =========================================================================
    # Caller-supplied ref and remote names
    # =========================================================================
    #
    # A remote or branch that reaches a git command line from a caller must
    # never be parsed by git as an option (`--receive-pack=<cmd>` runs a
    # command) or as a URL or path (a push to anywhere). Values are validated
    # as names here and, where git supports it, passed after
    # `--end-of-options` as well. tests/test_git_ref_arguments.py pins every
    # git argument list in this module.

    @staticmethod
    def is_valid_branch_name(name: object) -> bool:
        """True for a valid branch name that git cannot read as an option.

        Nor as anything but a branch: a leading "+" is a forced update in a
        push refspec, and `git check-ref-format` accepts it.
        """
        if not isinstance(name, str) or name.startswith(("-", "+")):
            return False
        # `git check-ref-format` accepts "refs/heads/-x", hence the check
        # above; it rejects whitespace, control characters, "..", "@{" and
        # the other forms a ref name may not take. The argument starts with
        # "refs/", so git cannot read it as an option either.
        try:
            result = subprocess.run(
                ["git", "check-ref-format", f"refs/heads/{name}"],
                capture_output=True,
                text=True,
                env=_git_env(),
            )
        except (subprocess.SubprocessError, OSError):
            return False
        return result.returncode == 0

    @staticmethod
    def validate_branch_name(name: object) -> str:
        """Return `name` if it is a valid branch name, else raise InvalidGitRefError."""
        if not GitService.is_valid_branch_name(name):
            raise InvalidGitRefError("Invalid branch name")
        return name  # type: ignore[return-value]

    @staticmethod
    def list_remotes(local_path: Path) -> set[str]:
        """The names of the repository's configured remotes."""
        try:
            result = subprocess.run(
                ["git", "remote"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to list remotes for %s", local_path, exc_info=True)
            return set()
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    @staticmethod
    def validate_remote_name(local_path: Path, name: object) -> str:
        """Return `name` if it is one of the repository's configured remotes.

        A URL, a path or an option is never a configured remote's name, so
        membership is the whole check.
        """
        if not isinstance(name, str) or name not in GitService.list_remotes(local_path):
            raise InvalidGitRefError("Remote must be the name of a configured remote")
        return name

    @staticmethod
    def push(
        local_path: Path,
        remote: str = "origin",
        branch: str | None = None,
        token: str | None = None,
        allowed_hosts: Sequence[str] | None = None,
    ) -> tuple[bool, str]:
        """
        Push commits to a remote repository.

        Args:
            local_path: Path to the git repository
            remote: Remote name (default: origin)
            branch: Branch to push (default: current branch)
            token: OAuth token for authentication
            allowed_hosts: given for a push Pyrite makes for a caller (export
                to repo): every push URL of `remote` must be https on one of
                these hosts, and git runs with the same transport, LFS and
                redirect restrictions as a clone. None
                (an operator pushing their own KB to the remote they
                configured) leaves the remote unrestricted.

        Returns:
            (success, message)

        Raises:
            InvalidGitRefError: `remote` is not a configured remote, or
                `branch` is not a valid branch name. Git is not run.
        """
        if not GitService.is_git_repo(local_path):
            return False, "Not a git repository"

        if not GitService.list_remotes(local_path):
            # Nothing can be pushed anywhere: an ordinary push failure, not a
            # rejected value, reported with its real cause.
            return False, "Push failed: this repository has no remotes configured"
        if branch is None:
            branch = GitService._checked_out_branch(local_path)
            if branch is None:
                # Not a rejected value: there is nothing to default to.
                return (
                    False,
                    "Push failed: the repository has no current branch "
                    "(detached HEAD); name the branch to push",
                )
        GitService.validate_remote_name(local_path, remote)
        GitService.validate_branch_name(branch)
        if token:
            # An operator's own push (no token) keeps whatever remote they set.
            GitService.scrub_remote_credentials(local_path)
        # The refspec is built here, never taken from the caller: a branch
        # value names that branch on both sides and nothing else (not a tag
        # of the same name, not a forced update).
        refspec = f"refs/heads/{branch}:refs/heads/{branch}"

        extra = {}
        cmd = ["git", "push", "-u", "--end-of-options", remote, refspec]
        if allowed_hosts is not None:
            try:
                GitService._check_remote_urls(local_path, remote, allowed_hosts, push=True)
            except RemoteNotAllowedError as e:
                return False, f"Push failed: {e}"
            extra.update(_NETWORK_GUARD_ENV)
            cmd = [
                "git",
                "-c",
                "http.followRedirects=false",
                "push",
                "-u",
                "--end-of-options",
                remote,
                refspec,
            ]

        try:
            creds = GitService._remote_credentials(local_path, remote, token, push=True)
            result = subprocess.run(
                cmd,
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env({**extra, **creds.env}, config=creds.config),
                timeout=60,
            )
            if result.returncode == 0:
                return True, result.stderr.strip() or result.stdout.strip() or "Pushed successfully"
            logger.warning(
                "git push to %s/%s failed (rc=%s): %s",
                remote,
                branch,
                result.returncode,
                GitService._sanitize_output(result.stderr, token).strip(),
            )
            # Git's own words, token- and path-redacted — see the note in
            # `pull`. A rejected push ("the tip of your current branch is
            # behind") must reach the user as git wrote it.
            message = GitService.sanitize_error(result.stderr, token)
            return False, f"Push failed: {message}" if message else "Push failed"
        except subprocess.TimeoutExpired:
            return False, "Push timed out"
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git push to %s/%s raised", remote, branch, exc_info=True)
            return False, f"Push failed: {GitService.sanitize_error(outside_text(e), token)}"

    @staticmethod
    def get_status(local_path: Path) -> GitWorkingTreeStatus:
        """
        Get git working tree status.

        Returns dict with: clean (bool), staged (list), unstaged (list), untracked (list)
        """
        result: GitWorkingTreeStatus = {
            "clean": True,
            "staged": [],
            "unstaged": [],
            "untracked": [],
        }

        if not GitService.is_git_repo(local_path):
            return result

        kb_prefix = GitService.get_kb_prefix(local_path)
        if kb_prefix is None:
            return result

        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if status.returncode != 0:
                return result

            for line in status.stdout.rstrip("\n").split("\n"):
                if not line:
                    continue
                if len(line) < 4:
                    continue
                index_status = line[0]
                work_status = line[1]
                filename = line[3:]

                rel_name = GitService._kb_relative_path(filename, kb_prefix)
                if not rel_name:
                    continue

                result["clean"] = False
                if index_status == "?":
                    result["untracked"].append(rel_name)
                elif index_status != " ":
                    result["staged"].append(rel_name)
                if work_status not in (" ", "?"):
                    result["unstaged"].append(rel_name)

            return result
        except (subprocess.SubprocessError, OSError):
            logger.warning("Failed to get git status for repo", exc_info=True)
            return result

    @staticmethod
    def _is_github_url(url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        return parts.scheme == "https" and (parts.hostname or "").lower() in _GITHUB_HOSTS

    @staticmethod
    def _credentials(token: str | None, urls: Sequence[str]) -> "_Credentials":
        """What lets one git command authenticate with `token`.

        The token reaches git only through an inline credential helper that
        reads it from the command's environment: nothing is written to disk,
        and the token is not in argv, a remote URL or a config file. The
        helper is scoped to the one GitHub origin(s) the command contacts, so
        it answers no other host's or proxy's prompt. Configured helpers (a
        system keychain, a credential cache) are reset for the command, so
        none is consulted and none is handed the token to store.

        `urls` are the URLs git will really contact, after the account's
        ``url.<base>.insteadOf`` rules. The token is offered only when every
        one is https on GitHub; otherwise git runs without the token.

        Whose helpers: a command made for a network caller (a REST request, an
        MCP call with a principal) authenticates with that caller's token or
        with nothing. The server account's configured helpers (a keychain, a
        store) are reset for it (``credential.helper=``) whether or not a token
        is given and whatever the host, so a caller never borrows the server
        account's credential. The local operator at the CLI
        (``acting.is_local_process``, the one switch) keeps their own helpers
        when no token is given.
        """
        isolated = _account_isolation_env()
        reset = (("credential.helper", ""), ("core.askPass", "")) if isolated else ()
        if not token or not urls or not all(GitService._is_github_url(u) for u in urls):
            return _Credentials(isolated, reset)
        origins = []
        for url in urls:
            parts = urlsplit(url)
            origin = f"https://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
            if origin not in origins:
                origins.append(origin)
        config = [("credential.helper", ""), *([("core.askPass", "")] if isolated else [])]
        config += [(f"credential.{origin}.helper", _CREDENTIAL_HELPER) for origin in origins]
        return _Credentials({**isolated, _TOKEN_ENV: token}, tuple(config))

    @staticmethod
    def _remote_credentials(
        local_path: Path, remote: str, token: str | None, *, push: bool
    ) -> "_Credentials":
        """`_credentials` for the URLs git would contact for `remote`
        (``git remote get-url`` reports them after insteadOf rewriting)."""
        urls: list[str] = []
        if token:
            cmd = ["git", "remote", "get-url", "--all"] + (["--push"] if push else [])
            try:
                out = subprocess.run(
                    [*cmd, "--", remote],
                    cwd=str(local_path),
                    capture_output=True,
                    text=True,
                    env=_git_env(_account_isolation_env()),
                    timeout=10,
                )
                if out.returncode == 0:
                    urls = out.stdout.split()
            except (subprocess.SubprocessError, OSError):
                urls = []
        return GitService._credentials(token, urls)

    @staticmethod
    def _without_userinfo(url: str) -> str:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        if (
            parts.scheme != "https"
            or "@" not in parts.netloc
            or (parts.hostname or "").lower() not in _GITHUB_HOSTS
        ):
            return url
        netloc = parts.netloc.rpartition("@")[2]
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))

    @staticmethod
    def _is_repository_root(local_path: Path) -> bool:
        """Whether ``local_path`` is the top level of a repository of its own:
        it has a ``.git`` and git's ``--show-toplevel`` from it is the same
        folder (``os.path.samefile``). A folder nested in another repository
        without its own ``.git`` is not (git would walk up to that one)."""
        try:
            if not (Path(local_path) / ".git").exists():
                return False
            out = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        if out.returncode != 0 or not out.stdout.strip():
            return False
        # The same folder, however it is spelled (letter case on a
        # case-insensitive filesystem, a link on the way): compared by identity.
        try:
            return os.path.samefile(out.stdout.strip(), local_path)
        except OSError:
            return False

    @staticmethod
    def scrub_remote_credentials(local_path: Path) -> int:
        """Remove user information from every remote URL of the repository.

        A workspace cloned by an earlier version carries the token in
        ``remote.<name>.url``. Run before a pull or push so the next use
        removes it; returns the number of URLs rewritten. Only the userinfo
        changes: scheme, host, port and path stay as they were. Only https GitHub
        URLs are touched, the ones this service put a token into; a remote an
        operator configured elsewhere keeps what they set.

        Only the repository whose top level is ``local_path`` itself is
        changed: a folder with no repository of its own is left alone, never
        the enclosing repository git would find by walking up from it
        (``_is_repository_root``). Each key is rewritten in place
        (``--replace-all`` with the first clean URL, then ``--add`` for the
        rest), so a failure part way never leaves a remote with no URL.
        """
        scrubbed = 0
        if not GitService._is_repository_root(local_path):
            return 0
        try:
            listing = subprocess.run(
                ["git", "config", "--local", "--get-regexp", r"^remote\..*\.(push)?url$"],
                cwd=str(local_path),
                capture_output=True,
                text=True,
                env=_git_env(),
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Could not read the remotes of a repository", exc_info=True)
            return 0
        urls_by_key: dict[str, list[str]] = {}
        for line in listing.stdout.splitlines():
            key, _, url = line.partition(" ")
            urls_by_key.setdefault(key, []).append(url)
        for key, urls in urls_by_key.items():
            clean_urls = [GitService._without_userinfo(u) for u in urls]
            if clean_urls == urls:
                continue
            try:
                # Replaced, not unset then added: the key holds a URL at every
                # step, so a failed command leaves the old value or the new one.
                subprocess.run(
                    ["git", "config", "--local", "--replace-all", "--", key, clean_urls[0]],
                    cwd=str(local_path),
                    capture_output=True,
                    text=True,
                    env=_git_env(),
                    timeout=10,
                    check=True,
                )
                for url in clean_urls[1:]:
                    subprocess.run(
                        ["git", "config", "--local", "--add", "--", key, url],
                        cwd=str(local_path),
                        capture_output=True,
                        text=True,
                        env=_git_env(),
                        timeout=10,
                        check=True,
                    )
                scrubbed += len(urls)
            except (subprocess.SubprocessError, OSError):
                logger.warning("Could not remove credentials from a remote URL", exc_info=True)
        return scrubbed

    @staticmethod
    def _sanitize_output(output: str, token: str | None) -> str:
        """Remove tokens from error messages."""
        if token and token in output:
            return output.replace(token, "***")
        return output

    @staticmethod
    def redact_paths(output: str) -> str:
        """Replace absolute filesystem paths with a stable placeholder and drop
        the lines that exist only to narrate local filesystem work.

        git's first clone line is `Cloning into '<absolute dest>'...`, which
        tells a remote caller the server's `$HOME` and workspace root. Nothing
        a caller can act on lives in it.
        """
        cleaned = _PATH_NARRATION_RE.sub("", output)
        cleaned = _ABS_PATH_RE.sub(_PATH_PLACEHOLDER, cleaned)
        # Collapse the blank lines the narration removal leaves behind.
        return "\n".join(line for line in cleaned.splitlines() if line.strip()).strip()

    @staticmethod
    def sanitize_error(output: str, token: str | None = None) -> str:
        """Everything that may be shown to a caller: tokens removed (the
        existing behaviour) *and* absolute paths redacted (new)."""
        return GitService.redact_paths(GitService._sanitize_output(output, token))

    @staticmethod
    def classify_git_error(output: str, token: str | None = None) -> tuple[str, str]:
        """Map git stderr onto a stable `(code, safe_message)` pair.

        The three failures a write-tier caller can act on -- the repository is
        not there, the credentials are wrong, the branch is wrong -- each get
        their own code, so collapsing them into one opaque "Clone failed" is a
        regression rather than a fix. The message never carries a path; the
        caller gets the code, and the operator gets the full stderr in the log.
        """
        for code, pattern, message in _GIT_ERROR_PATTERNS:
            if pattern.search(output):
                return code, message
        safe = GitService.sanitize_error(output, token)
        # An unrecognised shape may still embed a URL or a hostname, so only
        # the fixed fallback text is returned; `safe` is what the caller logs.
        logger.debug("Unclassified git error: %s", safe)
        return _GIT_ERROR_FALLBACK

    # =========================================================================
    # Git worktree operations
    # =========================================================================

    @staticmethod
    def worktree_add(repo_path: Path, worktree_path: Path, branch: str) -> tuple[bool, str]:
        """Create a git worktree with a new branch.

        Args:
            repo_path: Path to the main git repository.
            worktree_path: Path where the worktree will be created.
            branch: Branch name for the worktree (created if not exists).

        Returns:
            (success, message) tuple.
        """
        if not GitService.is_valid_branch_name(branch):
            return False, "Invalid branch name"
        worktree_path = Path(worktree_path)
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Try creating with new branch first
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch, "--end-of-options", str(worktree_path)],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, f"Created worktree at {worktree_path} on branch {branch}"
            # Branch may already exist — try without -b
            result = subprocess.run(
                ["git", "worktree", "add", "--end-of-options", str(worktree_path), branch],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, f"Created worktree at {worktree_path} on existing branch {branch}"
            return False, result.stderr.strip()
        except (subprocess.SubprocessError, OSError) as e:
            return False, outside_text(e)

    @staticmethod
    def worktree_list(repo_path: Path) -> list[dict[str, str]]:
        """List all git worktrees for a repository.

        Returns list of dicts with 'worktree', 'HEAD', 'branch' keys.
        """
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode != 0:
                return []
            worktrees = []
            current: dict[str, str] = {}
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    if current:
                        worktrees.append(current)
                    current = {"worktree": line[9:]}
                elif line.startswith("HEAD "):
                    current["HEAD"] = line[5:]
                elif line.startswith("branch "):
                    current["branch"] = line[7:]
                elif line == "bare":
                    current["bare"] = "true"
                elif line == "detached":
                    current["detached"] = "true"
            if current:
                worktrees.append(current)
            return worktrees
        except (subprocess.SubprocessError, OSError):
            return []

    @staticmethod
    def worktree_remove(
        repo_path: Path, worktree_path: Path, force: bool = False
    ) -> tuple[bool, str]:
        """Remove a git worktree."""
        cmd = ["git", "worktree", "remove", str(worktree_path)]
        if force:
            cmd.append("--force")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, f"Removed worktree at {worktree_path}"
            return False, result.stderr.strip()
        except (subprocess.SubprocessError, OSError) as e:
            return False, outside_text(e)

    @staticmethod
    def merge_branch(repo_path: Path, branch: str, into: str = "main") -> tuple[bool, str]:
        """Merge a branch into another (typically main).

        Performs checkout + merge in the repo_path working directory.
        Returns (success, message). On conflict, returns (False, conflict_info).
        """
        if not (GitService.is_valid_branch_name(branch) and GitService.is_valid_branch_name(into)):
            return False, "Invalid branch name"
        try:
            # Checkout target branch
            result = subprocess.run(
                ["git", "checkout", into],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode != 0:
                return False, f"Failed to checkout {into}: {result.stderr.strip()}"

            # Merge
            result = subprocess.run(
                ["git", "merge", "--no-edit", "--end-of-options", branch],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, f"Merged {branch} into {into}"

            # Merge conflict — abort and report
            conflict_info = result.stdout.strip()
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                env=_git_env(),
            )
            return False, f"Merge conflict: {conflict_info}"
        except (subprocess.SubprocessError, OSError) as e:
            return False, outside_text(e)

    @staticmethod
    def diff_branches(
        repo_path: Path, base: str, head: str, stat_only: bool = False
    ) -> tuple[bool, str]:
        """Get diff between two branches.

        Args:
            repo_path: Path to the git repository.
            base: Base branch (e.g., "main").
            head: Head branch (e.g., "user/alice").
            stat_only: If True, return --stat summary only.

        Returns:
            (success, diff_output) tuple.
        """
        if not (GitService.is_valid_branch_name(base) and GitService.is_valid_branch_name(head)):
            return False, "Invalid branch name"
        cmd = ["git", "diff", "--no-ext-diff", "--no-textconv"]
        if stat_only:
            cmd.append("--stat")
        cmd.extend(["--end-of-options", f"{base}...{head}"])
        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=30,
                env=_git_env(),
            )
            if result.returncode == 0:
                return True, result.stdout
            _log_git_failure("git diff", repo_path, result.stderr)
            return False, f"Diff failed. {_GIT_SEE_LOG}"
        except subprocess.TimeoutExpired:
            return False, "Diff timed out"
        except (subprocess.SubprocessError, OSError) as e:
            _log_git_failure("git diff", repo_path, str(e))
            return False, f"Diff failed. {_GIT_SEE_LOG}"
