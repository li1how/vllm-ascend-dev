#!/usr/bin/env python3
"""Control one existing remote Dev Container without changing its checkout."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SSH_HELPER = ROOT / '.agents/skills/remote-execution/scripts'
sys.path.insert(0, str(SSH_HELPER))
from ssh import ssh_arguments  # noqa: E402

FULL_ID = re.compile(r'[0-9a-f]{64}\Z')
INSPECT = ('{"id":{{json .Id}},"image":{{json .Config.Image}},'
           '"imageId":{{json .Image}},"state":{{json .State.Status}},'
           '"workspace":{{json (index .Config.Labels "devcontainer.local_folder")}},'
           '"legacy":{{json (index .Config.Labels "vsch.local.folder")}},'
           '"config":{{json (index .Config.Labels "devcontainer.config_file")}},'
           '"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}'
           '{"type":{{json $m.Type}},"source":{{json $m.Source}},'
           '"destination":{{json $m.Destination}}}{{end}}]}')


def ssh_call(args: argparse.Namespace, command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    argv = ssh_arguments(Path(args.ssh_config), args.host) + [shlex.join(command)]
    return subprocess.run(argv, check=True, text=True, capture_output=capture)


def container_identity(args: argparse.Namespace) -> tuple[dict, str]:
    if not FULL_ID.fullmatch(args.container_id):
        raise ValueError('完整容器 ID 必须是 64 位小写十六进制')
    result = ssh_call(args, ['docker', 'inspect', '--type', 'container', '--format', INSPECT, args.container_id])
    item = json.loads(result.stdout)
    if item['id'] != args.container_id or item['state'] != 'running':
        raise ValueError('容器 ID 或运行状态不匹配')
    if item.get('workspace') != args.workspace_folder and item.get('legacy') != args.workspace_folder:
        raise ValueError('Dev Container 工作区标签与指定宿主机路径不匹配')
    if item.get('config') != args.config:
        raise ValueError('Dev Container 配置路径与指定路径不匹配')
    workspace = None
    for mount in item.get('mounts', []):
        if mount.get('type') != 'bind':
            continue
        source = mount.get('source', '').rstrip('/')
        host_path = args.workspace_folder.rstrip('/')
        if host_path == source or host_path.startswith(source + '/'):
            candidate = mount['destination'].rstrip('/') + host_path[len(source):]
            if workspace is None or len(source) > workspace[0]:
                workspace = (len(source), candidate)
    if not workspace:
        raise ValueError('容器未绑定指定宿主机工作区')
    return item, workspace[1]


def worker_call(args: argparse.Namespace, workspace: str, worker_args: list[str]) -> int:
    shell = ('source "$1/scripts/lib/common.sh"; '
             'ws_enter_workspace; ws_select_python_env vllm-ascend-dev; '
             'exec "$PYTHON_BIN" "$1/scripts/remote-worker.py" "${@:2}"')
    command = ['devcontainer', 'exec', '--container-id', args.container_id,
               '--workspace-folder', args.workspace_folder, '--config', args.config,
               '--', 'bash', '-c', shell, '_', workspace, *worker_args]
    result = ssh_call(args, command, capture=False)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-config', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--container-id', required=True)
    parser.add_argument('--workspace-folder', required=True, help='宿主机工作区绝对路径')
    parser.add_argument('--config', required=True, help='宿主机 Dev Container 配置绝对路径')
    parser.add_argument('--discovery-source', choices=['mcp', 'ssh'], default='ssh',
                        help='节点与镜像发现的信息来源')
    sub = parser.add_subparsers(dest='action', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--weight', action='append', default=[])
    prepare = sub.add_parser('prepare')
    prepare.add_argument('--jobs', type=int)
    prepare.add_argument('--tmp-dir')
    run = sub.add_parser('run')
    run.add_argument('--case', required=True)
    run.add_argument('command', nargs=argparse.REMAINDER)
    status = sub.add_parser('status')
    status.add_argument('run_id')
    weight = sub.add_parser('weight')
    weight.add_argument('operation', choices=['list', 'record', 'verify'])
    weight.add_argument('--path')
    weight.add_argument('--model')
    weight.add_argument('--purpose')
    weight.add_argument('--mount')
    args = parser.parse_args()
    try:
        item, workspace = container_identity(args)
        print(json.dumps({'host': args.host, 'containerId': args.container_id,
                          'image': item['image'], 'imageId': item['imageId'],
                          'workspace': workspace, 'identitySource': 'SSH docker inspect',
                          'discoverySource': args.discovery_source}, ensure_ascii=False), flush=True)
        command = [args.action, '--host', args.host, '--image-id', item['imageId'],
                   '--container-id', args.container_id, '--config', args.config,
                   '--discovery-source', args.discovery_source]
        if args.action == 'inspect':
            for value in args.weight:
                command += ['--weight', value]
        elif args.action == 'prepare':
            if args.jobs is not None:
                if not 1 <= args.jobs <= 256:
                    raise ValueError('--jobs 必须在 1 到 256 之间')
                command += ['--jobs', str(args.jobs)]
            if args.tmp_dir:
                command += ['--tmp-dir', args.tmp_dir]
        elif args.action == 'run':
            argv = args.command[1:] if args.command[:1] == ['--'] else args.command
            if not argv:
                raise ValueError('run 需要在 -- 后提供原命令及参数')
            if any(re.search(r'://[^/\s]+:[^@/\s]+@|sk-[A-Za-z0-9]{20,}|^--?(?:api[-_]?key|token|password|secret)(?:=|$)', value, re.I) for value in argv):
                raise ValueError('命令参数疑似包含凭据；通过 Dev Container 环境变量传入')
            command += ['--case', args.case, '--', *argv]
        elif args.action == 'status':
            command += [args.run_id]
        else:
            command += [args.operation]
            for key in ('path', 'model', 'purpose', 'mount'):
                value = getattr(args, key)
                if value:
                    command += ['--' + key, value]
        return worker_call(args, workspace, command)
    except subprocess.CalledProcessError as exc:
        print(f'ERROR: SSH 或远端命令退出码 {exc.returncode}', file=sys.stderr)
        return exc.returncode or 1
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
