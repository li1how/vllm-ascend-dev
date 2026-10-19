#!/usr/bin/env python3
"""Inspect, export and restore validated vLLM-Ascend action-cache entries."""
from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

BUILD_DIRS = ('vllm-ascend/csrc/build', 'vllm-ascend/build')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'JSON object required: {path}')
    return value


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def save_report(workspace, action, report):
    logs = workspace / 'log/cache-transfer'
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / (dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '-' + action + '.json')
    report['reportPath'] = str(path)
    write_json(path, report)


def command(argv, cwd=None):
    try:
        return subprocess.run(argv, cwd=cwd, check=True, text=True,
                              capture_output=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def contained(root, relative):
    if not isinstance(relative, str):
        raise ValueError('Relative path must be a string')
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or '..' in part.parts:
        raise ValueError(f'Invalid relative path: {relative}')
    path = root.joinpath(*part.parts)
    if not path.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes directory: {relative}')
    return path


def engine_format(workspace):
    # Read the tested engine's format without importing its build dependencies.
    source = workspace / 'vllm-ascend/csrc/scripts/build_cache.py'
    values = {}
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('SCHEMA_VERSION', 'ARTIFACT_MODEL_BY_DOMAIN'):
                    values[target.id] = ast.literal_eval(node.value)
    return values['SCHEMA_VERSION'], values['ARTIFACT_MODEL_BY_DOMAIN']


def validate_entry(entry, schema, models):
    if entry.is_symlink():
        raise ValueError(f'Entry is a symlink: {entry}')
    manifest = read_json(entry / 'manifest.json')
    domain, key = manifest.get('domain'), manifest.get('final_key', '')
    if (manifest.get('schema') != schema or domain not in models
            or manifest.get('artifact_model', 1) != models[domain]
            or not isinstance(key, str) or len(key) != 64
            or any(c not in '0123456789abcdef' for c in key) or entry.name != key):
        raise ValueError(f'Unsupported cache identity: {entry}')
    artifacts = manifest.get('artifacts')
    root = entry / 'artifacts'
    if not isinstance(artifacts, list) or not artifacts or root.is_symlink() or not root.is_dir():
        raise ValueError(f'Missing artifacts: {entry}')
    size = 0
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError(f'Invalid artifact record: {entry}')
        path = contained(root, item.get('path'))
        kind = item.get('kind', 'file')
        if kind == 'symlink':
            target = item.get('target')
            if (not isinstance(target, str) or not path.is_symlink()
                    or os.readlink(path) != target or Path(target).is_absolute()
                    or not path.resolve().is_relative_to(root.resolve()) or not path.exists()):
                raise ValueError(f'Invalid artifact symlink: {path}')
        elif (kind != 'file' or path.is_symlink() or not path.is_file()
              or sha256(path) != item.get('sha256')):
            raise ValueError(f'Artifact hash mismatch: {path}')
        else:
            size += path.stat().st_size
    return manifest, size


def entries(cache):
    for base, dirs, files in os.walk(cache, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(base) / d).is_symlink())
        if 'manifest.json' in files:
            yield Path(base)
            dirs[:] = []


@contextlib.contextmanager
def entry_lock(cache, entry, domain, create=False):
    lock = cache / domain / '.locks' / (hashlib.sha256(str(entry).encode()).hexdigest() + '.lock')
    if not create and not lock.exists():
        yield
        return
    if create:
        lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b' if create else 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def inventory(cache, schema, models):
    report = {'entries': [], 'rejected': [], 'bytes': 0}
    for entry in entries(cache):
        relative = entry.relative_to(cache).as_posix()
        try:
            manifest, size = validate_entry(entry, schema, models)
            if relative.split('/')[0] != manifest['domain']:
                raise ValueError('Cache domain does not match directory')
            report['entries'].append(relative)
            report['bytes'] += size
        except (OSError, ValueError, TypeError, KeyError) as exc:
            report['rejected'].append({'entry': relative, 'reason': str(exc)})
    return report


