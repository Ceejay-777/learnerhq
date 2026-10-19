# Wicky Backend — Codebase Audit Report

## 1. ARCHITECTURE

**Organization**: 3 Django apps (`core`, `ai`, `learning`) plus `config` infrastructure. Clean separation of concerns: `core` owns auth/users, `ai` is a provider-agnostic LLM abstraction layer, `learning` is the entire domain.

**Deliberate choice, not accidental.** The commit log shows a clear build-up: initial prod deploy on Jul 9, then iterative feature additions over 14 days. The architecture was consistent from the start — no mid-flight restructuring commits. The `services.py` pattern (views → services → models, never views → models directly) is consistent across all apps. The `ai/services.py` docstring explicitly states the design intent: "One function per role — keeping provider-specific code contained here so swapping providers later is a config change, not a rewrite."

The project follows a **GenericAPIView + service layer** pattern (no ViewSets), with `drf-spectacular` OpenAPI schema decorators on every endpoint. Response shape is uniform: `{"data": {...}, "status": "success"}`.

**Leadership/architecture claim**: Designed and implemented a service-layer-first Django REST architecture with clean provider abstraction, enforcing single-responsibility boundaries across 3 apps from day one.

---

## 2. SCALE AND COMPLEXITY

| Metric | Value |
|--------|-------|
| Git commits | 21 |
| Date range | Jul 9 – Jul 23, 2026 (15 days) |
| Python files (non-migration) | 76 |
| Lines of code (non-migration) | ~6,277 |
| Django models | 8 (User, UserPreferences, PasswordResetToken, Subject, Topic, UserSubjectProgress, TopicProgress, QuizAttempt, UserInterest) |
| API endpoints | 19 learning + 7 auth + 1 health = 27 |
| Test files | 17 |
| Tests passing | 328 / 328 (100%) |
| Migrations | 9 (learning) + 3 (core) = 12 |
| Celery tasks | 6 (3 periodic, 3 ad-hoc) |
| Third-party integrations | 3 AI providers (Gemini, Groq, Brevo), Sentry, Redis, PostgreSQL/pgvector |

The project built 27 endpoints, 8 models, 328 tests, a full Celery async pipeline, and multi-AI provider integration in 15 days. That's a meaningful velocity metric.

**Hands-on claim**: Built 27 REST endpoints with 328 tests (100% pass rate) and a 6-task Celery pipeline in 15 days.

---

## 3. DATA MODELING

**Most complex model: `Topic` + `TopicProgress` + `QuizAttempt` interaction.**

The `Topic` model carries a full content lifecycle: `content_status` (NOT_GENERATED → GENERATING → READY/FAILED) and `review_status` (PENDING → PASSED/FAILED/FLAGGED). This isn't just a state machine — it tracks *why* something is in that state (`generation_started_at`, `review_attempts`) and supports concurrent lock claiming via `ensure_topic_content_ready()`.

`TopicProgress` has a 5-state progression (NOT_STARTED → READING → QUIZ_READY → PASSED → ADVANCED_PASSED) with separate attempt counters for normal and advanced quizzes. The `is_bonus` flag handles the edge case where curriculum topics are inserted *behind* a user's current position.

`QuizAttempt` stores the full AI-generated question set as JSON, with a unique constraint scoped to `(user, topic, quiz_type, attempt_number)` — this allows independent retry tracking per quiz type while preventing duplicate submissions.

**Why this is non-trivial**: A naive version would just have "user completed topic" as a boolean. This models a multi-stage learning pipeline with AI-generated content lifecycle, two-tier quiz difficulty gating, cooldown enforcement, and curriculum mutation safety — all while maintaining data integrity across insertions that shift topic ordering.

**Leadership claim**: Designed a multi-state content lifecycle and gamified progression data model supporting AI-generated curriculum mutation with user-progress preservation.

---

## 4. RELIABILITY AND CORRECTNESS

**Content generation locking** (`ensure_topic_content_ready` in `apps/learning/services.py:201-209`): Uses Django's `QuerySet.update()` returning affected row count as an atomic compare-and-swap. If two Celery workers claim the same topic simultaneously, only one wins. There's a **concurrent thread test** (`test_content_generation.py:354-394`) that uses `threading.Barrier` to verify exactly one of 5 threads wins the lock. This is the real-world version of the problem — Celery with `--concurrency=2` can dispatch two workers that both pick up jobs for the same topic.

**Quiz submission idempotency** (`submit_quiz` in `services.py:1105-1156`): Checks `attempt.answers is not None` before allowing submission. Prevents double-submit race conditions.

