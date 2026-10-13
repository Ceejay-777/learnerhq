"""
Message builders and schema constants for AI provider calls.

Pure functions — no provider logic, no imports from providers.py.
Each builder returns a list[dict] ready for chat completions.
"""

import json

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "accuracy": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["pass", "note"],
        },
        "audience_fit": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["pass", "note"],
        },
        "relevance": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["pass", "note"],
        },
        "completeness": {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "note": {"type": "string"},
            },
            "required": ["pass", "note"],
        },
        "overall_passed": {"type": "boolean"},
    },
    "required": [
        "accuracy",
        "audience_fit",
        "relevance",
        "completeness",
        "overall_passed",
    ],
}

TOPIC_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "resource_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["article", "video", "book", "course", "podcast"],
                    },
                },
                "required": ["title", "url", "type"],
            },
        },
    },
    "required": ["summary", "resource_links"],
}

RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["resolve", "create", "narrow"]},
        "subject_id": {
            "type": "integer",
            "description": "ID of the matched subject (only for resolve action)",
        },
        "standardized_name": {
            "type": "string",
            "description": "Standardized name for a new subject (only for create action)",
        },
        "suggestion": {
            "type": "string",
            "description": "Suggestion to narrow the query (only for narrow action)",
        },
    },
    "required": ["action"],
}

STANDARDIZE_NAME_SCHEMA = {
    "type": "object",
    "properties": {
        "standardized_name": {
            "type": "string",
            "description": "Properly formatted subject name (e.g. 'ww2' -> 'World War II')",
        },
        "is_specific": {
            "type": "boolean",
            "description": "True if the subject is specific enough to be learnable; false if too broad",
        },
        "suggestion": {
            "type": "string",
            "description": "If not specific, suggest 2-3 narrower alternatives",
        },
    },
    "required": ["standardized_name", "is_specific"],
}


# ---------------------------------------------------------------------------
# System instructions
# ---------------------------------------------------------------------------

REVIEW_SYSTEM_INSTRUCTION = (
    "You are a senior editor reviewing educational content for a learning platform. "
    "Evaluate each axis independently. Be precise -- flag real problems, not stylistic preferences. "
    "A axis passes unless there is a concrete, identifiable problem."
)

RANK_RESOLVE_SYSTEM_INSTRUCTION = (
    "You are a curriculum librarian matching a learner's free-text subject input against "
    "an existing catalog. Your job is to decide whether the input refers to a subject already "
    "in the catalog, needs a new entry, or is too broad to use as-is.\n\n"
    "Guidelines:\n"
    "- Match based on semantic equivalence, not exact string. 'WW2' = 'World War II'.\n"
    "- A similarity score of 0.85+ almost always means it's the same subject -- resolve.\n"
    "- A similarity score below 0.4 means it's likely unrelated -- create new.\n"
    "- Overly broad inputs like 'History', 'Science', or 'Music' should trigger 'narrow' "
    "with a suggestion listing 2-3 specific alternatives.\n"
    "- When creating, produce a properly capitalized, specific standardized name."
)

STANDARDIZE_NAME_SYSTEM_INSTRUCTION = (
    "You are a curriculum designer. Given a raw subject name from a learner, "
    "produce a properly formatted, specific standardized name suitable for a course catalog. "
    "If the input is too broad to be a learnable subject on its own, say so."
)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def build_generation_messages(
    prompt: str,
    system_instruction: str | None = None,
    output_schema: dict | None = None,
) -> list[dict]:
    """Build messages for content generation."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
        
    user_content = prompt
    if output_schema:
        user_content += (
            f"\n\nRespond with valid JSON matching this schema: "
            f"{json.dumps(output_schema)}"
        )
        
    messages.append({"role": "user", "content": user_content})
    return messages


def build_review_messages(
    content: str,
    criteria: dict | None = None,
) -> list[dict]:
    """Build messages for content review."""
    context = ""
    if criteria:
        context = (
            f"\n\nContext -- Topic: {criteria.get('topic_title', 'unknown')}, "
            f"Subject: {criteria.get('subject_name', 'unknown')}"
        )
    prompt = (
        f"Review this educational content for quality and accuracy.{context}\n\n"
        f"Content:\n{content}\n\n"
        f"Evaluate each axis independently:\n"
        f"1. Accuracy -- any claims that are verifiably wrong or hallucinated?\n"
        f"2. Audience fit -- is the reading level and tone appropriate for general learners?\n"
        f"3. Relevance -- does the content actually cover the stated topic and subject?\n"
        f"4. Completeness -- is anything essential missing that would leave a learner confused?\n\n"
        f"Set overall_passed to true only if ALL four axes pass.\n\n"
        f"Respond with valid JSON matching this schema: {json.dumps(REVIEW_SCHEMA)}"
    )
    return [
        {"role": "system", "content": REVIEW_SYSTEM_INSTRUCTION},
        {"role": "user", "content": prompt},
    ]


def build_resolve_messages(
    input_text: str,
    candidates: list[dict],
    instruction: str | None = None,
) -> list[dict]:
    """Build messages for subject resolution."""
    prompt = (
        f'Learner\'s input: "{input_text}"\n\n'
        f"Existing catalog entries (with pgvector cosine similarity scores):\n"
        f"{json.dumps(candidates, indent=2)}"
    )
    
    if instruction:
        prompt = f"{instruction}\n\n{prompt}"
        
    prompt += (
        f"\n\nRespond with JSON matching this schema: {json.dumps(RESOLVE_SCHEMA)}"
    )
    
    return [
        {"role": "system", "content": RANK_RESOLVE_SYSTEM_INSTRUCTION},
        {"role": "user", "content": prompt},
    ]


def build_standardize_messages(raw_input: str) -> list[dict]:
    """Build messages for subject name standardization."""
    prompt = (
        f'Standardize this learning subject name: "{raw_input}"\n\n'
        f"Rules:\n"
        f"- Fix capitalization, spelling, and formatting (e.g. 'ww2' -> 'World War II', "
        f"'python for beginners' -> 'Python for Beginners').\n"
        f"- If the input is too broad to be a single learnable subject (e.g. 'History', "
        f"'Science', 'Music'), set is_specific=false and provide 2-3 narrower suggestions e.g. 'History of World War II'.\n"
        f"- If it is adequately specific, set is_specific=true and return the best standardized name.\n\n"
        f"Respond with valid JSON matching this schema: {json.dumps(STANDARDIZE_NAME_SCHEMA)}"
    )
    return [
        {"role": "system", "content": STANDARDIZE_NAME_SYSTEM_INSTRUCTION},
        {"role": "user", "content": prompt},
    ]
