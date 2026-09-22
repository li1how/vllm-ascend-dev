#!/usr/bin/env python3
"""Configure the user-scoped NPU Monitor entry without printing credentials."""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import tomllib
from bark_mcp_config_helper import atomic_write, parse_table_header

MCP_NAME = "npu-monitor"


class MonitorConfigError(ValueError):
    """Configuration error whose message is safe to display."""


def read_config(path: Path, client: str):
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if client == "codex":
        data = tomllib.loads(content)
    else:
        data = json.loads(content or "{}")
    if not isinstance(data, dict):
        raise MonitorConfigError("Invalid configuration")
    key = "mcp_servers" if client == "codex" else "mcpServers"
    if not isinstance(data.get(key, {}), dict):
        raise MonitorConfigError("Invalid MCP table")
    return content, data, key


def update_codex_text(content: str, entry: dict | None) -> str:
    # Refuse unusual inline/dotted definitions rather than deleting unrelated data.
    kept = []
    skip = False
    for line in content.splitlines(keepends=True):
        header = parse_table_header(line)
        if header is not None:
            skip = header[:2] == ("mcp_servers", MCP_NAME)
        if not skip:
            kept.append(line)
    result = "".join(kept)
    if MCP_NAME in tomllib.loads(result).get("mcp_servers", {}):
        raise MonitorConfigError("Use a standard [mcp_servers.npu-monitor] table")
    if entry is not None:
        result = result.rstrip() + "\n\n[mcp_servers.npu-monitor]\n"
        result += f"url = {json.dumps(entry['url'])}\n"
        result += "[mcp_servers.npu-monitor.http_headers]\n"
        result += (
            f"Authorization = {json.dumps(entry['http_headers']['Authorization'])}\n"
        )
    tomllib.loads(result)
    return result


def configure_with_cli(
    client: str, action: str, exists: bool, url: str, auth_header: str
) -> None:
    cli = shutil.which(client)
    if not cli:
        return

    commands = []
    if exists:
        command = [cli, "mcp", "remove"]
        if client == "claude":
            command.extend(["--scope", "user"])
        commands.append(command + [MCP_NAME])

    if action == "install":
        if client == "codex":
            command = [cli, "mcp", "add", MCP_NAME, "--url", url]
        else:
            command = [cli, "mcp", "add", "--scope", "user", "--transport", "http"]
            command.extend([MCP_NAME, url, "--header", "Authorization: " + auth_header])
        commands.append(command)

    for command in commands:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )


def configure(
    client: str, path: Path, action: str, force: bool, url: str, token: str
) -> str:
    content, original, key = read_config(path, client)
    expected = copy.deepcopy(original)
    entries = expected.setdefault(key, {})
    exists = MCP_NAME in entries
    if action == "install" and exists and not force:
        raise MonitorConfigError("同名配置已存在；使用 --force 替换")
    if action == "uninstall" and not exists:
        return "不存在，无需卸载"
    auth_header = "Bearer " + token
    if client == "codex":
        entry = {"url": url, "http_headers": {"Authorization": auth_header}}
    else:
        entry = {
            "type": "http",
            "url": url,
            "headers": {"Authorization": auth_header},
        }
    if action == "install":
        entries[MCP_NAME] = entry
    else:
        entries.pop(MCP_NAME)
    if client == "codex":
        candidate = update_codex_text(content, entry if action == "install" else None)
        parsed = tomllib.loads(candidate)
    else:
        candidate = json.dumps(expected, indent=2, ensure_ascii=False) + "\n"
        parsed = json.loads(candidate)
    if client == "codex" and not entries and "mcp_servers" not in parsed:
        expected.pop("mcp_servers", None)
    if parsed != expected:
        raise MonitorConfigError("拒绝修改其他配置；请使用标准 MCP 表格式")
    existed = path.exists()
    backup = path.with_name(path.name + ".npu-monitor-backup")
    if backup.exists():
        raise MonitorConfigError("配置备份已存在，请先检查上次操作")
    atomic_write(backup, content)
    try:
        configure_with_cli(client, action, exists, url, auth_header)
        # Persist the header while preserving surrounding configuration.
        atomic_write(path, candidate)
        _, verified, _ = read_config(path, client)
        if verified != expected:
            raise MonitorConfigError("配置校验失败")
    except Exception:
        if existed:
            atomic_write(path, content)
        else:
            path.unlink(missing_ok=True)
        backup.unlink()
        raise
    backup.unlink()
    return "配置已保存" if action == "install" else "已卸载"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--target", choices=["all", "codex", "claude"], default="all")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    url = os.environ.get("NPU_MONITOR_MCP_URL", "")
    token = os.environ.get("NPU_MONITOR_MCP_TOKEN", "")
    if args.action == "install":
        try:
            endpoint = urlsplit(url)
            valid = (
                endpoint.scheme == "http"
                and endpoint.hostname in ("127.0.0.1", "localhost")
                and endpoint.port
                and endpoint.path == "/mcp"
                and not endpoint.query
                and not endpoint.fragment
                and not endpoint.username
            )
        except ValueError:
            valid = False
        if not valid or not re.fullmatch(r"[0-9a-f]{64}", token):
            print("无效连接变量，请重新从插件复制", file=sys.stderr)
            return 1
    paths = {
        "codex": Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        / "config.toml",
        "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home())))
        / ".claude.json",
    }
    failed = False
    for client in paths if args.target == "all" else [args.target]:
        try:
            result = configure(
                client, paths[client], args.action, args.force, url, token
            )
            print(f"{client}: {result}")
        except MonitorConfigError as error:
            print(f"{client}: {error}", file=sys.stderr)
            failed = True
        except Exception:
            # Parser and CLI errors can contain credentials; do not print them.
            print(
                f"{client}: 配置失败；请检查依赖、配置格式及权限，若恢复失败请保留备份",
                file=sys.stderr,
            )
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
