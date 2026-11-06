# Final security model

See THREAT_MODEL.md and STAGE7_SECURITY_ARCHITECTURE.md for the detailed boundaries. Agents propose; monitors advise; deterministic trusted code authorizes; trusted humans approve.

New laboratory mutations require RESEARCHER role, server session, exact local Origin, JSON and CSRF. Label-bearing research reads require RESEARCHER. Synthetic research uses the existing bounded shared worker and refuses live mode. Restart marks unfinished experiments inconclusive; no automatic resume occurs. Browser inputs cannot define policy, credentials or tool execution.

Local simulators provide architectural in-process isolation. The separated Stage 7 deployment provides signed gateway-to-executor grants and at-most-once states within documented assumptions. Audit chains are tamper-evident relative to external checkpoints. No production-security or formal-proof claim follows from test success.
