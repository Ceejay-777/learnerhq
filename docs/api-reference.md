# LearnerHQ API Reference

Base URL: `/api`

**Auth:** JWT in HTTP-only cookies (`access_token` = 15min, `refresh_token` = 7 days).
All endpoints marked "Auth required" will return `401` if the access token is missing or expired.

**Date format:** ISO 8601 (e.g. `2026-07-09T10:30:00Z`)

---

## Health Check

### `GET /api/health/`

No auth required. Returns a simple liveness check.

```
Response 200:
{
  "status": "ok"
}
```

---

## Auth — `/api/auth/`

### `POST /api/auth/signup`

Create a new account. Sets `access_token` and `refresh_token` cookies on success.

```
Request body:
{
  "email":        string,   // required, unique
  "password":     string,   // required, Django password validators enforced
  "display_name": string,   // optional, max 100 chars
  "avatar":       string,   // optional, URL
  "bio":          string    // optional
}

Response 201:
{
  "data": {
    "email":                    string,   // read-only on updates
    "display_name":             string,
    "avatar":                   string,
    "bio":                      string,
    "leaderboard_visible":      boolean,  // default true
    "others_learning_visible":  boolean,  // default true
    "date_joined":              string    // ISO 8601, read-only
  },
  "status": "success"
}
```

---

### `POST /api/auth/signin`

Authenticate existing user. Sets `access_token` and `refresh_token` cookies on success.

```
Request body:
{
  "email":    string,   // required
  "password": string    // required
}

Response 200:
{
  "data": { /* UserProfile object — same shape as signup response */ },
  "status": "success"
}

Response 401:
{
  "detail": "Invalid email or password.",
  "status": "error"
}
```

---

### `POST /api/auth/refresh`

Refresh an expiring access token. Reads the `refresh_token` cookie (no request body).
Sets new `access_token` and `refresh_token` cookies.

No request body needed — token is read from the `refresh_token` cookie.

```
Response 200:
{
  "detail": "Token refreshed.",
  "status": "success"
}

Response 401:
{
  "detail": "Refresh token not provided." | "Invalid or expired refresh token.",
  "status": "error"
}
```

---

### `POST /api/auth/signout`

Auth required. Clears `access_token` and `refresh_token` cookies. No request body.

```
Response 200:
{
  "detail": "Signed out.",
  "status": "success"
}
```

---

### `POST /api/auth/password-reset/request`

Request a password reset email. Always returns 200 regardless of whether the email exists
(prevents email enumeration). An async Celery task dispatches the email via Brevo.

```
Request body:
{
  "email": string   // required
}

Response 200:
{
  "detail": "If that email exists, a reset link has been sent.",
  "status": "success"
}
```

---

### `POST /api/auth/password-reset/confirm`

Complete a password reset using the token received via email. Tokens expire after 1 hour.

```
Request body:
{
  "email":    string,   // required, must match the token's user
  "token":    string,   // required, the reset token from the email
  "password": string    // required, new password
}

Response 200:
{
  "detail": "Password reset successful.",
  "status": "success"
}

Response 400 (validation errors):
{
  "token":   ["Token has expired."],
  "email":   ["Invalid token or email."],
  "password":["This password is too common.", ...]
}
```

---

## Profile — `/api/profile/`

Auth required (CookieJWTAuthentication — reads `access_token` cookie).

### `GET /api/profile/`

Get the current user's profile.

```
Response 200:
{
  "data": { /* UserProfile object */ },
  "status": "success"
}
```

---

### `PATCH /api/profile/`

Update the current user's profile. Partial updates allowed — send only the fields you want to change.

```
Request body:
{
  "display_name":             string,   // optional
  "avatar":                   string,   // optional, URL
  "bio":                      string,   // optional
  "leaderboard_visible":      boolean,  // optional, hide from leaderboard (still counts points)
  "others_learning_visible":  boolean   // optional, hide from "others learning" lists
}

Response 200:
{
  "data": { /* Updated UserProfile object */ },
  "status": "success"
}
```

---

## Learning — `/api/learning/`

All learning endpoints require auth (standard `IsAuthenticated` permission — reads `access_token` cookie).

---

### `GET /api/learning/explore`

Browse all available subjects, ordered by popularity (enrollment count descending).

```
Response 200:
[
  {
    "id":                int,      // subject ID
    "name":              string,   // subject name
    "enrollment_count":  int,      // active learners
    "is_enrolled":       boolean,  // current user is enrolled
    "is_completed":      boolean,  // current user completed this subject
    "is_interested":     boolean   // current user marked interest
  },
  ...
]
```

---

### `POST /api/learning/explore/<subject_id>/interest`

Mark interest in a subject (does not enroll, just signals interest for suggestions).

No request body.

```
Response 201: (empty body)
```

---

### `DELETE /api/learning/explore/<subject_id>/interest`

Remove interest marker.

No request body.

```
Response 204: (empty body)
```

---

### `GET /api/learning/subjects/suggestions`

Get personalized subject suggestions. Prioritizes subjects the user has marked interest in,
then popular subjects.

```
Response 200:
[
  {
    "id":     int,    // subject ID
    "name":   string, // subject name
    "reason": string  // e.g. "You marked interest in this subject" | "Popular subject (N learners)"
  },
  ...
]
```

---

### `POST /api/learning/subjects/<subject_id>/add`

Enroll in a subject. If the subject has no roadmap yet, one is generated (LLM call).

No request body.

```
Response 201: (empty body)

Response 400:
{
  "detail": "User is at capacity (5 active subjects)"
}
```

---

