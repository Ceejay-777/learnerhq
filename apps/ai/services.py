"""
AI Provider Service Layer

One function per role -- generate, review, embed, rank/resolve -- keeping
provider-specific code contained here so swapping providers later is a
config change, not a rewrite.

Each function accepts clean domain data, delegates to prompts.py for message
building and providers.py for client creation / response format, then
validates the parsed JSON shape before returning.
"""

import json
import logging
import os
import time
from typing import Any

import httpx
import sentry_sdk

from .exceptions import ProviderError
from .prompts import (
    RESOLVE_SCHEMA,
    REVIEW_SCHEMA,
    STANDARDIZE_NAME_SCHEMA,
    TOPIC_CONTENT_SCHEMA,
    build_generation_messages,
    build_resolve_messages,
    build_review_messages,
    build_standardize_messages,
)
from .providers import (
    PROVIDERS,
    ProviderConfig,
    build_groq_response_format,
    call_gemini_native,
    get_client,
    verify_provider_models,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retryable exception classes
# ---------------------------------------------------------------------------

try:
    from google.api_core.exceptions import (
        BadGateway,
        DeadlineExceeded,
        GatewayTimeout,
        ResourceExhausted,
        ServiceUnavailable,
    )
    from google.api_core.exceptions import (
        InternalServerError as GoogleInternalError,
    )

    _GOOGLE_RETRYABLE: tuple = (
        BadGateway,
        DeadlineExceeded,
        GatewayTimeout,
        GoogleInternalError,
        ResourceExhausted,
        ServiceUnavailable,
    )
except ImportError:
    _GOOGLE_RETRYABLE = ()

try:
    import httpx as _httpx

    _HTTPX_RETRYABLE = (
        _httpx.TimeoutException,
        _httpx.ConnectError,
        _httpx.RemoteProtocolError,
    )
except ImportError:
    _HTTPX_RETRYABLE = ()


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, _GOOGLE_RETRYABLE):
        return True
    if isinstance(exc, _HTTPX_RETRYABLE):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
        429,
        502,
        503,
        504,
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# JSON shape validation
# ---------------------------------------------------------------------------


def _validate_shape(data: Any, schema: dict, path: str = "") -> None:
    """Validate parsed JSON matches expected shape.

    Raises GenerationError on mismatch. Covers: missing keys, wrong types,
    non-object top-level values.
    """
    from apps.learning.exceptions import GenerationError

    if not isinstance(data, dict):
        raise GenerationError(
            f"Expected object, got {type(data).__name__}",
            phase="shape_validation",
        )

    required_keys = set(schema.get("required", []))
    for key, prop_schema in schema.get("properties", {}).items():
        full_path = f"{path}.{key}" if path else key

        if key in required_keys and key not in data:
            raise GenerationError(
                f"Missing required key: {full_path}",
                phase="shape_validation",
            )

        if key not in data:
            continue

        expected_type = prop_schema.get("type")
        actual = data[key]

        if expected_type == "string" and not isinstance(actual, str):
            raise GenerationError(
                f"{full_path}: expected string, got {type(actual).__name__}",
                phase="shape_validation",
            )
        elif expected_type == "boolean" and not isinstance(actual, bool):
            raise GenerationError(
                f"{full_path}: expected boolean, got {type(actual).__name__}",
                phase="shape_validation",
            )
        elif expected_type == "array" and not isinstance(actual, list):
            raise GenerationError(
                f"{full_path}: expected array, got {type(actual).__name__}",
                phase="shape_validation",
            )
        elif expected_type == "array" and isinstance(actual, list):
            items_schema = prop_schema.get("items", {})
            if items_schema.get("type") == "object":
                for i, item in enumerate(actual):
                    _validate_shape(item, items_schema, f"{full_path}[{i}]")
        elif expected_type == "object" and isinstance(actual, dict):
            _validate_shape(actual, prop_schema, full_path)
        elif expected_type == "object" and not isinstance(actual, dict):
            raise GenerationError(
                f"{full_path}: expected object, got {type(actual).__name__}",
                phase="shape_validation",
            )


# ---------------------------------------------------------------------------
# Role-to-provider mapping
# ---------------------------------------------------------------------------