def native_inputs(workspace):
    result = {}
    for name in ('vllm', 'vllm-ascend'):
        repo = workspace / name
        tracked = command(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], repo)
        if tracked is None:
            result[name] = None
            continue
        digest = hashlib.sha256()
        for relative in sorted(set(filter(None, tracked.split('\0')))):
            if not (relative.startswith(('csrc/', 'cmake/', 'requirements'))
                    or relative in ('CMakeLists.txt', 'setup.py', 'setup.cfg', 'pyproject.toml', '.gitmodules',
                                    'vllm_ascend/envs.py', 'vllm_ascend/device/hardware.py')):
                continue
            path = repo / relative
            digest.update(relative.encode() + b'\0')
            if path.is_file():
                digest.update(sha256(path).encode())
            elif path.is_dir():
                # A dirty/missing submodule cannot prove build-state equivalence.
                status = command(['git', 'status', '--porcelain'], path)
                head = command(['git', 'rev-parse', 'HEAD'], path)
                if status != '' or head is None:
                    result[name] = None
                    break
                digest.update(head.encode())
            else:
                digest.update(b'missing')
        else:
            result[name] = digest.hexdigest()
    return result


def environment(image_id='', soc=''):
    cann = os.environ.get('ASCEND_HOME_PATH')
    if not cann and os.environ.get('ASCEND_OPP_PATH'):
        cann = str(Path(os.environ['ASCEND_OPP_PATH']).parent)
    if not cann:
        cann = next((p for p in ('/usr/local/Ascend/ascend-toolkit/latest', '/usr/local/Ascend/latest')
                     if Path(p).is_dir()), None)
    metadata = {}
    if cann:
        for name in ('version.info', 'ascend_toolkit_install.info',
                     f'{platform.machine()}-linux/ascend_toolkit_install.info'):
            path = Path(cann).resolve() / name
            if path.is_file():
                metadata[name] = sha256(path)
    packages = {}
    for name in ('torch', 'torch-npu', 'pybind11'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    release = Path('/etc/os-release')
    return {'architecture': platform.machine(), 'system': platform.system(),
            'libc': list(platform.libc_ver()), 'osRelease': sha256(release) if release.exists() else None,
            'imageId': image_id or None, 'soc': soc or os.environ.get('SOC_VERSION'),
            'python': str(Path(sys.executable).resolve()), 'pythonVersion': platform.python_version(),
            'cann': str(Path(cann).resolve()) if cann else None, 'cannMetadata': metadata,
            'packages': packages,
            'tools': {n: command([p, '--version']) for n, p in (
                ('cxx', os.environ.get('CXX_COMPILER', 'c++')), ('cmake', 'cmake'),
                ('bisheng', 'bisheng'), ('ccec', 'ccec'))},
            'buildOptions': {n: os.environ.get(n) for n in ('CMAKE_BUILD_TYPE', 'CXX_COMPILER',
                'C_COMPILER', 'VLLM_BATCH_INVARIANT', 'VLLM_ASCEND_ENABLE_BATCH_MEMCPY')}}


def provenance(workspace, cache, args):
    repos = {n: {'sha': command(['git', 'rev-parse', 'HEAD'], workspace / n),
                 'dirty': command(['git', 'status', '--porcelain'], workspace / n) not in ('', None)}
             for n in ('vllm', 'vllm-ascend')}
    return {'host': args.host or socket.gethostname(), 'workspace': str(workspace),
            'cacheDir': str(cache), 'repos': repos, 'nativeInputs': native_inputs(workspace),
            'environment': environment(args.image_id, args.soc)}


def event_summary(path):
    counts = {n: 0 for n in ('HIT', 'MISS', 'BYPASS')}
    if path and path.is_file():
        for line in path.read_text().splitlines():
            try:
                item = json.loads(line)
                if item.get('event') == 'cache_result' and item.get('status') in counts:
                    counts[item['status']] += 1
            except (ValueError, AttributeError):
                continue
    return counts


def tree_manifest(root):
    result = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            if path.is_symlink():
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f'External build-state symlink: {path}')
                result[path.relative_to(root).as_posix()] = {'target': os.readlink(path)}
            elif path.is_file():
                result[path.relative_to(root).as_posix()] = {'sha256': sha256(path)}
    return result