### `DELETE /api/learning/subjects/<subject_id>/remove`

Unenroll from a subject. Removes the `UserSubjectProgress` record.

No request body.

```
Response 204: (empty body)

Response 404:
{
  "detail": "Not found."
}
```

---

### `POST /api/learning/subjects/<subject_id>/progress/check`

Check if the user has completed enough topics in the current level to advance.

No request body.

```
Response 200:
{
  "action":                 "none" | "level_up" | "subject_completed",
  // Only present when action == "level_up":
  "new_level_unlocked":     int,        // the new level number (2 or 3)
  "slots_available":        int,        // how many more subjects user can add (max 5)
  // Only present when action == "subject_completed":
  "needs_subject_selection": boolean,   // true — user should pick a new subject
  // Present for both level_up and subject_completed:
  "suggestions": [
    {
      "id":     int,
      "name":   string,
      "reason": string
    },
    ...
  ]
}
```

---

### `PATCH /api/learning/subjects/<subject_id>/notification-frequency`

Set how often the user wants study reminders for this subject. The interval must be 1–24 hours.

```
Request body:
{
  "frequency_hours": int   // 1–24, required
}

Response 200: (empty body)

Response 400:
{
  "detail": "frequency_hours is required" | "frequency_hours must be an integer" | "Notification frequency must be between 1 and 24 hours"
}
```

---

### `GET /api/learning/subjects/<subject_id>/notification-status`

Get the current notification configuration for this subject.

```
Response 200:
{
  "frequency_hours": int | null,   // interval in hours, null if not configured
  "next_due_at":     string | null // ISO 8601, null if no upcoming notification
}

Response 404:
{
  "detail": "Not found."
}
```

---

### `GET /api/learning/leaderboard`

Global leaderboard — top 50 users by total accumulated points across all subjects.
Users with `leaderboard_visible = false` are hidden from the list but their points
still affect other users' rank calculations. Tie-breaker: user ID ascending.

```
Response 200:
[
  {
    "rank":          int,    // 1-based position
    "user_id":       int,
    "display_name":  string, // display_name or email fallback
    "total_points":  int     // points across all subjects
  },
  ...
]
```

---

### `GET /api/learning/topics/<topic_id>/leaderboard`

Per-topic mini-leaderboard — top 20 users by points earned on this specific topic.

```
Response 200:
[
  {
    "rank":         int,    // 1-based position
    "user_id":      int,
    "display_name": string,
    "points":       int     // points on this topic
  },
  ...
]
```

---

### `GET /api/learning/topics/<topic_id>/others-learning`

Discover other learners on the same topic. Priority order:
1. Active users at a similar level (±1 level)
2. Users who have completed the topic

Users with `others_learning_visible = false` are excluded. Max 10 results.

```
Response 200:
[
  {
    "user_id":      int,
    "display_name": string,
    "status":       string,   // e.g. "ACTIVE", "COMPLETED"
    "level":        int       // the topic's level (1, 2, or 3)
  },
  ...
]
```

---

### `POST /api/learning/topics/<topic_id>/quiz/generate`

Generate a quiz for a topic. The questions are created by an LLM and validated
against a JSON schema before being returned.

**Cooldown:** 1 hour between quiz generations for the same user + topic + quiz type.

**Question shape** (the `questions` array in the response):
```
[
  {
    "question":       string,   // the question text
    "options":        [string, string, string, string],  // 4 choices
    "correct_index":  int,      // 0–3, index of correct option
    "explanation":    string    // why the correct answer is right
  },
  ...
]
```

```
Request body:
{
  "quiz_type":              "NORMAL" | "ADVANCED",   // optional, defaults to "NORMAL"
  "prior_missed_questions": [string, ...]             // optional, for targeted retry
}

Response 200:
{
  "id":              int,      // quiz attempt ID (used in submit)
  "quiz_type":       string,   // "NORMAL" or "ADVANCED"
  "attempt_number":  int,      // 1-based, increments per retry
  "questions":       [ /* Question objects — see above */ ],
  "total_points":    int       // max possible points (normal: 90–110, advanced: 180–220)
}

Response 400:
{
  "detail": "Normal quiz already passed for this topic" |
            "Advanced quiz not available: pass the normal quiz and engage with resource links first" |
            "Please wait N minutes before retaking this quiz" |
            "..."
}
```

---

### `POST /api/learning/topics/<topic_id>/quiz/submit`

Submit answers to a quiz and get results. Points are only earned if the user passes (≥60%).
Submission is one-time — re-submitting the same attempt_id returns an error.

```
Request body:
{
  "attempt_id": int,         // ID returned by quiz/generate
  "answers":    [int, ...]   // selected option index (0–3) for each question, same order
}

Response 200:
{
  "id":              int,      // quiz attempt ID
  "passed":          boolean,  // score >= 60% of total_points
  "score":           int,      // points earned (0 if failed)
  "total_points":    int,      // max possible points
  "attempt_number":  int,      // attempt number
  "quiz_type":       string    // "NORMAL" or "ADVANCED"
}

Response 400:
{
  "detail": "Quiz already submitted" |
            "Expected N answers, got M" |
            "Answer X is not a valid option index" |
            "..."
}
```

---

### `POST /api/learning/topics/<topic_id>/resource-links-viewed`

Mark that the user has read/viewed the resource links for a topic. This is a
prerequisite for taking the advanced quiz.

No request body.

```
Response 200:
{
  "status":                  string,   // current topic progress status (e.g. "NOT_STARTED", "PASSED")
  "resource_links_viewed_at": string | null  // ISO 8601 timestamp, null if not yet viewed
}
```
