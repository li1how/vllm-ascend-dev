#!/usr/bin/env python3
"""Container-side preflight, incremental install decision, and durable run records."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / 'log/remote-runs'
NATIVE_MARKERS = ('csrc/', 'cmake/', 'CMakeLists.txt', 'setup.py', 'pyproject.toml',
                  'setup.cfg', 'requirements', '.cpp', '.cc', '.cuh', '.cu', '.h', '.hpp')


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


def native_digest(repo: Path) -> str:
    files = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z'], check=True,
                           capture_output=True).stdout.decode().split('\0')
    digest = hashlib.sha256()
    for name in sorted(filter(None, files)):
        if not any(name.startswith(prefix) or name.endswith(prefix) for prefix in NATIVE_MARKERS):
            continue
        path = repo / name
        if not path.is_file():
            continue
        digest.update(name.encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def package_origin(name: str, repo: Path) -> dict:
    code = '''import importlib.util,json,sys
s=importlib.util.find_spec(sys.argv[1]);print(json.dumps({"origin":s.origin if s else None,"locations":list(s.submodule_search_locations or []) if s else []}))'''
    with tempfile.TemporaryDirectory() as neutral:
        proc = subprocess.run([sys.executable, '-c', code, name], cwd=neutral,
                              text=True, capture_output=True, timeout=30)
    if proc.returncode:
        return {'ok': False, 'error': proc.stderr.strip()[-500:]}
    data = json.loads(proc.stdout)
    origin = data['origin']
    try:
        good = bool(origin) and Path(origin).resolve().is_relative_to(repo.resolve())
    except (OSError, ValueError):
        good = False
    return {'ok': good, 'origin': origin, 'locations': data['locations']}


def environment(image_id: str) -> dict:
    try:
        import torch_npu
        torch_npu_version = getattr(torch_npu, '__version__', 'unknown')
    except Exception as exc:
        torch_npu_version = f'unavailable: {type(exc).__name__}'
    return {'imageId': image_id, 'python': str(Path(sys.executable).resolve()),
            'torchNpu': torch_npu_version, 'cann': os.environ.get('ASCEND_HOME_PATH') or
            os.environ.get('ASCEND_OPP_PATH') or 'unknown'}


def repos() -> dict:
    output = {}
    for name, module in [('vllm', 'vllm'), ('vllm-ascend', 'vllm_ascend')]:
        repo = ROOT / name
        output[name] = {'sha': git(repo, 'rev-parse', 'HEAD'),
                        'dirty': bool(git(repo, 'status', '--porcelain')),
                        'origin': package_origin(module, repo),
                        'nativeDigest': native_digest(repo)}
    return output


def cache_state() -> dict:
    path = ROOT / 'vllm-ascend/csrc/build/CMakeCache.txt'
    if not path.exists():
        return {'exists': False, 'path': str(path)}
    root = os.environ.get('ASCEND_HOME_PATH') or (
        str(Path(os.environ['ASCEND_OPP_PATH']).parent) if os.environ.get('ASCEND_OPP_PATH') else None)
    mismatches = []
    if root:
        for line in path.read_text(errors='replace').splitlines():
            if not any(line.startswith(key + ':') for key in ('CUSTOM_ASCEND_CANN_PACKAGE_PATH',
                    'ASCEND_CMAKE_DIR', 'ACL_INC_DIR', 'RUNTIME_INC_DIR', 'TILINGAPI_INC_DIR')):
                continue
            value = line.partition('=')[2]
            if value.startswith('/') and not Path(value).resolve().is_relative_to(Path(root).resolve()):
                mismatches.append(line.split(':', 1)[0] + '=' + value)
    return {'exists': True, 'path': str(path), 'cannMismatches': mismatches,
            'compatibility': 'unknown' if not root else ('mismatch' if mismatches else 'matched')}


def weight_file(host: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', host)
    return ROOT / 'log/remote-nodes' / safe / 'weights.json'


def load_weights(host: str) -> list[dict]:
    path = weight_file(host)
    return json.loads(path.read_text()) if path.is_file() else []


def weight(args: argparse.Namespace) -> int:
    path = weight_file(args.host)
    rows = load_weights(args.host)
    if args.operation == 'record':
        if not args.path or not args.model or not args.purpose:
            raise ValueError('record 需要 --path、--model 和 --purpose')
        target = Path(args.path)
        if not target.is_absolute() or not target.exists():
            raise ValueError('权重路径必须是已存在的绝对路径')
        row = {'path': str(target.resolve()), 'model': args.model, 'purpose': args.purpose,
               'mount': args.mount, 'verifiedAt': now()}
        rows = [old for old in rows if old['path'] != row['path']] + [row]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    elif args.operation == 'verify':
        for row in rows:
            if not args.path or row['path'] == args.path:
                row['exists'] = Path(row['path']).exists()
                if row['exists']:
                    row['verifiedAt'] = now()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{k: v for k, v in row.items() if k != 'exists'} for row in rows],
                                   ensure_ascii=False, indent=2) + '\n')
    emit({'file': str(path), 'weights': rows})
    return 0


def proxy_state() -> dict:
    value = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
    if not value:
        return {'configured': False, 'reachable': False}
    parsed = urlsplit(value if '://' in value else 'http://' + value)
    try:
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        hostname, port = None, None
    if not hostname or not port:
        return {'configured': True, 'reachable': False, 'reason': 'invalid proxy URL'}
    try:
        with socket.create_connection((hostname, port), timeout=3):
            return {'configured': True, 'reachable': True}
    except OSError as exc:
        return {'configured': True, 'reachable': False, 'reason': type(exc).__name__}


def inspect(args: argparse.Namespace) -> int:
    known = load_weights(args.host)
    requested = args.weight or [row['path'] for row in known]
    emit({'host': args.host, 'imageId': args.image_id, 'observedAt': now(),
          'environment': environment(args.image_id), 'repositories': repos(),
          'proxy': proxy_state(),
          'cache': cache_state(),
          'weights': [{'path': path, 'exists': Path(path).exists(),
                       'inventory': next((row for row in known if row['path'] == path), None)}
                      for path in requested]})
    return 0


@contextlib.contextmanager
def checkout_lock(exclusive: bool):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'checkout.lock').open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield stream


def prepare(args: argparse.Namespace) -> int:
    started = time.monotonic()
    with checkout_lock(True):
        stamp_file = STATE / 'install-stamp.json'
        stamp = json.loads(stamp_file.read_text()) if stamp_file.is_file() else {}
        current = repos()
        env = environment(args.image_id)
        cache = cache_state()
        decisions = {}
        reasons = {}
        for name in ('vllm', 'vllm-ascend'):
            previous = stamp.get(name, {})
            current_repo = current[name]
            cause = []
            if not current_repo['origin']['ok']:
                cause.append('editable source missing or points elsewhere')
            if previous:
                if previous.get('nativeDigest') != current_repo['nativeDigest']:
                    cause.append('native or dependency input changed')
                old_env = previous.get('environment', {})
                for key in ('imageId', 'cann', 'python', 'torchNpu'):
                    if old_env.get(key) != env.get(key):
                        cause.append(key + ' changed')
            elif current_repo['origin']['ok']:
                cause.append('no trusted install fingerprint')
            decisions[name] = ('install' if not current_repo['origin']['ok'] or
                               (previous and any(reason != 'no trusted install fingerprint' for reason in cause)) else
                               'unverified_skip' if not previous else 'skip')
            if decisions[name] == 'skip' and previous.get('sha') != current_repo['sha']:
                cause.append('source changed without native input change; restart service')
            reasons[name] = cause
        if decisions['vllm'] == 'install' and decisions['vllm-ascend'] == 'skip':
            decisions['vllm-ascend'] = 'install'
            reasons['vllm-ascend'].append('vllm dependency is reinstalling')
        if cache.get('cannMismatches') and 'install' in decisions.values():
            emit({'decisions': decisions, 'reasons': reasons, 'cache': cache, 'error': '构建缓存 CANN 路径失配；未清理或构建'})
            return 3
        commands = []
        install = ROOT / 'scripts/install-vllm-source.sh'
        for name, flag in [('vllm', '--vllm-only'), ('vllm-ascend', '--ascend-only')]:
            if decisions[name] != 'install':
                continue
            command = [str(install), '--skip-uninstall', flag]
            if args.jobs and name == 'vllm-ascend':
                command += ['--jobs', str(args.jobs)]
            if args.tmp_dir:
                command += ['--tmp-dir', args.tmp_dir]
            commands.append(command)
            result = subprocess.run(command, cwd=ROOT)
            if result.returncode:
                emit({'decisions': decisions, 'reasons': reasons, 'failedCommand': command,
                      'exitCode': result.returncode, 'elapsedSeconds': round(time.monotonic() - started, 1)})
                return result.returncode
            stamp[name] = {'sha': current[name]['sha'], 'nativeDigest': current[name]['nativeDigest'],
                           'environment': env, 'installedAt': now()}
            stamp_file.write_text(json.dumps(stamp, ensure_ascii=False, indent=2) + '\n')
        report = {'decisions': decisions, 'reasons': reasons, 'commands': commands, 'cache': cache,
                  'elapsedSeconds': round(time.monotonic() - started, 1), 'finishedAt': now(),
                  'note': 'Python-only changes use editable source; restart the service. Unknown prior install is left untouched.'}
        (STATE / 'prepare-last.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        emit(report)
    return 0


def redact(argv: list[str]) -> list[str]:
    masked = []
    hide_next = False
    for value in argv:
        if hide_next:
            masked.append('[REDACTED]')
            hide_next = False
        elif re.search(r'(token|password|secret|api[_-]?key)', value, re.I):
            masked.append(value.split('=')[0] + '=[REDACTED]' if '=' in value else value)
            hide_next = '=' not in value
        elif re.search(r'://[^/\s]+:[^@/\s]+@', value):
            masked.append('[REDACTED URL]')
        else:
            masked.append(value)
    return masked


def run(args: argparse.Namespace) -> int:
    argv = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not argv:
        raise ValueError('run 需要 -- 后的命令')
    if any(re.search(r'://[^/\s]+:[^@/\s]+@|sk-[A-Za-z0-9]{20,}|^--?(?:api[-_]?key|token|password|secret)(?:=|$)', value, re.I) for value in argv):
        raise ValueError('命令参数疑似包含凭据；通过 Dev Container 环境变量传入')
    if argv[0].startswith('./') and (ROOT / argv[0]).is_file():
        argv[0] = str((ROOT / argv[0]).resolve())
    case = re.sub(r'[^A-Za-z0-9_.-]', '_', args.case)
    if not case or case in ('.', '..'):
        raise ValueError('无效的用例名')
    with checkout_lock(False) as lock:
        run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        directory = STATE / (case + '-' + run_id)
        directory.mkdir(parents=True)
        prepare_file = STATE / 'prepare-last.json'
        manifest = {'runId': run_id, 'case': case, 'host': args.host, 'containerId': args.container_id,
                    'imageId': args.image_id, 'config': args.config, 'workspace': str(ROOT),
                    'discoverySource': args.discovery_source,
                    'startedAt': now(), 'command': redact(argv), 'repositories': repos(),
                    'installation': json.loads(prepare_file.read_text()) if prepare_file.is_file() else None,
                    'log': str(directory / 'run.log'), 'status': 'running'}
        (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_execute', str(directory), '--', *argv],
                                 cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                                 pass_fds=(lock.fileno(),))
        (directory / 'pid').write_text(str(child.pid))
        emit({'runId': run_id, 'directory': str(directory), 'pid': child.pid,
              'log': manifest['log'], 'execution': manifest})
    return 0


def execute(directory: Path, argv: list[str]) -> int:
    started = time.monotonic()
    with checkout_lock(False), (directory / 'run.log').open('ab', buffering=0) as log:
        try:
            proc = subprocess.run(argv, cwd=directory, stdout=log, stderr=subprocess.STDOUT)
            code = proc.returncode
        except OSError as exc:
            log.write((str(exc) + '\n').encode())
            code = 127
    status = {'status': 'complete' if code == 0 else 'failed', 'exitCode': code,
              'finishedAt': now(), 'elapsedSeconds': round(time.monotonic() - started, 1)}
    (directory / 'result.json').write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n')
    return code


def status(args: argparse.Namespace) -> int:
    if not args.run_id or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', args.run_id):
        raise ValueError('无效运行 ID')
    matches = [path for path in STATE.glob('*-' + args.run_id) if path.is_dir()]
    if len(matches) != 1:
        raise ValueError('未找到唯一运行记录')
    directory = matches[0]
    manifest = json.loads((directory / 'manifest.json').read_text())
    result_file = directory / 'result.json'
    if result_file.is_file():
        manifest.update(json.loads(result_file.read_text()))
    else:
        pid = int((directory / 'pid').read_text()) if (directory / 'pid').exists() else None
        try:
            if pid is None:
                raise ProcessLookupError()
            os.kill(pid, 0)
            manifest['status'] = 'running'
        except ProcessLookupError:
            manifest['status'] = 'unknown_after_disconnect'
    emit(manifest)
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == '_execute':
        argv = sys.argv[4:] if sys.argv[3:4] == ['--'] else sys.argv[3:]
        return execute(Path(sys.argv[2]), argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'prepare', 'run', 'status', 'weight'])
    parser.add_argument('--host', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--container-id', default='')
    parser.add_argument('--config', default='')
    parser.add_argument('--discovery-source', choices=['mcp', 'ssh'], default='ssh')
    parser.add_argument('--weight', action='append', default=[])
    parser.add_argument('--jobs', type=int)
    parser.add_argument('--tmp-dir')
    parser.add_argument('--case')
    parser.add_argument('--path')
    parser.add_argument('--model')
    parser.add_argument('--purpose')
    parser.add_argument('--mount')
    parser.add_argument('operation_or_run_id', nargs='?')
    input_args = sys.argv[1:]
    command = []
    if input_args[:1] == ['run'] and '--' in input_args:
        split = input_args.index('--')
        command = input_args[split + 1:]
        input_args = input_args[:split]
    args = parser.parse_intermixed_args(input_args)
    if args.action == 'run':
        args.command = command
    if args.action == 'inspect': return inspect(args)
    if args.action == 'prepare': return prepare(args)
    if args.action == 'run': return run(args)
    if args.action == 'status':
        args.run_id = args.operation_or_run_id
        return status(args)
    args.operation = args.operation_or_run_id
    return weight(args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
