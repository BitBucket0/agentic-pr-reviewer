#!/usr/bin/env python3
"""CLI entry point for the agentic pull request reviewer."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from reviewer.diff_utils import DiffError, load_diff_from_file, load_diff_from_git
from reviewer.graph import build_graph

# Load OPENAI_API_KEY / OPENAI_MODEL from .env in the project root
load_dotenv(Path(__file__).resolve().parent / ".env")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review a local Git diff with a LangGraph agentic workflow.",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Path to a local git repository to diff",
    )
    parser.add_argument(
        "--base",
        type=str,
        default="HEAD~1",
        help="Git revision to diff against (default: HEAD~1)",
    )
    parser.add_argument(
        "--diff",
        type=str,
        default=None,
        help="Path to a unified diff file (skips git)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="OpenAI model name (default: OPENAI_MODEL or gpt-4o-mini)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.model:
        os.environ["OPENAI_MODEL"] = args.model

    if not args.diff and not args.repo:
        print("Error: provide --diff PATH or --repo PATH", file=sys.stderr)
        return 2

    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "").startswith(
        "sk-your-key"
    ):
        print(
            "Error: OPENAI_API_KEY is not set. "
            "Add it to .env (see .env.example) or export it in your shell.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.diff:
            diff_text = load_diff_from_file(args.diff)
            repo_path = args.repo
        else:
            diff_text = load_diff_from_git(args.repo, args.base)
            repo_path = args.repo
    except (DiffError, OSError) as exc:
        print(f"Error loading diff: {exc}", file=sys.stderr)
        return 1

    graph = build_graph()
    initial_state = {
        "diff": diff_text,
        "repo_path": str(Path(repo_path).resolve()) if repo_path else None,
        "base_ref": args.base,
        "retry_count": 0,
        "started_at": time.time(),
    }

    # recursion_limit guards against accidental infinite loops
    result = graph.invoke(initial_state, config={"recursion_limit": 25})

    markdown = result.get("review_markdown") or ""
    if result.get("error") and not markdown:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    print(markdown)
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
