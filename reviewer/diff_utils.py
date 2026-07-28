"""Utilities for loading and parsing Git diffs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

MAX_DIFF_CHARS = 100_000

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_PLUS_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


class DiffError(ValueError):
    """Raised when a diff cannot be used for review."""


def load_diff_from_file(path: str | Path) -> str:
    """Read a unified diff from disk."""
    text = Path(path).read_text(encoding="utf-8")
    return validate_diff(text)


def load_diff_from_git(repo_path: str | Path, base_ref: str = "HEAD~1") -> str:
    """Run `git diff <base_ref>` inside a repository and return the patch."""
    repo = Path(repo_path).resolve()
    if not (repo / ".git").exists() and not _is_git_worktree(repo):
        # Still allow bare workdirs that are inside a git repo
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise DiffError(f"Not a git repository: {repo}")

    result = subprocess.run(
        ["git", "-C", str(repo), "diff", base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "unknown git error"
        raise DiffError(f"git diff failed: {stderr}")
    return validate_diff(result.stdout)


def _is_git_worktree(repo: Path) -> bool:
    return (repo / ".git").exists()


def validate_diff(diff: str) -> str:
    """Reject empty diffs. Oversized diffs are handled by truncation, not errors."""
    text = diff.strip()
    if not text:
        raise DiffError("Diff is empty — nothing to review.")
    return text


def truncate_to_limit(
    diff: str, max_chars: int = MAX_DIFF_CHARS
) -> tuple[str, bool]:
    """Cap a diff at ``max_chars`` characters, cutting on a line boundary.

    Returns ``(text, truncated)``. When truncation happens, a marker line is
    appended so the reviewer knows the patch is incomplete. A diff at or under
    the limit is returned unchanged with ``truncated=False``.
    """
    if max_chars <= 0 or len(diff) <= max_chars:
        return diff, False

    clipped = diff[:max_chars]
    # Prefer to cut at the last newline so we don't split a line mid-token.
    newline = clipped.rfind("\n")
    if newline > 0:
        clipped = clipped[:newline]
    marker = (
        f"\n... [diff truncated to {max_chars} chars; "
        f"{len(diff)} chars total — review is partial] ..."
    )
    return clipped + marker, True


def extract_changed_files(diff: str) -> list[str]:
    """Extract unique file paths from a unified git diff."""
    files: list[str] = []
    seen: set[str] = set()

    for match in _DIFF_GIT_RE.finditer(diff):
        path = match.group(2)
        if path == "/dev/null":
            path = match.group(1)
        if path not in seen:
            seen.add(path)
            files.append(path)

    if not files:
        for match in _PLUS_PLUS_RE.finditer(diff):
            path = match.group(1)
            if path not in seen and path != "/dev/null":
                seen.add(path)
                files.append(path)

    return files
