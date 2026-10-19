# Gemini Latency Investigation

Date: 2026-10-13 · Trigger: live roadmap took 271s and quiz 146s after the schema fix (a quiz took 31s earlier).
Method: direct SDK calls per model (same prompts/schemas as the app, schemas sanitized), timed, with DEBUG logging of the gRPC layer and token usage. SDK: `google-generativeai 0.8.6`. Generate chain (`apps/ai/services.py::_ROLE_PROVIDERS["generate"]`): `gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-3.5-flash-lite`.

## Verdict
The slowness is **not our code** (no schema, validation or DB cost; Groq review takes ~6s). It comes from four stacked causes, all in how the Gemini calls are made and which models/tier are used:

1. **Thinking models spend hidden reasoning tokens** (confirmed). `gemini-3.8-flash` and `gemini-3.7-flash` generate 90–350 hidden thought tokens even for a two-sentence reply; `gemini-3.5-flash-lite` generates none. Roadmaps and quizzes make that far larger. The app has no way to see or cap it (SDK 0.8.6 does not expose `thoughts_token_count` or a thinking setting).
2. **Server-side slowness and 503 "high demand"** (confirmed). One trivial request (44 prompt tokens, 5 output tokens) to `gemini-3.8-flash` took **181s** as a single gRPC call with no retry, so Google itself was slow. `gemini-3.7-flash` returned repeated `503 This model is currently experiencing high demand`.
3. **The SDK silently retries 503s for up to 10 minutes** (confirmed in SDK source and DEBUG log). `generate_content` is wrapped with `Retry(initial=1.0, maximum=10.0, multiplier=1.3, predicate=ServiceUnavailable, deadline=600.0)` and `default_timeout=600.0`. Our `_call_with_fallback` sets no per-attempt timeout and disables nothing, so a degraded model can block the fallback chain for up to ~600s before the next model is tried.
4. **Free-tier quota is exhausted** (confirmed). `gemini-3.8-flash` returns `429 ... generate_content_free_tier_requests, limit: 20, quota_id: GenerateRequestsPerDayPerProject​PerModel-FreeTier`, retry in ~19h41m. The key in `.env` is on the free tier: **20 requests per day per model**. One new subject costs a roadmap call plus up to 3 initial topic calls (+ retries), so the chain falls through to `3.7-flash` after only a handful of operations.

Net effect in the app: `3.8-flash` is rate-limited or slow, the call falls to `3.7-flash` (thinking, 13–50s even for tiny prompts, minutes for roadmap-size output, with 503 retries), and only `3.5-flash-lite` is fast.

## Measurements

Same prompts as the app, sequential, sanitized schemas.

| Case | gemini-3.8-flash | gemini-3.7-flash | gemini-3.5-flash-lite |
|---|---|---|---|
| ping (44 in / ~5 out) | **181.4s** (hidden thinking ≈ 90 tok) | **31.7s** (≈ 138 tok, several 503 retries) | **0.7s** (0) |
| topic content (~500 tok out) | 429 in 5.2s | 429 in 4.9s | **2.2s** |
| normal quiz (~616 tok out) | 429 in 0.3s | 429 in 0.5s | **2.4s** |
| roadmap (~861 tok out) | 429 in 0.3s | 429 in 0.5s | **3.4s** |
| 2 sentences, 3 tries | 429 daily quota (0.9s) | 45.5s / 50.0s / 13.7s (hidden thinking 300–345 tok) | 0.6s / 1.2s / 0.6s (0) |

Hidden thinking tokens = `total_token_count − prompt_token_count − candidates_token_count`.
Earlier in the session (app, before this investigation): topic generation 17–30s, Groq review ~6s, quiz 31s, then roadmap 271s and quiz 146s.

## What was ruled out
- Our schema/validation code and DB: negligible. Groq review stays ~6s.
- Output length alone: `flash-lite` produces the same size of output in 2–3s.
- The compose stack (web/worker/beat) sharing the API key: `docker logs` for the worker over the last 90 minutes showed no task activity, so the quota was consumed by this session's testing and earlier use.

## Not proven / caveats
- The exact split of the 271s and 146s runs (per-attempt time, which model served) was not instrumented at the time. The mechanisms above are confirmed; attributing that specific run to them is an inference. `_call_with_fallback` logs only the winning model.
- Quality of `flash-lite` output for roadmap/quiz/topic was **not** evaluated. It returned well-formed JSON of the expected size, but it was not run through `_validate_roadmap_response` / `_validate_quiz_response`, nor assessed for correctness.
- 429 quotas and 503 demand spikes vary over time; a retest later may show different numbers. The 181s ping may be a transient spike.

## Recommended follow-ups (not done)
1. **Move to a paid / higher-tier key.** 20 req/day/model on the free tier cannot support the app's call pattern.
2. **Add a per-attempt timeout and stop the SDK's long 503 retry** (e.g. `request_options={"timeout": ~60}` and `retry=None` on `generate_content`) so a slow or overloaded model falls through to the next in seconds, not minutes. Also log elapsed time and model per attempt in `_call_with_fallback`.
3. **Reconsider chain order / models per role.** `flash-lite` is 10–100× faster for these workloads. Evaluate its roadmap/quiz/topic quality (run its output through the validators and the review step), then consider it first for latency-sensitive roles (quiz, topic) and keep thinking models for roadmap if quality demands.
4. **Control thinking.** Migrate from the deprecated `google-generativeai` to `google-genai`, which exposes thinking configuration and `thoughts_token_count`, then set a small thinking budget for structured-JSON tasks.
5. **Don't generate inside the request thread.** Roadmap generation runs synchronously in `POST /subjects/create` (see bug 9 in `known-bugs.md`: long LLM calls also drop the Neon connection). Moving it to a Celery task with a `GENERATING` status would keep the request fast.
6. **Add an opt-in live smoke test** that sends the real schemas to each model and asserts a latency ceiling.
