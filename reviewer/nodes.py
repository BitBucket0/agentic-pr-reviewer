"""Graph nodes: deterministic helpers and LLM review/verify steps."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from reviewer.checks import run_static_checks
from reviewer.diff_utils import (
    DiffError,
    extract_changed_files,
    load_diff_from_file,
    load_diff_from_git,
    validate_diff,
)
from reviewer.prompts import REVIEW_SYSTEM, REVIEW_USER, VERIFY_SYSTEM, VERIFY_USER
from reviewer.schemas import Finding, ReviewResult, VerificationResult
from reviewer.state import ReviewState

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


def _default_model_name() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _build_review_llm() -> Any:
    if _review_llm_factory is not None:
        return _review_llm_factory()
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=_default_model_name(), temperature=0).with_structured_output(
        ReviewResult
    )


def _build_verify_llm() -> Any:
    if _verify_llm_factory is not None:
        return _verify_llm_factory()
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=_default_model_name(), temperature=0).with_structured_output(
        VerificationResult
    )


def load_diff(state: ReviewState) -> dict[str, Any]:
    """Validate / load the diff into state. Expects `diff` already set by CLI,
    or `repo_path` + `base_ref` to load from git.
    """
    updates: dict[str, Any] = {
        "started_at": state.get("started_at") or time.time(),
        "retry_count": state.get("retry_count", 0),
        "candidate_findings": [],
        "verified_findings": [],
        "rejected_count": 0,
        "needs_retry": False,
        "verification_feedback": "",
        "static_check_results": [],
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
        updates["diff"] = state.get("diff") or ""
        updates["changed_files"] = []
    return updates


def identify_files(state: ReviewState) -> dict[str, Any]:
    if state.get("error"):
        return {}
    files = extract_changed_files(state.get("diff", ""))
    if not files:
        return {
            "error": "Could not extract changed files from the diff.",
            "changed_files": [],
        }
    return {"changed_files": files}


def run_checks(state: ReviewState) -> dict[str, Any]:
    if state.get("error"):
        return {"static_check_results": []}
    results = run_static_checks(
        state.get("repo_path"),
        state.get("changed_files") or [],
    )
    return {"static_check_results": results}


def review_code(state: ReviewState) -> dict[str, Any]:
    if state.get("error"):
        return {"candidate_findings": []}

    llm = _build_review_llm()
    prompt = REVIEW_USER.format(
        changed_files="\n".join(state.get("changed_files") or []) or "(none)",
        static_check_results="\n".join(state.get("static_check_results") or [])
        or "(none)",
        verification_feedback=state.get("verification_feedback") or "(none)",
        diff=state.get("diff", ""),
    )

    try:
        result = llm.invoke(
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        findings = _normalize_findings(result)
    except Exception as exc:  # noqa: BLE001 — surface model failures cleanly
        return {
            "candidate_findings": [],
            "verification_feedback": f"Reviewer failed: {exc}",
            "needs_retry": False,
        }

    return {"candidate_findings": findings}


def verify_findings(state: ReviewState) -> dict[str, Any]:
    if state.get("error"):
        return {
            "verified_findings": [],
            "rejected_count": 0,
            "needs_retry": False,
            "verification_feedback": "",
        }

    candidates = state.get("candidate_findings") or []
    if not candidates:
        return {
            "verified_findings": [],
            "rejected_count": 0,
            "needs_retry": False,
            "verification_feedback": "",
        }

    llm = _build_verify_llm()
    prompt = VERIFY_USER.format(
        diff=state.get("diff", ""),
        static_check_results="\n".join(state.get("static_check_results") or [])
        or "(none)",
        candidate_findings=json.dumps(candidates, indent=2),
    )

    try:
        result = llm.invoke(
            [
                {"role": "system", "content": VERIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        if not isinstance(result, VerificationResult):
            result = VerificationResult.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        # On verifier failure, keep candidates but do not retry forever
        return {
            "verified_findings": candidates,
            "rejected_count": 0,
            "needs_retry": False,
            "verification_feedback": f"Verifier failed: {exc}",
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

    # If the model returned no verdicts, treat as all rejected / weak
    if not result.verdicts:
        rejected = len(candidates)
        accepted = []

    retry_count = state.get("retry_count", 0)
    needs_retry = bool(result.needs_retry) and retry_count < 1 and not accepted

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

    if state.get("error"):
        markdown = f"# PR Review\n\n**Error:** {state['error']}\n"
        return {"review_markdown": markdown, "elapsed_seconds": elapsed}

    findings = state.get("verified_findings") or []
    by_severity: dict[str, list[dict[str, Any]]] = {
        "high": [],
        "medium": [],
        "low": [],
    }
    for finding in findings:
        sev = finding.get("severity", "low")
        by_severity.setdefault(sev, []).append(finding)

    lines: list[str] = ["# PR Review", ""]

    if not findings:
        lines.append("No verified defects found.")
        lines.append("")
    else:
        for severity in ("high", "medium", "low"):
            group = by_severity.get(severity) or []
            if not group:
                continue
            lines.append(f"## {severity.capitalize()} severity")
            lines.append("")
            for finding in group:
                title = finding.get("category", "issue").replace("_", " ").title()
                lines.append(f"### {title}")
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

    lines.append("## Automated checks")
    lines.append("")
    checks = state.get("static_check_results") or ["(none)"]
    for item in checks:
        for check_line in item.splitlines() or [item]:
            lines.append(f"- {check_line}")
    lines.append("")

    lines.append("## Stats")
    lines.append("")
    lines.append(f"- Files reviewed: {len(state.get('changed_files') or [])}")
    lines.append(f"- Candidate findings: {len(state.get('candidate_findings') or [])}")
    lines.append(f"- Verified findings: {len(findings)}")
    lines.append(f"- Findings rejected by verifier: {state.get('rejected_count', 0)}")
    lines.append(f"- Review retries: {state.get('retry_count', 0)}")
    lines.append(f"- Elapsed seconds: {elapsed:.2f}")
    lines.append("")

    return {"review_markdown": "\n".join(lines), "elapsed_seconds": elapsed}


def _normalize_findings(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, ReviewResult):
        return [f.model_dump() for f in result.findings]
    if isinstance(result, dict) and "findings" in result:
        findings = []
        for item in result["findings"] or []:
            if isinstance(item, Finding):
                findings.append(item.model_dump())
            elif isinstance(item, dict):
                try:
                    findings.append(Finding.model_validate(item).model_dump())
                except Exception:  # noqa: BLE001
                    continue
        return findings
    return []


# Re-export for CLI convenience
__all__ = [
    "load_diff",
    "identify_files",
    "run_checks",
    "review_code",
    "verify_findings",
    "prepare_retry",
    "generate_report",
    "set_llm_factories",
    "load_diff_from_file",
]
