#!/usr/bin/env python3
"""Standard-library integration tests for serving validation scripts."""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Iterator

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import serving_common  # noqa: E402
import serving_diff  # noqa: E402


class FakeHandler(http.server.BaseHTTPRequestHandler):
    mode = "match"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send({"data": [{"id": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        is_chat = self.path.endswith("/chat/completions")
        text = "different" if self.mode == "mismatch" else "answer"
        choice: dict[str, object] = {"finish_reason": "stop"}
        if is_chat:
            choice["message"] = {"role": "assistant", "content": text}
        else:
            choice["text"] = text
        if self.mode != "no-tokens":
            choice["token_ids"] = [11, 12]
        self._send(
            {
                "id": f"dynamic-{time.time_ns()}",
                "created": int(time.time()),
                "choices": [choice],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
                "seen_model": payload.get("model"),
            }
        )

    def _send(self, value: object) -> None:
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def fake_server(mode: str = "match") -> Iterator[str]:
    handler = type("ConfiguredFakeHandler", (FakeHandler,), {"mode": mode})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_script(script: str, *args: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="skill-test-pycache-")
    return subprocess.run(
        (sys.executable, str(SCRIPTS / script), *args),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=environment,
        check=False,
    )


class ServingValidationTests(unittest.TestCase):
    def test_request_probe_completion_and_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, fake_server() as base_url:
            for kind in ("completion", "chat"):
                summary = Path(temp_dir) / f"{kind}.json"
                result = run_script(
                    "request_probe.py",
                    "--base-url",
                    base_url,
                    "--kind",
                    kind,
                    "--model",
                    "model",
                    "--prompt",
                    "hello",
                    "--warmup-requests",
                    "1",
                    "--requests",
                    "2",
                    "--summary-json",
                    str(summary),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(summary.read_text())
                self.assertEqual(len(data["records"]), 3)
                self.assertEqual(data["records"][-1]["normalized"]["output_text"], "answer")

    def test_existing_services_match_and_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with fake_server("match") as baseline, fake_server("match") as candidate:
                code, report = self._run_case(
                    root, self._case(baseline, candidate), "match"
                )
                self.assertEqual(code, 0, report)
                self.assertEqual(report["status"], "match")
            with fake_server("match") as baseline, fake_server("mismatch") as candidate:
                code, report = self._run_case(
                    root, self._case(baseline, candidate), "mismatch"
                )
                self.assertEqual(code, 1)
                self.assertEqual(report["mismatches"][0]["field"], "output_text")

    def test_managed_lifecycle_cleanup_and_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            port = free_port()
            pid_files = [root / "baseline.pid", root / "candidate.pid"]
            sides = []
            for pid_file in pid_files:
                sides.append(
                    {
                        "base_url": f"http://127.0.0.1:{port}",
                        "command": [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--fake-server",
                            str(port),
                            "match",
                            str(pid_file),
                        ],
                        "env": {"API_TOKEN": "very-secret-value"},
                        "startup_timeout_sec": 5,
                        "shutdown_grace_sec": 2,
                    }
                )
            case = self._case(sides[0]["base_url"], sides[1]["base_url"])
            case["baseline"] = sides[0]
            case["candidate"] = sides[1]
            code, report = self._run_case(root, case, "managed")
            self.assertEqual(code, 0, report)
            output_dir = Path(report["output_dir"])
            redacted = (output_dir / "case.redacted.json").read_text()
            self.assertNotIn("very-secret-value", redacted)
            self.assertIn("<redacted>", redacted)
            for name, pid_file in zip(("baseline", "candidate"), pid_files):
                pid = int(pid_file.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                log = (output_dir / f"{name}-service.log").read_text()
                self.assertNotIn("very-secret-value", log)
                self.assertIn("<redacted>", log)

    def test_startup_timeout_and_early_exit_return_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pid_file = root / "sleep.pid"
            sleep_side = {
                "base_url": f"http://127.0.0.1:{free_port()}",
                "command": [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--sleep-process",
                    str(pid_file),
                ],
                "startup_timeout_sec": 0.2,
                "shutdown_grace_sec": 0.2,
            }
            case = self._case(sleep_side["base_url"], "http://127.0.0.1:1")
            case["baseline"] = sleep_side
            case_path = root / "timeout.json"
            case_path.write_text(json.dumps(case))
            result = run_script(
                "serving_diff.py",
                str(case_path),
                "--output-dir",
                str(root / "timeout-out"),
            )
            self.assertEqual(result.returncode, 2)
            pid = int(pid_file.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

            exit_side = {
                "base_url": f"http://127.0.0.1:{free_port()}",
                "command": [sys.executable, "-c", "raise SystemExit(7)"],
                "startup_timeout_sec": 2,
            }
            case = self._case(exit_side["base_url"], "http://127.0.0.1:1")
            case["baseline"] = exit_side
            case_path = root / "exit.json"
            case_path.write_text(json.dumps(case))
            result = run_script(
                "serving_diff.py",
                str(case_path),
                "--output-dir",
                str(root / "exit-out"),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("exited with code 7", result.stderr)

    def test_exact_tokens_required_and_environment_only_redaction(self) -> None:
        baseline = [{"normalized": {"token_ids": None, "output_text": "x"}}]
        with self.assertRaises(serving_common.ServingError):
            serving_diff.compare_records(
                baseline,
                baseline,
                {"fields": ["output_text"], "exact_token_ids": True},
            )
        redacted = serving_common.redact_data(
            {
                "request": {"max_tokens": 8},
                "env": {"API_TOKEN": "secret", "NORMAL": "visible"},
            }
        )
        self.assertEqual(redacted["request"]["max_tokens"], 8)
        self.assertEqual(redacted["env"]["API_TOKEN"], "<redacted>")
        self.assertEqual(redacted["env"]["NORMAL"], "visible")

    @staticmethod
    def _case(baseline: str, candidate: str) -> dict[str, object]:
        return {
            "request": {
                "endpoint": "/v1/completions",
                "payload": {"model": "model", "prompt": "hello", "stream": False},
                "requests": 1,
                "timeout_sec": 3,
            },
            "baseline": {"base_url": baseline},
            "candidate": {"base_url": candidate},
            "compare": {"prompt_tokens_min": 4},
        }

    @staticmethod
    def _run_case(
        root: Path, case: dict[str, object], name: str
    ) -> tuple[int, dict[str, object]]:
        case_path = root / f"{name}.json"
        case_path.write_text(json.dumps(case))
        args = serving_diff.parse_args(
            [str(case_path), "--output-dir", str(root / f"{name}-out")]
        )
        return serving_diff.execute(args)


def run_fake_server(port: int, mode: str, pid_file: Path) -> None:
    pid_file.write_text(str(os.getpid()))
    print(os.environ.get("API_TOKEN", ""), flush=True)
    handler = type("ManagedFakeHandler", (FakeHandler,), {"mode": mode})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.serve_forever()


if __name__ == "__main__" and "--fake-server" in sys.argv:
    index = sys.argv.index("--fake-server")
    run_fake_server(int(sys.argv[index + 1]), sys.argv[index + 2], Path(sys.argv[index + 3]))
elif __name__ == "__main__" and "--sleep-process" in sys.argv:
    index = sys.argv.index("--sleep-process")
    Path(sys.argv[index + 1]).write_text(str(os.getpid()))
    time.sleep(60)
elif __name__ == "__main__":
    unittest.main()
