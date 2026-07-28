"""Tests for diff loading and parsing."""

from pathlib import Path

import pytest

from reviewer.diff_utils import (
    DiffError,
    extract_changed_files,
    load_diff_from_file,
    truncate_to_limit,
    validate_diff,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def test_validate_empty_diff_fails():
    with pytest.raises(DiffError, match="empty"):
        validate_diff("   \n  ")


def test_validate_large_diff_no_longer_raises():
    # Oversized diffs are handled by truncation, not rejection.
    assert validate_diff("x" * 100_001) == "x" * 100_001


def test_truncate_to_limit_truncates_large_diff():
    text = "line\n" * 1000
    out, truncated = truncate_to_limit(text, 100)
    assert truncated is True
    assert len(out) < len(text)
    assert "truncated" in out
    assert "review is partial" in out


def test_truncate_to_limit_leaves_small_diff_unchanged():
    out, truncated = truncate_to_limit("small diff", 100)
    assert truncated is False
    assert out == "small diff"


def test_extract_changed_files_from_buggy_example():
    diff = (EXAMPLES / "buggy_null.diff").read_text()
    files = extract_changed_files(diff)
    assert files == ["user_service.py"]


def test_load_diff_from_file():
    text = load_diff_from_file(EXAMPLES / "clean_change.diff")
    assert "greeting.py" in text
    assert extract_changed_files(text) == ["greeting.py"]
