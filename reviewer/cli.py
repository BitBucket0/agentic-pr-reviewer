"""CLI entry point for the agentic pull request reviewer."""

from __future__ import annotations

import argparse
import os
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from reviewer.diff_utils import DiffError, load_diff_from_file, load_diff_from_git
from reviewer.graph import build_graph

# Exit codes (see plan): 0 success/partial, 1 review incomplete/model failure,
# 2 invalid invocation/configuration/input.
EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_INVALID = 2

_STATUS_EXIT = {
    "success": EXIT_OK,
    "partial": EXIT_OK,
    "input_error": EXIT_INVALID,
    "reviewer_failed": EXIT_INCOMPLETE,
    "verifier_failed": EXIT_INCOMPLETE,
}


def _package_version() -> str:
    try:
        return version("agentic-pr-reviewer")
    except PackageNotFoundError:
        return "0.0.0+local"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agentic-pr-reviewer",
        description="Review a Git diff with a LangGraph agentic workflow.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Path to a local git repository to diff (defaults to current dir)",
    )
    source.add_argument(
        "--diff",
        type=str,
        default=None,
        help="Path to a unified diff file (skips git)",
    )
    parser.add_argument(
        "--base",
        type=str,
        default="HEAD~1",
        help="Git revision to diff against (default: HEAD~1)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="OpenAI model name (default: OPENAI_MODEL or gpt-4o-mini)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write the Markdown review to this file (in addition to stdout)",
    )
    parser.add_argument(
        "--run-checks",
        action="store_true",
        help="Run the target repo's pytest/ruff. WARNING: this executes code "
        "from the target repository. Only enable for repositories you trust.",
    )
    args = parser.parse_args(argv)
    if args.repo is None and args.diff is None:
        args.repo = "."
    return args


def _write_output(path: str | None, markdown: str) -> None:
    if not path:
        return
    try:
        Path(path).write_text(markdown, encoding="utf-8")
    except OSError as exc:
        print(f"Warning: could not write --output file: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # Load .env from the directory the command is invoked in (run from repo root).
    load_dotenv(find_dotenv(usecwd=True))

    args = parse_args(argv)

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    if args.run_checks:
        print(
            "Warning: --run-checks executes tests/linters from the target "
            "repository. Only use it on repositories you trust.",
            file=sys.stderr,
        )

    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key.startswith("sk-your-key"):
        print(
            "Error: OPENAI_API_KEY is not set. Add it to .env (see .env.example) "
            "or export it in your shell.",
            file=sys.stderr,
        )
        return EXIT_INVALID

    try:
        if args.diff:
            diff_text = load_diff_from_file(args.diff)
            repo_path = None
        else:
            diff_text = load_diff_from_git(args.repo, args.base)
            repo_path = args.repo
    except (DiffError, OSError) as exc:
        markdown = f"# PR Review\n\n**Input error:** {exc}\n"
        _write_output(args.output, markdown)
        print(f"Error loading diff: {exc}", file=sys.stderr)
        return EXIT_INVALID

    graph = build_graph()
    initial_state = {
        "diff": diff_text,
        "repo_path": str(Path(repo_path).resolve()) if repo_path else None,
        "base_ref": args.base,
        "run_checks": bool(args.run_checks),
        "retry_count": 0,
        "started_at": time.time(),
    }

    # recursion_limit guards against accidental infinite loops
    result = graph.invoke(initial_state, config={"recursion_limit": 25})

    markdown = result.get("review_markdown") or ""
    status = result.get("status", "success")

    _write_output(args.output, markdown)

    if markdown:
        print(markdown)
    elif result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)

    return _STATUS_EXIT.get(status, EXIT_INCOMPLETE)


if __name__ == "__main__":
    raise SystemExit(main())
