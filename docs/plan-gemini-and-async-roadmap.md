# Plan: paid Gemini key (1), google-genai migration (4), async roadmap generation (5)

Source: `docs/gemini-latency-report.md` recommendations 1, 4, 5. Planning only; nothing here is implemented.
Suggested order: **1 → 4 → 5** (1 is ops and unblocks measuring 4; 4 fixes latency and removes the 600s hang; 5 changes the API contract so it goes last and gets its own frontend sync).

## Verified facts the plan relies on
From the `google-genai` 2.30.0 wheel (read, not installed): Python >=3.10 (project is >=3.11); `types.ThinkingConfig(thinking_budget: int, thinking_level: MINIMAL|LOW|MEDIUM|HIGH, include_thoughts)`; `types.HttpOptions(timeout=<ms>, retry_options=HttpRetryOptions(attempts=...))`; `usage_metadata.thoughts_token_count` exists; `GenerateContentConfig.response_json_schema` accepts JSON Schema; `errors.APIError(.code)` with `ClientError` (4xx) and `ServerError` (5xx); `client.models.embed_content(model=, contents=, config=)`.
Current code touchpoints: `apps/ai/providers.py::call_gemini_native`, `apps/ai/services.py::generate_embedding` and `_is_retryable_error`/`_GOOGLE_RETRYABLE`, `apps/ai/tests/test_providers.py` (patches `google.generativeai.*`), `requirements.txt` (`google-generativeai==0.8.6`).

---

## 1. Paid / higher-tier Gemini key

Mostly ops; small code hardening.

**Ops steps**
1. Enable billing on the Google Cloud project behind the key (project number is in `GEMINI_PROJECT_NUMBER`), confirm the tier in AI Studio / the rate-limits page.
2. Set a budget and alert in Cloud Billing; decide a monthly cap before enabling.
3. Use **separate keys per environment** (dev/test vs production) so local testing cannot exhaust production quota. Put the prod key only in Railway variables; keep `.env.example` as placeholders.
4. Check the Groq key's limits too (it serves `review`; same failure mode exists).
5. Rotate the current key after moving: it has been used heavily in this session and sits in a local `.env`.

**Code hardening (small PR)**
- Make the model chain configurable (`settings`/env, e.g. `GEMINI_GENERATE_MODELS`) so model changes don't need a deploy.
- Classify errors structurally (per AGENTS.md rule): a 429 that is a daily/project quota should **skip that model immediately** and not be retried; 429 per-minute is retryable with backoff. Add a short in-process or Redis circuit breaker (skip a model that returned a quota 429 for N minutes) so each request doesn't re-probe a dead model.
- Log per attempt: model, elapsed, outcome, thinking tokens. Currently only the winner is logged and every failure triggers `sentry_sdk.capture_message` (noisy); keep Sentry for "all providers failed" and quota-exhausted events.

**Done when:** a 429-free sustained run of N roadmap + quiz + topic calls in staging, billing alert configured, per-env keys in place.
**Risk:** cost is unmodelled; measure tokens per operation in step 4 before projecting spend.

---

## 4. Migrate `google-generativeai` → `google-genai`

**Why:** the old SDK is deprecated (FutureWarning on every call), has no thinking control, hides thought tokens, and silently retries 503s for up to 600s.

**Step 0 — spike (half day, no production code):** against the real models, with real roadmap/quiz/topic prompts and schemas, measure latency, quality and `thoughts_token_count` for: default thinking; `thinking_budget=0` (or the lowest `thinking_level`) where the model allows it; and `gemini-3.5-flash-lite`. Also test whether `response_json_schema` accepts `minItems/maxItems/minimum/maximum` (if yes, `sanitize_schema_for_gemini` can be deleted; if no, keep it). Outcome decides the per-role model and thinking settings. Unknowns to resolve here: which 3.x models accept `thinking_budget` vs `thinking_level` and whether thinking can be turned off.

**Step 1 — client + call (`providers.py`)**
- One module-level `genai.Client(api_key=..., http_options=HttpOptions(timeout=<~60000 ms>, retry_options=HttpRetryOptions(attempts=1)))` so a slow or overloaded model fails fast and `_call_with_fallback` moves to the next model. Per-role timeouts as settings (roadmap longer than quiz).
- `call_gemini_native(messages, schema, model, thinking=...)` → `client.models.generate_content(model=, contents=, config=GenerateContentConfig(system_instruction=(... or None), response_mime_type="application/json", response_json_schema=..., thinking_config=...))`. Keep the "empty system_instruction → None" fix.
- Return/log `usage_metadata` (prompt, output, `thoughts_token_count`).

