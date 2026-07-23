#!/usr/bin/env python3
"""Standard-library fixture tests for profile_compare.py."""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "profile_compare.py"
SPEC = importlib.util.spec_from_file_location("profile_compare", SCRIPT)
assert SPEC and SPEC.loader
profile_compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_compare)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_case(root: Path, scale: int) -> Path:
    output = root / "rank0_ascend_pt" / "ASCEND_PROFILER_OUTPUT"
    write_csv(
        output / "op_statistic.csv",
        ["OP Type", "Core Type", "Total Time(us)", "Count"],
        [
            {
                "OP Type": "MatMul",
                "Core Type": "AI_CORE",
                "Total Time(us)": str(100 * scale),
                "Count": "2",
            }
        ],
    )
    write_csv(
        output / "step_trace_time.csv",
        ["Stage", "Computing", "Communication(Not Overlapped)", "Free"],
        [
            {
                "Stage": str(1000 * scale),
                "Computing": "800",
                "Communication(Not Overlapped)": "100",
                "Free": "100",
            }
        ],
    )
    write_csv(
        output / "kernel_details.csv",
        ["Duration(us)", "Type", "Input Shapes"],
        [
            {"Duration(us)": str(50 * scale), "Type": "ShapeA", "Input Shapes": "1,2"},
            {"Duration(us)": str(25 * scale), "Type": "ShapeB", "Input Shapes": "3,4"},
        ],
    )
    return root


class ProfileCompareTests(unittest.TestCase):
    def test_no_shape_op_has_no_shape_section(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline = make_case(root / "baseline", 1)
            candidate = make_case(root / "candidate", 2)
            output = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    ["profile_compare.py", str(baseline), str(candidate)],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(profile_compare.main(), 0)
            self.assertNotIn("By Input Shape", output.getvalue())

    def test_multiple_shape_ops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            baseline = make_case(root / "baseline", 1)
            candidate = make_case(root / "candidate", 2)
            output = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "profile_compare.py",
                        str(baseline),
                        str(candidate),
                        "--shape-op",
                        "ShapeA",
                        "--shape-op",
                        "ShapeB",
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(profile_compare.main(), 0)
            text = output.getvalue()
            self.assertIn("== ShapeA By Input Shape ==", text)
            self.assertIn("== ShapeB By Input Shape ==", text)


if __name__ == "__main__":
    unittest.main()
