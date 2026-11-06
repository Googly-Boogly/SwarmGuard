"""Network-capable LLM provider adapters, kept OUTSIDE the trusted ``swarmguard`` core.

``swarmguard/`` never imports this package (enforced by tests). Only entry-point scripts
(``demo_stage3.py``, ``run_ethics_evaluation.py``) build a provider here and inject it into
monitors, so the core keeps its no-network invariant (AGENTS.md rule 5).
"""
