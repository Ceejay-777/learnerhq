# Deployment Spec

Factual reference for deploying Wicky Backend. No recommendations — only what exists in the codebase.

---

## 1. Services

### 1.1 Web (Django + Gunicorn)

| Property | Value |
|----------|-------|
| **What it does** | Serves the Django REST API, admin panel, Swagger docs, and health check endpoint |
| **Build command** | `pip install -r requirements.txt && python manage.py collectstatic --noinput` |
| **Start command** | `python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 4 --timeout 120 --graceful-timeout 60` |
| **Entrypoint file** | `config/wsgi.py` (WSGI application) |
| **WSGI module** | `config.wsgi:application` |
| **Gunicorn config** | 4 workers, 120s timeout, 60s graceful timeout (all hardcoded in Dockerfile CMD) |
| **Settings module** | `config.settings.production` (set via `DJANGO_SETTINGS_MODULE` env var in Dockerfile) |
| **Static files** | Served by Whitenoise middleware with `CompressedManifestStaticFilesStorage`. Collected to `staticfiles/` directory |
| **Migrations** | Run inline at container startup via `python manage.py migrate --noinput` |

### 1.2 Worker (Celery Worker)

| Property | Value |
|----------|-------|
| **What it does** | Processes async tasks: AI content generation, AI content review, notification fan-out, password reset emails |
| **Build command** | Same as web |
| **Start command** | `celery -A config worker -l info --concurrency=2` (docker-compose) |
| **Concurrency** | 2 (explicit in docker-compose.yml) |
| **Settings module** | Same as web (inherits `DJANGO_SETTINGS_MODULE`) |
| **Prefetch multiplier** | 1 (set in `base.py`: `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`) |
| **Task ack** | Late acknowledgment (`CELERY_TASK_ACKS_LATE = True`) |
| **Time limits** | Hard: 30 min, Soft: 25 min (both set in `base.py`) |
| **Shutdown timeout** | 60s (`CELERY_WORKER_SHUTDOWN_TIMEOUT = 60`) |

### 1.3 Beat (Celery Beat Scheduler)

| Property | Value |
|----------|-------|
| **What it does** | Sends periodic tasks to the worker queue on a schedule |
| **Build command** | Same as web |
| **Start command** | `celery -A config beat -l info` |
| **Scheduler** | `django_celery_beat.schedulers:DatabaseScheduler` (reads schedule from PostgreSQL) |
| **Settings module** | Same as web |
| **Periodic tasks defined** | 3 (see §1.4) |

### 1.4 Periodic Tasks (defined in `config/celery.py`)

| Task name | Interval | Function |
|-----------|----------|----------|
| `dispatch-notifications-every-15-min` | 15 minutes | `apps.learning.tasks.dispatch_notifications` — queries `UserSubjectProgress` for due notifications, fans out `send_notification` per user |
| `cleanup-stuck-generations-every-hour` | 60 minutes | `apps.learning.tasks.cleanup_stuck_generations` — resets topics stuck in GENERATING for >1 hour |
| `auto-select-subjects-every-15-min` | 15 minutes | `apps.learning.tasks.auto_select_subjects` — auto-enrolls opted-in idle users into top suggestion |

### 1.5 Redis

| Property | Value |
|----------|-------|
| **What it does** | Celery message broker |
| **Image** | `redis:7-alpine` (docker-compose) |
| **Port** | 6379 |
| **Healthcheck** | `redis-cli ping` every 2s, 1s timeout, 10 retries |
| **Persistence** | None configured (default RDB/AOF from Redis image) |
| **Database** | 0 (default, specified in `CELERY_BROKER_URL: redis://redis:6379/0`) |

### 1.6 PostgreSQL (external, not in docker-compose)

| Property | Value |
|----------|-------|
| **What it does** | Primary datastore for all models, Celery result backend (`django-db`), pgvector for subject embeddings |
| **Driver** | `psycopg` 3.3.4 (with `psycopg-binary`) |
| **Extension** | pgvector (enabled via migration `apps/ai/migrations/0001_enable_pgvector.py`) |
| **Celery result backend** | `CELERY_RESULT_BACKEND = 'django-db'` (stores results in `django_celery_results` tables) |
| **Result expiry** | 24 hours (`CELERY_RESULT_EXPIRES = 60 * 60 * 24`) |

---

## 2. Redis Usage

### 2.1 What Redis is used for

