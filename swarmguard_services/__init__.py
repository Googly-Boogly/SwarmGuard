"""Network entry points for Stage 7 trusted services (internal HTTP transport only; no email, no SMTP).

``swarmguard/`` keeps no network imports; this package injects the gateway->executor channel and exposes
the uvicorn applications. Run one worker per service.
"""
