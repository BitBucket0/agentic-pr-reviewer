"""Conditional routing after verification."""

from reviewer.state import ReviewState


def route_after_verification(state: ReviewState) -> str:
    """Return `retry` or `finish` based on verifier output and retry budget."""
    if state.get("error"):
        return "finish"
    if state.get("needs_retry") and state.get("retry_count", 0) < 1:
        return "retry"
    return "finish"
