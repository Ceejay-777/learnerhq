# Production Readiness Backlog

Issues from the live happy-path E2E walkthrough (2026-07-12).

Filter: **"Does this block 1 or 100 people from using the app?"**

---

## Pass 1 — User-facing operability

> Things that directly break the user experience. Do these first.

### 1.1 — Deep health check

- [x] **Where:** `config/urls.py` (current `health_check` view)
- **Problem:** `/api/health/` returns 200 unconditionally. Dead DB or Redis → traffic still routes to broken instance.
- **Acceptance:**
  - New view checks DB (`SELECT 1`), Redis (`PING`), and Celery worker liveness.
  - 200 if all pass, 503 if any fail. Cached 5s.
  - Per-check status in response body.
- **Files:** `config/urls.py`, new `apps/core/health.py`, tests
- **Effort:** 1-2h

### 1.2 — Graceful shutdown config

- [x] **Where:** `Dockerfile:18`, `config/settings/base.py`
- **Problem:** Gunicorn graceful-timeout is default 30s. Worker shutdown is unbounded. Deploy kills in-flight requests.
- **Acceptance:**
  - Gunicorn `--graceful-timeout 60` in Dockerfile CMD.
  - Celery `worker_shutdown_timeout = 60` in base settings.
  - Document that Railway's deploy grace period is the real limit, not the gunicorn config.
- **Files:** `Dockerfile`, `config/settings/base.py`
- **Effort:** 1h

### 1.3 — Auto-retry exhaustion alerting

- [x] **Where:** `apps/learning/tasks.py`
- **Problem:** Topics stuck in FAILED after 3 retries — no Sentry event, no user notification, nobody knows.
- **Acceptance:**
  - Verify Sentry `CeleryIntegration` in production settings.
  - `sentry_sdk.capture_message()` on final FAILED state.
  - Sentry alert rule for "5+ FAILED topics in 1 hour."
- **Files:** `apps/learning/tasks.py`, `config/settings/production.py`, Sentry dashboard
- **Effort:** 2-3h

---

## Pass 2 — Reliability

> Small fixes. 4-6h total.

### 2.1 — LLM rate-limit (429) handling

- [x] **Where:** `apps/learning/services.py`, `seed_subjects.py`
- **Problem:** `seed_subjects.py` does `if "429" in str(e)` — brittle. Views don't catch 429s → returns 500 to user.
- **Acceptance:**
  - `_is_retryable_error()` in `apps/ai/services.py` uses `isinstance` against real SDK exception classes (`groq.RateLimitError`, `google.api_core.exceptions.ResourceExhausted`, etc.).
  - Tasks check `recoverable` flag; non-recoverable ProviderError goes to FAILED immediately.
  - `seed_subjects.py` uses `getattr(e, 'recoverable')` instead of string match.
  - GenerateQuizView catches ProviderError → returns 503.

### 2.2 — Quiz generation retry loop skips ProviderError

- [x] **Where:** `apps/learning/services.py:917-929`
- **Problem:** Validation retry loop catches `GenerationError` but lets `ProviderError` break out.
- **Acceptance:** Changed to `except (GenerationError, ProviderError):`. Tests pass.

### 2.3 — Password reset flow untested

- [x] **Where:** `apps/core/views.py:152-198`, `config/utils/tasks.py`
- **Problem:** Brevo email dispatch not exercised in E2E. Configuration unverified.
- **Acceptance:**
  - Unit test: Brevo failure still returns 200 (doesn't leak user exists).
  - E2E send of real Brevo email deferred to Pass 4.2 (CI E2E).

### 2.4 — Dead try/except in `resolve_or_create_subject`

- [x] **Where:** `apps/learning/services.py:79-86` and `99-105`
- **Problem:** `try: ... except ProviderError: raise; except Exception: raise` is a no-op. `transaction.atomic()` handles rollback.
- **Acceptance:** Removed both blocks. Tests pass.

### 2.5 — Unfriendly capacity error message

- [x] **Where:** `apps/learning/services.py:564`
- **Problem:** `"User is at capacity (5 active subjects)"` — internal-speak.
- **Acceptance:**
  - Changed to: `"You've reached the limit of 5 active subjects. Remove one or complete a subject to add a new one."`
  - Tests updated.

---

## Pass 3 — Auto-select refactor

> New `UserPreferences` model. Opt-in consent. Fixed idle trigger. 4-6h total.

### 3.1 — `UserPreferences` model + migration

- [x] **Files:** `apps/core/models.py`, new migration
- **Acceptance:**
  - `UserPreferences` (1:1 with User) holds: `leaderboard_visible`, `others_learning_visible`, `auto_select_subjects_enabled` (default False), `auto_select_subjects_consent_at` (null).
  - Data migration copies existing values from User.
  - Old `User.leaderboard_visible` / `User.others_learning_visible` removed.
- **Effort:** 1h

### 3.2 — Update call sites

- [x] **Files:** `apps/learning/services.py`, `apps/core/serializers.py`, `apps/core/views.py`, tests
- **Acceptance:** All reads of `leaderboard_visible` / `others_learning_visible` go through `user.preferences`. Full suite passes.
- **Effort:** 1h

### 3.3 — Opt-in on signup form

- [x] **Files:** serializers, views
- **Acceptance:** Signup accepts `auto_select_subjects_enabled`. If True, stamps `consent_at=now()`. Response includes preferences.
- **Effort:** 30min

### 3.4 — Toggle in profile/settings

- [x] **Files:** serializers, views
- **Acceptance:** PATCH profile with `{"preferences": {"auto_select_subjects_enabled": true}}` updates + stamps consent. GET includes preferences.
- **Effort:** 30min

### 3.5 — Rewrite `auto_select_subjects` task

- [x] **Files:** `apps/learning/tasks.py:168-187`
- **Acceptance:** Only fires for users who: a) opted in, b) have 0 active USPs, c) `max(last_login, date_joined) < now - 24h`. Enrolls once, clears all `needs_subject_selection` flags.
- **Effort:** 1h

### 3.6 — Tests

- [x] **Files:** test file in `apps/learning/tests/`
- **Acceptance:**
  - Opted in + active USPs → not enrolled.
  - Opted in + idle 24h+ → enrolled.
  - Not opted in → never enrolled.
  - Multiple completed subjects → enrolled once, all flags cleared.
- **Effort:** 1-2h

---

## Pass 4 — Pre-launch

### 4.1 — Migrate from `google.generativeai` to `google.genai`

- [ ] Deprecated library. Every import logs a warning. 1-2h.

### 4.2 — E2E walkthrough in CI

- [ ] 288 pytest tests don't exercise real pgvector. 3-4h.

### 4.3 — Verify Sentry captures errors

- [ ] We have zero confidence Sentry fires. 1h.

### 4.4 — Quiz cooldown: per-user instead of per quiz

- [ ] Prevent cross-topic quiz spam. 1-2h.

---

## Pass 5 — Performance (when needed)

### 5.1 — HNSW index on `Subject.embedding`
### 5.2 — pgvector performance test
### 5.3 — Cache layer on explore/suggestions
### 5.4 — Standardize Celery retry config

---

## YAGNI (within auto-select only, per "what is my business with 10k users")

- Iteration cap on auto_select task
- Auto-enroll notification email