@contextlib.contextmanager
def checkout_lock(workspace):
    path = workspace / 'log/remote-runs/checkout.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def copy_entry(source, destination, schema, models):
    manifest, _ = validate_entry(source, schema, models)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.transfer-', dir=destination.parent) as temp:
        staging = Path(temp) / destination.name
        (staging / 'artifacts').mkdir(parents=True)
        for item in manifest['artifacts']:
            src = contained(source / 'artifacts', item['path'])
            dst = contained(staging / 'artifacts', item['path'])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if item.get('kind') == 'symlink':
                dst.symlink_to(item['target'])
            else:
                shutil.copy2(src, dst)
        shutil.copy2(source / 'manifest.json', staging / 'manifest.json')
        validate_entry(staging, schema, models)
        os.rename(staging, destination)


def export_snapshot(workspace, cache, snapshot, args):
    if snapshot.exists():
        raise ValueError(f'Snapshot exists; choose a new directory: {snapshot}')
    schema, models = engine_format(workspace)
    report = {'schema': 1, 'cacheSchema': schema,
              'createdAt': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source': provenance(workspace, cache, args), 'entries': [], 'rejected': [], 'bytes': 0,
              'buildState': {'directories': [], 'files': {}}}
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=snapshot.parent) as temp:
        staging = Path(temp) / 'snapshot'
        staging.mkdir()
        for source in entries(cache):
            relative = source.relative_to(cache).as_posix()
            try:
                manifest, size = validate_entry(source, schema, models)
                if relative.split('/')[0] != manifest['domain']:
                    raise ValueError('Cache domain does not match directory')
                with entry_lock(cache, source, manifest['domain']):
                    copy_entry(source, staging / 'cache' / relative, schema, models)
                report['entries'].append(relative)
                report['bytes'] += size
            except (OSError, ValueError, TypeError, KeyError) as exc:
                report['rejected'].append({'entry': relative, 'reason': str(exc)})
        if args.include_build_state:
            with checkout_lock(workspace):
                # Cache export can be long; inspect the checkout again under its lock.
                report['source'] = provenance(workspace, cache, args)
                origin_file = workspace / 'log/last-native-build.json'
                try:
                    origin = read_json(origin_file) if origin_file.is_file() else {}
                    reasons = build_state_reasons(origin, report['source']) if origin else ['successful build origin unavailable']
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    reasons = ['invalid successful build origin: ' + str(exc)]
                report['buildState']['skipped'] = reasons
                if not reasons:
                    for relative in BUILD_DIRS:
                        source = workspace / relative
                        if source.is_dir() and not source.is_symlink():
                            shutil.copytree(source, staging / 'build-state' / relative, symlinks=True,
                                            ignore=shutil.ignore_patterns('*.lock', '*.tmp', '.locks',
                                                '.action_locks', '*.pid', '.transfer-*'))
                            report['buildState']['directories'].append(relative)
                state = staging / 'build-state'
                if state.exists():
                    report['buildState']['files'] = tree_manifest(state)
        write_json(staging / 'manifest.json', report)
        os.rename(staging, snapshot)
    return report


def build_state_reasons(source, target):
    reasons = [n + ' differs' for n in ('workspace', 'cacheDir', 'nativeInputs', 'environment')
               if source.get(n) != target.get(n)]
    env = target['environment']
    if (not env['imageId'] or not env['soc'] or not env['cannMetadata']
            or any(v is None for v in env['packages'].values())
            or any(env['tools'].get(n) is None for n in ('cxx', 'cmake'))
            or any(v is None for v in target['nativeInputs'].values())):
        reasons.append('insufficient evidence for build-state compatibility')
    return reasons


