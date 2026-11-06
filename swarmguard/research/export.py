"""Exclusive, checksummed research bundles with no credential/environment-file collection."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any
from swarmguard.research.report_generator import generate_report
from swarmguard.research.reproducibility import canonical
from swarmguard.research.validation import validate_result


def bundle_files(record: dict[str, Any]) -> dict[str, bytes]:
    report = generate_report(record)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("trial_id", "scenario_id", "monitor", "prediction", "ground_truth", "valid"))
    for row in record["rows"]:
        for name, value in sorted(row.get("predictions", {}).items()):
            writer.writerow((row["trial_id"], row["dataset_scenario_id"], name,
                             "unavailable" if value is None else str(value), row.get("ground_truth", {}).get("label"), row["evaluation_valid"]))
    files = {"experiment.json": canonical(record), "experiment_manifest.json": canonical(record["manifest"]),
        "dataset_manifest.json": canonical(record["dataset"]), "configuration.json": canonical(record["config"]),
        "predictions.csv": stream.getvalue().encode(), "metrics.json": canonical(record["metrics"]),
        "failures.json": canonical({"accounting": record["metrics"]["accounting"], "error": record["error"],
            "records": [f for r in record["rows"] for f in r.get("failures", [])]}),
        "report.md": report["markdown"].encode(), "report.html": report["html"].encode(), "report.json": canonical(report),
        "README.md": ("# SwarmGuard reproducibility bundle\n\nSynthetic data; no live-model claims.\n\n"
            "Verify without model calls: `python run_research.py --verify-bundle PATH_TO_BUNDLE`\n"
            "Inspect `experiment.json` for exact observations, decisions, graphs, failures and labels.\n"
            "Fresh deterministic run: `python run_research.py --config PATH_TO_BUNDLE/configuration.json "
            "--import-dataset PATH_TO_BUNDLE/dataset_manifest.json --output NEW_DIRECTORY`\n"
            "Use the recorded source revision and dependency versions. Timestamps, latency and opaque approval tokens "
            "are not byte-reproducible. Recorded replay makes no model requests. Live re-execution is never exact.\n").encode()}
    # A standalone SVG uses only measured recall and labels from the fixed monitor allowlist.
    bars = []
    for i, (name, metric) in enumerate(sorted(record["metrics"]["detection"].items())):
        recall = metric["recall"]
        y = 40+i*40
        bars.append(f'<text x="5" y="{y}">{name} (n={metric["n"]})</text>')
        if recall is not None:
            bars.append(f'<rect x="420" y="{y-14}" width="{recall*200:.2f}" height="18" fill="#277f78"/>')
        bars.append(f'<text x="630" y="{y}">{recall if recall is not None else "undefined"}</text>')
    files["figures/recall.svg"] = ('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="360" '
        'viewBox="0 0 800 360"><rect width="800" height="360" fill="white"/>'
        '<text x="5" y="18">Synthetic recall; descriptive samples only</text>' + ''.join(bars) + '</svg>').encode()
    files["checksums.sha256"] = ''.join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items())).encode()
    return files


def bundle_zip(record: dict[str, Any]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in bundle_files(record).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


def export_bundle(record: dict[str, Any], destination: Path) -> Path:
    files = bundle_files(record)
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
    return destination


def verify_bundle(directory: Path) -> dict[str, Any]:
    entries = (directory / "checksums.sha256").read_text().splitlines()
    seen = set()
    for entry in entries:
        checksum, name = entry.split("  ", 1)
        path = directory / name
        if path.resolve().is_relative_to(directory.resolve()) is False or path.is_symlink() or name in seen:
            raise ValueError("unsafe or duplicate bundle path")
        seen.add(name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError(f"artifact checksum mismatch: {name}")
    record = json.loads((directory / "experiment.json").read_text())
    expected = bundle_files(record)
    if seen != set(expected) - {"checksums.sha256"}:
        raise ValueError("missing or unexpected bundle artifacts")
    for name, data in expected.items():
        if (directory / name).read_bytes() != data:
            raise ValueError(f"bundle does not reproduce: {name}")
    return validate_result(record)
