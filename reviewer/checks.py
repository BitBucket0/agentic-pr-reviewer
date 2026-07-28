"""Deterministic static checks (pytest, ruff) for changed Python files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def run_static_checks(
    repo_path: str | Path | None,
    changed_files: list[str],
) -> list[str]:
    """Run available tools against Python files in the changed set."""
    if not repo_path:
        return ["Skipped static checks: no --repo provided."]

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return [f"Skipped static checks: repo path not found ({repo})."]

    python_files = [
        f for f in changed_files if f.endswith(".py") and not f.startswith("tests/")
    ]
    test_files = [f for f in changed_files if f.endswith(".py") and "test" in f.lower()]
    results: list[str] = []

    results.extend(_run_ruff(repo, python_files + test_files))
    results.extend(_run_pytest(repo, changed_files))

    if not results:
        results.append("No applicable static checks for the changed files.")
    return results


def _run_ruff(repo: Path, files: list[str]) -> list[str]:
    if not files:
        return ["Ruff: skipped (no Python files in diff)."]

    ruff = shutil.which("ruff")
    if not ruff:
        return ["Ruff: not installed; skipped."]

    existing = [f for f in files if (repo / f).exists()]
    if not existing:
        return ["Ruff: skipped (changed Python files not present on disk)."]

    proc = subprocess.run(
        [ruff, "check", *existing],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0:
        return [f"Ruff: clean ({len(existing)} file(s))."]
    summary = output.splitlines()[:20]
    joined = "\n".join(summary) if summary else "lint issues found"
    return [f"Ruff: issues reported\n{joined}"]


def _run_pytest(repo: Path, changed_files: list[str]) -> list[str]:
    pytest_bin = shutil.which("pytest")
    if not pytest_bin:
        return ["pytest: not installed; skipped."]

    # Prefer running the whole test suite when the repo looks like a Python project
    has_tests = (repo / "tests").is_dir() or any(
        p.name.startswith("test_") and p.suffix == ".py"
        for p in repo.rglob("test_*.py")
    )
    if not has_tests and not any("test" in f.lower() for f in changed_files):
        return ["pytest: skipped (no tests detected)."]

    proc = subprocess.run(
        [pytest_bin, "-q", "--tb=line"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    tail = "\n".join((stdout + "\n" + stderr).strip().splitlines()[-30:])
    if proc.returncode == 0:
        return [f"pytest: passed\n{tail}" if tail else "pytest: passed"]
    return [f"pytest: failed (exit {proc.returncode})\n{tail}"]