def restore_snapshot(workspace, cache, snapshot, args):
    schema, models = engine_format(workspace)
    manifest = read_json(snapshot / 'manifest.json')
    if manifest.get('schema') != 1 or manifest.get('cacheSchema') != schema:
        raise ValueError('Snapshot or action-cache schema mismatch')
    source = manifest['source']
    target = provenance(workspace, cache, args)
    # This is the runtime boundary required for host-built action artifacts.
    for key in ('architecture', 'system', 'libc', 'osRelease'):
        if source['environment'].get(key) != target['environment'].get(key):
            raise ValueError(f'Action-cache runtime mismatch: {key}')
    report = {'source': source, 'target': target, 'copied': [], 'existing': [], 'rejected': [],
              'bytes': 0, 'buildState': {'restored': [], 'skipped': []}}
    for relative in manifest['entries']:
        try:
            src = contained(snapshot / 'cache', relative)
            entry, size = validate_entry(src, schema, models)
            if PurePosixPath(relative).parts[0] != entry['domain']:
                raise ValueError('Cache domain does not match directory')
            dst = contained(cache, relative)
            with entry_lock(cache, dst, entry['domain'], create=True):
                if dst.exists() or dst.is_symlink():
                    validate_entry(dst, schema, models)
                    report['existing'].append(relative)
                    continue
                copy_entry(src, dst, schema, models)
            report['copied'].append(relative)
            report['bytes'] += size
        except (OSError, ValueError, TypeError, KeyError) as exc:
            report['rejected'].append({'entry': relative, 'reason': str(exc)})
    if args.include_build_state:
        state = manifest.get('buildState', {})
        with checkout_lock(workspace):
            target = provenance(workspace, cache, args)
            report['target'] = target
            reasons = state.get('skipped', []) + build_state_reasons(source, target)
            if reasons:
                report['buildState']['skipped'] = reasons
            elif state.get('directories'):
                state_root = snapshot / 'build-state'
                if tree_manifest(state_root) != state.get('files'):
                    raise ValueError('Build-state snapshot hash mismatch')
                for relative in state['directories']:
                    if relative not in BUILD_DIRS:
                        raise ValueError(f'Unsupported build-state directory: {relative}')
                    dst = workspace / relative
                    if dst.exists() or dst.is_symlink():
                        report['buildState']['skipped'].append(relative + ' already exists; preserved')
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix='.build-transfer-', dir=dst.parent) as temp:
                        stage = Path(temp) / 'build'
                        shutil.copytree(state_root / relative, stage, symlinks=True)
                        os.rename(stage, dst)
                    report['buildState']['restored'].append(relative)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('inspect', 'export', 'restore'):
        item = sub.add_parser(name)
        item.add_argument('--workspace', required=True)
        item.add_argument('--cache-dir', required=True)
        item.add_argument('--snapshot-dir', required=name != 'inspect')
        item.add_argument('--image-id', default='')
        item.add_argument('--soc', default='')
        item.add_argument('--host', default='')
        item.add_argument('--include-build-state', action='store_true')
        item.add_argument('--event-log')
    args = parser.parse_args(argv)
    started = time.monotonic()
    workspace = Path(args.workspace).expanduser().resolve()
    try:
        if not workspace.is_dir():
            raise ValueError(f'Workspace missing: {workspace}')
        cache = Path(args.cache_dir).expanduser().resolve()
        snapshot = Path(args.snapshot_dir).expanduser().resolve() if args.snapshot_dir else None
        if snapshot and (snapshot.is_relative_to(cache) or cache.is_relative_to(snapshot)):
            raise ValueError('Snapshot and cache directories must not overlap')
        if snapshot and args.include_build_state:
            for relative in BUILD_DIRS:
                build = workspace / relative
                if snapshot.is_relative_to(build) or build.is_relative_to(snapshot):
                    raise ValueError('Snapshot and build-state directories must not overlap')
        if args.action == 'export':
            report = export_snapshot(workspace, cache, snapshot, args)
        elif args.action == 'restore':
            report = restore_snapshot(workspace, cache, snapshot, args)
        else:
            schema, models = engine_format(workspace)
            report = inventory(cache, schema, models)
            report['source'] = provenance(workspace, cache, args)
        report.update(action=args.action, elapsedSeconds=round(time.monotonic() - started, 3),
                      workspace=str(workspace), cacheDir=str(cache),
                      snapshotDir=str(snapshot) if snapshot else None, exitCode=0,
                      cacheResults=event_summary(Path(args.event_log) if args.event_log else None))
        if args.action != 'inspect':
            save_report(workspace, args.action, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        if args.action != 'inspect' and workspace.is_dir():
            report = {'action': args.action, 'workspace': str(workspace),
                      'cacheDir': args.cache_dir, 'snapshotDir': args.snapshot_dir,
                      'exitCode': 1, 'error': str(exc),
                      'elapsedSeconds': round(time.monotonic() - started, 3)}
            try:
                save_report(workspace, args.action, report)
                print(json.dumps(report, ensure_ascii=False, indent=2))
            except OSError as log_error:
                print(f'ERROR: Cannot save transfer report: {log_error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
