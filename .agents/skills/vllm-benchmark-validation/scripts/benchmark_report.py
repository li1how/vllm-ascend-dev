#!/usr/bin/env python3
"""Parse and compare AISBench accuracy and performance results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

EXIT_OK = 0
EXIT_INPUT_ERROR = 2

ACCURACY_DIMENSIONS = {"dataset", "version", "metric", "mode", "total_count"}
MISSING_VALUES = {"", "-", "n/a", "na", "none", "null"}
NUMBER_WITH_UNIT = re.compile(
    r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$"
)


@dataclass(frozen=True)
class MetricKey:
    kind: str
    model: str
    dataset: str
    version: str
    mode: str
    metric: str
    stage: str
    statistic: str
    unit: str


@dataclass(frozen=True)
class MetricRecord:
    key: MetricKey
    value: float
    source: str


@dataclass(frozen=True)
class InputSpec:
    label: str
    path: Path


def _canonical_metric(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "", value.casefold())
    known = {
        "e2el": "E2EL",
        "ttft": "TTFT",
        "tpot": "TPOT",
        "itl": "ITL",
        "inputtokens": "InputTokens",
        "outputtokens": "OutputTokens",
        "outputtokenthroughput": "OutputTokenThroughput",
        "benchmarkduration": "BenchmarkDuration",
        "totalrequests": "TotalRequests",
        "failedrequests": "FailedRequests",
        "successrequests": "SuccessRequests",
        "concurrency": "Concurrency",
        "maxconcurrency": "MaxConcurrency",
        "requestthroughput": "RequestThroughput",
        "totalinputtokens": "TotalInputTokens",
        "prefilltokenthroughput": "PrefillTokenThroughput",
        "totalgeneratedtokens": "TotalGeneratedTokens",
        "inputtokenthroughput": "InputTokenThroughput",
        "totaltokenthroughput": "TotalTokenThroughput",
    }
    return known.get(compact, value.strip())


def _parse_number(value: Any) -> tuple[float, str] | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return (number, "") if math.isfinite(number) else None

    text = str(value).strip()
    if text.casefold() in MISSING_VALUES:
        return None
    match = NUMBER_WITH_UNIT.match(text)
    if not match:
        return None
    number = float(match.group(1))
    if not math.isfinite(number):
        return None
    return number, match.group(2).strip()


def _relative_delta(baseline: float, value: float) -> float | None:
    if baseline == 0:
        return None
    return (value - baseline) / abs(baseline) * 100.0


def _relative_source(path: Path, run_dir: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def _parse_accuracy_csv(
    path: Path, run_dir: Path, warnings: list[str]
) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        required = {"dataset", "version", "metric", "mode"}
        if not required.issubset(headers):
            warnings.append(f"{path}: missing accuracy columns")
            return records

        model_columns = [name for name in headers if name not in ACCURACY_DIMENSIONS]
        if not model_columns:
            warnings.append(f"{path}: no model columns")
            return records

        for line_number, row in enumerate(reader, start=2):
            for model in model_columns:
                parsed = _parse_number(row.get(model))
                if parsed is None:
                    raw = (row.get(model) or "").strip()
                    if raw.casefold() not in MISSING_VALUES:
                        warnings.append(
                            f"{path}:{line_number}: non-numeric value for {model}: {raw!r}"
                        )
                    continue
                value, unit = parsed
                key = MetricKey(
                    kind="accuracy",
                    model=model.strip(),
                    dataset=(row.get("dataset") or "").strip(),
                    version=(row.get("version") or "").strip(),
                    mode=(row.get("mode") or "").strip(),
                    metric=(row.get("metric") or "").strip(),
                    stage="",
                    statistic="Value",
                    unit=unit,
                )
                records.append(
                    MetricRecord(key, value, _relative_source(path, run_dir))
                )
    return records


def _parse_performance_csv(
    path: Path, run_dir: Path, warnings: list[str]
) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        metric_column = "Performance Parameters"
        if metric_column not in headers or "Stage" not in headers:
            warnings.append(f"{path}: missing performance columns")
            return records

        statistics = [name for name in headers if name not in {metric_column, "Stage"}]
        model = path.parent.name
        dataset = path.stem
        for line_number, row in enumerate(reader, start=2):
            metric = _canonical_metric(row.get(metric_column) or "")
            stage = (row.get("Stage") or "").strip()
            for statistic in statistics:
                parsed = _parse_number(row.get(statistic))
                if parsed is None:
                    raw = (row.get(statistic) or "").strip()
                    if raw.casefold() not in MISSING_VALUES:
                        warnings.append(
                            f"{path}:{line_number}: non-numeric {statistic}: {raw!r}"
                        )
                    continue
                value, unit = parsed
                key = MetricKey(
                    kind="performance",
                    model=model,
                    dataset=dataset,
                    version="",
                    mode="perf",
                    metric=metric,
                    stage=stage,
                    statistic=statistic.strip(),
                    unit=unit,
                )
                records.append(
                    MetricRecord(key, value, _relative_source(path, run_dir))
                )
    return records


def _parse_performance_json(
    path: Path, run_dir: Path, warnings: list[str]
) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        warnings.append(f"{path}: performance JSON root is not an object")
        return records

    model = path.parent.name
    dataset = path.stem
    for raw_metric, stage_values in payload.items():
        if not isinstance(stage_values, dict):
            warnings.append(f"{path}: {raw_metric!r} is not grouped by stage")
            continue
        for stage, raw_value in stage_values.items():
            parsed = _parse_number(raw_value)
            if parsed is None:
                warnings.append(
                    f"{path}: non-numeric value for {raw_metric}/{stage}: "
                    f"{raw_value!r}"
                )
                continue
            value, unit = parsed
            key = MetricKey(
                kind="performance",
                model=model,
                dataset=dataset,
                version="",
                mode="perf",
                metric=_canonical_metric(raw_metric),
                stage=str(stage),
                statistic="Value",
                unit=unit,
            )
            records.append(MetricRecord(key, value, _relative_source(path, run_dir)))
    return records


def parse_run(run_dir: Path) -> tuple[list[MetricRecord], list[str]]:
    warnings: list[str] = []
    records: list[MetricRecord] = []

    accuracy_files = sorted(run_dir.rglob("summary/summary_*.csv"))
    performance_csvs = sorted(run_dir.rglob("performances/*/*.csv"))
    performance_jsons = sorted(
        path
        for path in run_dir.rglob("performances/*/*.json")
        if not path.name.endswith("_details.json")
    )

    for path in accuracy_files:
        try:
            records.extend(_parse_accuracy_csv(path, run_dir, warnings))
        except (OSError, csv.Error, UnicodeError) as exc:
            warnings.append(f"{path}: failed to parse accuracy CSV: {exc}")
    for path in performance_csvs:
        try:
            records.extend(_parse_performance_csv(path, run_dir, warnings))
        except (OSError, csv.Error, UnicodeError) as exc:
            warnings.append(f"{path}: failed to parse performance CSV: {exc}")
    for path in performance_jsons:
        try:
            records.extend(_parse_performance_json(path, run_dir, warnings))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.append(f"{path}: failed to parse performance JSON: {exc}")

    if not accuracy_files and not performance_csvs and not performance_jsons:
        warnings.append(f"{run_dir}: no supported AISBench result files found")
    return records, warnings


def _key_sort_value(key: MetricKey) -> tuple[str, ...]:
    return (
        key.kind,
        key.model,
        key.dataset,
        key.version,
        key.mode,
        key.metric,
        key.stage,
        key.statistic,
        key.unit,
    )


def build_report(inputs: list[InputSpec]) -> dict[str, Any]:
    values_by_input: dict[str, dict[MetricKey, MetricRecord]] = {}
    input_summaries: list[dict[str, Any]] = []
    all_warnings: list[str] = []

    for spec in inputs:
        records, warnings = parse_run(spec.path)
        indexed: dict[MetricKey, MetricRecord] = {}
        for record in records:
            previous = indexed.get(record.key)
            if previous is not None:
                warnings.append(
                    f"{spec.path}: duplicate metric key in {previous.source} and "
                    f"{record.source}; keeping {previous.source}"
                )
                continue
            indexed[record.key] = record
        values_by_input[spec.label] = indexed
        all_warnings.extend(f"{spec.label}: {warning}" for warning in warnings)
        input_summaries.append(
            {
                "label": spec.label,
                "path": str(spec.path),
                "metric_count": len(indexed),
                "warnings": warnings,
            }
        )

    baseline_label = inputs[0].label
    all_keys = sorted(
        {key for metrics in values_by_input.values() for key in metrics},
        key=_key_sort_value,
    )
    metrics: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for key in all_keys:
        values: dict[str, float | None] = {}
        sources: dict[str, str | None] = {}
        missing_labels: list[str] = []
        for spec in inputs:
            record = values_by_input[spec.label].get(key)
            values[spec.label] = record.value if record else None
            sources[spec.label] = record.source if record else None
            if record is None:
                missing_labels.append(spec.label)

        baseline_value = values[baseline_label]
        deltas: dict[str, dict[str, float | None]] = {}
        for spec in inputs[1:]:
            current = values[spec.label]
            if baseline_value is None or current is None:
                deltas[spec.label] = {
                    "absolute": None,
                    "relative_percent": None,
                }
            else:
                deltas[spec.label] = {
                    "absolute": current - baseline_value,
                    "relative_percent": _relative_delta(baseline_value, current),
                }

        metric_entry = {
            **asdict(key),
            "values": values,
            "deltas": deltas,
            "sources": sources,
        }
        metrics.append(metric_entry)
        if missing_labels:
            missing.append(
                {
                    "key": asdict(key),
                    "missing_inputs": missing_labels,
                }
            )

    return {
        "baseline": baseline_label,
        "inputs": input_summaries,
        "metrics": metrics,
        "missing": missing,
        "warnings": all_warnings,
    }


def _format_number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6g}"


def _escape_markdown(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def _metric_name(metric: dict[str, Any]) -> str:
    parts = [metric["metric"]]
    if metric["stage"]:
        parts.append(metric["stage"])
    if metric["statistic"]:
        parts.append(metric["statistic"])
    return " / ".join(parts)


def render_markdown(report: dict[str, Any]) -> str:
    labels = [item["label"] for item in report["inputs"]]
    baseline = report["baseline"]
    lines = [
        "# AISBench Benchmark Report",
        "",
        f"Baseline: `{_escape_markdown(baseline)}`",
        "",
        "## Inputs",
        "",
        "| Label | Run directory | Metrics | Warnings |",
        "| --- | --- | ---: | ---: |",
    ]
    for item in report["inputs"]:
        lines.append(
            f"| {_escape_markdown(item['label'])} | "
            f"`{_escape_markdown(item['path'])}` | "
            f"{item['metric_count']} | {len(item['warnings'])} |"
        )

    lines.extend(["", "## Metrics", ""])
    header = [
        "Kind",
        "Model",
        "Dataset",
        "Version",
        "Mode",
        "Metric",
        "Unit",
        baseline,
    ]
    for label in labels[1:]:
        header.extend([label, f"{label} Δ", f"{label} Δ%"])
    lines.append("| " + " | ".join(_escape_markdown(item) for item in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")

    for metric in report["metrics"]:
        row = [
            metric["kind"],
            metric["model"],
            metric["dataset"],
            metric["version"],
            metric["mode"],
            _metric_name(metric),
            metric["unit"],
            _format_number(metric["values"][baseline]),
        ]
        for label in labels[1:]:
            delta = metric["deltas"][label]
            row.extend(
                [
                    _format_number(metric["values"][label]),
                    _format_number(delta["absolute"]),
                    _format_number(delta["relative_percent"]),
                ]
            )
        lines.append("| " + " | ".join(_escape_markdown(item) for item in row) + " |")

    if report["missing"]:
        lines.extend(["", "## Missing Metrics", ""])
        for item in report["missing"]:
            key = item["key"]
            identity = (
                f"{key['kind']}/{key['model']}/{key['dataset']}/"
                f"{key['version']}/{key['mode']}/{key['metric']}/"
                f"{key['stage']}/{key['statistic']}/{key['unit']}"
            )
            labels_text = ", ".join(item["missing_inputs"])
            lines.append(
                f"- `{_escape_markdown(identity)}` missing from "
                f"{_escape_markdown(labels_text)}"
            )

    if report["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {_escape_markdown(warning)}" for warning in report["warnings"])

    lines.extend(
        [
            "",
            "> This report shows measurements and deltas only. "
            "It does not apply pass/fail thresholds.",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_input_spec(value: str) -> InputSpec:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected LABEL=RUN_DIR")
    label, raw_path = value.split("=", 1)
    label = label.strip()
    raw_path = raw_path.strip()
    if not label or not raw_path:
        raise argparse.ArgumentTypeError("LABEL and RUN_DIR must be non-empty")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"run directory does not exist: {path}")
    return InputSpec(label=label, path=path)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse and compare AISBench accuracy/performance run directories."
    )
    parser.add_argument(
        "--input",
        action="append",
        type=_parse_input_spec,
        required=True,
        metavar="LABEL=RUN_DIR",
        help="Labeled AISBench timestamp run directory; repeat for comparisons.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        help="Write the Markdown report to this path; otherwise print it.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Optionally write the normalized JSON report to this path.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    labels = [spec.label for spec in args.input]
    if len(labels) != len(set(labels)):
        parser.error("--input labels must be unique")

    report = build_report(args.input)
    if not report["metrics"]:
        for warning in report["warnings"]:
            print(f"[WARN] {warning}", file=sys.stderr)
        print("[ERROR] no AISBench metrics could be parsed", file=sys.stderr)
        return EXIT_INPUT_ERROR

    markdown = render_markdown(report)
    if args.markdown_output:
        _write_text(args.markdown_output, markdown)
    else:
        print(markdown, end="")
    if args.json_output:
        _write_text(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
