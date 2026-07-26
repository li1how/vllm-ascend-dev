from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "benchmark_report.py"
SPEC = importlib.util.spec_from_file_location("benchmark_report", SCRIPT_PATH)
assert SPEC and SPEC.loader
benchmark_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark_report
SPEC.loader.exec_module(benchmark_report)


class BenchmarkReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_accuracy_run(self, name: str, first: float, second: str) -> Path:
        run = self.root / name
        path = run / "summary" / "summary_20260101.csv"
        path.parent.mkdir(parents=True)
        path.write_text(
            "dataset,version,metric,mode,total_count,model-a,model-b\n"
            f"gsm8k,v1,accuracy,gen,10,{first},{second}\n",
            encoding="utf-8",
        )
        return run

    def _make_performance_run(
        self, name: str, ttft: float, throughput: float, failed: int = 0
    ) -> Path:
        run = self.root / name
        perf_dir = run / "performances" / "model-a"
        perf_dir.mkdir(parents=True)
        (perf_dir / "synthetic.csv").write_text(
            "Performance Parameters,Stage,Average,Min,Max,Median,P75,P90,P99,N\n"
            f"TTFT,total,{ttft} ms,9 ms,12 ms,10 ms,11 ms,12 ms,12 ms,2\n"
            "OutputTokenThroughput,total,0 token/s,0 token/s,0 token/s,"
            "0 token/s,0 token/s,0 token/s,0 token/s,2\n",
            encoding="utf-8",
        )
        (perf_dir / "synthetic.json").write_text(
            json.dumps(
                {
                    "Failed Requests": {"total": failed},
                    "Request Throughput": {"total": f"{throughput} req/s"},
                }
            ),
            encoding="utf-8",
        )
        return run

    def test_parse_accuracy_with_models_total_count_and_missing_value(self) -> None:
        run = self._make_accuracy_run("accuracy", 62.5, "-")
        records, warnings = benchmark_report.parse_run(run)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].key.kind, "accuracy")
        self.assertEqual(records[0].key.model, "model-a")
        self.assertEqual(records[0].key.dataset, "gsm8k")
        self.assertEqual(records[0].value, 62.5)
        self.assertEqual(warnings, [])

    def test_parse_performance_csv_and_json_with_units(self) -> None:
        run = self._make_performance_run("perf", 10.5, 8.25)
        records, warnings = benchmark_report.parse_run(run)
        by_metric = {
            (record.key.metric, record.key.statistic): record for record in records
        }

        self.assertEqual(warnings, [])
        self.assertEqual(by_metric[("TTFT", "Average")].value, 10.5)
        self.assertEqual(by_metric[("TTFT", "Average")].key.unit, "ms")
        self.assertEqual(
            by_metric[("RequestThroughput", "Value")].key.unit, "req/s"
        )

    def test_build_report_handles_deltas_zero_baseline_and_missing(self) -> None:
        baseline = self._make_performance_run("baseline", 10.0, 0.0)
        candidate = self._make_performance_run("candidate", 12.0, 2.0)
        candidate_csv = (
            candidate / "performances" / "model-a" / "synthetic.csv"
        )
        candidate_csv.write_text(
            candidate_csv.read_text(encoding="utf-8")
            + "ITL,total,3 ms,3 ms,3 ms,3 ms,3 ms,3 ms,3 ms,2\n",
            encoding="utf-8",
        )

        report = benchmark_report.build_report(
            [
                benchmark_report.InputSpec("baseline", baseline),
                benchmark_report.InputSpec("candidate", candidate),
            ]
        )
        ttft = next(
            metric
            for metric in report["metrics"]
            if metric["metric"] == "TTFT" and metric["statistic"] == "Average"
        )
        throughput = next(
            metric
            for metric in report["metrics"]
            if metric["metric"] == "RequestThroughput"
        )

        self.assertEqual(ttft["deltas"]["candidate"]["absolute"], 2.0)
        self.assertEqual(ttft["deltas"]["candidate"]["relative_percent"], 20.0)
        self.assertEqual(throughput["deltas"]["candidate"]["absolute"], 2.0)
        self.assertIsNone(throughput["deltas"]["candidate"]["relative_percent"])
        self.assertTrue(report["missing"])

    def test_malformed_file_is_warning_when_other_metrics_exist(self) -> None:
        run = self._make_accuracy_run("mixed", 50.0, "not-a-number")
        broken = run / "performances" / "model-a" / "synthetic.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{broken", encoding="utf-8")

        report = benchmark_report.build_report(
            [benchmark_report.InputSpec("run", run)]
        )

        self.assertTrue(report["metrics"])
        self.assertTrue(
            any("failed to parse performance JSON" in item for item in report["warnings"])
        )
        self.assertTrue(
            any("non-numeric value for model-b" in item for item in report["warnings"])
        )

    def test_mixed_accuracy_and_performance_are_not_paired(self) -> None:
        accuracy = self._make_accuracy_run("accuracy", 50.0, "-")
        performance = self._make_performance_run("performance", 10.0, 2.0)

        report = benchmark_report.build_report(
            [
                benchmark_report.InputSpec("accuracy", accuracy),
                benchmark_report.InputSpec("performance", performance),
            ]
        )

        self.assertEqual(len(report["missing"]), len(report["metrics"]))
        for metric in report["metrics"]:
            delta = metric["deltas"]["performance"]
            self.assertIsNone(delta["absolute"])
            self.assertIsNone(delta["relative_percent"])

    def test_main_writes_reports_and_does_not_fail_on_regression(self) -> None:
        baseline = self._make_accuracy_run("baseline", 90.0, "-")
        candidate = self._make_accuracy_run("candidate", 80.0, "-")
        markdown = self.root / "report.md"
        output_json = self.root / "report.json"

        result = benchmark_report.main(
            [
                "--input",
                f"baseline={baseline}",
                "--input",
                f"candidate={candidate}",
                "--markdown-output",
                str(markdown),
                "--json-output",
                str(output_json),
            ]
        )

        self.assertEqual(result, benchmark_report.EXIT_OK)
        self.assertIn("This report shows measurements", markdown.read_text())
        self.assertEqual(json.loads(output_json.read_text())["baseline"], "baseline")

    def test_main_returns_two_for_empty_run(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()

        result = benchmark_report.main(["--input", f"empty={empty}"])

        self.assertEqual(result, benchmark_report.EXIT_INPUT_ERROR)


if __name__ == "__main__":
    unittest.main()
