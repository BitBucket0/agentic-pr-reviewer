"""Tests for conditional routing after verification."""

from reviewer.routes import route_after_verification


def test_route_finish_when_no_retry_needed():
    assert route_after_verification({"needs_retry": False, "retry_count": 0}) == "finish"


def test_route_retry_when_needed_and_budget_left():
    assert route_after_verification({"needs_retry": True, "retry_count": 0}) == "retry"


def test_route_finish_after_retry_budget_exhausted():
    assert route_after_verification({"needs_retry": True, "retry_count": 1}) == "finish"


def test_route_finish_on_error():
    assert (
        route_after_verification(
            {"error": "Diff is empty", "needs_retry": True, "retry_count": 0}
        )
        == "finish"
    )
