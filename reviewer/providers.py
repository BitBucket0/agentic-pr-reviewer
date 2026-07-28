"""Provider resolution and LLM construction.

The reviewer picks an LLM provider from whichever API key is present, so a user
only has to set (for example) ``ANTHROPIC_API_KEY`` to review with Claude. An
explicit ``--provider`` / ``PR_REVIEWER_PROVIDER`` always wins, and the model can
be overridden with ``--model`` / ``PR_REVIEWER_MODEL``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class ProviderError(Exception):
    """Raised when a provider/model/key cannot be resolved or constructed."""


@dataclass(frozen=True)
class Provider:
    name: str
    env_key: str
    default_model: str
    module: str
    class_name: str
    extra: str  # pip extra that installs `module` ("" means bundled by default)


# Order matters: used as the priority when exactly one key is present is not the
# case is handled explicitly, but the list also defines display order.
PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        name="openai",
        env_key="OPENAI_API_KEY",
        default_model="gpt-4o-mini",
        module="langchain_openai",
        class_name="ChatOpenAI",
        extra="",
    ),
    "anthropic": Provider(
        name="anthropic",
        env_key="ANTHROPIC_API_KEY",
        default_model="claude-3-5-sonnet-latest",
        module="langchain_anthropic",
        class_name="ChatAnthropic",
        extra="anthropic",
    ),
    "google": Provider(
        name="google",
        env_key="GOOGLE_API_KEY",
        default_model="gemini-1.5-flash",
        module="langchain_google_genai",
        class_name="ChatGoogleGenerativeAI",
        extra="google",
    ),
    "groq": Provider(
        name="groq",
        env_key="GROQ_API_KEY",
        default_model="llama-3.3-70b-versatile",
        module="langchain_groq",
        class_name="ChatGroq",
        extra="groq",
    ),
    "mistral": Provider(
        name="mistral",
        env_key="MISTRAL_API_KEY",
        default_model="mistral-small-latest",
        module="langchain_mistralai",
        class_name="ChatMistralAI",
        extra="mistral",
    ),
}

PROVIDER_NAMES = tuple(PROVIDERS)


def _key_is_set(value: str | None) -> bool:
    """A key counts as set only if it is non-empty and not a placeholder."""
    if not value:
        return False
    lowered = value.strip().lower()
    if not lowered:
        return False
    return "your-key" not in lowered and "here" not in lowered


def detected_providers() -> list[str]:
    """Provider names whose API key is present (and not a placeholder)."""
    return [
        name
        for name, provider in PROVIDERS.items()
        if _key_is_set(os.getenv(provider.env_key))
    ]


def resolve_provider(explicit: str | None = None) -> Provider:
    """Choose the provider from an explicit selection or the environment.

    Precedence: ``explicit`` arg -> ``PR_REVIEWER_PROVIDER`` env -> auto-detect.
    Auto-detect requires exactly one provider key to be present.
    """
    choice = explicit or os.getenv("PR_REVIEWER_PROVIDER")
    if choice:
        choice = choice.strip().lower()
        if choice == "auto":
            choice = None
        elif choice not in PROVIDERS:
            raise ProviderError(
                f"Unknown provider '{choice}'. "
                f"Choose one of: {', '.join(PROVIDER_NAMES)}."
            )
        else:
            provider = PROVIDERS[choice]
            if not _key_is_set(os.getenv(provider.env_key)):
                raise ProviderError(
                    f"Provider '{choice}' selected but {provider.env_key} is not set."
                )
            return provider

    detected = detected_providers()
    if not detected:
        keys = ", ".join(p.env_key for p in PROVIDERS.values())
        raise ProviderError(
            "No LLM API key found. Set one of the following (or add it to .env): "
            f"{keys}."
        )
    if len(detected) > 1:
        raise ProviderError(
            "Multiple provider keys are set "
            f"({', '.join(detected)}). Choose one with --provider "
            "or PR_REVIEWER_PROVIDER."
        )
    return PROVIDERS[detected[0]]


def resolve_model(provider: Provider, explicit: str | None = None) -> str:
    """Resolve the model name for a provider."""
    model = explicit or os.getenv("PR_REVIEWER_MODEL")
    if not model and provider.name == "openai":
        model = os.getenv("OPENAI_MODEL")  # back-compat
    return model or provider.default_model


def _load_chat_class(provider: Provider) -> Any:
    try:
        module = __import__(provider.module, fromlist=[provider.class_name])
    except ImportError as exc:  # provider integration not installed
        hint = (
            f'pip install "agentic-pr-reviewer[{provider.extra}]"'
            if provider.extra
            else f"pip install {provider.module}"
        )
        raise ProviderError(
            f"Provider '{provider.name}' requires the '{provider.module}' package. "
            f"Install it with: {hint}"
        ) from exc
    return getattr(module, provider.class_name)


def build_llm(
    schema: Any,
    provider: str | None = None,
    model: str | None = None,
) -> Any:
    """Construct a structured-output chat model for the resolved provider."""
    resolved = resolve_provider(provider)
    model_name = resolve_model(resolved, model)
    chat_cls = _load_chat_class(resolved)
    try:
        llm = chat_cls(model=model_name, temperature=0)
    except Exception as exc:
        raise ProviderError(
            f"Could not initialize {resolved.name} model '{model_name}': {exc}"
        ) from exc
    return llm.with_structured_output(schema)
