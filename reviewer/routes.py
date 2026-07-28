"""Conditional routing between graph nodes."""

from reviewer.state import ReviewState


def route_after_review(state: ReviewState) -> str:
    """Skip verification entirely when the review step already failed."""
    if state.get("status") in {"reviewer_failed", "input_error"} or state.get("error"):
        return "report"
    return "verify"


def route_after_verification(state: ReviewState) -> str:
    """Return `retry` or `finish` based on verifier output and retry budget."""
    if state.get("error") or state.get("status") in {
        "reviewer_failed",
        "verifier_failed",
        "input_error",
    }:
        return "finish"
    if state.get("needs_retry") and state.get("retry_count", 0) < 1:
        return "retry"
    return "finish"
