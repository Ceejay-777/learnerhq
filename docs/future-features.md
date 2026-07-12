# Future Features (Post-MVP)

## Broad-Topic AI Sub-Topic Generation

When a user enters a very broad subject (e.g., "Biology", "History", "Music"),
the AI should generate specific sub-topic suggestions to help them niche down
(e.g., "Did you mean: Cellular Biology, Marine Biology, Genetics, or Ecology?").

This extends the existing `narrow` action in `canonicalize_subject()` —
instead of returning a single suggestion when no match is found, generate
multiple options from the AI when the query is too broad/general.
