"""Kaval collector: turns observations into `signal` rows. INSERT only, read-only credentials.

Phase 1 ships only the synthetic source (`kaval_collector.synthetic`). The real sources
(Kubernetes events, Prometheus, Alertmanager, Cost Explorer) arrive in Phases 3 and 7 and
write the same row shapes, so nothing downstream can tell a fake from a real one except
the `synthetic` flag in `value`.
"""
