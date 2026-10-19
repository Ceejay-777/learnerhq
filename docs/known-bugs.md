# Known Bugs (running list)

Found during the 2026-10-12 live end-to-end run (dev server + Celery worker + real Gemini/Groq, Neon DB).
Update the **Status** column as items are fixed; append new findings at the bottom.

Severity: **Blocker** (core flow broken) · **High** (security / data integrity) · **Medium** · **Low**

| # | Severity | Status | Area | Summary |
|---|----------|--------|------|---------|
| 1 | Blocker | Fixed 2026-10-13 | AI | `generate_content()` ignored `output_schema` |
| 2 | Blocker | Fixed 2026-10-13 | AI | Gemini rejects `minItems`/`maxItems`/`minimum`/`maximum` in schemas |
| 3 | High | Open | Quiz | `SubmitQuizView` has no ownership or topic check |
| 4 | High | Open | Quiz | Quiz response leaks `correct_index` and `explanation` |
| 5 | Medium | Open | Quiz/Subjects | Weak input validation (null, bool, quiz_type, attempt_id, frequency) |
| 6 | Medium | Fixed 2026-10-13 (uncommitted) | Subjects | Existing standardized name causes 500 (unique violation) |
| 7 | Medium | Fixed 2026-10-13 (uncommitted) | Subjects | Subject limit checked after the LLM work; leaves orphan subjects |
| 8 | Medium | Open | Content | `ensure_buffer_ahead()` never called; stale `NOT_GENERATED` status |
| 9 | Medium | Mitigated 2026-10-13 (uncommitted) | Infra | Stale DB connection after long LLM call causes 500 then spurious 401s |
| 10 | Low | Open | Misc | Smaller issues (see list) |
| 11 | Low | Fixed 2026-10-13 | AI | `call_gemini_native` sent empty `system_instruction` when none given |
| 12 | High | Fixed 2026-10-14 (uncommitted) | AI/Perf | Slow generation: moved text generation from Gemini to Groq (see `groq-model-test-report.md`) |
| 13 | - | Moved | - | Resource-link quality: see `follow-ups.md` F-1 (not a bug) |
| 14 | - | Moved | - | Quiz answer-key verification: see `follow-ups.md` F-2 (not a bug) |
| 15 | Low | Open | AI/Quality | Primary reviewer (Qwen) falsely rejects good content ~2 in 8 runs on "completeness" |

---

## 1. `generate_content()` ignores `output_schema` — Blocker, FIXED
- **Where:** `apps/ai/services.py`, `generate_content`.
- **Symptom:** Creating a new subject returned 500 after 50–110s (`[roadmap_validation] Response missing 'topics' key`); every quiz generation returned 500 (`[quiz_validation] Missing 'questions' array`).
- **Cause:** The function always used `TOPIC_CONTENT_SCHEMA` for the provider call and shape validation, so roadmap and quiz calls received `{summary, resource_links}`. Regression from the AI-layer refactor; the committed pre-refactor version honored the schema.
- **Fix:** Use `output_schema or TOPIC_CONTENT_SCHEMA` for the prompt, provider call and `_validate_shape`.
- **Tests:** `apps/ai/tests/test_services.py::TestGenerateContent`.

## 2. Gemini rejects JSON-schema size/range keywords — Blocker, FIXED
- **Where:** `apps/ai/providers.py::call_gemini_native`; schemas in `apps/learning/services.py` (`ROADMAP_SCHEMA`, `NORMAL_QUIZ_SCHEMA`, `ADVANCED_QUIZ_SCHEMA`).
- **Symptom:** With bug 1 patched, every Gemini model failed with `Unknown field for Schema: minItems` and the call returned 503.
- **Fix:** `sanitize_schema_for_gemini()` keeps only Gemini-supported keywords (`type, format, description, nullable, enum, items, properties, required`). The full schema is still shown to the model in the prompt, and the learning validators (`_validate_roadmap_response`, `_validate_quiz_response`) still enforce counts and ranges.
- **Tests:** `apps/ai/tests/test_providers.py::TestSanitizeSchemaForGemini` (includes the real learning schemas).
- **Why unit tests missed it:** provider calls are mocked everywhere, so no test ever sent a real schema to Gemini. Consider one opt-in live smoke test.

