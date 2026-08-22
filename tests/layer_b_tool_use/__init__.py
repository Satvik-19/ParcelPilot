"""Layer B tool-use evaluation (04_EVAL_SPEC.md §3).

Two kinds of tests live here:
- deterministic boundary/security tests (FakeClient — no LLM, no network);
- `live` marked tests that run the CHOSEN model through the real runtime and
  record every run as JSON under `recordings/` for offline replay.
"""
