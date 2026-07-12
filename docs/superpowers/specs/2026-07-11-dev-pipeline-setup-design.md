# Dev Pipeline Setup Design

**Goal:** Get every content pipeline working end-to-end in the local dev
environment — subject creation, roadmap generation, content generation,
quiz generation, review — so the API actually produces real results.

## Objective

- Fix `development.py` settings (Sentry bug, throttling)
- Create a seed command to populate the DB with 15 specific subjects
- Wire `canonicalize_subject` to a new `POST /api/learning/subjects/create` endpoint
- Verify the full Celery-powered pipeline generates topic content and quizzes

## Infrastructure

- **DB:** Neon Postgres (via `DATABASE_URL` in `.env`)
- **Redis:** Docker `redis:7` on port 6379
- **Celery:** Worker processes tasks dispatched by content generation pipeline
- **Email:** Real Brevo (`.env` has `BREVO_API_KEY`)

## Changes

### `config/settings/development.py`
- Fix Sentry import (`LoggingIntegration`)
- Disable DRF throttling for dev convenience
- Keep `CELERY_TASK_ALWAYS_EAGER = False`

### `apps/learning/management/commands/seed_subjects.py`
Management command that creates the following subjects via
`canonicalize_subject()` and enrolls a system user to trigger roadmap
generation:

1. American Football
2. Python for Beginners
3. AP Calculus AB
4. World War II
5. Creative Writing
6. Spanish for Beginners
7. Machine Learning
8. Digital Photography
9. Acoustic Guitar
10. Yoga for Beginners
11. Personal Finance
12. Cognitive Psychology
13. Ancient Egypt
14. Wine Basics
15. Film History

### `POST /api/learning/subjects/create`
New endpoint for user-facing subject creation. Calls
`canonicalize_subject(name)` which resolves via AI (Gemini embeddings
+ Groq ranking) to either match an existing subject, create a new one,
or ask for narrowing. On resolve/create, auto-enrolls the authenticated
user and triggers roadmap generation on first enrollment.

## Verification

1. `docker run -d -p 6379:6379 redis:7`
2. `celery -A config worker -l info`
3. `python manage.py migrate`
4. `python manage.py seed_subjects`
5. `python manage.py runserver 8000`
6. Hit `POST /api/learning/subjects/create`, `POST .../quiz/generate`,
   `POST .../quiz/submit` to verify each pipeline stage produces real data
