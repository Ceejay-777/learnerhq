# Future Features (Post-MVP)

## Broad-Topic AI Sub-Topic Generation

When a user enters a very broad subject (e.g., "Biology", "History", "Music"),
the AI should generate specific sub-topic suggestions to help them niche down
(e.g., "Did you mean: Cellular Biology, Marine Biology, Genetics, or Ecology?").

This extends the existing `narrow` action in `resolve_or_create_subject()` —
instead of returning a single suggestion when no match is found, generate
multiple options from the AI when the query is too broad/general.

=======================

Decouple model choice from code, with a fallback chain, not a single hardcoded model. Right now a deprecation is a crash. It should be: try primary model, on 404/429 fall through to a secondary model, log that the fallback fired. That turns "the pipeline is down" into "the pipeline noticed something changed and kept going," which is the difference between you finding out from a monitoring alert versus finding out from broken content days later.

Add a canary check before batch runs, not during them. A single cheap call to each configured model at the start of a run, confirming it actually responds, would have caught both of today's breaks before they touched real topics instead of after. This is a five-minute addition that turns "generate/review tasks crash mid-run" into "startup check fails loudly and immediately."

Stop making the LLM review the only quality gate. Some of what your review step is checking doesn't need a model call at all. Resource links can be validated with an actual HTTP check (does the URL resolve, is it not a 404 itself). Word count and structure can be checked deterministically. That shrinks what the probabilistic judge actually has to catch down to the things that genuinely need judgment, factual accuracy and topic relevance, and makes the whole gate less fragile since half of it no longer depends on an API call succeeding at all.