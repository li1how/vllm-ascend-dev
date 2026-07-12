#!/usr/bin/env python3
"""Safely install or remove Bark MCP entries in Codex and Claude configs."""

import argparse
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path


CONFIG_EXISTS_EXIT_CODE = 3


class BarkConfigExistsError(Exception):
    pass


class BarkConfigError(Exception):
    pass


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def find_marker_path(value, marker: str, path=()):
    if isinstance(value, list):
        for child in value:
            found = find_marker_path(child, marker, path)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    if marker in value:
        return path
    for key, child in value.items():
        found = find_marker_path(child, marker, path + (key,))
        if found is not None:
            return found
    return None


def parse_table_header(line: str):
    stripped = line.strip()
    if not stripped.startswith("["):
        return None
    marker = "__bark_config_marker__"
    try:
        parsed = tomllib.loads(f"{stripped}\n{marker} = true\n")
    except tomllib.TOMLDecodeError:
        return None
    return find_marker_path(parsed, marker)


def remove_codex_bark_tables(content: str) -> str:
    lines = content.splitlines(keepends=True)
    headers = []
    for index, line in enumerate(lines):
        table_path = parse_table_header(line)
        if table_path is not None:
            headers.append((index, table_path))

    bark_prefix = ("mcp_servers", "bark")
    ranges = []
    for position, (start, table_path) in enumerate(headers):
        if table_path[:2] != bark_prefix:
            continue
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        ranges.append((start, end))

    if not ranges:
        raise BarkConfigError(
            "已检测到 Codex bark MCP，但无法安全定位对应 TOML 表，请改为标准 [mcp_servers.bark] 写法"
        )

    kept = []
    for index, line in enumerate(lines):
        if any(start <= index < end for start, end in ranges):
            continue
        kept.append(line)
    return "".join(kept)


def edit_codex(path: Path, action: str, force: bool, url: str, auth_header: str) -> str:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise BarkConfigError(f"Codex 配置不是有效 TOML: {error}") from error

    mcp_servers = parsed.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        raise BarkConfigError("Codex 配置中的 mcp_servers 必须是 TOML 表")
    bark_exists = "bark" in mcp_servers

    if action == "install" and bark_exists and not force:
        raise BarkConfigExistsError
    if action == "uninstall" and not bark_exists:
        return "absent"

    if bark_exists:
        content = remove_codex_bark_tables(content)

    if action == "install":
        bark_block = (
            "[mcp_servers.bark]\n"
            f"url = {json.dumps(url, ensure_ascii=False)}\n\n"
            "# Compatibility workaround for https://github.com/openai/codex/issues/22667\n"
            "[mcp_servers.bark.http_headers]\n"
            f"Authorization = {json.dumps(auth_header, ensure_ascii=False)}\n"
        )
        content = f"{content.rstrip()}\n\n{bark_block}" if content.strip() else bark_block
    else:
        content = f"{content.rstrip()}\n" if content.strip() else ""

    try:
        verified = tomllib.loads(content)
    except tomllib.TOMLDecodeError as error:
        raise BarkConfigError(f"更新后的 Codex 配置不是有效 TOML: {error}") from error

    verified_bark = verified.get("mcp_servers", {}).get("bark")
    if action == "install":
        if not isinstance(verified_bark, dict):
            raise BarkConfigError("更新后的 Codex bark MCP 配置缺失")
        if verified_bark.get("url") != url:
            raise BarkConfigError("更新后的 Codex bark MCP URL 校验失败")
        if verified_bark.get("http_headers", {}).get("Authorization") != auth_header:
            raise BarkConfigError("更新后的 Codex bark MCP Header 校验失败")
    elif verified_bark is not None:
        raise BarkConfigError("Codex bark MCP 卸载校验失败")

    atomic_write(path, content)
    return "configured" if action == "install" else "removed"


def edit_claude(path: Path, action: str, force: bool, url: str) -> str:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        parsed = json.loads(content) if content.strip() else {}
    except json.JSONDecodeError as error:
        raise BarkConfigError(f"Claude Code 配置不是有效 JSON: {error}") from error

    if not isinstance(parsed, dict):
        raise BarkConfigError("Claude Code 配置的根节点必须是 JSON 对象")

    mcp_servers = parsed.get("mcpServers")
    if mcp_servers is None:
        mcp_servers = {}
        parsed["mcpServers"] = mcp_servers
    elif not isinstance(mcp_servers, dict):
        raise BarkConfigError("Claude Code 配置中的 mcpServers 必须是 JSON 对象")

    bark_exists = "bark" in mcp_servers
    if action == "install" and bark_exists and not force:
        raise BarkConfigExistsError
    if action == "uninstall" and not bark_exists:
        return "absent"

    if action == "install":
        mcp_servers["bark"] = {"type": "http", "url": url}
    else:
        del mcp_servers["bark"]

    trailing_newline = "\n" if content.endswith("\n") else ""
    updated = json.dumps(parsed, ensure_ascii=False, indent=2) + trailing_newline
    try:
        verified = json.loads(updated)
    except json.JSONDecodeError as error:
        raise BarkConfigError(f"更新后的 Claude Code 配置不是有效 JSON: {error}") from error

    verified_bark = verified.get("mcpServers", {}).get("bark")
    if action == "install":
        if verified_bark != {"type": "http", "url": url}:
            raise BarkConfigError("更新后的 Claude Code bark MCP 配置校验失败")
    elif verified_bark is not None:
        raise BarkConfigError("Claude Code bark MCP 卸载校验失败")

    atomic_write(path, updated)
    return "configured" if action == "install" else "removed"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Safely update the Bark MCP entry in a Codex or Claude config file."
    )
    parser.add_argument("config_kind", choices=("codex", "claude"))
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("config_file")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.config_file).expanduser()
    url = os.environ.get("BARK_MCP_URL_VALUE", "")
    auth_header = os.environ.get("CODEX_BARK_AUTH_HEADER_VALUE", "")

    try:
        if args.action == "install" and not url:
            raise BarkConfigError("安装模式需要 BARK_MCP_URL_VALUE")
        if args.config_kind == "codex" and args.action == "install" and not auth_header:
            raise BarkConfigError("Codex 安装模式需要 CODEX_BARK_AUTH_HEADER_VALUE")

        if args.config_kind == "codex":
            result = edit_codex(path, args.action, args.force, url, auth_header)
        else:
            result = edit_claude(path, args.action, args.force, url)
    except BarkConfigExistsError:
        return CONFIG_EXISTS_EXIT_CODE
    except (BarkConfigError, OSError) as error:
        print(error, file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
