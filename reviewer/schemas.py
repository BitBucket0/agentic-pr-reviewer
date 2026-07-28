"""Pydantic models for structured LLM outputs."""

from typing import Literal

from pydantic import BaseModel, Field


Severity = Literal["low", "medium", "high"]
Category = Literal[
    "correctness",
    "security",
    "performance",
    "maintainability",
    "testing",
]


class Finding(BaseModel):
    """A single review finding tied to a concrete location in the diff."""

    file: str = Field(description="Path of the affected file")
    line: int | None = Field(
        default=None,
        description="Approximate line number in the new file, if known",
    )
    severity: Severity = Field(description="Impact severity")
    category: Category = Field(description="Defect category")
    explanation: str = Field(
        description="Concrete failure scenario supported by the diff"
    )
    suggested_fix: str = Field(description="Specific suggested fix")


class ReviewResult(BaseModel):
    """Structured output from the review node."""

    findings: list[Finding] = Field(default_factory=list)


class FindingVerdict(BaseModel):
    """Verifier decision for one candidate finding."""

    finding_index: int = Field(description="0-based index into candidate_findings")
    accepted: bool = Field(description="Whether the finding is supported by the diff")
    reason: str = Field(description="Why the finding was accepted or rejected")
    adjusted_severity: Severity | None = Field(
        default=None,
        description="Corrected severity when the original is unreasonable",
    )


class VerificationResult(BaseModel):
    """Structured output from the verifier node."""

    verdicts: list[FindingVerdict] = Field(default_factory=list)
    needs_retry: bool = Field(
        default=False,
        description="True when findings are too weak and the reviewer should try again",
    )
    feedback: str = Field(
        default="",
        description="Guidance for the reviewer on a retry",
    )
