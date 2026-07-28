"""Compile the LangGraph PR review workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from reviewer.nodes import (
    generate_report,
    identify_files,
    load_diff,
    prepare_retry,
    review_code,
    run_checks,
    verify_findings,
)
from reviewer.routes import route_after_review, route_after_verification
from reviewer.state import ReviewState


def build_graph():
    """Assemble and compile the review StateGraph."""
    builder = StateGraph(ReviewState)

    builder.add_node("load_diff", load_diff)
    builder.add_node("identify_files", identify_files)
    builder.add_node("run_checks", run_checks)
    builder.add_node("review_code", review_code)
    builder.add_node("verify_findings", verify_findings)
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node("generate_report", generate_report)

    builder.add_edge(START, "load_diff")
    builder.add_edge("load_diff", "identify_files")
    builder.add_edge("identify_files", "run_checks")
    builder.add_edge("run_checks", "review_code")
    builder.add_conditional_edges(
        "review_code",
        route_after_review,
        {
            "verify": "verify_findings",
            "report": "generate_report",
        },
    )
    builder.add_conditional_edges(
        "verify_findings",
        route_after_verification,
        {
            "retry": "prepare_retry",
            "finish": "generate_report",
        },
    )
    builder.add_edge("prepare_retry", "review_code")
    builder.add_edge("generate_report", END)

    return builder.compile()
