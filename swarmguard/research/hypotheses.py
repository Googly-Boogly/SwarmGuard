"""Pre-registered hypothesis verdicts, computed from stored experiment records.

The decision rules live as data in ``research/rq1/hypothesis_registry.json``. Each entry quotes its source protocol
verbatim and pins that document's hash. This module evaluates every applicable rule against an experiment's
*stored* record and result. It reads nothing else, never reruns a workflow, never writes, and never sees a
monitor or the gateway. Verdicts are recomputed on every read, so a historical record does not need rewriting.

Arithmetic is exact (``Fraction`` over the stored TP/FP/TN/FN counts), so thresholds such as ``FPR <= Local FPR + 1/12``
cannot flip because of float rounding. A metric with a zero denominator is UNDEFINED and can never support a hypothesis.

Verdicts:
- ``NOT_APPLICABLE``: the run is not the registered procedure (dataset, seed, subset, mode or provider differ). The
  conditions are still evaluated for orientation and labelled as such.
- ``INCONCLUSIVE``: an UNKNOWN prediction the protocol marks as inconclusive, or any UNDEFINED metric.
- ``SUPPORTED``: every condition passes. This is descriptive support on the evaluated dataset only.
- ``H0_RETAINED``: at least one condition fails.
"""
from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from swarmguard.models.adversarial import StrictModel

ENGINE_VERSION = "hypothesis-engine-1"
REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "research" / "rq1" / "hypothesis_registry.json"

Arm = Literal["local_action", "global_workflow", "llm_local", "llm_global"]
Metric = Literal["recall", "false_positive_rate", "precision"]
LLMMode = Literal["none", "mock", "live"]
RULES_ARMS: tuple[str, ...] = ("local_action", "global_workflow")
LLM_ARMS: tuple[str, ...] = ("llm_local", "llm_global")
ARM_LABEL = {"local_action": "Rules Local", "global_workflow": "Rules Global",
             "llm_local": "LLM Local", "llm_global": "LLM Global"}
METRIC_LABEL = {"recall": "recall", "false_positive_rate": "FPR", "precision": "precision"}
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
       "<=": lambda a, b: a <= b, "==": lambda a, b: a == b}


def _fraction(text: str) -> Fraction:
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"not an exact number: {text!r}") from exc


class MetricRef(StrictModel):
    arm: Arm
    metric: Metric


class MetricCondition(StrictModel):
    kind: Literal["metric"]
    id: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=300)
    left: MetricRef
    op: Literal[">", ">=", "<", "<=", "=="]
    right: MetricRef | None = None
    constant: str | None = None
    offset: str = "0"

    @model_validator(mode="after")
    def _one_right_side(self) -> MetricCondition:
        if (self.right is None) == (self.constant is None):
            raise ValueError("a metric condition needs exactly one of right or constant")
        _fraction(self.offset)
        if self.constant is not None:
            _fraction(self.constant)
            if self.offset != "0":
                raise ValueError("offset applies only to arm-vs-arm comparisons")
        return self


class IntegrityCondition(StrictModel):
    kind: Literal["integrity"]
    id: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=300)
    checks: list[Literal["observation_checks", "run_integrity"]] = Field(min_length=1)


Condition = Annotated[MetricCondition | IntegrityCondition, Field(discriminator="kind")]


class Source(StrictModel):
    document: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    section: str
    quoted_text: str = Field(min_length=1)


class Applicability(StrictModel):
    partition: str
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    n_workflows: int = Field(gt=0)
    seed: int
    llm_modes: list[LLMMode] = Field(min_length=1)
    require_live_provider: bool


class Hypothesis(StrictModel):
    hypothesis_id: str
    protocol_version: str
    statement: str
    source: Source
    transcribed_at: str
    applies: Applicability
    roles: dict[LLMMode, Literal["primary", "secondary"]]
    role_note: str | None = None
    not_applicable_if_unknown: list[Arm] = []
    inconclusive_if_unknown: list[Arm] = []
    conditions: list[Condition] = Field(min_length=1)

    @model_validator(mode="after")
    def _roles_cover_modes(self) -> Hypothesis:
        if set(self.roles) != set(self.applies.llm_modes):
            raise ValueError(f"{self.hypothesis_id}: roles must name exactly the applicable LLM modes")
        if len({c.id for c in self.conditions}) != len(self.conditions):
            raise ValueError(f"{self.hypothesis_id}: duplicate condition ids")
        return self


class Registry(StrictModel):
    registry_version: str
    note: str
    hypotheses: list[Hypothesis] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Registry:
        ids = [h.hypothesis_id for h in self.hypotheses]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate hypothesis ids")
        return self


