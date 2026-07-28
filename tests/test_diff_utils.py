"""Tests for diff loading and parsing."""

from pathlib import Path

import pytest

from reviewer.diff_utils import (
    DiffError,
    extract_changed_files,
    load_diff_from_file,
    validate_diff,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def test_validate_empty_diff_fails():
    with pytest.raises(DiffError, match="empty"):
        validate_diff("   \n  ")


def test_validate_oversized_diff_fails():
    with pytest.raises(DiffError, match="too large"):
        validate_diff("x" * 100_001)


def test_extract_changed_files_from_buggy_example():
    diff = (EXAMPLES / "buggy_null.diff").read_text()
    files = extract_changed_files(diff)
    assert files == ["user_service.py"]


def test_load_diff_from_file():
    text = load_diff_from_file(EXAMPLES / "clean_change.diff")
    assert "greeting.py" in text
    assert extract_changed_files(text) == ["greeting.py"]
