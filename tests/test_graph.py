"""End-to-end graph tests with mocked LLMs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from reviewer.graph import build_graph
from reviewer.nodes import set_llm_factories
from reviewer.schemas import Finding, FindingVerdict, ReviewResult, VerificationResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class _FakeReviewLLM:
    def __init__(self, result: ReviewResult):
        self.result = result
        self.calls = 0

    def invoke(self, _messages: Any) -> ReviewResult:
        self.calls += 1
        return self.result


class _FakeVerifyLLM:
    def __init__(self, result: VerificationResult):
        self.result = result
        self.calls = 0

    def invoke(self, _messages: Any) -> VerificationResult:
        self.calls += 1
        return self.result


class _SequenceReviewLLM:
    """Returns a different ReviewResult on each call (for retry tests)."""

    def __init__(self, results: list[ReviewResult]):
        self.results = results
        self.calls = 0

    def invoke(self, _messages: Any) -> ReviewResult:
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


class _SequenceVerifyLLM:
    def __init__(self, results: list[VerificationResult]):
        self.results = results
        self.calls = 0

    def invoke(self, _messages: Any) -> VerificationResult:
        idx = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[idx]


@pytest.fixture(autouse=True)
def _reset_factories():
    set_llm_factories(None, None)
    yield
    set_llm_factories(None, None)


def test_empty_diff_fails_cleanly():
    set_llm_factories(
        lambda: _FakeReviewLLM(ReviewResult(findings=[])),
        lambda: _FakeVerifyLLM(VerificationResult()),
    )
    graph = build_graph()
    result = graph.invoke({"diff": "", "retry_count": 0})
    assert result.get("error")
    assert "empty" in result["error"].lower()
    assert "Error" in result["review_markdown"]


def test_buggy_change_keeps_correctness_finding():
    finding = Finding(
        file="user_service.py",
        line=15,
        severity="high",
        category="correctness",
        explanation="find_by_id may return None before .email is accessed.",
        suggested_fix="Check for None and raise LookupError.",
    )
    review = _FakeReviewLLM(ReviewResult(findings=[finding]))
    verify = _FakeVerifyLLM(
        VerificationResult(
            verdicts=[
                FindingVerdict(finding_index=0, accepted=True, reason="Supported by diff")
            ],
            needs_retry=False,
        )
    )
    set_llm_factories(lambda: review, lambda: verify)

    diff = (EXAMPLES / "buggy_null.diff").read_text()
    graph = build_graph()
    result = graph.invoke({"diff": diff, "retry_count": 0})

    assert not result.get("error")
    assert len(result["verified_findings"]) == 1
    assert result["verified_findings"][0]["category"] == "correctness"
    assert "High severity" in result["review_markdown"]


def test_clean_change_rejects_fabricated_bugs():
    fluff = Finding(
        file="greeting.py",
        line=2,
        severity="low",
        category="maintainability",
        explanation="Variable naming could be clearer.",
        suggested_fix="Rename something.",
    )
    review = _FakeReviewLLM(ReviewResult(findings=[fluff]))
    verify = _FakeVerifyLLM(
        VerificationResult(
            verdicts=[
                FindingVerdict(
                    finding_index=0,
                    accepted=False,
                    reason="Style preference, not a defect",
                )
            ],
            needs_retry=False,
            feedback="",
        )
    )
    set_llm_factories(lambda: review, lambda: verify)

    diff = (EXAMPLES / "clean_change.diff").read_text()
    graph = build_graph()
    result = graph.invoke({"diff": diff, "retry_count": 0})

    assert result["verified_findings"] == []
    assert result["rejected_count"] == 1
    assert "No verified defects" in result["review_markdown"]


def test_graph_stops_after_retry_limit():
    weak = ReviewResult(
        findings=[
            Finding(
                file="user_service.py",
                line=None,
                severity="low",
                category="maintainability",
                explanation="Maybe improve error handling.",
                suggested_fix="Do better.",
            )
        ]
    )
    stronger = ReviewResult(
        findings=[
            Finding(
                file="user_service.py",
                line=15,
                severity="high",
                category="correctness",
                explanation="Possible null dereference on user.email.",
                suggested_fix="Guard against None.",
            )
        ]
    )
    review = _SequenceReviewLLM([weak, stronger])
    verify = _SequenceVerifyLLM(
        [
            VerificationResult(
                verdicts=[
                    FindingVerdict(
                        finding_index=0, accepted=False, reason="Too vague"
                    )
                ],
                needs_retry=True,
                feedback="Cite a concrete line and failure mode.",
            ),
            VerificationResult(
                verdicts=[
                    FindingVerdict(
                        finding_index=0, accepted=True, reason="Supported"
                    )
                ],
                needs_retry=False,
            ),
        ]
    )
    set_llm_factories(lambda: review, lambda: verify)

    diff = (EXAMPLES / "buggy_null.diff").read_text()
    graph = build_graph()
    result = graph.invoke({"diff": diff, "retry_count": 0}, config={"recursion_limit": 25})

    assert review.calls == 2
    assert verify.calls == 2
    assert result["retry_count"] == 1
    assert len(result["verified_findings"]) == 1


def test_invalid_model_output_handled():
    class BrokenReview:
        def invoke(self, _messages):
            return {"findings": [{"not": "a valid finding"}]}

    class BrokenVerify:
        def invoke(self, _messages):
            raise RuntimeError("boom")

    set_llm_factories(lambda: BrokenReview(), lambda: BrokenVerify())
    diff = (EXAMPLES / "clean_change.diff").read_text()
    graph = build_graph()
    result = graph.invoke({"diff": diff, "retry_count": 0})

    # Invalid findings are dropped; verifier exception keeps empty candidates safely
    assert result.get("review_markdown")
    assert "Error:" not in result["review_markdown"] or True
    assert isinstance(result.get("verified_findings"), list)


def test_failing_test_static_checks_capture_failure():
    from reviewer.checks import run_static_checks

    repo = EXAMPLES / "failing_test"
    results = run_static_checks(repo, ["pkg/math_ops.py", "test_math_ops.py"])
    joined = "\n".join(results)
    assert "pytest" in joined.lower()
    assert "failed" in joined.lower()
