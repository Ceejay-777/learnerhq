# Plan: replace Gemini calls with Groq

Date: 2026-10-13 · Status: **chains, per-action effort and token caps implemented 2026-10-14 (uncommitted); link verification and quiz answer-key check deferred** · Supersedes items 1 and 4 of `plan-gemini-and-async-roadmap.md` for generation (the billing and SDK-migration work shrinks to embeddings).

## Why
`docs/gemini-latency-report.md`: the `generate` chain's big Gemini models are slow (thinking tokens, 503 retries up to 600s) and the key is free-tier capped (20 req/day/model). Groq billing is now connected.

## Evidence gathered today (real prompts, real validators, current Groq code path)

All results run through `_call_provider("groq", ...)` with the app's own prompts/schemas and checked by `_validate_topic_content`, `_validate_quiz_response`, `_validate_roadmap_response`.

| Model (Groq) | Topic | Quiz | Roadmap | Reliability |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | 2.6–3.1s | 3.3–4.6s | 4.2–6.4s | **9/9 valid** (3 runs) |
| `qwen/qwen3.8-27b` | 1.8s | 2.9s | 3.8s | 3/3 valid (1 run) |
| `openai/gpt-oss-20b` | 400 `json_validate_failed` | 400 | invalid (5 topics) | **unreliable** |
| `qwen/qwen3.6-27b` | valid | 400 | invalid (9 topics) | **unreliable** |
| for comparison: Gemini chain | minutes (measured earlier) | 31–146s | 271s | quota/503 problems |

- Groq's model list for this key has **no embedding model**: `allam-2-7b`, `canopylabs/orpheus-*`, `meta-llama/llama-prompt-guard-*`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b`, `qwen/qwen3.6-27b`, `qwen/qwen3.8-27b`, `whisper-large-v3(-turbo)`. So **embeddings cannot move to Groq**.
- Rate limits on the key (response headers): **250,000 tokens/minute and 500,000 requests/day** per model. Plenty for this app.
- `gpt-oss-120b` spends hidden reasoning tokens (28 on a trivial prompt); `qwen3.8-27b` reported none.
- **Caveat:** validation proves the JSON shape and counts, not that the *content* is correct or well written. Not yet evaluated.
- **Open question:** `gpt-oss-120b` reported 273,005 of 500,000 daily requests remaining while my tests made ~30 calls, i.e. ~227,000 requests were used by something else today (another project/script sharing the key or org, or a loop). `qwen3.8-27b` showed 499,999 remaining. Check Groq's usage page before relying on the daily budget.

## Scope of the change

| Task | Today | Proposed |
|---|---|---|
| Roadmap, topic content, quiz (`generate`) | Gemini 3.8 → 3.7 → 3.5-flash-lite | Groq `gpt-oss-120b` → Groq `qwen3.8-27b` (→ optionally Gemini `3.5-flash-lite` as last resort, behind a setting) |
| Subject standardize / rank (`resolve`) | Gemini flash-lite ×2 → Groq 120b | Groq `qwen3.8-27b` (fast) → Groq `gpt-oss-120b` |
| Content review (`review`) | Groq 120b → 20b | Groq, **a different model than the generator** (see below); drop `gpt-oss-20b` |
| Embeddings | Gemini only | **Stay on Gemini** (no Groq equivalent) |

## Work items

