"""Shared graph state for the PR review workflow."""

from typing import Any, TypedDict


class ReviewState(TypedDict, total=False):
    """Clipboard shared by every node in the review graph."""

    # Inputs
    diff: str
    repo_path: str | None
    base_ref: str

    # Diff analysis
    changed_files: list[str]

    # Deterministic tools
    static_check_results: list[str]

    # LLM review loop
    candidate_findings: list[dict[str, Any]]
    verified_findings: list[dict[str, Any]]
    rejected_count: int
    verification_feedback: str
    needs_retry: bool
    retry_count: int

    # Output
    review_markdown: str
    error: str

    # Timing / stats
    started_at: float
    elapsed_seconds: float
