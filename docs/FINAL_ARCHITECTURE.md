# Final architecture

The final laboratory extends the existing console and research runners. It does not add an execution authority.

React → authenticated console laboratory routes → shared single-worker semaphore → Laboratory → existing simulator/AgentRuntime → credential-bound client → CapabilityGateway → PolicyEngine → approval validation → protected executor.

ObservationBuilder creates as-of local/global evidence. Advisory councils use identical evidence. Predictions freeze before trusted labels are joined. Dataset and experiment records use the existing PlatformRepository CAS/version history. Completed experiment records are immutable through ExperimentRegistry. Reports and ZIP bundles derive from recorded rows and hashes.

The original main:app remains GET-only. Hardened control mode rejects synthetic execution POSTs. External framework comparisons retain the existing Stage 6 runner and pages; the unified native dataset runner rejects external framework selections rather than misrepresenting them as tested equivalents.
