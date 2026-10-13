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
        generate_models=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite"],
        review_models=[],
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        generate_models=[],
        review_models=["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
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
        system_instruction=system_msg,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    response = model_obj.generate_content(user_msg)
    return json.loads(response.text)
