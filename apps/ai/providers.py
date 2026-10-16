"""
Provider configuration, client creation, model verification,
and response format builders.

Each provider (Groq, Gemini) has its own config and response format
strategy. The rest of the codebase never constructs clients or response
formats directly -- it goes through this module.
"""

import json
import logging
import os
from dataclasses import dataclass

from .exceptions import ProviderError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    generate_models: list[str]
    review_models: list[str]


PROVIDERS: dict[str, ProviderConfig] = {
    "gemini": ProviderConfig(
        name="gemini",
        base_url="",
        api_key_env="GEMINI_API_KEY",
        # Text generation moved to Groq; Gemini is only used for embeddings.
        generate_models=[],
        review_models=[],
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        generate_models=["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
        review_models=["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    ),
}


# ---------------------------------------------------------------------------
# Client creation (OpenAI-compatible providers only)
# ---------------------------------------------------------------------------


def get_client(config: ProviderConfig):
    """Return an httpx client pointed at the given provider's base URL."""
    import httpx

    api_key = os.environ.get(config.api_key_env, "")
    return httpx.Client(
        base_url=config.base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=120.0,
    )


# ---------------------------------------------------------------------------
# Model verification (canary check, cached per worker lifecycle)
# ---------------------------------------------------------------------------

_verified: dict[str, list[str]] = {}


def verify_provider_models(config: ProviderConfig) -> list[str]:
    """Hit /models, confirm configured model IDs exist. Cache result.

    Gemini is skipped -- the native SDK validates models at call time.
    """
    if config.name in _verified:
        return _verified[config.name]

    if config.name == "gemini":
        _verified[config.name] = config.generate_models + config.review_models
        return _verified[config.name]

    import httpx

    api_key = os.environ.get(config.api_key_env, "")
    url = f"{config.base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = httpx.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    model_ids = [m["id"] for m in data.get("data", [])]
    _verified[config.name] = model_ids

    all_models = config.generate_models + config.review_models
    missing = [m for m in all_models if m not in model_ids]
    if missing:
        raise ProviderError(
            f"Provider {config.name}: models not available: {missing}",
            provider=config.name,
            recoverable=False,
        )

    logger.info("Provider %s verified: %s", config.name, all_models)
    return model_ids


def reset_verification() -> None:
    """Clear verification cache. Used in tests."""
    _verified.clear()


# ---------------------------------------------------------------------------
# Response format builders (per-provider structured output strategy)
# ---------------------------------------------------------------------------


def build_groq_response_format(schema: dict, name: str) -> dict:
    """Groq best-effort JSON schema mode -- validates after generation."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": False,
            "schema": schema,
        },
    }


def build_gemini_response_format():
    """Gemini uses native SDK -- returns None, handled by genai library."""
    return


def get_response_format(config_name: str, schema: dict, schema_name: str):
    """Return the correct response_format for the given provider."""
    if config_name == "groq":
        return build_groq_response_format(schema, schema_name)
    if config_name == "gemini":
        return build_gemini_response_format()
    return None


# ---------------------------------------------------------------------------
# Gemini native call (uses google-generativeai SDK, not OpenAI-compatible)
# ---------------------------------------------------------------------------


# Keywords the Gemini native SDK accepts in a response schema. Anything else
# (minItems, maxItems, minimum, maximum, additionalProperties, ...) is rejected
# with "Unknown field for Schema". Size and range limits stay in the prompt text
# and in the caller's post-generation validators.
_GEMINI_SCHEMA_KEYWORDS = frozenset(
    {"type", "format", "description", "nullable", "enum", "items", "properties", "required"}
)


def sanitize_schema_for_gemini(schema: dict) -> dict:
    """Return a copy of a JSON schema containing only Gemini-supported keywords."""
    cleaned = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYWORDS:
            continue
        if key == "properties":
            cleaned[key] = {
                name: sanitize_schema_for_gemini(sub) for name, sub in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            cleaned[key] = sanitize_schema_for_gemini(value)
        else:
            cleaned[key] = value
    return cleaned


def call_gemini_native(
    messages: list[dict],
    schema: dict,
    model: str = "gemini-3.8-flash",
) -> dict:
    """Call Gemini via native SDK. Works for generation, review, resolve, etc."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

    model_obj = genai.GenerativeModel(
        model_name=model,
        system_instruction=system_msg or None,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=sanitize_schema_for_gemini(schema),
        ),
    )
    response = model_obj.generate_content(user_msg)
    return json.loads(response.text)
