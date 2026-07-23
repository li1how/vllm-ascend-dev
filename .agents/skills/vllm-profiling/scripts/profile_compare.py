#!/usr/bin/env python3
"""Compare two analysed vLLM Ascend profile archives."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

MICROSECONDS = 1_000_000.0


def _resolve_case(value: str, analysed_root: Path | None) -> Path:
    path = Path(value)
    if path.exists():
        return path
    if analysed_root is not None:
        candidate = analysed_root / value
        if candidate.exists():
            return candidate
    raise SystemExit(f"profile case not found: {value}")


def _csv_files(root: Path, filename: str) -> list[Path]:
    return sorted(root.glob(f"*_ascend_pt/ASCEND_PROFILER_OUTPUT/{filename}"))


def _warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


def _csv_files_or_warn(root: Path, filename: str) -> list[Path]:
    paths = _csv_files(root, filename)
    if not paths:
        _warn(f"{filename} not found under {root}")
    return paths


def _float(value: str | None) -> float:
    if not value:
        return 0.0
    return float(value.strip())


def _read_dict_rows(paths: Iterable[Path]) -> Iterable[dict[str, str]]:
    for path in paths:
        with path.open(newline="") as handle:
            yield from csv.DictReader(handle)


def op_totals(root: Path) -> dict[tuple[str, str], tuple[float, int]]:
    totals: dict[tuple[str, str], list[float | int]] = defaultdict(lambda: [0.0, 0])
    for row in _read_dict_rows(_csv_files_or_warn(root, "op_statistic.csv")):
        key = (row.get("OP Type", ""), row.get("Core Type", ""))
        totals[key][0] += _float(row.get("Total Time(us)"))
        totals[key][1] += int(float(row.get("Count") or 0))
    return {key: (float(value[0]), int(value[1])) for key, value in totals.items()}


def step_totals(root: Path) -> dict[str, float]:
    rows = []
    for row in _read_dict_rows(_csv_files_or_warn(root, "step_trace_time.csv")):
        rows.append(
            {
                "stage": _float(row.get("Stage") or row.get("Stage Time(us)")),
                "compute": _float(row.get("Computing")),
                "comm": _float(row.get("Communication(Not Overlapped)")),
                "free": _float(row.get("Free")),
            }
        )
    if not rows:
        return {"max_stage": 0.0, "avg_compute": 0.0, "avg_comm": 0.0, "avg_free": 0.0}
    return {
        "max_stage": max(row["stage"] for row in rows),
        "avg_compute": sum(row["compute"] for row in rows) / len(rows),
        "avg_comm": sum(row["comm"] for row in rows) / len(rows),
        "avg_free": sum(row["free"] for row in rows) / len(rows),
    }


def kernel_shape_totals(root: Path, op_type: str) -> dict[str, tuple[float, int]]:
    counts: Counter[str] = Counter()
    totals: defaultdict[str, float] = defaultdict(float)
    for row in _read_dict_rows(_csv_files_or_warn(root, "kernel_details.csv")):
        if row.get("Type") != op_type:
            continue
        shape = row.get("Input Shapes", "")
        counts[shape] += 1
        totals[shape] += _float(row.get("Duration(us)"))
    return {shape: (totals[shape], counts[shape]) for shape in counts}


def top_kernels(root: Path, limit: int) -> list[tuple[float, str, str, str]]:
    rows: list[tuple[float, str, str, str]] = []
    for path in _csv_files_or_warn(root, "kernel_details.csv"):
        rank = path.parent.parent.name
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(
                    (
                        _float(row.get("Duration(us)")),
                        rank,
                        row.get("Type", ""),
                        row.get("Input Shapes", ""),
                    )
                )
    return sorted(rows, reverse=True)[:limit]


def _delta(base: float, opt: float) -> str:
    diff = opt - base
    if base == 0:
        return f"{diff / 1000:.3f}ms"
    return f"{diff / 1000:.3f}ms ({diff / base * 100:+.2f}%)"


def print_step_compare(base: Path, opt: Path) -> None:
    base_steps = step_totals(base)
    opt_steps = step_totals(opt)
    print("== Step Trace ==")
    for key in ("max_stage", "avg_compute", "avg_comm", "avg_free"):
        print(
            f"{key:>12s}: "
            f"baseline={base_steps[key] / MICROSECONDS:.6f}s "
            f"optimized={opt_steps[key] / MICROSECONDS:.6f}s "
            f"delta={(opt_steps[key] - base_steps[key]) / MICROSECONDS:+.6f}s"
        )


def print_op_compare(base: Path, opt: Path, limit: int) -> None:
    base_ops = op_totals(base)
    opt_ops = op_totals(opt)
    print("\n== Top Ops By Baseline Time ==")
    sorted_ops = sorted(
        base_ops.items(),
        key=lambda item: item[1][0],
        reverse=True,
    )[:limit]
    for (op, core), (base_us, base_count) in sorted_ops:
        opt_us, opt_count = opt_ops.get((op, core), (0.0, 0))
        print(
            f"{op[:42]:42s} core={core[:18]:18s} "
            f"base={base_us / 1000:10.3f}ms/{base_count:<5d} "
            f"opt={opt_us / 1000:10.3f}ms/{opt_count:<5d} "
            f"delta={_delta(base_us, opt_us)}"
        )


def print_shape_compare(base: Path, opt: Path, op_type: str) -> None:
    base_shapes = kernel_shape_totals(base, op_type)
    opt_shapes = kernel_shape_totals(opt, op_type)
    if not base_shapes and not opt_shapes:
        return
    print(f"\n== {op_type} By Input Shape ==")
    shapes = set(base_shapes) | set(opt_shapes)
    ordered_shapes = sorted(
        shapes,
        key=lambda shape: max(base_shapes.get(shape, (0.0, 0))[0], opt_shapes.get(shape, (0.0, 0))[0]),
        reverse=True,
    )
    for shape in ordered_shapes:
        base_us, base_count = base_shapes.get(shape, (0.0, 0))
        opt_us, opt_count = opt_shapes.get(shape, (0.0, 0))
        base_avg = base_us / base_count if base_count else 0.0
        opt_avg = opt_us / opt_count if opt_count else 0.0
        print(
            f"count={base_count}->{opt_count} "
            f"total={base_us / 1000:.3f}->{opt_us / 1000:.3f}ms "
            f"avg={base_avg:.3f}->{opt_avg:.3f}us "
            f"delta={_delta(base_us, opt_us)} "
            f"shape={shape[:220]}"
        )


def print_top_kernels(title: str, root: Path, limit: int) -> None:
    print(f"\n== {title} Top Kernels ==")
    for duration, rank, op_type, shape in top_kernels(root, limit):
        print(f"{duration / 1000:10.3f}ms rank={rank} type={op_type} shape={shape[:180]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="Baseline analysed profile directory or case name")
    parser.add_argument("optimized", help="Optimized analysed profile directory or case name")
    parser.add_argument("--analysed-root", type=Path, help="Directory containing named analysed profile cases")
    parser.add_argument("--top-ops", type=int, default=20)
    parser.add_argument("--top-kernels", type=int, default=12)
    parser.add_argument(
        "--shape-op",
        action="append",
        default=[],
        help="Operator type to aggregate by input shape; repeat for multiple operators",
    )
    args = parser.parse_args()

    base = _resolve_case(args.baseline, args.analysed_root)
    opt = _resolve_case(args.optimized, args.analysed_root)
    print(f"baseline={base}")
    print(f"optimized={opt}\n")
    print_step_compare(base, opt)
    print_op_compare(base, opt, args.top_ops)
    for op_type in args.shape_op:
        print_shape_compare(base, opt, op_type)
    print_top_kernels("Baseline", base, args.top_kernels)
    print_top_kernels("Optimized", opt, args.top_kernels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