## 3. Quiz submit has no authorization — High (code-verified; cross-topic confirmed live)
- **Where:** `apps/learning/views.py::SubmitQuizView`, `apps/learning/services.py::submit_quiz`.
- **Problem:** The view reads `attempt_id`/`answers` from `request.data` without `SubmitQuizRequestSerializer`, never checks `attempt.user == request.user`, and ignores `topic_id` in the URL. Any authenticated user could submit another user's attempt (awarding that user points); an attempt for topic 88 was accepted via `/topics/87/quiz/submit`.
- **Not verified live:** cross-user access (needs a second account).
- **Fix:** Look up the attempt with `user=request.user, topic_id=topic_id` (404 otherwise) and validate input with the serializer.

## 4. Quiz generate response leaks answers — High
- **Where:** `GenerateQuizView` response; `docs/api-reference.md` documents `correct_index` and `explanation` as part of the question shape.
- **Problem:** The client receives the correct answers before submitting.
- **Fix:** Return only `question` and `options` on generate; return `correct_index`/`explanation` (for review) from the submit response.

## 5. Weak input validation — Medium
- `quiz/submit` with `answers: null` → **500** (`len(None)`).
- `answers: [true, ...]` accepted as option index 1 (`bool` is an `int`).
- `answers: "0123"` treated as a 4-element list.
- Missing `attempt_id` → 404 instead of 400.
- `quiz/generate` ignores `GenerateQuizRequestSerializer`, so `quiz_type: "EXPERT"` is not rejected; it ran the normal-quiz path, spent ~50s of LLM calls, then 500'd (bug 1).
- `notification-frequency` accepts `1.5` and `true`.
- **Fix:** Use the existing request serializers in the views; reject `bool` explicitly.

## 6. Existing standardized name → 500 — Medium, FIXED (uncommitted)
- Fix: `_get_or_create_subject` (case-insensitive lookup, `IntegrityError` fallback). Tests: `TestResolveGetOrCreate` in `apps/learning/tests/test_async_roadmap.py`.
- **Where:** `apps/learning/services.py::resolve_or_create_subject` (`Subject.objects.create`).
- **Repro:** `POST /api/learning/subjects/create {"name": "python programing"}` → `UniqueViolation` on `learning_subject_name_key` ("Python Programming").
- **Fix:** After standardizing, look up by `name__iexact` and use `get_or_create`; handle `IntegrityError` for concurrent creates.

## 7. Subject limit checked after LLM work; orphan subjects — Medium, FIXED (uncommitted)
- Fix: `check_enrollment_capacity` runs before resolving; roadmap generation moved to a background task with `Subject.roadmap_status` (FAILED instead of leaving a silent empty subject, retry endpoint). The three existing orphans (56, 58, 59) are still in the Neon DB; they will be generated if a user enrolls in them.
- **Where:** `CreateSubjectView` → `resolve_or_create_subject` runs before `add_subject_to_user` checks `get_available_slots`.
- **Problem:** A user at the 5-subject limit still triggers embedding + LLM calls and may create a `Subject` row with no topics. Failed roadmap generation (bug 1) also leaves topic-less subjects.
- **Leftovers from the test run:** subjects 56 "Astronomy for Beginners", 58 "Artisan Sourdough Bread Baking", 59 "Japanese Language Study" exist with no topics in the Neon DB (explore hides topic-less subjects). Delete if unwanted.
- **Fix:** Check available slots first; create the subject and its roadmap atomically (or clean up on failure).

## 8. Content generation entry points — Medium
- `ensure_buffer_ahead()` in `apps/learning/services.py` is never called; only enrolling (`generate_initial_batch`), review retry and `send_notification` trigger generation.
- Topic 86 had a summary and links but `content_status=NOT_GENERATED`, `review_status=PENDING`; nothing in the read path regenerates or repairs it.
- **Fix:** Call `ensure_buffer_ahead(topic)` when a topic is passed/opened; reconcile topics with content but a stale status.