def load_registry(path: Path = REGISTRY_PATH) -> tuple[Registry, str]:
    """Return the validated registry and the sha256 of its exact bytes."""
    raw = path.read_bytes()
    return Registry.model_validate(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def source_documents_match(registry: Registry, root: Path = REPO_ROOT) -> dict[str, bool]:
    """Whether each cited protocol document still has the hash recorded at transcription."""
    out = {}
    for h in registry.hypotheses:
        p = root / h.source.document
        out[h.hypothesis_id] = p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == h.source.sha256
    return out


# --------------------------------------------------------------------------- metrics from stored counts


def arm_counts(result: dict[str, Any]) -> dict[str, dict[str, int]]:
    """TP/FP/TN/FN (and UNKNOWN for LLM arms) per arm, exactly as stored."""
    arms: dict[str, dict[str, int]] = {}
    for source, names in (((result.get("analysis") or {}).get("primary") or {}, RULES_ARMS),
                          (((result.get("llm_analysis") or {}).get("primary") or {}), LLM_ARMS)):
        for arm in names:
            m = source.get(arm)
            if m is not None:
                arms[arm] = {k: int(m.get(k) or 0) for k in ("tp", "fp", "tn", "fn", "unknown")}
    return arms


def _metric(counts: dict[str, int], metric: str) -> tuple[Fraction | None, str]:
    num, den = {"recall": (counts["tp"], counts["tp"] + counts["fn"]),
                "false_positive_rate": (counts["fp"], counts["fp"] + counts["tn"]),
                "precision": (counts["tp"], counts["tp"] + counts["fp"])}[metric]
    return (Fraction(num, den) if den else None), f"{num}/{den}"


def _num(x: Fraction | None) -> dict[str, Any] | None:
    return None if x is None else {"value": float(x), "exact": f"{x.numerator}/{x.denominator}"}


# --------------------------------------------------------------------------- evaluation


def _applicability(h: Hypothesis, record: dict[str, Any], result: dict[str, Any],
                   arms: dict[str, dict[str, int]]) -> list[str]:
    config, a = record.get("config") or {}, h.applies
    mode = config.get("llm_mode", "none")
    reasons = []
    if config.get("partition") != a.partition:
        reasons.append(f"dataset partition is {config.get('partition')!r}, not {a.partition!r}")
    if (result.get("manifest") or {}).get("content_hash") != a.dataset_hash:
        reasons.append("dataset content hash differs from the registered frozen hash")
    if config.get("scenario_ids") is not None:
        reasons.append("a scenario subset was selected; the registered procedure needs the complete dataset")
    if (result.get("analysis") or {}).get("n_workflows") != a.n_workflows:
        reasons.append(f"{(result.get('analysis') or {}).get('n_workflows')} workflows evaluated, not {a.n_workflows}")
    if config.get("seed") != a.seed:
        reasons.append(f"seed is {config.get('seed')}, not {a.seed}")
    if mode not in a.llm_modes:
        reasons.append(f"LLM mode {mode!r} is not one of {a.llm_modes}")
    if a.require_live_provider and (result.get("llm") or {}).get("is_synthetic") is not False:
        reasons.append("no live, non-synthetic LLM provider recorded; mock outputs are never evidence")
    for arm in h.not_applicable_if_unknown:
        if arms.get(arm, {}).get("unknown"):
            reasons.append(f"{ARM_LABEL[arm]} has {arms[arm]['unknown']} UNKNOWN workflow predictions "
                           "(the protocol makes the comparison descriptive)")
    return reasons


def _integrity(checks: list[str], record: dict[str, Any], result: dict[str, Any]) -> tuple[bool, list[str]]:
    problems = []
    if "observation_checks" in checks:
        v = result.get("verification") or {}
        if v.get("checks_passed") is not True:
            problems.append(f"observation-boundary verification failed ({len(v.get('failures') or [])} failures)")
        if result.get("crosscheck"):
            problems.append(f"{len(result['crosscheck'])} evidence/scoring cross-check mismatches")
        if record.get("failures"):
            problems.append(f"{len(record['failures'])} failed workflows")
    if "run_integrity" in checks:
        if record.get("status") != "COMPLETED":
            problems.append(f"run status is {record.get('status')}, not COMPLETED")
        if record.get("integrity_problems"):
            problems.append(f"{len(record['integrity_problems'])} recorded integrity problems")
        if (result.get("analysis") or {}).get("missing_or_failed"):
            problems.append(f"{len(result['analysis']['missing_or_failed'])} workflows missing or failed in scoring")
    return not problems, problems


def _condition(c: MetricCondition | IntegrityCondition, arms: dict[str, dict[str, int]],
               record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if isinstance(c, IntegrityCondition):
        ok, problems = _integrity(c.checks, record, result)
        return {"id": c.id, "text": c.text, "kind": "integrity", "status": "PASS" if ok else "FAIL",
                "detail": "all checks passed" if ok else "; ".join(problems)}
    if c.left.arm not in arms or (c.right is not None and c.right.arm not in arms):
        missing = [r.arm for r in (c.left, c.right) if r is not None and r.arm not in arms]
        return {"id": c.id, "text": c.text, "kind": "metric", "status": "UNDEFINED", "left": None, "right": None,
                "detail": f"arm not present in this experiment: {', '.join(ARM_LABEL[m] for m in missing)}"}
    left, left_counts = _metric(arms[c.left.arm], c.left.metric)
    left_name = f"{ARM_LABEL[c.left.arm]} {METRIC_LABEL[c.left.metric]}"
    if c.right is not None:
        base, right_counts = _metric(arms[c.right.arm], c.right.metric)
        offset = _fraction(c.offset)
        right = None if base is None else base + offset
        right_name = f"{ARM_LABEL[c.right.arm]} {METRIC_LABEL[c.right.metric]} ({right_counts})" + (
            f" + {c.offset}" if offset else "")
    else:
        right = _fraction(c.constant or "0")
        right_name = f"threshold {c.constant}"
    if left is None or right is None:
        status, detail = "UNDEFINED", "metric undefined (zero denominator)"
    else:
        status = "PASS" if OPS[c.op](left, right) else "FAIL"
        detail = f"{left_name} ({left_counts}) = {float(left):.4f} {c.op} {right_name} = {float(right):.4f}"
    return {"id": c.id, "text": c.text, "kind": "metric", "status": status, "left": _num(left), "op": c.op,
            "right": _num(right), "detail": detail}


def evaluate_hypothesis(h: Hypothesis, record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    arms = arm_counts(result)
    mode = (record.get("config") or {}).get("llm_mode", "none")
    reasons = _applicability(h, record, result, arms)
    conditions = [_condition(c, arms, record, result) for c in h.conditions]
    inconclusive = [f"{ARM_LABEL[a]} has {arms[a]['unknown']} UNKNOWN workflow predictions"
                    for a in h.inconclusive_if_unknown if arms.get(a, {}).get("unknown")]
    undefined = [c["id"] for c in conditions if c["status"] == "UNDEFINED"]
    failed = [c["id"] for c in conditions if c["status"] == "FAIL"]
    if reasons:
        status = "NOT_APPLICABLE"
        note = "Not the registered procedure; conditions are shown for orientation only and decide nothing."
    elif inconclusive:
        status, note = "INCONCLUSIVE", "The protocol makes the run inconclusive: " + "; ".join(inconclusive) + "."
    elif undefined:
        status, note = "INCONCLUSIVE", f"Condition(s) {', '.join(undefined)} could not be evaluated (undefined metric)."
    elif failed:
        status, note = "H0_RETAINED", f"Condition(s) {', '.join(failed)} not met: H0 is retained on this dataset."
    else:
        status, note = "SUPPORTED", ("Every registered condition holds. Descriptive support on this constructed dataset "
                                     "only; not proof of the hypothesis in general.")
    return {"hypothesis_id": h.hypothesis_id, "protocol_version": h.protocol_version, "statement": h.statement,
            "role": h.roles.get(mode, "not applicable"), "role_note": h.role_note, "status": status, "note": note,
            "applicable": not reasons, "applicability_reasons": reasons, "conditions": conditions,
            "source": h.source.model_dump(), "transcribed_at": h.transcribed_at}


def _relevant(h: Hypothesis, record: dict[str, Any], result: dict[str, Any]) -> bool:
    """Show a hypothesis for runs on its dataset partition whose arms it can reference."""
    if (record.get("config") or {}).get("partition") != h.applies.partition:
        return False
    arms = arm_counts(result)
    return all(c.left.arm in arms for c in h.conditions if isinstance(c, MetricCondition))


def evaluate_experiment(record: dict[str, Any], result: dict[str, Any], *,
                        registry_path: Path = REGISTRY_PATH) -> dict[str, Any]:
    """All registered verdicts for one stored experiment. Pure: reads its arguments and the registry only."""
    registry, digest = load_registry(registry_path)
    sources = source_documents_match(registry, registry_path.parents[2])
    verdicts = [evaluate_hypothesis(h, record, result) for h in registry.hypotheses if _relevant(h, record, result)]
    for v in verdicts:
        v["source_document_unchanged"] = sources[v["hypothesis_id"]]
    primary = [v for v in verdicts if v["applicable"] and v["role"] == "primary"]
    if not verdicts:
        summary = "No registered hypothesis covers this dataset partition; results are descriptive only."
    elif not primary:
        summary = "No registered hypothesis applies to this run as configured; every comparison is descriptive."
    else:
        summary = "; ".join(f"{v['hypothesis_id']}: {v['status']}" for v in primary)
    return {"engine_version": ENGINE_VERSION, "experiment_id": record.get("experiment_id"),
            "registry": {"version": registry.registry_version, "sha256": digest,
                         "path": str(registry_path.relative_to(registry_path.parents[2]))},
            "computed_from": "stored experiment record and stored result only (read-only; nothing rerun)",
            "arm_counts": arm_counts(result), "primary_summary": summary,
            "primary": [{"hypothesis_id": v["hypothesis_id"], "status": v["status"]} for v in primary],
            "verdicts": verdicts}
