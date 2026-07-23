#!/usr/bin/env python3
"""Shared HTTP, response-normalization, and redaction helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

SENSITIVE_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


class ServingError(RuntimeError):
    """Raised when a serving request or readiness check fails."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes
    elapsed_sec: float

    def json_object(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServingError("response is not a JSON object") from exc
        if not isinstance(value, dict):
            raise ServingError("response is not a JSON object")
        return value


def is_sensitive_name(name: object) -> bool:
    upper = str(name).upper()
    return any(marker in upper for marker in SENSITIVE_MARKERS)


def redact_environment(environment: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): "<redacted>" if is_sensitive_name(key) else redact_data(value)
        for key, value in environment.items()
    }


def redact_data(value: Any) -> Any:
    """Return a JSON-safe copy with sensitive environment values redacted."""
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if str(key).lower() in ("env", "environment") and isinstance(item, Mapping):
                result[str(key)] = redact_environment(item)
            else:
                result[str(key)] = redact_data(item)
        return result
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def redact_log_file(path: Path, environment: Mapping[str, Any]) -> None:
    """Replace sensitive environment values that a managed process printed."""
    secrets = {
        str(value)
        for key, value in environment.items()
        if is_sensitive_name(key) and str(value)
    }
    if not secrets or not path.is_file():
        return
    content = path.read_text(encoding="utf-8", errors="replace")
    for secret in sorted(secrets, key=len, reverse=True):
        content = content.replace(secret, "<redacted>")
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(redact_data(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def join_url(base_url: str, endpoint: str) -> str:
    return base_url.rstrip("/") + "/" + endpoint.lstrip("/")


def _opener(use_env_proxy: bool) -> urllib.request.OpenerDirector:
    if use_env_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_sec: float,
    use_env_proxy: bool = False,
) -> HttpResult:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    started = time.perf_counter()
    try:
        with _opener(use_env_proxy).open(request, timeout=timeout_sec) as response:
            return HttpResult(
                status=response.status,
                body=response.read(),
                elapsed_sec=time.perf_counter() - started,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ServingError(f"HTTP {exc.code} from {url}: {body}") from exc
    except Exception as exc:
        raise ServingError(f"{type(exc).__name__} from {url}: {exc}") from exc


def wait_for_health(
    base_url: str,
    health_path: str = "/v1/models",
    *,
    timeout_sec: float = 1200,
    interval_sec: float = 1,
    use_env_proxy: bool = False,
    process_exited: Callable[[], int | None] | None = None,
) -> None:
    if timeout_sec <= 0 or interval_sec <= 0:
        raise ServingError("health timeout and interval must be positive")
    url = join_url(base_url, health_path)
    deadline = time.monotonic() + timeout_sec
    last_error = "service did not respond"
    while time.monotonic() < deadline:
        if process_exited is not None:
            return_code = process_exited()
            if return_code is not None:
                raise ServingError(
                    f"managed service exited with code {return_code} before becoming healthy"
                )
        try:
            result = http_json(
                url,
                timeout_sec=min(5.0, max(0.1, deadline - time.monotonic())),
                use_env_proxy=use_env_proxy,
            )
            if 200 <= result.status < 300:
                return
            last_error = f"HTTP status {result.status}"
        except ServingError as exc:
            last_error = str(exc)
        time.sleep(min(interval_sec, max(0.0, deadline - time.monotonic())))
    raise ServingError(f"service health check timed out after {timeout_sec:g}s: {last_error}")


def _choice_token_ids(choice: Mapping[str, Any]) -> list[Any] | None:
    direct = choice.get("token_ids")
    if isinstance(direct, list):
        return direct
    logprobs = choice.get("logprobs")
    if not isinstance(logprobs, Mapping):
        return None
    tokens = logprobs.get("tokens")
    if isinstance(tokens, list):
        return tokens
    content = logprobs.get("content")
    if isinstance(content, list):
        extracted = []
        for item in content:
            if not isinstance(item, Mapping):
                return None
            token = item.get("token_id", item.get("token"))
            if token is None:
                return None
            extracted.append(token)
        return extracted
    return None


def normalize_response(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ServingError("response does not contain choices[0]")
    choice = choices[0]
    message = choice.get("message")
    if isinstance(message, Mapping):
        output_text = message.get("content", "")
    else:
        output_text = choice.get("text", "")
    usage = response.get("usage")
    usage_dict = dict(usage) if isinstance(usage, Mapping) else {}
    token_ids = _choice_token_ids(choice)
    if token_ids is None and isinstance(response.get("token_ids"), list):
        token_ids = list(response["token_ids"])
    return {
        "output_text": output_text,
        "finish_reason": choice.get("finish_reason"),
        "output_tokens": usage_dict.get("completion_tokens"),
        "prompt_tokens": usage_dict.get("prompt_tokens"),
        "token_ids": token_ids,
        "id": response.get("id"),
        "created": response.get("created"),
        "usage": usage_dict,
    }