## 9. Stale DB connection after long LLM calls — Medium, MITIGATED (uncommitted)
- The longest call (roadmap) no longer runs inside a web request. Quiz generation (`POST .../quiz/generate`) still calls the LLM inside the request, so `CONN_MAX_AGE`/`CONN_HEALTH_CHECKS` is still worth setting.
- **Symptom:** During a ~45s quiz generation, Neon closed the idle connection (`server closed the connection unexpectedly`) → 500, and the next requests returned 401 `Given token not valid`.
- **Fix to investigate:** `CONN_MAX_AGE` / `CONN_HEALTH_CHECKS = True` for the Neon pooler; avoid holding a transaction across the LLM call.

## 10. Smaller issues — Low
- One AI-generated YouTube ID is a 404 (`YqS-7WvJ8wY`, topic 86); the reviewer does not verify links.
- Link `type` is sometimes wrong (a Wikipedia URL labeled `video`, docs.python.org labeled `wikipedia`).
- Non-integer topic id (`/topics/abc`) returns Django's HTML 404, not the JSON error envelope.
- `resource-links-viewed` overwrites `resource_links_viewed_at` on every call.
- After `ADVANCED_PASSED`, requesting another advanced quiz says "pass the normal quiz and engage with resource links first".
- `/api/health/` reported `celery: true` with no worker running.
- Sign-in failures take ~5s (wrong password / unknown email), which may be intentional.
- `google.generativeai` is deprecated (FutureWarning); migrate to `google-genai`.
- `TopicDetailView` returns any topic to any authenticated user (no enrollment check); confirm this is intended.

## 11. Empty `system_instruction` to Gemini — Low, FIXED
- **Where:** `apps/ai/providers.py::call_gemini_native`.
- **Symptom:** `generate_content(prompt, output_schema=...)` with no system instruction failed on every Gemini model: `'content' argument must not be empty`.
- **Fix:** Pass `system_instruction=system_msg or None`. Test: `test_call_gemini_native_omits_empty_system_instruction`.

## 12. Slow Gemini calls — High, FIXED (uncommitted)
- **Resolution:** text generation moved to Groq with per-action chains, `reasoning_effort` and `max_completion_tokens` (`apps/ai/services.py::_ACTION_CHAINS`). Verified live: roadmap 4.8s, topic 3.3s, quiz 2.8-7s, review/standardize/resolve 1.5-2.5s. Embeddings remain on Gemini. Original analysis below.
Full write-up: `docs/gemini-latency-report.md`.
- Thinking models (`3.8-flash`, `3.7-flash`) spend 90–350 hidden thought tokens on trivial prompts; `3.5-flash-lite` spends none (0.6–3.4s for topic/quiz/roadmap vs 30–180s+).
- A single trivial request to `3.8-flash` took 181s; `3.7-flash` returned repeated 503 "high demand".
- The SDK retries 503 silently for up to 600s per call (`deadline=600`, `timeout=600`), and `_call_with_fallback` sets no per-attempt timeout, so a degraded model blocks the fallback chain.
- `3.8-flash` is on the free tier: 20 requests/day/model, already exhausted (429, resets in ~19h).
- Follow-ups from the original report that no longer apply: timeouts/circuit breaker for Gemini generation. Still relevant to Groq: add a circuit breaker for quota/503 (`qwen/qwen3.8-27b` returned `503 over capacity` in testing).

## 13. Moved to `follow-ups.md` (F-1)
Resource-link quality is an improvement to investigate, not a defect.

## 14. Moved to `follow-ups.md` (F-2)
Quiz answer-key verification is an improvement to investigate, not a defect.

## 15. Reviewer false rejections - Low, OPEN
- With the primary reviewer `qwen/qwen3.8-27b` (thinking on), known-good text was rejected 2 of 8 runs, both on the `completeness` axis (wanted the loop `else` clause). A rejection triggers regeneration (up to 2 attempts, then `FLAGGED`). Consider softening the completeness criterion or requiring two failing axes.

---

## Not yet covered by the live run
Brevo emails (password reset, notifications), Celery Beat dispatch, level-up / subject completion (needs ~7 passed topics), auto-select subjects, signup / password-reset flows.