**Roadmap insertion with F() expressions** (`insert_roadmap_topics` in `services.py:371-425`): When inserting topics mid-roadmap, uses a temporary offset (`10^9`) to shift existing orders without collision, then shifts back. This is a pattern to avoid deadlocks in concurrent order-rewriting scenarios.

**Retry with backoff + jitter** (Celery tasks): `autoretry_for=(ProviderError,)`, `retry_backoff=True`, `retry_backoff_max=300`, `retry_jitter=True`, `max_retries=3`. This prevents thundering herd on API recovery.

**Notification scheduling** (`advance_due_time` in `services.py:480-482`): Advances from the *original due time*, not the actual send time. This prevents schedule drift when Celery tasks run late — a subtle but real correctness issue in notification systems.

**Hands-on claim**: Implemented atomic compare-and-swap content generation locking with concurrent-thread test coverage, retry backoff with jitter across 6 Celery tasks, and drift-proof notification scheduling.

---

## 5. INTEGRATIONS

**Gemini (Google AI)**: Used for embeddings only (`generate_embedding` in `apps/ai/services.py:47-54`). Uses `gemini-embedding-001` model to generate 3072-dimensional vectors stored as pgvector `HalfVectorField`.

**Groq (Llama 3.3 70B)**: Used for all content generation — roadmaps, topic summaries, quiz questions, content review, subject name standardization, and rank/resolve decisions. 5 distinct prompt engineering pipelines, each with its own JSON schema validation.

**Brevo (email)**: Password reset emails via `django-anymail`.

**pgvector**: Cosine distance similarity search with a 0.15 threshold for subject matching. The `_find_similar_subjects` function (`services.py:70-87`) queries embeddings and returns similarity scores.

**Sentry**: Production monitoring with Django, Celery, and logging integrations. Traces at 10% sample rate. Captures non-recoverable provider failures.

**Why this is non-trivial**: The subject resolution pipeline (`resolve_or_create_subject`) chains: case-insensitive DB match → embedding generation → pgvector similarity search → LLM rank/resolve → LLM name standardization. This is a multi-stage AI pipeline with fallbacks, not a simple API call.

**Hands-on claim**: Integrated 3 AI providers (Gemini for embeddings, Groq for generation, Brevo for email) and pgvector semantic search into a multi-stage subject resolution pipeline with JSON schema validation at every LLM boundary.

---

## 6. SECURITY

**Verified claims**:
- ✅ JWT in HTTP-only cookies (`access_token` httponly=True, secure=not DEBUG, samesite=Lax; `refresh_token` httponly=True, secure=not DEBUG, samesite=Strict). Set in `apps/core/views.py:33-50`.
- ✅ Password validation: Django's 4 built-in validators enforced on signup and password reset.
- ✅ Rate limiting: Anonymous 10/hour, authenticated 100/hour (`config/settings/base.py:107-113`).
- ✅ CORS configured with `CORS_ALLOW_CREDENTIALS = True`.
- ✅ Production: HSTS (1 year, includeSubDomains, preload), SSL redirect, secure cookies, X-Frame-Options DENY, Content-Type nosniff, XSS filter.
- ✅ Custom exception handler (`apps/core/exceptions.py`) catches `ObjectDoesNotExist` and normalizes all errors to `{"detail": "...", "status": "error"}` — prevents Django model-level exceptions from leaking.
- ✅ Password reset tokens have 1-hour expiry, are deleted after use.

**Gaps / Honesty flags**:
- ⚠️ The `PasswordResetConfirmSerializer` (`apps/core/serializers.py:54-75`) validates the token by looking up the user by email, then finding the token. There's no rate limiting on the confirm endpoint specifically — the global 10/hour anonymous throttle applies, but that's shared with all unauthenticated endpoints.
- ⚠️ The `.env` file is committed to the repo (listed in directory read). This is likely a development artifact, but worth noting — the `.env.example` exists as intended template.
- ⚠️ CSRF middleware is in the middleware stack but the API uses cookie-based JWT auth without CSRF tokens — the SameSite cookie attribute provides CSRF protection for cookie-sending clients, but any JavaScript client making cross-origin requests could be vulnerable if CORS is misconfigured.

**Leadership claim**: Implemented cookie-based JWT auth with HTTP-only secure cookies, production-grade security headers (HSTS, SSL redirect, X-Frame-Options), and centralized error handling that prevents model-layer exceptions from leaking.

---

## 7. PERFORMANCE

**pgvector with halfvec storage**: The `Subject.embedding` field uses `HalfVectorField(dimensions=3072)` — half-precision floats (4 bytes instead of 8). For 3072 dimensions, that's 12KB per vector instead of 24KB. This halves storage and improves index scan speed.

