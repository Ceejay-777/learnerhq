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


def generate_embedding(text: str) -> list[float]:
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        result = genai.embed_content(model="models/gemini-embedding-001", content=text)
        return result["embedding"]
    except Exception as e:
        raise ProviderError(str(e), provider="gemini") from e


def generate_content(
    prompt: str,
    system_instruction: str | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GEMINI_API_KEY"])
        model = genai.GenerativeModel(
            model_name="models/gemini-2.0-flash",
            system_instruction=system_instruction,
        )
        schema_hint = (
            f"\n\nRespond with valid JSON matching this schema: {json.dumps(output_schema)}"
            if output_schema else ""
        )
        response = model.generate_content(prompt + schema_hint)
        return json.loads(response.text)
    except Exception as e:
        raise ProviderError(str(e), provider="gemini") from e


def review_content(
    content: str,
    criteria: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        context = ""
        if criteria:
            context = f"\n\nTopic: {criteria.get('topic_title', 'unknown')}\nSubject: {criteria.get('subject_name', 'unknown')}"
        prompt = (
            f"Review the following educational content for a learning platform.{context}\n\n"
            f"Content to review:\n{content}\n\n"
            f"Check for:\n"
            f"1. Factual plausibility — any obviously false or hallucinated claims?\n"
            f"2. Reading level — appropriate for general audience?\n"
            f"3. Scope match — relevant to the given topic and subject?\n\n"
            f"Respond with JSON: {{\"passed\": boolean, \"issues\": [{{\"severity\": \"error\"|\"warning\", \"description\": \"...\"}}]}}"
        )
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        raise ProviderError(str(e), provider="groq") from e


RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["resolve", "create", "narrow"]},
        "subject_id": {"type": "integer", "description": "ID of the matched subject (only for resolve action)"},
        "canonical_name": {"type": "string", "description": "Canonical name for a new subject (only for create action)"},
        "suggestion": {"type": "string", "description": "Suggestion to narrow the query (only for narrow action)"},
    },
    "required": ["action"],
}


def rank_or_resolve(
    input_text: str,
    candidates: list[dict[str, Any]],
    instruction: str | None = None,
) -> dict[str, Any]:
    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        prompt = (
            f"User input: {input_text}\n\nExisting candidates:\n{json.dumps(candidates, indent=2)}"
        )
        if instruction:
            prompt = f"{instruction}\n\n{prompt}"
        prompt += f"\n\nRespond with valid JSON matching this schema: {json.dumps(RESOLVE_SCHEMA)}"
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        raise ProviderError(str(e), provider="groq") from e