# Each role maps to an ordered list of (provider_name, model_id) pairs.
# The dispatcher tries each in order until one succeeds.
_ROLE_PROVIDERS: dict[str, list[tuple[str, str]]] = {
    "generate": [
        ("gemini", "gemini-3.8-flash"),
        ("gemini", "gemini-3.7-flash"),
        ("gemini", "gemini-3.5-flash-lite"),
    ],
    "review": [
        ("groq", "openai/gpt-oss-120b"),
        ("groq", "openai/gpt-oss-20b"),
    ],
    "resolve": [
        ("gemini", "gemini-3.5-flash-lite"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("groq", "openai/gpt-oss-120b"),
    ],
}


# ---------------------------------------------------------------------------
# Core calling functions
# ---------------------------------------------------------------------------


def _call_openai_compatible(
    config: ProviderConfig,
    model: str,
    messages: list[dict],
    response_format: dict | None = None,
) -> dict:
    """Call an OpenAI-compatible provider and return parsed JSON."""
    client = get_client(config)
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if response_format:
        payload["response_format"] = response_format
    resp = client.post("/chat/completions", json=payload)
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def _call_provider(provider_name: str, model: str, messages: list[dict], schema: dict) -> dict:
    """Route to the correct calling mechanism per provider."""
    if provider_name == "gemini":
        return call_gemini_native(messages, schema, model=model)
    elif provider_name == "groq":
        config = PROVIDERS["groq"]
        response_format = build_groq_response_format(schema, "Response")
        return _call_openai_compatible(config, model, messages, response_format)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def _call_with_fallback(role: str, messages: list[dict], schema: dict, schema_name: str) -> dict:
    """Try providers in order for the given role. Raises ProviderError when all fail."""
    chain = _ROLE_PROVIDERS.get(role, [])
    last_error = None

    for provider_name, model_id in chain:
        try:
            result = _call_provider(provider_name, model_id, messages, schema)
            logger.info("%s served by %s/%s", role, provider_name, model_id)
            return result
        except Exception as e:
            logger.warning(
                "%s %s/%s failed: %s", role, provider_name, model_id, e
            )
            sentry_sdk.capture_message(
                f"{role} {provider_name}/{model_id} failed: {e}",
                level="warning",
            )
            last_error = e

    raise ProviderError(
        f"All providers failed for {role}",
        provider=chain[0][0] if chain else "none",
        recoverable=False,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_content(
    prompt: str,
    system_instruction: str | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate educational content. Returns validated dict."""
    messages = build_generation_messages(prompt, system_instruction, output_schema)
    result = _call_with_fallback(
        "generate", messages, TOPIC_CONTENT_SCHEMA, "TopicContent"
    )
    _validate_shape(result, TOPIC_CONTENT_SCHEMA)
    return result


def review_content(
    content: str,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review content against quality rubric. Returns validated dict.

    If all review providers fail, returns a flagged result so the caller
    can handle manual review without raising.
    """
    messages = build_review_messages(content, criteria)
    try:
        result = _call_with_fallback(
            "review", messages, REVIEW_SCHEMA, "ReviewResult"
        )
    except ProviderError:
        logger.warning("All review providers failed, flagging for manual review")
        return {
            "accuracy": {"pass": False, "note": "Review unavailable — all providers failed"},
            "audience_fit": {"pass": False, "note": "Review unavailable — all providers failed"},
            "relevance": {"pass": False, "note": "Review unavailable — all providers failed"},
            "completeness": {"pass": False, "note": "Review unavailable — all providers failed"},
            "overall_passed": False,
        }
    _validate_shape(result, REVIEW_SCHEMA)
    return result


def rank_or_resolve(
    input_text: str,
    candidates: list[dict[str, Any]],
    instruction: str | None = None,
) -> dict[str, Any]:
    """Rank candidates or resolve to a subject. Returns validated dict."""
    messages = build_resolve_messages(input_text, candidates, instruction)
    result = _call_with_fallback("resolve", messages, RESOLVE_SCHEMA, "ResolveResult")
    _validate_shape(result, RESOLVE_SCHEMA)
    return result


def standardize_subject_name(raw_input: str) -> dict[str, Any]:
    """Standardize a raw subject name. Returns validated dict."""
    messages = build_standardize_messages(raw_input)
    result = _call_with_fallback(
        "resolve", messages, STANDARDIZE_NAME_SCHEMA, "StandardizeName"
    )
    _validate_shape(result, STANDARDIZE_NAME_SCHEMA)
    return result


def generate_embedding(text: str) -> list[float]:
    """Generate an embedding vector. Gemini-only, with retry."""
    max_attempts = 3
    last_error = None

    for attempt in range(max_attempts):
        try:
            import google.generativeai as genai

            genai.configure(api_key=os.environ["GEMINI_API_KEY"])
            result = genai.embed_content(
                model="models/gemini-embedding-001", content=text
            )
            return result["embedding"]
        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Embedding attempt %d failed, retrying in %ds: %s",
                    attempt + 1,
                    wait,
                    e,
                )
                time.sleep(wait)

    raise ProviderError(
        str(last_error), provider="gemini", recoverable=_is_retryable_error(last_error)
    )
