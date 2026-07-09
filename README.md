# LearnerHQ Backend

A gamified, AI-powered learning platform backend. Learners explore subjects, follow structured roadmaps, take quizzes, earn points, climb leaderboards, and receive spaced-repetition notifications — all powered by LLM-generated content with human-in-the-loop review.

**Stack:** Python 3.12 · Django 6.0 · DRF 3.15 · Celery · PostgreSQL (pgvector) · Redis · Sentry · Docker · Railway

---

## Project Traction

The codebase originated from the opencore community as a large-language-model coding benchmark. I completed the full implementation across all 10 phases, including:

- 280 **passing tests** (pytest, 6s runtime)
- **Zero warnings**, clean CI
- Production Dockerfile with multi-service Railway deployment (web + worker + beat)
- GitHub Actions CI pipeline (test-on-push)
- Sentry error tracking, structured logging, DRF throttling
- Email service via Brevo/Anymail with async Celery dispatch
- Full OpenAPI schema (`/api/docs/`) with documented request/response shapes on every endpoint

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Frontend   │────▶│  Gunicorn    │────▶│  PostgreSQL  │
│  (React/    │     │  (Django     │     │  (pgvector)  │
│   whatever) │◀────│   + DRF)     │◀────│              │
└─────────────┘     └──────┬───────┘     └──────────────┘
                           │
                    ┌──────┴───────┐
                    │   Celery     │
                    │  Worker +    │
                    │   Beat       │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │    Redis     │
                    │   (broker)   │
                    └──────────────┘
```

**Design decisions:**

| Decision | Rationale |
|----------|-----------|
| GenericAPIView over ViewSet | Explicit per-endpoint control; each action maps to a named service function rather than being implicit in a ViewSet's lifecycle |
| Service layer | Views never touch models or AI providers directly. Business logic lives in `services.py`, making it testable without HTTP |
| Dataclass return types | `LevelCheckResult`, `ResolveResult` etc. encode outcomes as typed contracts rather than bare dicts |
| Celery for async | Quiz generation, content generation, email dispatch, and scheduled notification checks all run out-of-band; Beat handles scheduling |
| pgvector + HNSW index | Semantic search over subjects for intelligent topic resolution |
| HTTP-only cookies for auth | JWT in cookies (not localStorage) prevents XSS token exfiltration |
| Config/utils over apps/utils | Shared infrastructure (email, Celery tasks) lives alongside settings, not as a domain app; keeps apps/ purely domain code |

---

## Features

### Phase 0 — Subject Discovery
- Users search for subjects; the system canonicalizes via semantic similarity (pgvector + LLM rank/resolve)
- Explore subjects by popularity with enrollment/interested/completed state per user

### Phase 1 — Subject Enrollment
- Up to 5 active subjects per user
- Interest marking, subject suggestions based on interest + popularity
- Auto-advance subject selection when a subject is completed

### Phase 2 — Smart Roadmaps
- LLM generates 20–25 topics across 3 depth levels per subject
- Roadmap editing: insert/append/update topics with bonus credit for existing progress

### Phase 3 — Topic Content
- LLM generates summaries + resource links with JSON schema validation
- Content review pipeline with retry logic; failed content auto-regenerates
- Pre-generation: next 2 topics buffered in background

### Phase 4 — Topic Progression
- Users progress through topics; passing 80% of current level's topics unlocks the next level
- Completing level 3 marks the subject as completed
- Mid-level-up returns subject suggestions

### Phase 5 — Quizzes
- Normal (4–6 questions) and advanced (9–12 questions) quiz types
- Cooldown timer (1 hour) between retakes
- Prior-missed-question context for targeted retry
- 60% pass threshold; points only awarded on pass
- Advanced quiz requires normal pass + resource links viewed

### Phase 6 — Leaderboard & Social
- Global leaderboard (top 50 by total points across all subjects)
- Per-topic mini-leaderboard
- "Others learning this topic" — prioritizes active users at similar level
- Visibility toggles (hide from leaderboard/others still counts points for ranking)

### Phase 7 — Notifications & Scheduling
- Configurable notification frequency (1–24 hours) per subject
- Celery Beat dispatch every 15 minutes; fan-out per-user notification tasks
- Due time advances from original schedule (not actual send time)
- Retry-then-send-late mechanism for failed notifications

### Phase 8 — Auth & Account
- Sign up/sign in with JWT in HTTP-only cookies (access: 15min, refresh: 7 days)
- Password reset with time-limited tokens + async email dispatch
- Profile management (display name, avatar, bio, visibility toggles)

---

## Testing

```
apps/
├── core/tests/          # Auth, profile, password reset, email
├── ai/tests/            # Provider abstraction, retry logic
└── learning/tests/      # Views, content generation, progression, leaderboard, notifications, quizzes
```

- **280 tests**, all passing in ~6s
- SQLite in-memory database (no Postgres required for test suite)
- `CELERY_TASK_ALWAYS_EAGER = True` — no Redis needed in CI
- Test settings disable throttling and use fast password hasher

### Running tests

```bash
pytest           # all tests
pytest -v        # verbose
pytest --cov     # coverage report
```

---

## API Overview

Interactive docs at `/api/docs/` (Swagger UI) or raw schema at `/api/docs/schema/`.

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/auth/signup/` | POST | No | Create account |
| `/api/auth/signin/` | POST | No | Sign in |
| `/api/auth/refresh/` | POST | Cookie | Refresh JWT |
| `/api/auth/signout/` | POST | Yes | Sign out |
| `/api/auth/password-reset/request/` | POST | No | Request reset email |
| `/api/auth/password-reset/confirm/` | POST | No | Confirm reset |
| `/api/profile/` | GET/PATCH | Yes | Read/update profile |
| `/api/learning/explore/` | GET | Yes | Browse subjects |
| `/api/learning/subject-suggestions/` | GET | Yes | Get suggestions |
| `/api/learning/subjects/{id}/interest/` | POST/DELETE | Yes | Toggle interest |
| `/api/learning/subjects/{id}/add/` | POST | Yes | Enroll |
| `/api/learning/subjects/{id}/` | DELETE | Yes | Unenroll |
| `/api/learning/subjects/{id}/check-progress/` | POST | Yes | Check level-up |
| `/api/learning/subjects/{id}/notification-frequency/` | PATCH | Yes | Set notification interval |
| `/api/learning/subjects/{id}/notification-status/` | GET | Yes | Notification state |
| `/api/learning/leaderboard/` | GET | Yes | Global leaderboard |
| `/api/learning/leaderboard/topics/{id}/` | GET | Yes | Topic leaderboard |
| `/api/learning/leaderboard/topics/{id}/others/` | GET | Yes | Others learning |
| `/api/learning/topics/{id}/quiz/generate/` | POST | Yes | Generate quiz |
| `/api/learning/topics/{id}/quiz/submit/` | POST | Yes | Submit answers |
| `/api/learning/topics/{id}/resource-links-viewed/` | POST | Yes | Mark links viewed |
| `/api/health/` | GET | No | Health check |

