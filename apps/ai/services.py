"""
AI Provider Service Layer

One function per role — generate, review, embed, rank/resolve — keeping
provider-specific code contained here so swapping providers later is a
config change, not a rewrite.

Each function accepts clean domain data and an output schema definition,
and returns the provider's response already validated against that schema.
No provider SDK calls are made outside these functions.
"""

import json
import os
from typing import Any

from .exceptions import ProviderError

try:
    from groq import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError as GroqInternalError,
        RateLimitError,
    )
    from google.api_core.exceptions import (
        BadGateway,
        DeadlineExceeded,
        GatewayTimeout,
        InternalServerError as GoogleInternalError,
        ResourceExhausted,
        ServiceUnavailable,
    )
    _RETRYABLE_EXC_CLASSES: tuple = (
        APIConnectionError, APITimeoutError, GroqInternalError, RateLimitError,
        BadGateway, DeadlineExceeded, GatewayTimeout, GoogleInternalError,
        ResourceExhausted, ServiceUnavailable,
    )
except ImportError:
    _RETRYABLE_EXC_CLASSES = ()


def _is_retryable_error(exc: Exception) -> bool:
    return isinstance(exc, _RETRYABLE_EXC_CLASSES)


def generate_embedding(text: str) -> list[float]:
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        result = genai.embed_content(model="models/gemini-embedding-001", content=text)
        return result["embedding"]
    except Exception as e:
        raise ProviderError(str(e), provider="gemini", recoverable=_is_retryable_error(e)) from e


def generate_content(
    prompt: str,
    system_instruction: str | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        user_content = prompt
        if output_schema:
            user_content += f"\n\nRespond with valid JSON matching this schema: {json.dumps(output_schema)}"
        messages.append({"role": "user", "content": user_content})
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        raise ProviderError(str(e), provider="groq", recoverable=_is_retryable_error(e)) from e


REVIEW_SYSTEM_INSTRUCTION = (
    "You are a senior editor reviewing educational content for a learning platform. "
    "Your role is to catch factual errors, hallucinated claims, and content that misses the mark. "
    "Be precise — flag real problems, not stylistic preferences. "
    "A passing review means the content is accurate, appropriate for learners, and on-topic."
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning"]},
                    "description": {"type": "string"},
                },
                "required": ["severity", "description"],
            },
        },
    },
    "required": ["passed"],
}


def review_content(
    content: str,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        context = ""
        if criteria:
            context = (
                f"\n\nContext — Topic: {criteria.get('topic_title', 'unknown')}, "
                f"Subject: {criteria.get('subject_name', 'unknown')}"
            )
        messages = [
            {"role": "system", "content": REVIEW_SYSTEM_INSTRUCTION},
            {"role": "user", "content": (
                f"Review this educational content for quality and accuracy.{context}\n\n"
                f"Content:\n{content}\n\n"
                f"Evaluate:\n"
                f"1. Factual accuracy — any claims that are verifiably wrong or hallucinated?\n"
                f"2. Audience fit — is the reading level and tone appropriate for general learners?\n"
                f"3. Topic relevance — does the content actually cover the stated topic and subject, "
                f"or does it drift into unrelated territory?\n"
                f"4. Completeness — is anything essential missing that would leave a learner confused?\n\n"
                f"Respond with JSON matching this schema: {json.dumps(REVIEW_SCHEMA)}"
            )},
        ]
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        raise ProviderError(str(e), provider="groq", recoverable=_is_retryable_error(e)) from e


RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["resolve", "create", "narrow"]},
        "subject_id": {"type": "integer", "description": "ID of the matched subject (only for resolve action)"},
        "standardized_name": {"type": "string", "description": "Standardized name for a new subject (only for create action)"},
        "suggestion": {"type": "string", "description": "Suggestion to narrow the query (only for narrow action)"},
    },
    "required": ["action"],
}

RANK_RESOLVE_SYSTEM_INSTRUCTION = (
    "You are a curriculum librarian matching a learner's free-text subject input against "
    "an existing catalog. Your job is to decide whether the input refers to a subject already "
    "in the catalog, needs a new entry, or is too broad to use as-is.\n\n"
    "Guidelines:\n"
    "- Match based on semantic equivalence, not exact string. 'WW2' = 'World War II'.\n"
    "- A similarity score of 0.85+ almost always means it's the same subject — resolve.\n"
    "- A similarity score below 0.4 means it's likely unrelated — create new.\n"
    "- Overly broad inputs like 'History', 'Science', or 'Music' should trigger 'narrow' "
    "with a suggestion listing 2-3 specific alternatives.\n"
    "- When creating, produce a properly capitalized, specific standardized name."
)


def rank_or_resolve(
    input_text: str,
    candidates: list[dict[str, Any]],
    instruction: str | None = None,
) -> dict[str, Any]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        prompt = (
            f"Learner's input: \"{input_text}\"\n\n"
            f"Existing catalog entries (with pgvector cosine similarity scores):\n"
            f"{json.dumps(candidates, indent=2)}"
        )
        if instruction:
            prompt = f"{instruction}\n\n{prompt}"
        prompt += f"\n\nRespond with JSON matching this schema: {json.dumps(RESOLVE_SCHEMA)}"
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": RANK_RESOLVE_SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        raise ProviderError(str(e), provider="groq", recoverable=_is_retryable_error(e)) from e


STANDARDIZE_NAME_SYSTEM_INSTRUCTION = (
    "You are a curriculum designer. Given a raw subject name from a learner, "
    "produce a properly formatted, specific standardized name suitable for a course catalog. "
    "If the input is too broad to be a learnable subject on its own, say so."
)

STANDARDIZE_NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "standardized_name": {"type": "string", "description": "Properly formatted subject name (e.g. 'ww2' → 'World War II')"},
        "is_specific": {"type": "boolean", "description": "True if the subject is specific enough to be learnable; false if too broad"},
        "suggestion": {"type": "string", "description": "If not specific, suggest 2-3 narrower alternatives (e.g. 'Try: Cellular Biology, Genetics, or Ecology')"},
    },
    "required": ["standardized_name", "is_specific"],
}


def standardize_subject_name(raw_input: str) -> dict[str, Any]:
    try:
        return generate_content(
            prompt=(
                f"Standardize this learning subject name: \"{raw_input}\"\n\n"
                f"Rules:\n"
                f"- Fix capitalization, spelling, and formatting (e.g. 'ww2' → 'World War II', "
                f"'python for beginners' → 'Python for Beginners').\n"
                f"- If the input is too broad to be a single learnable subject (e.g. 'History', "
                f"'Science', 'Music'), set is_specific=false and provide 2-3 narrower suggestions.\n"
                f"- If it is adequately specific, set is_specific=true and return the best standardized name."
            ),
            system_instruction=STANDARDIZE_NAME_SYSTEM_INSTRUCTION,
            output_schema=STANDARDIZE_NAME_SCHEMA,
        )
    except Exception as e:
        raise ProviderError(str(e), provider="groq") from e
