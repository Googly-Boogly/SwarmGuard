"""Provider-neutral LLM infrastructure (no network code lives in this package).

* ``base``: the ``LLMProvider`` interface and ``ProviderCallError``.
* ``schemas``: ``ProviderResult``, ``LLMUsage``, ``LLMError`` and error kinds.
* ``configuration``: ``LLMSettings``, read from the environment (credentials are never logged).
* ``usage``: ``GuardedProvider`` (request, budget and concurrency limits) and ``RetryingProvider``.
* ``mock_provider``: deterministic, clearly synthetic responses for tests and CI.

The network-capable OpenAI adapter lives OUTSIDE this package, in ``swarmguard_providers``,
so the trusted core keeps its no-network invariant (enforced by an AST test).
"""
