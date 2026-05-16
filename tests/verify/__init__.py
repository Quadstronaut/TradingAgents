"""End-to-end verification harness.

Drives every agent_assist intent against a real Ollama backend with
seeded random tickers / vague prompts, checks structured-output shape,
runs a small directional-sanity backtest, and asserts cloud client
contracts via recorded fixtures.

Entry points:
    - ``tests.verify.matrix.run_matrix(reps=1, seed=0)`` — programmatic.
    - ``python -m tests.verify.matrix`` — CLI.
    - ``./scripts/verify.ps1 -Reps 3`` — Windows wrapper with green-streak loop.

See ``docs/superpowers/specs/2026-05-15-full-rectification-design.md``
for the design.
"""