| Use case | Details |
|----------|---------|
| **Celery broker** | Primary and only use. All task messages are sent through Redis. Configured via `CELERY_BROKER_URL` |
| **Celery result backend** | NOT Redis — uses PostgreSQL (`django-db`) |
| **Caching** | None. No Django cache framework configured. No `django-redis`, no cache middleware, no cache backends defined |
| **Sessions** | None. Session middleware is in `MIDDLEWARE` but no session backend is configured (Django defaults to `django.contrib.sessions.backends.db`). No Redis session store |
| **Pub/Sub** | Not used |
| **Rate limiting** | Not done via Redis. DRF throttling uses Django's in-memory cache or database |

### 2.2 Redis client library

| Property | Value |
|----------|-------|
| **Library** | `redis` Python package, version 6.4.0 |
| **Import** | `import redis` (used directly in `apps/core/health.py` for health checks) |
| **Celery transport** | `kombu` 5.6.2 (Celery's AMQP abstraction, uses `redis` underneath) |

### 2.3 Redis configuration

| Setting | Value | Source |
|---------|-------|--------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` (default) | `config/settings/base.py:162` |
| `CELERY_ACCEPT_CONTENT` | `['json']` | `base.py:164` |
| `CELERY_TASK_SERIALIZER` | `json` | `base.py:165` |
| `CELERY_RESULT_SERIALIZER` | `json` | `base.py:166` |
| `CELERY_TASK_TRACK_STARTED` | `True` | `base.py:167` |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1` | `base.py:170` |
| `CELERY_TASK_ACKS_LATE` | `True` | `base.py:171` |
| `CELERY_RESULT_EXPIRES` | `86400` (24h) | `base.py:172` |
| Key prefix | Celery default (`celery`) | Not overridden |
| TTLs | None configured for broker keys | — |
| Persistence | Not configured (relies on Redis image defaults) | — |

---

## 3. Environment Variables

### 3.1 Required by all services (web, worker, beat)

All three services share the same `DJANGO_SETTINGS_MODULE` and need the same env vars because they all run the same Django codebase.

| Variable | Default | Source | Purpose |
|----------|---------|--------|---------|
| `DJANGO_SETTINGS_MODULE` | `config.settings.production` | Dockerfile ENV | Selects settings module |
| `DATABASE_URL` | `postgresql://dummy:dummy@localhost:5432/dummy` (Dockerfile build-time dummy) | Runtime env | PostgreSQL connection URL. Parsed with `urlparse` in `base.py:70` |
| `DJANGO_SECRET_KEY` | `build-time-dummy-key` (Dockerfile) | Runtime env | Django secret key for sessions, CSRF, tokens |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | `base.py:162` | Redis connection URL for Celery broker |
| `GEMINI_API_KEY` | — | Runtime env | Google Gemini API key for AI content generation and embedding |
| `GROQ_API_KEY` | — | Runtime env | Groq API key for AI content review and ranking |
| `BREVO_API_KEY` | — | Runtime env | Brevo/Anymail API key for transactional email (password reset) |
| `ALLOWED_HOSTS` | `''` (empty, production) | `production.py:26` | Comma-separated list of allowed hostnames |
| `CORS_ALLOWED_ORIGINS` | `''` (empty, production) | `production.py:41-45` | Comma-separated list of allowed CORS origins |
| `SENTRY_DSN` | `None` | `production.py:11` | Sentry DSN for error tracking (optional — only initialized if set) |
| `ENVIRONMENT` | `production` | `production.py:21` | Sentry environment tag |
| `SECURE_SSL_REDIRECT` | `True` | `production.py:29` | Whether to redirect HTTP to HTTPS |
| `SERVICE_BASE_URL` | `http://localhost:8000` | `base.py:181` | Base URL for generating password reset links in emails |

### 3.2 Additional variables used in development only

| Variable | Default | Source | Purpose |
|----------|---------|--------|---------|
| `CELERY_TASK_ALWAYS_EAGER` | `False` | `development.py:25` | Run tasks synchronously (only in test settings) |

### 3.3 Variables per service

| Service | Needs |
|---------|-------|
| **web** | All of §3.1. Additionally needs `PORT` (set by Railway/host platform, used in Dockerfile CMD) |
| **worker** | All of §3.1. Does NOT need `PORT` |
| **beat** | All of §3.1. Does NOT need `PORT` |

In practice, all three services receive the same env var set (docker-compose uses YAML anchor `&django_env` / `*django_env`).

---

## 4. Ports and Networking

### 4.1 Listening ports

| Service | Port | Binding | Protocol |
|---------|------|---------|----------|
| **Web (Gunicorn)** | `$PORT` | `0.0.0.0` | HTTP (behind reverse proxy/TLS terminator) |
| **Redis** | `6379` | `0.0.0.0` (docker-compose) | Redis protocol |
| **Worker** | — | No listening port | Connects outbound to Redis and PostgreSQL |
| **Beat** | — | No listening port | Connects outbound to Redis (scheduler) and PostgreSQL |
| **PostgreSQL** | `5432` (default) | External service | PostgreSQL protocol |

### 4.2 Service-to-service connectivity

```
┌──────────┐  HTTP   ┌──────────┐  SQL    ┌────────────┐
│  Client  │────────▶│   Web    │────────▶│ PostgreSQL │
│ (frontend│◀────────│ (Gunicorn│◀────────│            │
│  /proxy) │         │  :$PORT) │         └────────────┘
└──────────┘         └────┬─────┘
                          │ SQL
                     ┌────┴─────┐
                     │ PostgreSQL│
                     └────┬─────┘
                          │
            ┌─────────────┼─────────────┐
            │ SQL         │ SQL         │ SQL
       ┌────┴─────┐ ┌────┴─────┐ ┌────┴─────┐
       │  Worker  │ │   Beat   │ │   Web    │
       │(Celery)  │ │(Celery)  │ │(Gunicorn)│
       └────┬─────┘ └────┬─────┘ └──────────┘
            │ Redis       │ Redis
            └──────┬──────┘
              ┌────┴─────┐
              │  Redis   │
              │  :6379   │
              └──────────┘
```

| Source | Destination | Protocol | Purpose |
|--------|-------------|----------|---------|
| Web | PostgreSQL | TCP/5432 | Database queries, migrations |
| Web | Redis | TCP/6379 | Health check only (`redis.Redis.from_url()` ping) |
| Worker | PostgreSQL | TCP/5432 | Task database access, Celery result backend |
| Worker | Redis | TCP/6379 | Task queue (consume/produce messages) |
| Beat | Redis | TCP/6379 | Scheduler (periodic task dispatch) |
| Beat | PostgreSQL | TCP/5432 | Database scheduler (`django_celery_beat.schedulers:DatabaseScheduler`) |
| Web | External APIs | HTTPS/443 | Gemini API, Groq API, Brevo API |
| Worker | External APIs | HTTPS/443 | Gemini API, Groq API (AI content generation/review) |

---

## 5. Dependencies Between Services

### 5.1 Startup dependencies

| Service | Must wait for | Condition |
|---------|---------------|-----------|
| **Web** | PostgreSQL | Migrations run at startup (`python manage.py migrate --noinput`) |
| **Web** | Redis | Health check runs on requests, but not required for startup |
| **Worker** | Redis | `depends_on: redis: condition: service_healthy` (docker-compose) |
| **Worker** | PostgreSQL | Implicit — tasks query DB immediately |
| **Beat** | Redis | `depends_on: redis: condition: service_healthy` (docker-compose) |
| **Beat** | PostgreSQL | Implicit — `DatabaseScheduler` reads schedule from DB |

### 5.2 Runtime dependencies

| Service | Runtime dependency | Failure behavior |
|---------|-------------------|------------------|
| **Web** | PostgreSQL | 500 errors on any DB query. Health check returns `{"status": "degraded"}` |
| **Web** | Redis | Health check shows degraded. API still functions (no caching, no sessions via Redis). Beat dispatch still works |
| **Worker** | Redis | Worker cannot consume tasks. All queued tasks accumulate. Worker may crash if Redis is unreachable at startup |
| **Worker** | PostgreSQL | Tasks that query DB fail. Celery retries with exponential backoff (for `ProviderError`) or marks topic as FAILED |
| **Beat** | Redis | Beat cannot dispatch periodic tasks. Queue remains empty |
| **Beat** | PostgreSQL | `DatabaseScheduler` cannot read/update schedule |

### 5.3 Service independence

- **Web and Worker are fully independent** at runtime. The web app does not need the worker to be running to serve requests. Tasks are queued in Redis and consumed asynchronously.
- **Beat and Worker are decoupled via Redis**. Beat publishes periodic task messages; Worker consumes them. Either can restart independently.
- **No service depends on another service's HTTP endpoint**. All inter-service communication is through PostgreSQL and Redis.

---

## 6. Resource Footprint

### 6.1 Known values

| Resource | Value | Source |
|----------|-------|--------|
| Gunicorn workers | 4 | Dockerfile CMD |
| Gunicorn timeout | 120s | Dockerfile CMD |
| Celery worker concurrency | 2 | docker-compose.yml |
| Celery task hard time limit | 30 min | `base.py:168` |
| Celery task soft time limit | 25 min | `base.py:169` |
| Celery prefetch multiplier | 1 | `base.py:170` |
| DRF page size | 50 | `base.py:115` |
| Health check cache | 5s | `health.py:14` |

### 6.2 Estimated memory (unknown — not profiled)

| Service | Estimated RAM | Notes |
|---------|--------------|-------|
| Web (Gunicorn, 4 workers) | ~200–400 MB | 4 pre-forked Django processes. Each worker loads Django + all dependencies. `google-generativeai` and `pydantic` are large packages |
| Worker (Celery, concurrency=2) | ~150–300 MB | 2 preforked child processes. Same Django + AI library load |
| Beat | ~80–120 MB | Single process, minimal workload (just scheduling) |
| Redis | ~30–60 MB | Depends on queue depth. Celery message overhead is small |
| PostgreSQL | External | Not managed by this deployment |

### 6.3 Disk

| Item | Size | Notes |
|------|------|-------|
| Python dependencies | ~300–500 MB | `google-generativeai`, `pydantic`, `Django`, `psycopg-binary` are the largest |
| Static files (`staticfiles/`) | ~5–20 MB | Compressed manifest storage via Whitenoise |
| Container image (Python 3.12-slim) | ~800 MB–1.2 GB | Including deps |

---

## 7. Build Output

### 7.1 What the production build produces

| Artifact | Location | Generated by |
|----------|----------|-------------|
| Python bytecode | `__pycache__/` dirs | `PYTHONDONTWRITEBYTECODE=1` prevents `.pyc` files in Docker |
| Static files | `staticfiles/` directory | `python manage.py collectstatic --noinput` (run twice: once at build, once at startup) |
| Django migrations | `apps/*/migrations/` | Already in repo; applied at startup via `migrate --noinput` |
| WSGI application | `config/wsgi.py` | Entry point for Gunicorn |

### 7.2 Runtime behavior

- **No Node.js runtime required**. The backend is pure Python/Django.
- **No frontend build step**. Static files are served by Whitenoise from the `staticfiles/` directory.
- **Static files are collected twice**: once at Docker build time (`RUN python manage.py collectstatic --noinput` in Dockerfile line 16), and again at container startup (in Dockerfile CMD line 18). The startup collectstatic ensures any migration-added static files are captured.

### 7.3 Dockerfile specifics

```dockerfile
FROM python:3.12-slim-bookworm

# Build-time env (overridden at runtime)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings.production
ENV DATABASE_URL=postgresql://dummy:dummy@localhost:5432/dummy
ENV DJANGO_SECRET_KEY=build-time-dummy-key

# Build
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput

# Run
CMD python manage.py migrate --noinput && \
    python manage.py collectstatic --noinput && \
    gunicorn config.wsgi:application \
        --bind 0.0.0.0:$PORT \
        --workers 4 \
        --timeout 120 \
        --graceful-timeout 60
```

- The build-time `DATABASE_URL` and `DJANGO_SECRET_KEY` are dummy values to satisfy `collectstatic` (which imports Django settings). They are overridden at runtime.
- `$PORT` is injected by the hosting platform (Railway, etc.).

### 7.4 Railway deployment

| Property | Value |
|----------|-------|
| Build config | `railway.json` — `{"build": {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}}` |
| Services to deploy | 3 separate Railway services: web, worker, beat (each with its own start command) |
| PostgreSQL | Provisioned by Railway, URL injected as `DATABASE_URL` |
| Redis | Provisioned by Railway, URL injected as `CELERY_BROKER_URL` |
| Health check endpoint | `GET /api/health/` — returns `{"status": "ok", "checks": {"database": true, "redis": true, "celery": true}}` |

---

## 8. CI/CD

| Property | Value |
|----------|-------|
| Platform | GitHub Actions |
| Trigger | Push or PR to `main` |
| Runner | `ubuntu-latest` |
| Python version | 3.12 |
| Settings module | `config.settings.test` |
| Database | SQLite in-memory (no PostgreSQL in CI) |
| Redis | Not required (`CELERY_TASK_ALWAYS_EAGER = True`) |
| Test command | `pytest --tb=short -v` |
| Test count | 280 tests, ~6s runtime |
| No deployment step | CI only runs tests; no CD pipeline configured in `ci.yml` |
