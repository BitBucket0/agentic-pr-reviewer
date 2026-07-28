"""Shared graph state for the PR review workflow."""

import operator
from typing import Annotated, Any, Literal, TypedDict

ReviewStatus = Literal[
    "success",
    "input_error",
    "reviewer_failed",
    "verifier_failed",
    "partial",
]
"""Terminal status of a review run.

- ``success``: the workflow completed and every surviving finding was verified
  (an empty verified list here means the model genuinely found nothing).
- ``input_error``: the diff was empty, too large, or unparseable.
- ``reviewer_failed``: the reviewer model raised, could not be constructed, or
  returned output that was entirely malformed.
- ``verifier_failed``: the verifier model raised or could not be constructed;
  candidates are surfaced as unverified and never promoted to verified.
- ``partial``: the LLM review ran, but some findings were malformed or dropped
  (e.g. referenced files outside the diff) or deterministic checks were
  unavailable. A partial result is never reported as "no defects".
"""


class ReviewState(TypedDict, total=False):
    """Clipboard shared by every node in the review graph."""

    # Inputs
    diff: str
    repo_path: str | None
    base_ref: str
    run_checks: bool

    # Diff analysis
    changed_files: list[str]

    # Deterministic tools
    static_check_results: list[str]

    # LLM review loop
    candidate_findings: list[dict[str, Any]]
    verified_findings: list[dict[str, Any]]
    unverified_findings: list[dict[str, Any]]
    rejected_count: int
    verification_feedback: str
    needs_retry: bool
    retry_count: int
    max_retries: int

    # Outcome
    status: ReviewStatus
    # Accumulated across nodes rather than overwritten.
    warnings: Annotated[list[str], operator.add]

    # Output
    review_markdown: str
    error: str

    # Timing / stats
    started_at: float
    elapsed_seconds: float