**Query optimization evidence**:
- `select_related` used in `get_subject_detail` (`services.py:493`), `topic_leaderboard` (`services.py:925`), `others_learning` (`services.py:953`), `send_notification` (`tasks.py:134`).
- `Subquery` annotations in `get_subject_detail` (`services.py:496-514`) to avoid N+1 queries for per-topic user progress.
- `annotate` with `Count` and `Q` filters for `explore_subjects` and `EnrolledSubjectsView` — single-query aggregation instead of per-subject queries.
- `bulk_create` for roadmap topic creation (`services.py:351,365,386`) and bonus progress records (`services.py:423`).
- `bulk_update` for bonus flag marking (`services.py:418-420`).
- `F()` expressions for atomic point increments (`services.py:994-999,1134-1140`).

**Content pre-generation buffer** (`ensure_buffer_ahead` in `services.py:254-262`): Pre-generates content for the next 2 topics ahead of the user's current position. `generate_initial_batch` triggers generation for the first 3 topics when a user enrolls. This is a speculative prefetching pattern.

**Health check caching** (`apps/core/health.py:50-62`): 5-second in-memory cache avoids hammering DB/Redis/Celery on every health check request.

**Hands-on claim**: Optimized with pgvector halfvec storage, Subquery annotations to eliminate N+1 queries, bulk operations for roadmap mutations, F() expressions for atomic increments, and speculative content pre-generation.

---

## 8. TESTING (HONEST PICTURE)

**328 tests, 328 passing, 0 failing. 15.54 seconds.** No coverage report generated (pytest-cov is installed but no `--cov` flag in the CI workflow).

**What's well covered**:
- Quiz system: 52 tests covering generation, submission, validation, cooldown, advanced gating, retry logic, option prefix stripping, prior missed questions
- Roadmap evolution: 30+ tests covering append, insert (beginning/middle/end), multi-user bonus marking, combined operations, progress preservation
- Content generation: 30+ tests covering validation, retry on malformed output, provider errors, review pipeline, buffer-ahead, concurrent locking, cleanup of stuck generations
- Notifications: 20+ tests covering frequency setting, due detection, dispatch fan-out, retry with content generation, schedule drift prevention
- Progression: 25+ tests covering level thresholds, level-up, subject completion, slot management, auto-select, interest tracking
- Leaderboard: 15+ tests covering ranking, hidden users, display names, topic scope
- Auth: 12 tests covering signup, signin, refresh, signout, cookies
- Health: 8 tests covering all checks, caching, degraded status
- Models: 15+ tests covering constraints, unique_together, string representations