**Step 2 — embeddings (`services.py::generate_embedding`)**
- Switch to `client.models.embed_content(model="gemini-embedding-001", contents=text)` and read `embeddings[0].values`. **Check output dimension = 3072** (`Subject.embedding` is `HalfVectorField(dimensions=3072)`); if the default differs, set `output_dimensionality` explicitly. Existing stored vectors were produced by the old SDK; verify cosine similarity between old and new embeddings of the same text is ~1.0 before deploying, otherwise re-embed all subjects in a migration command.

**Step 3 — errors**
- Replace `_GOOGLE_RETRYABLE` (`google.api_core`) with `isinstance(exc, errors.ServerError)` and `isinstance(exc, errors.ClientError) and exc.code == 429`, keeping the `httpx` checks for Groq. Keep `ProviderError` propagation from Celery tasks (AGENTS.md rule).

**Step 4 — deps and tests**
- `requirements.txt` / `pyproject.toml`: remove `google-generativeai`, add `google-genai` (pin an exact version, e.g. the verified 2.30.0 after the spike). Rebuild the Docker image.
- Update `apps/ai/tests/test_providers.py` (patches `google.generativeai.*`) and add: timeout/retry options passed, thinking config passed, empty system instruction omitted, 429/5xx classification.
- Add an opt-in live smoke test (marker, skipped by default) that sends the real roadmap/quiz/topic schemas to each model with a latency ceiling. This would have caught bugs 1 and 2.

**Rollout:** feature-flag the new call path (`GEMINI_SDK=genai|legacy`) for one deploy, compare latency/failure rate in Sentry, then delete the legacy path.
**Done when:** p95 latency per role is within target from the spike, no calls exceed the per-attempt timeout, `thoughts_token_count` is logged, and the FutureWarning is gone.

---

## 5. Roadmap generation off the request thread

**Today:** `POST /subjects/create` → `resolve_or_create_subject` → `add_subject_to_user` runs `generate_roadmap` + `generate_initial_batch` inline (minutes, drops the Neon connection, leaves topic-less subjects). Related bugs: 6, 7, 9, 12.

**Design**
- **Model:** add `Subject.roadmap_status` (`NOT_GENERATED | GENERATING | READY | FAILED`) plus `roadmap_started_at`, with a migration (existing subjects with topics → `READY`; topic-less ones → `NOT_GENERATED`). Mirrors the existing `Topic.content_status` pattern.
- **Task:** `learning.generate_roadmap_for_subject(subject_id)` in `apps/learning/tasks.py`, deferred imports, `autoretry_for=(ProviderError,)` with backoff, same shape as `generate_content_for_topic`. Atomic claim (`filter(status=NOT_GENERATED).update(status=GENERATING)`) like `ensure_topic_content_ready` so duplicate dispatches are no-ops. Catch only `GenerationError` → `FAILED` + Sentry. On success: `bulk_create` topics in a transaction, set `READY`, then `generate_initial_batch`.
- **Request flow (`CreateSubjectView` / `add_subject_to_user`):**
  1. Check the 5-subject limit **first** (fixes bug 7: no LLM work or orphan rows when at the limit).
  2. Resolve/standardize (fast; with flash-lite ~1s) using get-or-create to fix bug 6.
  3. Create the `UserSubjectProgress` immediately, set `roadmap_status=GENERATING`, dispatch the task, return `200` with `{"action": "resolved", "subject": {id, name, roadmap_status}}`. Subjects that already have topics behave as today.
- **Reads / status delivery:** `roadmap_status` is exposed in `GET /subjects`, `GET /subjects/<id>` (always the source of truth, used for the initial state and after reconnects). The frontend wants **SSE instead of polling**; see "SSE" below. `explore` already hides topic-less subjects (`topics__isnull=False`), so generating subjects stay hidden from other users. Empty-roadmap guards needed in progress, level, notification and suggestion code (0 topics).
- **Failure and repair:** `FAILED` shown to the user with a retry action (`POST /subjects/<id>/roadmap/retry`, idempotent, same task). Extend `cleanup_stuck_generations` to reset `GENERATING` subjects older than ~1h and sweep existing topic-less subjects (ids 56, 58, 59 today).
- **Docs/contract:** update `docs/api-reference.md`, `CreateSubjectSerializer`/response serializers (OpenAPI), and tell the frontend: `create` no longer blocks; new `roadmap_status` field; new retry endpoint.

