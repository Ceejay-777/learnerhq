# Live Happy-Path E2E Walkthrough Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the LearnerHQ backend works for a real user by walking the full happy path against live infrastructure (real AI, real Celery, real pgvector, real cookie auth) and producing a comprehensive report of what works and what breaks.

**Architecture:** This is not a code task -- it's a verification task. We use a 4-service `docker-compose.yml` (web/worker/beat/redis) to stand up the full stack against a Neon branch database. The walkthrough drives the real HTTP API with curl + cookie capture, sequentially exercising the same endpoints a frontend dev would hit in Postman. No mocks, no shortcuts.

**Tech Stack:** Docker Compose · Django 5.1+ · DRF · Celery · Redis 7 · PostgreSQL+pgvector (Neon) · Gemini (embedding) · Groq (generation/review) · curl + jq

## Global Constraints

- Test user: `e2e-tester@learnerhq.test` / password `E2ETestPass!2026` (hardcoded, re-used every run)
- Subject: `ww2 pacific theater` (lowercase, abbreviation, subtopic -- stress-tests the standardize path)
- Settings module: `config.settings.development` (override `.env`'s `ENVIRONMENT=production` to avoid Sentry/HSTS/send_default_pii)
- Re-run behavior: re-enroll and accumulate data. Do NOT delete between runs.
- DB target: Neon branch (already in `.env` as `DATABASE_URL`). No local Postgres needed.
- Port 8000 and 6379 must be free locally.
- No file edits other than creating `docker-compose.yml` at repo root.
- LLM cost budget: ~11 calls per run (~1 embedding + 1 roadmap + 1 content + 3 review + 2 quiz + 1 standardize).

## File Structure

**Created:**
- `docker-compose.yml` -- 4 services: redis, web (Django runserver), worker (Celery), beat (Celery beat)
- `e2e_results/step_N_*.json` -- per-step request/response captures (gitignored)
- `docs/superpowers/plans/2026-07-12-happy-path-e2e.md` -- this plan

**Modified:** None.

**Read but not modified:**
- `.env` -- verify all keys present and valid
- `config/settings/development.py` -- confirm dev settings exist
- `Dockerfile` -- confirm base image works for all 3 Django services
- `config/celery.py` -- confirm beat schedule
