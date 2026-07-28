"""Graph nodes: deterministic helpers and LLM review/verify steps."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from reviewer.checks import run_static_checks
from reviewer.diff_utils import (
    DiffError,
    extract_changed_files,
    load_diff_from_file,
    load_diff_from_git,
    validate_diff,
)
from reviewer.prompts import REVIEW_SYSTEM, REVIEW_USER, VERIFY_SYSTEM, VERIFY_USER
from reviewer.providers import build_llm
from reviewer.schemas import Finding, ReviewResult, VerificationResult
from reviewer.state import ReviewState

# Statuses that mean the workflow should stop without further LLM work.
TERMINAL_STATUSES = {"input_error", "reviewer_failed", "verifier_failed"}

# Optional injection points for tests
_review_llm_factory: Callable[[], Any] | None = None
_verify_llm_factory: Callable[[], Any] | None = None


def set_llm_factories(
    review_factory: Callable[[], Any] | None = None,
    verify_factory: Callable[[], Any] | None = None,
) -> None:
    """Override LLM constructors (used by unit tests)."""
    global _review_llm_factory, _verify_llm_factory
    _review_llm_factory = review_factory
    _verify_llm_factory = verify_factory


def _build_review_llm() -> Any:
    if _review_llm_factory is not None:
        return _review_llm_factory()
    return build_llm(ReviewResult)


def _build_verify_llm() -> Any:
    if _verify_llm_factory is not None:
        return _verify_llm_factory()
    return build_llm(VerificationResult)


def _is_terminal(state: ReviewState) -> bool:
    return state.get("status") in TERMINAL_STATUSES or bool(state.get("error"))


def normalize_diff_path(path: str) -> str:
    """Canonicalize a diff/file path so equivalent forms compare equal."""
    path = (path or "").replace("\\", "/").strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    path = path.removeprefix("./")
    return path


def load_diff(state: ReviewState) -> dict[str, Any]:
    """Validate / load the diff into state. Expects `diff` already set by the CLI,
    or `repo_path` + `base_ref` to load from git.
    """
    updates: dict[str, Any] = {
        "started_at": state.get("started_at") or time.time(),
        "retry_count": state.get("retry_count", 0),
        "candidate_findings": [],
        "verified_findings": [],
        "unverified_findings": [],
        "rejected_count": 0,
        "needs_retry": False,
        "verification_feedback": "",
        "static_check_results": [],
        "status": "success",
        "error": "",
    }

    try:
        if "diff" in state:
            diff = validate_diff(state.get("diff") or "")
        elif state.get("repo_path"):
            diff = load_diff_from_git(
                state["repo_path"],
                state.get("base_ref") or "HEAD~1",
            )
        else:
            raise DiffError("Provide --diff or --repo so a patch can be loaded.")
        updates["diff"] = diff
    except DiffError as exc:
        updates["error"] = str(exc)
        updates["status"] = "input_error"
        updates["diff"] = state.get("diff") or ""
        updates["changed_files"] = []
    return updates


def identify_files(state: ReviewState) -> dict[str, Any]:
    if _is_terminal(state):
        return {}
    files = extract_changed_files(state.get("diff", ""))
    if not files:
        return {
            "error": "Could not extract changed files from the diff.",
            "status": "input_error",
            "changed_files": [],
        }
    return {"changed_files": files}


def run_checks(state: ReviewState) -> dict[str, Any]:
    if _is_terminal(state):
        return {"static_check_results": []}
    if not state.get("run_checks"):
        return {
            "static_check_results": [
                "Static checks disabled (enable with --run-checks)."
            ]
        }
    results = run_static_checks(
        state.get("repo_path"),
        state.get("changed_files") or [],
    )
    return {"static_check_results": results}


def review_code(state: ReviewState) -> dict[str, Any]:
    if _is_terminal(state):
        return {"candidate_findings": []}

    prompt = REVIEW_USER.format(
        changed_files="\n".join(state.get("changed_files") or []) or "(none)",
        static_check_results="\n".join(state.get("static_check_results") or [])
        or "(none)",
        verification_feedback=state.get("verification_feedback") or "(none)",
        diff=state.get("diff", ""),
    )

    # Model construction is inside the try so config/import failures fail closed.
    try:
        llm = _build_review_llm()
        result = llm.invoke(
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
    except Exception as exc:  # noqa: BLE001 — surface model failures cleanly
        return {
            "candidate_findings": [],
            "status": "reviewer_failed",
            "error": f"Reviewer failed: {exc}",
            "needs_retry": False,
        }

    raw_count = _raw_finding_count(result)
    findings, malformed = _normalize_findings(result)

    # The model tried to report findings but every one was malformed.
    if raw_count > 0 and not findings:
        return {
            "candidate_findings": [],
            "status": "reviewer_failed",
            "error": "Reviewer returned only malformed findings.",
            "needs_retry": False,
        }

    updates: dict[str, Any] = {}
    warnings: list[str] = []

    if malformed:
        warnings.append(f"{malformed} malformed finding(s) discarded.")
        updates["status"] = "partial"

    changed = {normalize_diff_path(f) for f in (state.get("changed_files") or [])}
    kept: list[dict[str, Any]] = []
    dropped = 0
    for finding in findings:
        if not changed or normalize_diff_path(finding.get("file", "")) in changed:
            kept.append(finding)
        else:
            dropped += 1

    if dropped:
        warnings.append(
            f"{dropped} finding(s) referenced files outside the diff and were dropped."
        )
        updates["status"] = "partial"

    updates["candidate_findings"] = kept
    if warnings:
        updates["warnings"] = warnings
    return updates


def verify_findings(state: ReviewState) -> dict[str, Any]:
    if _is_terminal(state):
        return {}

    candidates = state.get("candidate_findings") or []
    if not candidates:
        return {
            "verified_findings": [],
            "rejected_count": 0,
            "needs_retry": False,
            "verification_feedback": "",
        }

    prompt = VERIFY_USER.format(
        diff=state.get("diff", ""),
        static_check_results="\n".join(state.get("static_check_results") or [])
        or "(none)",
        candidate_findings=json.dumps(candidates, indent=2),
    )

    try:
        llm = _build_verify_llm()
        result = llm.invoke(
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        if not isinstance(result, VerificationResult):
            result = VerificationResult.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        # Fail closed: unverified candidates are NEVER promoted to verified.
        return {
            "status": "verifier_failed",
            "verified_findings": [],
            "unverified_findings": candidates,
            "rejected_count": 0,
            "needs_retry": False,
            "error": f"Verifier failed: {exc}",
        }

    accepted: list[dict[str, Any]] = []
    rejected = 0
    for verdict in result.verdicts:
        idx = verdict.finding_index
        if idx < 0 or idx >= len(candidates):
            continue
        if verdict.accepted:
            finding = dict(candidates[idx])
            if verdict.adjusted_severity:
                finding["severity"] = verdict.adjusted_severity
            accepted.append(finding)
        else:
            rejected += 1

    # No verdicts at all => treat every candidate as weak/rejected.
    if not result.verdicts:
        rejected = len(candidates)
        accepted = []

    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 1)
    needs_retry = (
        bool(result.needs_retry) and retry_count < max_retries and not accepted
    )

    return {
        "verified_findings": accepted,
        "rejected_count": rejected,
        "needs_retry": needs_retry,
        "verification_feedback": result.feedback or "",
    }


def prepare_retry(state: ReviewState) -> dict[str, Any]:
    return {
        "retry_count": state.get("retry_count", 0) + 1,
        "needs_retry": False,
        "candidate_findings": [],
    }


def generate_report(state: ReviewState) -> dict[str, Any]:
    started = state.get("started_at") or time.time()
    elapsed = time.time() - started
    status = state.get("status", "success")
    warnings = state.get("warnings") or []

    if status == "input_error":
        markdown = (
            "# PR Review\n\n"
            f"**Input error:** {state.get('error') or 'invalid diff.'}\n"
        )
        return {"review_markdown": markdown, "elapsed_seconds": elapsed}

    if status in {"reviewer_failed", "verifier_failed"}:
        return {
            "review_markdown": _incomplete_report(state, status, warnings),
            "elapsed_seconds": elapsed,
        }

    findings = state.get("verified_findings") or []
    lines: list[str] = ["# PR Review", ""]

    if status == "partial":
        lines.append(
            "The review completed with warnings. Some candidate findings or "
            "checks could not be evaluated, so this is not a clean bill of health."
        )
        lines.append("")

    if findings:
        lines.extend(_findings_sections(findings))
    elif status == "partial":
        lines.append("No findings could be verified from this review.")
        lines.append("")
    else:
        lines.append("No verified defects found.")
        lines.append("")

    lines.extend(_warnings_section(warnings))
    lines.extend(_checks_section(state))
    lines.extend(_stats_section(state, findings, elapsed))

    return {"review_markdown": "\n".join(lines), "elapsed_seconds": elapsed}


def _incomplete_report(
    state: ReviewState, status: str, warnings: list[str]
) -> str:
    which = "reviewer" if status == "reviewer_failed" else "verifier"
    lines = [
        "# PR Review",
        "",
        f"Review incomplete: the {which} step did not complete.",
        "",
        "This result does NOT indicate that the change is defect-free.",
        "",
        f"**Detail:** {state.get('error') or 'unknown error'}",
        "",
    ]
    unverified = state.get("unverified_findings") or []
    if unverified:
        lines.append("## Unverified candidate findings")
        lines.append("")
        lines.append(
            "Verification could not be completed. These are shown for reference "
            "and must NOT be treated as confirmed defects."
        )
        lines.append("")
        lines.extend(_findings_sections(unverified, heading_level="###"))
    lines.extend(_warnings_section(warnings))
    return "\n".join(lines)


def _findings_sections(
    findings: list[dict[str, Any]], heading_level: str = "###"
) -> list[str]:
    by_severity: dict[str, list[dict[str, Any]]] = {"high": [], "medium": [], "low": []}
    for finding in findings:
        by_severity.setdefault(finding.get("severity", "low"), []).append(finding)

    lines: list[str] = []
    for severity in ("high", "medium", "low"):
        group = by_severity.get(severity) or []
        if not group:
            continue
        lines.append(f"## {severity.capitalize()} severity")
        lines.append("")
        for finding in group:
            title = finding.get("category", "issue").replace("_", " ").title()
            lines.append(f"{heading_level} {title}")
            file_part = finding.get("file", "?")
            line_part = finding.get("line")
            loc = f"{file_part}, line {line_part}" if line_part else file_part
            lines.append(f"**File:** {loc}")
            lines.append("")
            lines.append(finding.get("explanation", ""))
            lines.append("")
            lines.append("**Suggested fix:**")
            lines.append("")
            lines.append(finding.get("suggested_fix", ""))
            lines.append("")
    return lines


def _warnings_section(warnings: list[str]) -> list[str]:
    if not warnings:
        return []
    lines = ["## Warnings", ""]
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
    return lines


def _checks_section(state: ReviewState) -> list[str]:
    lines = ["## Automated checks", ""]
    checks = state.get("static_check_results") or ["(none)"]
    for item in checks:
        for check_line in item.splitlines() or [item]:
            lines.append(f"- {check_line}")
    lines.append("")
    return lines


def _stats_section(
    state: ReviewState, findings: list[dict[str, Any]], elapsed: float
) -> list[str]:
    return [
        "## Stats",
        "",
        f"- Status: {state.get('status', 'success')}",
        f"- Files reviewed: {len(state.get('changed_files') or [])}",
        f"- Candidate findings: {len(state.get('candidate_findings') or [])}",
        f"- Verified findings: {len(findings)}",
        f"- Findings rejected by verifier: {state.get('rejected_count', 0)}",
        f"- Review retries: {state.get('retry_count', 0)}",
        f"- Elapsed seconds: {elapsed:.2f}",
        "",
    ]


def _raw_finding_count(result: Any) -> int:
    if isinstance(result, ReviewResult):
        return len(result.findings)
    if isinstance(result, dict):
        return len(result.get("findings") or [])
    return 0


def _normalize_findings(result: Any) -> tuple[list[dict[str, Any]], int]:
    """Return (valid findings as dicts, count of malformed findings dropped)."""
    if result is None:
        return [], 0
    if isinstance(result, ReviewResult):
        return [f.model_dump() for f in result.findings], 0
    if isinstance(result, dict) and "findings" in result:
        findings: list[dict[str, Any]] = []
        malformed = 0
        for item in result["findings"] or []:
            if isinstance(item, Finding):
                findings.append(item.model_dump())
            elif isinstance(item, dict):
                try:
                    findings.append(Finding.model_validate(item).model_dump())
                except Exception:  # noqa: BLE001
                    malformed += 1
            else:
                malformed += 1
        return findings, malformed
    return [], 0


__all__ = [
    "generate_report",
    "identify_files",
    "load_diff",
    "load_diff_from_file",
    "normalize_diff_path",
    "prepare_retry",
    "review_code",
    "run_checks",
    "set_llm_factories",
    "verify_findings",
]