### SSE (push instead of polling) — reuse the Konversa pattern (`django-eventstream`)
Reference implementation: `Desktop/Code/Konversa` (django-eventstream 5.3.3, daphne 4.2.1, Django 6, Celery 5.6, redis-py 7). What it does:
- `django_eventstream` + `daphne` in `INSTALLED_APPS`; `path("events/", include("django_eventstream.urls"))`; `ASGI_APPLICATION` set.
- Celery tasks publish with `send_event(channel=..., event_type="caption.generated", data={...})`. `EVENTSTREAM_REDIS` makes this cross-process, so a worker's event reaches clients connected to the web process.
- Channels are named `{domain}-job-{sqid}` (`get_job_channel`), and the create endpoint returns `"channel"` in its response; the client subscribes to `/events/?channel=<name>`.

**Apply to Wicky**
- Add `django-eventstream` (+ an ASGI server) and `EVENTSTREAM_REDIS` pointing at the same Redis as Celery (use env, not a hardcoded host).
- Channel `subject-roadmap-<subject_id>` (or per-user `user-<id>`); `create` returns `{subject, roadmap_status, channel}`.
- The roadmap task calls `send_event` on `GENERATING`, `READY` and `FAILED` (payload: `subject_id`, `roadmap_status`, `topic_count`).
- Keep `GET /subjects/<id>` for initial state, reconnects and fallback polling.

**Gaps in Konversa that we must not copy** (verified in its code and in the installed library):
1. **Channels are public.** No `EVENTSTREAM_CHANNELMANAGER_CLASS` is set, and `DefaultChannelManager.can_read_channel()` returns `True` for everyone. Anyone who knows a channel name can listen. We need a custom channel manager that requires an authenticated user (cookie JWT) and checks the user is enrolled in that subject; use per-user or unguessable channels.
2. **Production runs WSGI.** Konversa's `Dockerfile` starts `gunicorn konversa.wsgi:application`; only the dev compose (`runserver` under daphne) is ASGI. Long-lived SSE needs an ASGI server in production. For us: switch the web service to ASGI (e.g. `uvicorn`/`daphne`) or run a separate streaming service; today's `gunicorn config.wsgi --workers 4 --timeout 120` would pin workers and cut streams.
3. **No replay.** `EVENTSTREAM_STORAGE_CLASS` is unset, so events missed during a reconnect are lost. Send the current status as the first event on connect (or enable a storage class) and keep the GET fallback.
4. `EVENTSTREAM_REDIS` host is hardcoded to `"redis"`; read it from env.

**Cookie auth:** the browser `EventSource` sends cookies for same-site requests, or cross-origin with `withCredentials: true` plus `EVENTSTREAM_ALLOW_CREDENTIALS`/`EVENTSTREAM_ALLOW_ORIGINS`. Since the API uses HTTP-only JWT cookies with `SameSite=Lax/Strict`, confirm the frontend and API share a site, or the stream will be unauthenticated.

**Tests:** task claim/retry/failure paths (eager mode); view returns immediately and dispatches once; limit check before any AI call; duplicate create is idempotent; stuck-generation cleanup; empty-roadmap guards. Add an e2e check with a real worker.
**Done when:** `create` returns in a few seconds regardless of model latency, no topic-less subjects are created on failure, and a killed worker mid-generation recovers via cleanup.
**Risk:** this is a breaking API behavior change (enrolled subject without topics); ship behind a coordinated frontend change.

---

## Decisions needed from you
1. ~~API contract for 5~~ — **Decided: enroll immediately** with `roadmap_status`.
1b. **SSE:** reference found in Konversa (`django-eventstream`). Confirm we adopt it, with the channel-authorization and ASGI-in-production fixes above.
2. **Interim:** item 2 from the report (per-attempt timeout, no 503 retry) is folded into step 4. If 4 will be delayed more than a few days, do a 10-line interim fix on the old SDK first (`request_options={"timeout": 60}`, `retry=None`).
3. **Spend ceiling** for item 1, and whether prod and dev keys should be on different Google projects.
