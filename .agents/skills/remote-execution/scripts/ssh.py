#!/usr/bin/env python3
"""Run one SSH command with WSL paths and nonstandard host aliases."""

from __future__ import annotations

import argparse
import fnmatch
import glob
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def local_path(value: str) -> str:
    value = os.path.expandvars(os.path.expanduser(value))
    if (
        os.environ.get("WSL_DISTRO_NAME")
        or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    ):
        match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
        if match:
            return f"/mnt/{match[1].lower()}/" + match[2].replace("\\", "/")
    return value


def option_path(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def jump_target(value: str) -> tuple[str, list[str]]:
    user, separator, host = value.rpartition("@")
    if not separator:
        host = value
    match = re.fullmatch(r"\[([^]]+)\](?::([0-9]+))?|([^:]+)(?::([0-9]+))?", host)
    if not match:
        raise ValueError(
            "Unsupported SSH jump target; use an alias defined in the selected config"
        )
    address = match[1] or match[3]
    port = match[2] or match[4]
    overrides = (["-l", user] if separator else []) + (["-p", port] if port else [])
    return address, overrides


def _matches(patterns: Sequence[str], host: str) -> bool:
    positive = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if fnmatch.fnmatchcase(host.lower(), pattern.lstrip("!").lower()):
            if negated:
                return False
            positive = True
    return positive


def alias_options(config: Path, alias: str) -> dict[str, list[str]]:
    """Resolve Host sections when OpenSSH rejects a literal alias such as host(A5).

    Native aliases are left entirely to OpenSSH. Match cannot safely be emulated
    for an invalid alias, so it is rejected instead of silently selecting a host.
    """
    values: dict[str, list[str]] = {}
    active = True
    stack: set[Path] = set()

    def visit(path: Path) -> None:
        nonlocal active
        path = path.resolve()
        if path in stack or len(stack) >= 16:
            raise ValueError("Recursive SSH Include configuration")
        stack.add(path)
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                lexer = shlex.shlex(line, posix=True)
                lexer.whitespace_split = True
                lexer.escape = ""  # Preserve Windows IdentityFile paths.
                parts = list(lexer)
                if not parts:
                    continue
                key, equal, attached = parts[0].partition("=")
                arguments = ([attached] if equal and attached else []) + parts[1:]
                if arguments and arguments[0].startswith("="):
                    arguments[0] = arguments[0][1:]
                    if not arguments[0]:
                        arguments.pop(0)
                key = key.lower()
                if key == "host":
                    active = _matches(arguments, alias)
                elif key == "match":
                    raise ValueError(
                        "An invalid SSH alias combined with Match is unsupported; use a valid alias"
                    )
                elif key == "include" and active:
                    for pattern in arguments:
                        expanded = local_path(pattern)
                        if not Path(expanded).is_absolute():
                            expanded = str(config.parent / expanded)
                        for included in sorted(glob.glob(expanded)):
                            visit(Path(included))
                elif active and arguments:
                    value = " ".join(arguments)
                    if key in ("proxyjump", "proxycommand") and (
                        {"proxyjump", "proxycommand"} & values.keys()
                    ):
                        continue
                    if key == "proxycommand":
                        value = re.sub(r"^\s*[^=\s]+\s*(?:=\s*)?", "", line, count=1)
                    if key in ("identityfile", "certificatefile"):
                        values.setdefault(key, []).append(local_path(value))
                    elif key not in values:
                        values[key] = [value]
        finally:
            stack.remove(path)

    visit(config)
    hostname = values.get("hostname", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", hostname) or hostname.startswith("-"):
        raise ValueError("The selected SSH alias needs a valid HostName")
    return values


def ssh_arguments(
    config: Path,
    host: str,
    *,
    depth: int = 0,
    jump_override: str | None = None,
    overrides: Sequence[str] = (),
) -> list[str]:
    if depth >= 8:
        raise ValueError("SSH jump configuration is recursive or too deep")
    config = Path(local_path(str(config))).resolve()
    if not config.is_file():
        raise ValueError(f"SSH config does not exist: {config}")
    args = [
        "ssh",
        "-T",
        "-F",
        str(config),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UpdateHostKeys=no",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ConnectTimeout=10",
    ]
    args += list(overrides)
    native = bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", host)) and not host.startswith("-")
    if native:
        # -G delegates Include/Match and first-value semantics to OpenSSH; it
        # does not open a session. Only path and jump settings need adapting.
        output = subprocess.run(
            [*args, "-G", host],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
        values: dict[str, list[str]] = {}
        for line in output.splitlines():
            key, separator, value = line.partition(" ")
            if separator and key in (
                "identityfile",
                "certificatefile",
                "userknownhostsfile",
                "proxyjump",
            ):
                values.setdefault(key, []).append(value)
        destination = host
    else:
        values = alias_options(config, host)
        destination = values["hostname"][0]
    known_hosts = config.parent / "known_hosts"
    if known_hosts.is_file() and (
        "userknownhostsfile" not in values
        or values["userknownhostsfile"] == ["~/.ssh/known_hosts ~/.ssh/known_hosts2"]
    ):
        values["userknownhostsfile"] = [option_path(str(known_hosts))]
    proxy_jump = (
        jump_override
        if jump_override is not None
        else values.get("proxyjump", ["none"])[0]
    )
    if proxy_jump.lower() != "none":
        chain = proxy_jump.split(",")
        jump_host, jump_options = jump_target(chain[-1])
        child_args = ssh_arguments(
            config,
            jump_host,
            depth=depth + 1,
            jump_override=",".join(chain[:-1]) if len(chain) > 1 else None,
            overrides=jump_options,
        )
        child_args = [*child_args[:-1], "-W", "%h:%p", child_args[-1]]
        # Apply strict host-key options at each hop, not just the final host.
        args += ["-o", "ProxyCommand=" + shlex.join(child_args)]
    for key, entries in values.items():
        if key == "proxyjump":
            continue
        for value in entries:
            if value.lower() == "none" and key in (
                "identityfile",
                "certificatefile",
            ):
                continue
            if key == "identityfile":
                args += ["-i", local_path(value)]
            elif key == "certificatefile":
                args += ["-o", "CertificateFile=" + option_path(local_path(value))]
            elif key == "userknownhostsfile":
                lexer = shlex.shlex(value, posix=True)
                lexer.whitespace_split = True
                lexer.escape = ""
                args += [
                    "-o",
                    "UserKnownHostsFile="
                    + " ".join(option_path(local_path(path)) for path in lexer),
                ]
            else:
                args += ["-o", f"{key}={value}"]
    return args + [destination]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, type=Path, help="SSH configuration file"
    )
    parser.add_argument("host", help="Host alias from the selected SSH configuration")
    parser.add_argument(
        "command", nargs=argparse.REMAINDER, help="Command and arguments after --"
    )
    args = parser.parse_args(argv)
    remote_command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not remote_command:
        parser.error("a remote command is required after HOST --")
    try:
        command = ssh_arguments(args.config, args.host) + [shlex.join(remote_command)]
        os.execvp(command[0], command)
    except subprocess.CalledProcessError as exc:
        print(
            f"error: {exc.stderr.strip() or 'SSH configuration lookup failed'}",
            file=sys.stderr,
        )
        return exc.returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
