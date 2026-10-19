# Groq model test report

Date: 2026-10-14 · Purpose: check the proposed Groq chains in `plan-groq-migration.md` (the user's table) against real prompts before implementing.
Models: `openai/gpt-oss-120b` (120b), `openai/gpt-oss-20b` (20b), `qwen/qwen3.8-27b` (qwen). Embeddings: Gemini `gemini-embedding-001`.

## Method
- The app's real prompts, schemas and validators (`_validate_roadmap_response`, `_validate_topic_content`, `_validate_quiz_response`, shape checks for review/cleanup/matching), through Groq's `json_schema` response format.
- Each action ran with the **effort from the user's table** and with the **provider default** (parameter omitted). 3 runs per generation task, 2 runs per input for review/cleanup/matching.
- Ground truth where available: review (known-good text + a factually wrong text + an off-topic text), subject cleanup (5 inputs), subject matching (4 cases).
- Quiz quality proxy: a **blind answer-key check**. A different model answers each generated quiz without seeing `correct_index`; agreement with the key is reported. This is a proxy (the judge can be wrong too), with only 2-3 quizzes per cell.
- Links: every generated URL was fetched (YouTube via oEmbed). "Dead" = 404/410/DNS failure. 403/429 (bot blocking, mostly Wikipedia and realpython.com) is "unverifiable", not dead.
- Two passes: run 1 with the app's current call shape (no `max_completion_tokens`), run 2 with `max_completion_tokens=8192`.

Raw results were kept outside the repo (scratchpad). 202 + 89 calls.

## Headline findings
1. **The Groq call must set `max_completion_tokens`.** In run 1 nearly every failure was output cut off at a token ceiling: 2,048 completion tokens for 20b and qwen (qwen advanced quizzes 0/3, 20b roadmap/advanced quizzes), 3,072 for one 120b advanced quiz. Reasoning tokens count toward the cap, so thinking eats the room for the JSON. The 400 `json_validate_failed` errors seen earlier are the same cause (Groq's message: "max completion tokens reached before generating a valid document"). With 8,192 set, run 2 produced 59 valid generations out of 60 (roadmap, topic and both quiz types, all three models, both effort variants; see the table). The current `_call_openai_compatible` sets no cap.
2. **The proposed table holds up once the cap is set.** All three models produce valid output for roadmap, topic, quiz (normal and advanced); only 1 of 6 `20b` advanced-quiz runs still failed.
3. **AI-generated links are the biggest quality problem.** Of 56 links checked in run 2, 24 were dead, 13 ok, 19 unverifiable. By source: YouTube **13 dead / 1 ok**; recipe sites (kingarthurbaking.com, theperfectloaf.com, thefreshloaf.com) invented deep links (dead); kubernetes.io docs 7/7 ok; Wikipedia mostly unverifiable (bot-blocked) not dead. The same problem showed up with Gemini earlier. The review model does not check links.
4. **Roughly 1 in 5 quiz answer keys looks doubtful.** Blind-judge agreement: 75-90% for most cells (qwen normal quizzes 67%, with one quiz at 25%). Small samples, but it means wrong or ambiguous answer keys will reach learners without a check.
5. **Low/medium effort works.** For `120b` normal quizzes, `low` cut reasoning tokens from ~770-880 to ~40-50 and latency from 3.9-4.3s to 2.9-3.0s with the same validity and answer-key agreement.
6. **Subject cleanup and matching are solved cheaply.** 20b and 120b were 100% correct on every case, with `low` effort, in about 1.1-1.3s.

## Results (run 2, `max_completion_tokens=8192`; median latency)

| Action | Model | Effort | Valid | Latency | Reasoning tokens |
|---|---|---|---|---|---|
| Roadmap | 120b | medium | 3/3 | 4.2s | ~830 |
| Roadmap | 20b | medium | 3/3 | 3.7s | ~1,180 |
| Roadmap | qwen | default | 3/3 (1 of 3 had duplicate titles) | 3.7s | n/a |
| Topic content | 120b | medium | 3/3 | 3.4s | ~110 |
| Topic content | 20b | medium | 3/3 | 3.5s | ~850 |
| Topic content | qwen | default | 3/3 | 2.8s | n/a |
| Quiz normal | 120b | low | 3/3 | 3.0s | ~50 |
| Quiz normal | 20b | low | 3/3 | 1.6s | ~35 |
| Quiz normal | qwen | default | 3/3 | 3.1s | n/a |
| Quiz advanced | 120b | medium | 3/3 | 6.4s | ~890 |
| Quiz advanced | 20b | medium | 2/3 | 4.7s | ~1,520 |
| Quiz advanced | qwen | default | 3/3 | 4.9s | n/a |

Run 1 (no cap) for comparison: 120b topic medium 2/3, advanced default 2/3; 20b topic default 0/3, advanced 1/3; qwen advanced 0/3 and two `503 over capacity` errors on topic content.

### Quiz answer-key agreement (blind judge, run 2)
| Quiz | Generator (effort) | Agreement (mean, worst quiz) |
|---|---|---|
| Normal | 120b low | 87% (60%) |
| Normal | 120b default | 87% (80%) |
| Normal | 20b low | 82% (67%) |
| Normal | 20b default | 78% (75%) |
| Normal | qwen | 67% (25%) |
| Advanced | 120b medium | 75% (55%) |
| Advanced | 120b default | 83% (70%) |
| Advanced | 20b medium | 80% (70%) |
| Advanced | 20b default | 90% (80%) |
| Advanced | qwen | 78% (67%) |

Judges: qwen judged the gpt-oss quizzes; `120b` judged qwen's. No model clearly wins; `qwen` is weakest on normal quizzes, which supports keeping it last in the quiz chain.

### Review (ground truth, run 1; 6 judgments per cell)
| Model | Effort | Correct | Notes |
|---|---|---|---|
| qwen | thinking on (`default`) | 6/6 | table's primary reviewer |
| qwen | omitted | 5/6 | rejected the good text |
| 120b | medium / default | 6/6 / 6/6 | |
| 20b | medium / default | 6/6 / 5/6 | default rejected the good text |

No model passed the wrong or off-topic text (no false passes). The only errors were over-strict rejections of the good text. **Caveat:** the bad examples were blatant. Subtle factual errors were not tested.

### Subject cleanup and matching (run 1)
| Action | Model | Effort | Correct | Latency |
|---|---|---|---|---|
| Cleanup (5 inputs ×2) | 20b | low / default | 10/10 / 10/10 | 1.2s / 1.6s |
| Cleanup | 120b | low / default | 10/10 / 10/10 | 1.3s / 1.6s |
| Matching (4 cases ×2) | 20b | low / default | 8/8 / 8/8 | 1.1s / 1.5s |
| Matching | 120b | low / default | 8/8 / 8/8 | 1.1s / 1.3s |

Includes a prompt-injection input ("ignore previous instructions...") and "WW2" vs "World War I/II" disambiguation; all handled correctly.

### Embeddings
Gemini `gemini-embedding-001`: 3/3 calls OK, 3072 dimensions, 0.8-1.6s.

### Effort parameter
Accepted by all three models. `reasoning_effort` low/medium/high on gpt-oss changes reasoning tokens as expected (15 / 32 / 193 on a trivial prompt for 120b; 8 / 46 / 177 for 20b). Qwen accepts `default`, `none` and `low`.

## What this means for the plan
- **Add `max_completion_tokens` (start around 8,192) and `reasoning_effort` per action** in the Groq call. This is the single most important change.
- **Keep the user's table.** It is supported by the data. Keep `qwen` last in the quiz chain and note that it returned `503 over capacity` on 2 of 3 topic calls in run 1 (not repeated in run 2), so treat 503 as "try the next model".
- **Fix link quality before cutover:** drop or verify generated URLs server-side (YouTube oEmbed, HEAD/GET for others), prefer Wikipedia and official docs, or stop asking for YouTube links.
- **Add a quiz verification step:** have a second model answer each question blind and drop or regenerate questions where it disagrees with the key.
- Both of these quality issues exist with Gemini too; they are not caused by the provider switch.

## Limits of this test
- Small samples (3 runs per cell, 2-3 quizzes for the answer-key check); results indicate, not prove.
- Validation checks shape and counts, not teaching quality.
- Review was tested on blatant errors only.
- 503 "over capacity" and rate-limit behavior vary over time; the Groq daily request counter for `gpt-oss-120b` also looked heavily used by something else (see `plan-groq-migration.md`).