**What's missing or thin**:
- No test for the `send_notification` email actually being sent (it's a Celery task that calls email service)
- No test for the seed management command end-to-end
- No test for password reset email delivery flow
- The `EnrolledSubjectsView` test doesn't verify `percent_complete` calculation with edge cases
- No load/stress testing
- No integration tests with actual AI providers (all mocked)

**CI**: GitHub Actions runs `pytest --tb=short -v` on push/PR to main. Python 3.12, pip cache enabled.

**Honest assessment**: This is a genuinely solid test suite for a 15-day project. The service-layer unit tests are thorough and cover edge cases (malformed AI responses, concurrent lock contention, schedule drift). The main gap is no integration tests with real providers and no end-to-end API tests against a running server.

**Hands-on claim**: Wrote 328 tests (all passing) covering service logic, AI response validation, concurrent lock contention, Celery task behavior, and quiz progression edge cases, with CI enforcement via GitHub Actions.

---

## 9. HIDDEN GEMS

**Roadmap mutation with progress preservation** (`insert_roadmap_topics`): When topics are inserted mid-curriculum behind a user's current position, the system automatically marks them as `is_bonus=True` for affected users. This is a real product problem — if you insert a new topic at position 5 and a user is at position 8, they shouldn't be penalized for not having completed the new topic. The implementation handles multi-user scenarios where different users have different completion positions. 14 dedicated tests cover this.

**Adaptive quiz retry with prior misses** (`generate_quiz` in `services.py:1054-1058`): When regenerating a quiz, the system can inject the learner's previously missed questions into the prompt, forcing the AI to generate questions targeting those specific weak areas. This is a pedagogical feature that most quiz systems don't have.

**Option prefix stripping** (`_strip_option_prefix` in `services.py:787-791`): AI models frequently output quiz options with "a)", "b)", "A.", etc. prefixes. The system strips these with a regex and validates they aren't empty after stripping. 4 dedicated tests cover this edge case, including options that are *only* the prefix.

**Stuck generation cleanup** (`cleanup_stuck_generations` in `tasks.py:184-194`): A periodic Celery task that resets topics stuck in GENERATING status for over 1 hour back to NOT_GENERATED. This handles the case where a Celery worker crashes mid-generation without updating the status.

**Auto subject selection** (`auto_select_subjects` in `tasks.py:197-225`): Opted-in users who haven't logged in for 24+ hours and have no active subjects get automatically enrolled in their top suggestion. This is a retention feature — it clears the `needs_subject_selection` flag on all completed subjects when a new one is added.

**Health check endpoint** with deep DB/Redis/Celery checks and 5-second result caching. Returns 200 for healthy, 503 for degraded — ready for load balancer health probes.

**Seed management command** with retry-on-rate-limit, count parameter, and progress reporting.

**Hands-on claim**: Built an adaptive quiz retry system that injects learner's prior misses into prompts, a roadmap mutation engine that preserves user progress across curriculum insertions, and an auto-enrollment retention feature for idle users.

---

## 10. HONESTY FLAGS

1. **`.env` committed**: The `.env` file exists in the repo root. While it likely contains development values, this is a security concern if it ever contained real secrets. **Mitigation**: `.gitignore` should be checked (I didn't verify this).

2. **No `--cov` in CI**: pytest-cov is installed but the GitHub Actions workflow runs `pytest --tb=short -v` without coverage reporting. Coverage claims should be cautious.

3. **AI provider testing is 100% mocked**: No integration tests hit real Gemini/Groq APIs. The `_is_retryable_error` function has real exception class tests against the Groq SDK, but the actual API behavior is never tested end-to-end.

4. **No `CSRF_COOKIE_SECURE` or `CSRF_COOKIE_HTTPONLY`** in settings — Django's CSRF middleware is in the stack but cookie-based JWT auth means CSRF isn't the primary threat model. Worth noting if claiming CSRF protection.

5. **Rate limiting is basic**: Anonymous 10/hour, authenticated 100/hour. No per-endpoint throttling. No brute-force protection on signin beyond the global anonymous throttle (10/hour across all unauthenticated endpoints).

6. **Celery `CELERY_BEAT_SCHEDULER`** is set to `django_celery_beat.schedulers:DatabaseScheduler` but the actual periodic tasks are registered via `on_after_finalize` signal in `celery.py`. The `django_celery_beat` package is installed and in `INSTALLED_APPS` but the DB scheduler isn't used for task registration — this is a minor config inconsistency.

7. **`MIGRATION_MODULES = {'ai': None}`** in test settings — the `ai` app has no migrations in tests. This works because the `ai` app has no models, but the migration directory exists with `__init__.py`. This is fine but slightly misleading.

8. **No `CELERY_TASK_ACKS_LATE = True` + concurrency consideration**: `acks_late` is set, meaning tasks are acknowledged after execution, not before. With `prefetch_multiplier=1`, this is safe for exactly-once semantics with the content generation lock. But if someone increases concurrency without understanding this, duplicate work could occur.

9. **The `send_notification` task (`tasks.py:121-181`)** has complex retry logic with `self.retry()` inside a `try/except ProviderError` block. The task catches `ProviderError` with `recoverable` flag, but the `self.retry()` call will raise `celery.exceptions.Retry` which is caught by Celery's own retry mechanism. The flow is correct but brittle — a future refactor could easily break the retry chain.

10. **No API versioning**: All endpoints are at `/api/...` with no version prefix. This is fine for a v1 but will need addressing for backward-compatible changes.

**Honest claim**: Built a production-ready v1 backend with 328 tests and Sentry integration, with known technical debt in rate limiting granularity, API versioning, and provider integration test coverage.

---

## SUMMARY OF CLAIMS BY SENIORITY LEVEL

### For a Senior/Lead role:
- Designed a 3-app Django architecture with clean provider abstraction and service-layer separation, deployed to Railway with Docker
- Built a multi-stage AI pipeline (embedding → similarity search → LLM rank/resolve) for semantic subject resolution
- Implemented roadmap mutation engine with user-progress preservation across curriculum insertions
- Architected a content lifecycle (generate → review → retry → flag) with atomic locking and concurrent safety
- Set up production monitoring with Sentry, health checks, and structured error handling

### For a Mid-level/Implementation role:
- Built 27 REST endpoints with 328 tests (100% pass rate) in 15 days
- Implemented cookie-based JWT auth with HTTP-only secure cookies
- Integrated 3 AI providers (Gemini, Groq, Brevo) with typed error handling and retry backoff
- Wrote Celery task pipeline with atomic locks, retry jitter, and stuck-generation cleanup
- Implemented pgvector semantic search with halfvec storage for subject matching
