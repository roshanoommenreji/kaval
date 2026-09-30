"""The eval harness (KAV-43, ADR-0018): twenty golden incidents with known-good answers,
scored against the real pipeline (correlate -> context -> diagnose -> policy). See README.md
for the design and docs/labs/lab-13-eval-harness.md for a walkthrough.

Not an installed package -- evals/ isn't in pyproject.toml's [tool.hatch.build.targets.wheel]
packages, the same way scripts/ isn't. It imports kaval_agent/kaval_shared/kaval_collector
(already-installed packages) and is run directly: python -m evals.run, or its scoring-only
pieces are exercised by pytest evals/ (added to testpaths)."""