---

## Production Deployment

### Railway (recommended)

The project ships with `Dockerfile`, `railway.json` (web/worker/beat), and `.github/workflows/ci.yml`.

1. Push to GitHub
2. Create Railway project from the repo
3. Railway provisions Postgres + Redis; set env vars:

```
DJANGO_SECRET_KEY=<generated>
DATABASE_URL=<railway-postgres>
CELERY_BROKER_URL=<railway-redis>
ALLOWED_HOSTS=.railway.app,yourdomain.com
CORS_ALLOWED_ORIGINS=https://your-frontend.com
GEMINI_API_KEY=<your-key>
GROQ_API_KEY=<your-key>
BREVO_API_KEY=<your-key>
SENTRY_DSN=<your-dsn>
```

### CI/CD

GitHub Actions runs `pytest` on every push/PR to main — no external services required (SQLite + eager Celery in CI).

### Environment variables

See `.env.example` for the full list. Production settings (`config/settings/production.py`) include:

- HSTS (1-year preload), SSL redirect, secure cookies
- XSS protection, clickjacking, content-type sniffing protection
- CORS via `django-cors-headers`
- Whitenoise for static files
- Sentry (DjangoIntegration + LoggingIntegration, 0.1 traces/profile rate)
- Console logging with structured format

---

## Project Structure

```
config/
├── settings/           # base.py, test.py, production.py
├── utils/              # email_service, email_notifications, Celery tasks
├── celery.py           # Celery app + Beat schedule
├── urls.py             # Root URL config
└── wsgi.py             # WSGI entrypoint

apps/
├── core/               # User accounts, auth, password reset, profile
│   ├── authentication.py   # CookieJWTAuthentication
│   ├── serializers.py
│   ├── services.py
│   ├── models.py
│   └── views.py
├── ai/                 # AI provider abstraction (Gemini, Groq)
│   ├── services.py         # generate_content, review_content, etc.
│   └── providers.py        # Provider selection + failover
└── learning/           # Subjects, roadmaps, topics, quizzes, leaderboard
    ├── models.py           # Subject, Topic, TopicProgress, QuizAttempt, etc.
    ├── serializers.py
    ├── services.py
    ├── tasks.py            # Celery tasks (notifications, content generation)
    └── views.py
```

---

## Development Setup

```bash
# Clone
git clone <repo>
cd learnerhq-backend

# Virtual environment
python -m venv venv
.\venv\Scripts\activate   # Windows
source venv/bin/activate  # macOS/Linux

# Dependencies
pip install -r requirements.txt

# Environment
cp .env.example .env
# Edit .env with your DATABASE_URL, API keys, etc.

# Migrate
python manage.py migrate

# Run
python manage.py runserver 8000
celery -A config worker -l info   # separate terminal
celery -A config beat -l info     # separate terminal

# Test
pytest
```

---

## Gradients of Engineering Quality

This project demonstrates:

- **Test-driven iteration** — every feature was written against tests; the suite caught regressions across 10 phases
- **Defensive programming** — LLM output is validated against JSON schemas with typed exceptions, retry loops, and fallback paths
- **Separation of concerns** — views (HTTP), services (business logic), tasks (async), providers (AI), serializers (validation/docs) are strictly layered
- **Production readiness from day one** — Sentry, structured logging, throttling, security headers, CORS, Docker, CI/CD were part of the architecture, not retrofits
- **Schema-first API** — every endpoint has a documented OpenAPI schema with request/response shapes, field descriptions, and status codes
- **Trade-off awareness** — docs record *why* decisions were made (e.g., `config/utils/` over `apps/utils/`, GenericAPIView over ViewSet, cookies over localStorage)
