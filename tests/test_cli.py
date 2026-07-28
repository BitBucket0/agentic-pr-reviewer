"""CLI-level tests: exit codes and output file behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from reviewer.cli import main
from reviewer.nodes import set_llm_factories
from reviewer.schemas import ReviewResult, VerificationResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class _FakeLLM:
    def __init__(self, result):
        self.result = result

    def invoke(self, _messages):
        return self.result


@pytest.fixture(autouse=True)
def _env_and_factories(monkeypatch):
    # Neutralize .env discovery so tests do not depend on the host machine.
    monkeypatch.setattr("reviewer.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("reviewer.cli.find_dotenv", lambda *a, **k: "")
    # A real-looking key so the CLI guard passes.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-not-real")
    set_llm_factories(None, None)
    yield
    set_llm_factories(None, None)


def _clean_factories():
    set_llm_factories(
        lambda: _FakeLLM(ReviewResult(findings=[])),
        lambda: _FakeLLM(VerificationResult()),
    )


def test_exit_zero_on_success():
    _clean_factories()
    code = main(["--diff", str(EXAMPLES / "clean_change.diff")])
    assert code == 0


def test_exit_two_on_empty_diff(tmp_path):
    _clean_factories()
    empty = tmp_path / "empty.diff"
    empty.write_text("   \n")
    code = main(["--diff", str(empty)])
    assert code == 2


def test_exit_two_on_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _clean_factories()
    code = main(["--diff", str(EXAMPLES / "clean_change.diff")])
    assert code == 2


def test_exit_one_on_reviewer_failure():
    def boom():
        raise RuntimeError("model down")

    set_llm_factories(boom, lambda: _FakeLLM(VerificationResult()))
    code = main(["--diff", str(EXAMPLES / "clean_change.diff")])
    assert code == 1


def test_output_file_written_on_failure(tmp_path):
    def boom():
        raise RuntimeError("model down")

    set_llm_factories(boom, lambda: _FakeLLM(VerificationResult()))
    out = tmp_path / "review.md"
    code = main(["--diff", str(EXAMPLES / "clean_change.diff"), "--output", str(out)])
    assert code == 1
    assert out.exists()
    assert "review incomplete" in out.read_text().lower()
