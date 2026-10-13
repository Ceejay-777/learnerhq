# LearnerHQ Backend — Agent Instructions

## One-liner

Django 5.1+ · DRF 3.15 · Celery · PostgreSQL/pgvector · Redis · Docker/Railway.

## Default settings module

`config.settings.development` (manage.py), `config.settings.production` (wsgi.py, celery.py).
Always run commands with `--settings=config.settings.test` for test-sensitive tasks.

## Commands

```bash
pytest                 # all tests (SQLite in-memory, no Postgres/Redis needed)
pytest -v --cov        # verbose with coverage
pytest apps/learning/tests/test_views.py -k "test_name"  # single test
python manage.py migrate
python manage.py runserver 8000   # no SSE streaming
DJANGO_SETTINGS_MODULE=config.settings.development uvicorn config.asgi:application --port 8000 --reload   # with SSE
celery -A config worker -l info      # separate terminal
celery -A config beat -l info        # separate terminal
```

## Architecture

- **No ViewSets.** Every endpoint is a `GenericAPIView` with one method. Views call service functions; they never touch models.
- **Service layer** (`apps/*/services.py`) holds all business logic. Dataclass return types (e.g. `ResolveResult`, `LevelCheckResult`) instead of bare dicts.
- **Serializers** handle validation + OpenAPI schema. `drf-spectacular` at `/api/docs/`.
- **AI providers** live in `apps/ai/services.py`: Gemini for generation, resolve/rank/standardize, and embeddings (native SDK). Groq for review (OpenAI-compatible via httpx). Fallback chains are role-specific, hardcoded in `_ROLE_PROVIDERS`. Gemini calls use `call_gemini_native()`; Groq calls use `_call_openai_compatible()`. Review failures return a flagged result instead of raising.
- **Shared infra** in `config/utils/` (email, Celery tasks) rather than `apps/utils/`.
- **Custom DRF exception handler** (`apps/core/exceptions.py`) normalizes all errors to `{"detail": "...", "status": "error"}`.
- **Auth**: JWT in HTTP-only cookies (`access_token`: 15min, `refresh_token`: 7 days). No `Authorization` header. Use `CookieJWTAuthentication`.
- **Celery**: Beat schedule defined in `config/celery.py` via `on_after_finalize` (not `django_celery_beat` DB). Three periodic tasks.
- **SSE (`django-eventstream`)**: `GET /api/events` streams per-user events (channel `user-<id>`; only the owner may read it, enforced by `apps/core/eventstream.py`; JWT cookie auth via `apps/core/middleware.py`). Celery tasks publish with `apps/learning/events.py` (`send_event`, cross-process via `EVENTSTREAM_REDIS`). The DB is the source of truth: events are not replayable, so clients must also `GET /subjects/<id>`.
- **Roadmap generation is async**: `POST /subjects/create` enrolls immediately and queues `generate_roadmap_for_subject`; `Subject.roadmap_status` is `NOT_GENERATED|GENERATING|READY|FAILED`; `POST /subjects/<id>/roadmap/retry` retries.

## Test quirks

- `CELERY_TASK_ALWAYS_EAGER = True` — tasks execute synchronously in tests.
- `MIGRATION_MODULES = {'ai': None}` — ai app has no migrations in test.
- `MD5PasswordHasher` — fast, non-production.
- Throttling disabled in test settings.
- pgvector guards: all vector queries check `connection.vendor != "postgresql"` first (no-op on SQLite).
- Tests use `APIClient()` and `rest_framework_simplejwt.tokens.RefreshToken` for auth fixtures — set `client.cookies["access_token"]` manually.

## Key constraints

- Max **5 active subjects** per user.
- **60% pass threshold** for quizzes. **1-hour cooldown** between retakes.
- Normal quiz: 4–6 questions, total_points 90–110. Advanced: 9–12 questions, 180–220.
- Advanced quiz requires normal pass + resource links viewed.
- Notification frequency: 1–24 hours. Celery Beat dispatches every 15 min.

## Env & deploy

- `.env.example` lists all required vars. `python-decouple` + `dotenv` load them.
- Railway: 3 services (web/uvicorn ASGI, worker, beat) via `railway.json`. Web runs ASGI (not gunicorn/WSGI) because SSE streams are long-lived.
- `Dockerfile` collects static via Whitenoise.
- Sentry + HSTS + secure cookies in production settings.

## Code style conventions

- URL patterns end **without** trailing slashes (e.g. `/api/auth/signup`, not `/signup/`).
- Response shape: `{"data": {...}, "status": "success"}` for data, `{"detail": "...", "status": "error"}` for errors.
- Endpoints import `services` functions directly by name; no service class abstraction.
- Task functions in `apps/*/tasks.py` use deferred imports inside the function body.
- Database-level `unique_together` enforced at model layer (no soft uniqueness checks).
- **Execution branches must use structural type checks, never fragile heuristics.** Use `isinstance` against typed exception classes (e.g. `groq.RateLimitError`), typed return values, `getattr(obj, 'status_code')` on protocol-compliant objects, or explicit `is not None` — never `str(e)` substring matches or duck-typing by message content. If you're checking what error happened, use the class, not its string representation.
- **AI provider errors (`ProviderError`) must propagate from Celery tasks** for `autoretry_for` to work. Only catch domain-specific errors (`GenerationError`) in task bodies — never swallow provider errors with manual `except ProviderError` blocks that return `None`.

## Build references (historical)

- `.superpowers/build/` contains the original phased build plan and implementation brief — useful for understanding design intent behind features.
- `.opencode/skills/` has `django-celery` and `pgvector-semantic-search` skill files.

## Preferences
When testing, valid user creds will always be in the .env file. Use it to log in as a test user rather than creating a new one.