1. **Chains and config** (`apps/ai/services.py`, `providers.py`): replace the `generate`/`resolve`/`review` chains; move model IDs to settings/env (`AI_GENERATE_MODELS`, etc.) so swapping a deprecated model needs no deploy. Update `PROVIDERS["groq"]` (`generate_models`, `review_models`) and keep `verify_provider_models` as a startup canary against the live `/models` list (Groq retires models; the Qwen ones may be preview models, which is worth confirming).
2. **Harden the Groq call path** (`_call_openai_compatible`): per-attempt timeout (~45s) and no hidden long retries; on `429` honor `retry-after` and fall to the next model; treat Groq's `400 json_validate_failed` (seen with the 20B/3.6 models) as "try the next model", classified by status code plus the structured `error.code` field, not by message text (AGENTS.md rule). Log model, elapsed, and token usage (including reasoning tokens) per attempt.
3. **Structured output**: today `strict: False` (best-effort) and it worked with the full schemas (min/max items included). Spike `strict: true` where Groq supports it for the chosen models; keep our validators regardless. Remove the Gemini schema sanitizer from the hot path (keep it only if Gemini stays as a fallback).
4. **Reasoning effort**: spike `reasoning_effort` (low/medium) on `gpt-oss-120b` for quiz and topic content to cut latency and tokens; measure quality impact.
5. **Review independence**: today the reviewer is the same family as the proposed generator, and a model reviewing its own output is a weak check. Run review on the other model (e.g. generate on `gpt-oss-120b`, review on `qwen3.8-27b`, or vice versa) and test that the reviewer actually fails bad content (feed it a deliberately wrong summary).
6. **Embeddings (stay on Gemini)**: keep them but make them safe: move to the non-deprecated `google-genai` SDK (embeddings only; verify the output is still 3072-dim and matches stored vectors, else re-embed), add a timeout, and **degrade gracefully** (if embedding fails, skip the semantic-match step and go straight to standardize + `_get_or_create_subject` instead of failing `create`). Alternative if you want zero Gemini: drop vector search and let the LLM resolve names against the list of existing subjects (fine while there are only hundreds; changes pgvector design, so only if you want it).
7. **Dependencies/cleanup**: `google-generativeai` stays until step 6 lands, then is replaced by `google-genai`; delete the Gemini generation code path and its tests once Groq is proven; update `AGENTS.md` architecture notes and `docs/known-bugs.md` (bug 12).
8. **Tests**: update chain/provider tests; add tests for `json_validate_failed` → next model, 429 → next model, timeout → next model, config-driven chains; add an **opt-in live smoke test** (env-gated, skipped by default) that sends the real roadmap/quiz/topic schemas to each configured model and asserts validity and a latency ceiling. This would have caught bugs 1 and 2 earlier.
9. **Quality gate before cutover** (not just shape): generate ~5 topics, ~5 quizzes and ~3 roadmaps with the new chain; check roadmap ordering/level split, summary accuracy, quiz answer correctness (does `correct_index` match the explanation?), link validity (earlier Gemini output had a hallucinated YouTube ID; the reviewer does not check links). Compare against the current Gemini output.

## Rollout
- Behind `AI_GENERATE_PROVIDER=groq|gemini` for one deploy; default to `groq` in a staging run first.
- Watch Sentry/logs for fallback frequency (should be near zero), per-task latency, and 429s.
- Remove the flag and the Gemini generation path after a clean week.
- Use a **separate Groq key for dev/test** so local runs cannot consume production's budget.

## Risks
- **Quality is unverified** (shape-validated only). Gate on step 9.
- **Single-vendor dependency**: all text generation on Groq. Mitigate with two Groq models plus an optional Gemini last resort, and the canary check.
- **Model churn**: `qwen3.8-27b` may be a preview model that can be retired; keep chains in config.
- **Reasoning tokens** on `gpt-oss-120b` add latency/cost; tune effort (step 4).
- **Embeddings still depend on Gemini** (free-tier quota for embeddings was not measured); the graceful-degradation step makes this non-fatal.

## Decisions needed
1. Keep Gemini `3.5-flash-lite` as an optional last-resort fallback for generate/resolve, or remove Gemini from text generation entirely?
2. Embeddings: keep on Gemini (recommended), or drop vector search in favor of LLM-based resolution?
3. Which model should be the primary generator: `gpt-oss-120b` (proven 9/9, slower) or `qwen3.8-27b` (faster, only 3 runs so far)? Recommendation: `gpt-oss-120b` primary, `qwen3.8-27b` secondary, pending the quality gate.
