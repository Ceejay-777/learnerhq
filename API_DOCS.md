# LearnerHQ API — Production Endpoint Reference

**Base URL:** `https://learnerhq-production.up.railway.app`

**Auth:** JWT tokens in HTTP-only cookies (`access_token`: 15 min, `refresh_token`: 7 days). No `Authorization` header.

**Response format (working endpoints):**
- Success: `{"data": {...}, "status": "success"}` or `{"detail": "...", "status": "success"}`
- Error: `{"detail": "...", "status": "error"}` with appropriate HTTP status

---

## Health

### `GET /api/health/`

**No auth required.**

Response `200`:
```json
{"status": "ok"}
```

---

## Auth — `/api/auth/`

### `POST /api/auth/signup`

**No auth required.** Creates a user and sets auth cookies.

Request:
```json
{
  "email": "user@example.com",
  "password": "StrongPass123!",
  "first_name": "Jane",
  "last_name": "Doe"
}
```

Response `201`:
```json
{
  "data": {
    "email": "user@example.com",
    "display_name": "",
    "avatar": "",
    "bio": "",
    "leaderboard_visible": true,
    "others_learning_visible": true,
    "date_joined": "2026-07-11T04:32:30.199865Z"
  },
  "status": "success"
}
```

Sets cookies: `access_token`, `refresh_token`.

**Error cases:**
- `400` — Missing fields: `{"detail": "This field is required.", "status": "error"}`
- `400` — Weak password: `{"detail": "This password is too short. It must contain at least 8 characters.", "status": "error"}`
- `400` — Duplicate email: `{"detail": "A user with this email already exists.", "status": "error"}`

---

### `POST /api/auth/signin`

**No auth required.** Authenticates user and sets auth cookies.

Request:
```json
{
  "email": "user@example.com",
  "password": "StrongPass123!"
}
```

Response `200`:
```json
{
  "data": {
    "email": "user@example.com",
    "display_name": "",
    "avatar": "",
    "bio": "",
    "leaderboard_visible": true,
    "others_learning_visible": true,
    "date_joined": "2026-07-11T04:32:30.199865Z"
  },
  "status": "success"
}
```

Sets cookies: `access_token`, `refresh_token`.

**Error cases:**
- `401` — Invalid credentials: `{"detail": "Invalid email or password.", "status": "error"}`

---

### `POST /api/auth/refresh`

**No auth required.** Requires `refresh_token` cookie. Issues new token pair.

Request body: none

Response `200`:
```json
{"detail": "Token refreshed.", "status": "success"}
```

Sets cookies: `access_token`, `refresh_token`.

**Error cases:**
- `401` — No cookie: `{"detail": "Refresh token not provided.", "status": "error"}`
- `401` — Invalid/expired: `{"detail": "Invalid or expired refresh token.", "status": "error"}`

---

### `POST /api/auth/signout`

**Requires auth (CookieJWT).** Clears auth cookies.

Request body: none

Response `200`:
```json
{"detail": "Signed out.", "status": "success"}
```

---

### `POST /api/auth/password-reset/request`

**No auth required.** Sends a password reset email via Celery/Brevo.

Request:
```json
{"email": "user@example.com"}
```

Response `200` (always — no user enumeration):
```json
{"detail": "If that email exists, a reset link has been sent.", "status": "success"}
```

> ⚠️ **BUG:** Returns `500` HTML error when the email **exists** because the Celery task dispatch to Redis fails (likely no Redis provisioned or `CELERY_BROKER_URL` not configured).

---

### `POST /api/auth/password-reset/confirm`

**No auth required.** Resets password using a token.

Request:
```json
{
  "reset_token": "token-string-from-email",
  "password": "NewStrongPass123!"
}
```

Response `200`:
```json
{"detail": "Password reset successful.", "status": "success"}
```

**Error cases:**
- `400` — Missing fields: `{"detail": "This field is required.", "status": "error"}`
- `400` — Weak password: `{"detail": "This password is too short...", "status": "error"}`
- `400` — Invalid/expired token: `{"detail": "...", "status": "error"}`

---

### `GET /api/auth/profile`

**Requires auth (CookieJWT).** Returns the authenticated user's profile.

Response `200`:
```json
{
  "data": {
    "email": "user@example.com",
    "display_name": "",
    "avatar": "",
    "bio": "",
    "leaderboard_visible": true,
    "others_learning_visible": true,
    "date_joined": "2026-07-11T04:32:30.199865Z"
  },
  "status": "success"
}
```

**Error cases:**
- `401` — No auth: `{"detail": "Authentication credentials were not provided.", "status": "error"}`

---

### `PATCH /api/auth/profile`

**Requires auth (CookieJWT).** Updates profile fields.

Request:
```json
{
  "display_name": "Jane Updated",
  "bio": "My new bio"
}
```

Response `200` (returns full profile):
```json
{
  "data": {
    "email": "user@example.com",
    "display_name": "Jane Updated",
    "avatar": "",
    "bio": "My new bio",
    "leaderboard_visible": true,
    "others_learning_visible": true,
    "date_joined": "2026-07-11T04:32:30.199865Z"
  },
  "status": "success"
}
```

---

## Learning — `/api/learning/`

