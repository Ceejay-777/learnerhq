# Follow-ups: things to look into

A running list of **non-bug improvements** worth investigating to help the system: quality, reliability, cost and speed ideas that are not broken behavior. Real defects go in `known-bugs.md`.

**How to use this file**
- One entry per item, numbered `F-n`. Never reuse a number.
- Keep the evidence and the options, so whoever picks it up does not repeat the investigation.
- Status: `Open`, `In progress`, `Done`, or `Dropped` (say why).
- When an item is decided or finished, update its status here and link the commit or doc.

| # | Area | Status | Summary |
|---|------|--------|---------|
| F-1 | AI / content quality | Open (deferred until after MVP) | Generated resource links are mostly dead or invented |
| F-2 | AI / quiz quality | Open (deferred until after MVP) | Quiz answer keys are never verified (blind second-model check) |
| F-3 | Access control | Open | Topic leaderboard and others-learning endpoints have no enrollment check |

---

## F-1. Generated resource links are mostly dead or invented

**Area:** topic content (`generate_topic_content`), review step.
**Status:** Open, deferred until after the MVP.

**What is happening.** Each topic's content includes 2-5 external links written by the AI. Nothing checks them, and the review model does not either, so invented URLs reach learners.

**Evidence** (`docs/groq-model-test-report.md`, 56 links from real topic generations):
- 24 dead (404/410/DNS), 13 working, 19 unverifiable (the site blocks automated checks, mostly Wikipedia and realpython.com).
- YouTube: **13 dead, 1 working**. The model invents plausible video IDs.
- Recipe and blog sites (kingarthurbaking.com, theperfectloaf.com, thefreshloaf.com): invented deep links, all dead.
- Official docs (kubernetes.io): 7 of 7 working. Wikipedia article links look mostly valid but could not be verified from a script.
- The same problem existed with Gemini, so it is not caused by the model choice.

**Options to look into**
1. **Verify and drop dead links server-side** after generation: YouTube via its oEmbed endpoint, other sites with a GET using a normal browser User-Agent. Treat 404/410/DNS failure as dead; treat 403/429 as unknown and keep. Needs a minimum count (the schema wants 2-5), so regenerate or fall back when too few survive.
2. **Prefer sources that are hard to invent:** Wikipedia (look the title up through the Wikipedia API and build the URL ourselves), official documentation.
3. **Stop asking for YouTube links**, or resolve them through YouTube search instead of trusting model-written IDs.
4. **Reviewer check:** give the review step the verification result so it can fail content with bad links (only useful if option 1 runs first).

**Things to decide**
- How much latency is acceptable (link checks add a few seconds in the background task, no user waiting).
- Whether a topic with fewer than 2 valid links is blocked, regenerated, or published with what it has.
- Whether to re-check existing stored topics; a one-off cleanup script could prune dead links already in the database.

**Where the code is:** `apps/learning/services.py` (`_validate_topic_content`, `generate_topic_content`), `apps/learning/tasks.py` (`generate_content_for_topic`).

---

## F-2. Quiz answer keys are never verified (blind second-model check)

**Area:** quiz generation (`generate_quiz`).
**Status:** Open, deferred until after the MVP.

**What is happening.** A quiz is saved exactly as the AI wrote it, including its marked correct answer for each question. Nothing checks whether that answer is right or whether the question is ambiguous. A wrong key marks a learner who chose the true answer as wrong, and it scores points they did or did not earn.

**The idea.** After generating a quiz, ask a *different* model to answer every question **without seeing the marked answers**. Where its answer disagrees with the stored `correct_index`, treat that question as suspect: drop it, or regenerate the quiz.

**Evidence** (`docs/groq-model-test-report.md`, blind judge on generated quizzes, small samples of 2-3 quizzes per cell):
- Mean agreement with the key: normal quizzes 67-87%, advanced quizzes 75-90%. Worst single quizzes were as low as 25% (qwen normal) and 55% (gpt-oss-120b advanced).
- So roughly 1 in 5 questions disagrees with a blind second model. Some of that is the judge being wrong, but some are genuinely bad questions.
- Judge cost: about 1.1-1.5s for the gpt-oss models, 2.5-6s for qwen (it thinks longer).

**Options to look into**
1. **Drop disagreeing questions** and keep the quiz if at least 4 (normal) or 9 (advanced) questions remain; otherwise regenerate. The `total_points` window (90-110, 180-220) must still hold, so points need rescaling after a drop.
2. **Regenerate only the disagreeing questions** instead of the whole quiz.
3. **Two judges**, and drop only when both disagree with the key (fewer false drops, double the cost).
4. **Judge selection:** the judge should not be the generator model; use the other gpt-oss model, since qwen was slowest and the weakest quiz writer.
5. Show the judge's disagreement count in logs first (shadow mode) to measure the real rate before dropping anything.

**Risks and trade-offs**
- The judge can be wrong, so good questions will sometimes be dropped. Shadow mode first would quantify that.
- Adds latency to a path where the user is waiting: today quiz generation runs inside the HTTP request. Consider generating quizzes in the background ahead of time (also removes the 3-9s wait).
- Does not catch questions that are *ambiguous but agreed on*, or factually wrong in a way both models share.

**Where the code is:** `apps/learning/services.py` (`generate_quiz`, `_validate_quiz_response`), chains in `apps/ai/services.py` (`_ACTION_CHAINS`, new action would be e.g. `quiz_check`).

---

## F-3. Topic leaderboard and others-learning have no enrollment check

**Area:** `TopicLeaderboardView`, `OthersLearningView`, `SubjectLeaderboardView` (`apps/learning/views.py`).
**Status:** Open.

**What is happening.** Every other topic endpoint (detail, quiz generate/submit, resource-links-viewed, retry) goes through `get_accessible_topic`: 404 if the user is not enrolled in the subject, 403 if the topic's level is not unlocked. These endpoints still take a bare id, so any logged-in user can read a topic's leaderboard and who else is learning it, for subjects they are not in.

**Why it was left.** They only display data, and both respect the other users' privacy settings (`leaderboard_visible`, `others_learning_visible`), so nothing is exposed that a user opted out of. Not a defect, an inconsistency.

**Options to look into**
1. Call `get_accessible_topic(request.user, topic_id)` at the top of the two topic views (404/403 like the rest).
2. Decide separately whether the subject leaderboard should stay public, for example to show on the explore preview page.

**Things to decide**
- Does the frontend call any of these for subjects the user is not enrolled in (explore or preview pages)? If so, those calls would start returning 404 and the frontend would need to stop making them.
- Should a locked level hide its leaderboard too, or only the content?

**Where the code is:** `apps/learning/views.py`, `apps/learning/services.py` (`get_accessible_topic`, `topic_leaderboard`, `others_learning`, `subject_leaderboard`).
