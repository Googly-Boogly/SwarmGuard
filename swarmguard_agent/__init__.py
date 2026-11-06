"""Untrusted agent-runtime client (Stage 7). Depends only on httpx; imports nothing from ``swarmguard``.

It holds exactly one secret: its own short-lived agent credential. It has no database access, no
signing keys, no approval credentials and (in the hardened deployment) no network route to the executor.
"""