**All learning endpoints require auth (CookieJWT).**

### `GET /api/learning/subjects/suggestions`

Returns AI-generated subject suggestions for the user.

Response `200`:
```json
[]
```
(Empty array when no suggestions generated yet.)

---

### `GET /api/learning/explore`

Returns available subjects to explore.

Response `200`:
```json
[]
```
(Empty array when no subjects exist.)

---

### `POST /api/learning/explore/{subject_id}/interest`

Marks interest in a subject.

Response `201`: No body.

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `DELETE /api/learning/explore/{subject_id}/interest`

Removes interest in a subject.

Response `204`: No body.

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `POST /api/learning/subjects/{subject_id}/add`

Adds a subject to the user's active subjects (max 5).

Response `201`: No body.

**Error cases:**
- `400` — Limit reached / already added: `{"detail": "...", "status": "error"}`

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `DELETE /api/learning/subjects/{subject_id}/remove`

Removes a subject from the user.

Response `204`: No body.

**Error cases:**
- `404` — Not found: `{"detail": "Not found.", "status": "error"}` ✅

---

### `POST /api/learning/subjects/{subject_id}/progress/check`

Checks level progression.

Response `200`:
```json
{
  "action": "next_topic" | "level_up" | "subject_completed",
  ...
}
```

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `GET /api/learning/subjects/{subject_id}/notification-status`

Returns notification frequency status.

Response `200`:
```json
{
  "frequency_hours": 6,
  "next_due_at": "2026-07-11T10:00:00Z"
}
```

**Error cases:**
- `404` — Subject not added: `{"detail": "Not found.", "status": "error"}`

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `PATCH /api/learning/subjects/{subject_id}/notification-frequency`

Sets notification frequency (1–24 hours).

Request:
```json
{"frequency_hours": 6}
```

Response `200`: No body.

**Error cases:**
- `400` — Invalid input: `{"detail": "...", "status": "error"}`

> ⚠️ **BUG:** Returns `500` HTML error when `subject_id` doesn't exist — `Subject.DoesNotExist` not caught.

---

### `GET /api/learning/leaderboard`

Global leaderboard.

Response `200`:
```json
[
  {
    "rank": 1,
    "user_id": 1,
    "display_name": "Updated Name",
    "total_points": 0
  }
]
```

---

### `GET /api/learning/topics/{topic_id}/leaderboard`

Topic-specific leaderboard.

Response `200`:
```json
[]
```
(Empty when no data.)

---

### `GET /api/learning/topics/{topic_id}/others-learning`

Users also learning this topic.

> ⚠️ **BUG:** Returns `500` HTML error when `topic_id` doesn't exist — `topic_leaderboard` service doesn't handle missing topics.

---

### `POST /api/learning/topics/{topic_id}/quiz/generate`

Generates a quiz for a topic.

Request:
```json
{
  "quiz_type": "NORMAL",
  "prior_missed_questions": []
}
```

Response `200`:
```json
{
  "id": 1,
  "quiz_type": "NORMAL",
  "attempt_number": 1,
  "questions": [...],
  "total_points": 100
}
```

> ⚠️ **BUG:** Returns `500` HTML error when `topic_id` doesn't exist — `Topic.DoesNotExist` not caught.

---

### `POST /api/learning/topics/{topic_id}/quiz/submit`

Submits quiz answers.

Request:
```json
{
  "attempt_id": 1,
  "answers": [...]
}
```

> ⚠️ **BUG:** Returns `500` HTML error with invalid `attempt_id` — `submit_quiz` service doesn't handle missing attempts.

---

### `POST /api/learning/topics/{topic_id}/resource-links-viewed`

Marks resource links as viewed.

> ⚠️ **BUG:** Returns `500` HTML error when `topic_id` doesn't exist — `Topic.DoesNotExist` not caught.

---

## Issues Summary

### Critical — 500 HTML instead of JSON

| # | Endpoint | Cause |
|---|---|---|
| 1 | `POST /api/auth/password-reset/request` (when email exists) | Celery `delay()` to Redis fails (broker unreachable) |
| 2 | All `{subject_id}` endpoints with invalid ID | `Subject.objects.get(id=...)` raises uncaught `DoesNotExist` |
| 3 | All `{topic_id}` endpoints with invalid ID | `Topic.objects.get(id=...)` raises uncaught `DoesNotExist` |
| 4 | `POST /api/learning/topics/{id}/quiz/submit` (invalid attempt) | `submit_quiz` service doesn't handle missing attempt |

**Root cause:** Views use `Model.objects.get(id=...)` without try-except. Django's `DoesNotExist` is not a DRF `APIException`, so the custom exception handler misses it, and Django returns an HTML 500 page instead of JSON.

### Fix recommendations

1. **Fix the views:** Replace `.get()` with `get_object_or_404()` from Django shortcuts, or wrap in try-except returning JSON `404`.
2. **Fix the exception handler:** Update `apps/core/exceptions.py` to catch `django.core.exception.ObjectDoesNotExist` and return JSON 404.
3. **Fix password reset Celery issue:** Either provision Redis in Railway and set `CELERY_BROKER_URL`, or wrap the `delay()` call in a try-except.
