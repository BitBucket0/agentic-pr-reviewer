"""Provider resolution and model-selection tests."""

from __future__ import annotations

import builtins

import pytest

from reviewer.providers import (
    PROVIDERS,
    ProviderError,
    build_llm,
    detected_providers,
    resolve_model,
    resolve_provider,
)

_ALL_ENV = [p.env_key for p in PROVIDERS.values()] + [
    "PR_REVIEWER_PROVIDER",
    "PR_REVIEWER_MODEL",
    "OPENAI_MODEL",
]


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    for var in _ALL_ENV:
        monkeypatch.delenv(var, raising=False)
    yield


def test_single_key_auto_detects(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai")
    assert resolve_provider().name == "openai"


def test_single_anthropic_key_auto_detects(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-anthropic")
    assert detected_providers() == ["anthropic"]
    assert resolve_provider().name == "anthropic"


def test_no_key_errors():
    with pytest.raises(ProviderError, match="No LLM API key"):
        resolve_provider()


def test_multiple_keys_require_explicit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real")
    with pytest.raises(ProviderError, match="Multiple provider keys"):
        resolve_provider()


def test_explicit_provider_overrides_detection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real")
    assert resolve_provider("anthropic").name == "anthropic"


def test_explicit_provider_without_key_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY is not set"):
        resolve_provider("anthropic")


def test_placeholder_key_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-your-key-here")
    with pytest.raises(ProviderError, match="No LLM API key"):
        resolve_provider()


def test_unknown_provider_errors(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    with pytest.raises(ProviderError, match="Unknown provider"):
        resolve_provider("gpt5")


def test_resolve_model_defaults_and_overrides(monkeypatch):
    openai = PROVIDERS["openai"]
    assert resolve_model(openai) == "gpt-4o-mini"
    assert resolve_model(openai, "gpt-4o") == "gpt-4o"
    monkeypatch.setenv("PR_REVIEWER_MODEL", "gpt-4.1")
    assert resolve_model(openai) == "gpt-4.1"


def test_resolve_model_openai_backcompat(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert resolve_model(PROVIDERS["openai"]) == "gpt-4o"
    # Back-compat var only applies to openai.
    assert resolve_model(PROVIDERS["anthropic"]) == "claude-3-5-sonnet-latest"


def test_build_llm_missing_package_gives_install_hint(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain_anthropic":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ProviderError, match=r"agentic-pr-reviewer\[anthropic\]"):
        build_llm(object, provider="anthropic")
