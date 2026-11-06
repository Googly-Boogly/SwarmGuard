# Final project report

## 1. Executive summary

SwarmGuard now has a functional native research laboratory integrated with the existing console. It runs bounded scripted and mock autonomous experiments, validates exact dataset versions, persists trajectories and metrics, generates draft reports, and exports checksummed reproducibility bundles. Stage 8 is partially implemented; the full requested release scope is not complete.

## 2. Completed architecture

The laboratory reuses the existing simulator, AgentRuntime, Capability Gateway, PolicyEngine, approval manager, ObservationBuilder and councils. Browser controls share the authenticated console boundary and single execution semaphore. Research state uses the existing versioned platform store. The original read-only API and hardened deployment restrictions remain in place.

## 3. Implemented features

RQ1–RQ7 definitions; immutable template dataset registration; strict configuration rejection; bounded native execution; mock autonomous agent decisions; recorded replay without calls; terminal experiment protection; recovery to inconclusive; metric recomputation; evidence validation; descriptive paired effects; Wilson/cluster-bootstrap/Holm utilities; recorded prediction ablations; Markdown/HTML/JSON reports; deterministic ZIP generation; checksums and bundle validation.

## 4. Repository changes

Added swarmguard/research/, swarmguard/datasets/, swarmguard/console/laboratory.py, run_research.py, demo_final.py, verify_final_release.py, frontend/src/pages/Laboratory.tsx, frontend/e2e/laboratory.spec.ts and tests/test_final_research.py. Updated console wiring, frontend routes, evaluator scenario injection and research/release documentation. Historical results were preserved. New measured artifacts are under results/stage8/experiment_001.

## 5. Automated test results

Baseline: 551 backend passed, 12 skipped; 39 frontend passed; 8 browser passed; frontend lint/build passed.

Final full backend run: 560 passed, 12 skipped, 54 warnings (70.38 seconds). Final frontend lint, 39 unit tests and build passed. Final browser suite: 9 passed (1.3 minutes), including the new laboratory test. After correcting the mock adversarial recipient, the focused nine research tests passed again; the full suite/browser run preceded that final fixture correction. Python compilation and git diff whitespace checks passed.

Original demo.py, demo_stage2.py and demo_stage3.py --provider mock each printed their required PASS checks. demo_final.py passed for scripted and mock autonomous modes. run_research.py --provider mock completed and verified its bundle. verify_final_release.py was executed; it reports incomplete release evidence and missing license instead of unconditional success.

PostgreSQL and unavailable optional framework tests remain skipped. No paid API evaluation was performed. Docker deployment, dependency/security scans and every separately requested stage command were not rerun for this change.

## 6. Security verification

The existing full suite exercised identity, approval binding/replay/expiry, policy rejection, races, protected execution, audit and service controls. Browser tests exercised authenticated approval, regression and security-operation workflows. New laboratory reads require RESEARCHER, mutations additionally use the existing Origin/JSON/CSRF boundary. The new browser test verifies the authenticated working path; dedicated negative-route tests for every new endpoint remain incomplete. Passing tests are not a formal security proof.

## 7. Research evaluation results

Actual scripted evaluation results/stage8/experiment_001 contains six adaptive_search fixtures: five unsafe and one safe. Global monitoring recorded TP=5, FP=0, TN=1, FN=0. Local monitoring recorded TP=1, FP=0, TN=1, FN=4. Four-general and specialized mock councils matched global predictions by construction. Policy-only has no advisory alert and must not be confused with its independently measured enforcement outcomes.

The final temporary mock autonomous demonstration completed one legitimate and one controlled unsafe workflow through the gateway. The legitimate workflow completed; the unsafe workflow proposed and executed a synthetic disclosure covered by a valid exact approval, which the global monitor flagged. This demonstrates the distinction between deterministic operation authorization and workflow-level policy semantics. No real email was sent. Live LLM results are absent.

## 8. Statistical analysis

On those six scripted fixtures: global precision/recall/F1 = 1/1/1; local precision/recall/F1 = 1/0.2/0.3333. Both have zero observed false positives with only one negative case. These are descriptive synthetic observations. No population confidence interval or statistical significance is claimed. Public same-author fixtures and dependent variants cannot establish generalization or ethical specialization benefits.

## 9. Frontend verification

The existing frontend now exposes laboratory, designer, hypotheses, datasets, statistics, ablations and reports routes. The new browser test authenticates, selects a dataset, starts an actual backend experiment, waits for completion, generates a report and downloads a ZIP. It checks backend recorded trial counts and validation. It does not yet unpack the downloaded ZIP or exercise every requested twenty-step graph/assessment workflow. Statistics currently use measured JSON rather than the full requested new chart suite.

## 10. Framework integration status

Existing custom Python and installed external adapter coverage remains in the original regression suite and browser tests. Twelve backend skips include unavailable optional dependencies and PostgreSQL. The final native dataset designer explicitly rejects external framework selection and directs researchers to the existing framework comparison runner. Availability detection alone is not execution verification.

## 11. Running instructions

```bash
python -m pip install -e ".[test]"
python setup_console_operator.py
python -m uvicorn console_main:app --host 127.0.0.1 --port 8000
# Separate terminal:
cd frontend
npm ci
npm run dev
# Repository root:
python demo.py
python demo_final.py
python run_research.py --provider mock
python run_research.py --dataset swarmguard_autonomous --mode MOCK_AUTONOMOUS --question RQ4
python run_research.py --verify-bundle results/stage8/experiment_001
python -m pytest -v
python verify_final_release.py
# frontend/:
npm run lint
npm test
npm run build
npx playwright test
```

Reports and exports are produced by run_research.py and the console report/download actions. Use a fresh output directory to reproduce without overwriting existing artifacts. No installation starts paid experiments.

## 12. Known limitations

See LIMITATIONS.md. Missing release requirements include full designer/model/framework integration, richer statistical visualizations, persistent report history, complete dataset import/compare UI, complete final demonstration orchestration, dedicated negative laboratory API coverage, and complete optional deployment verification. Recorded ablations are descriptive output combinations rather than controlled reruns. Live autonomous experiments remain disabled in the unified laboratory.

## 13. Final release readiness

**NOT_READY_FOR_RESEARCH_RELEASE**

No legal license exists and the owner has not authorized selecting one. Several requested Stage 8 capabilities remain partial and optional security/deployment evidence is incomplete. The verified native laboratory can be used for local synthetic research, but this report does not certify the entire requested release as complete.

## 14. Remaining non-blocking improvements

After release blockers and incomplete required scope are resolved, ordinary maintenance can improve report presentation, accessibility, experiment-list pagination and performance, uncertainty visualization, and dependency-warning cleanup. No additional numbered development stage is proposed.
