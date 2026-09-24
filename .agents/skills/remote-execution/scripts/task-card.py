#!/usr/bin/env python3
"""Keep an inspectable task card and execute a frozen copy of its run script."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CARDS = ROOT / 'task-cards'
TEMPLATES = {
    'server': 'server.sh',
    'prefill': 'p_server.sh',
    'decode': 'd_server.sh',
    'proxy': 'proxy_server.sh',
}
OPERATIONS = ('serve', 'test')
CARD_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z')
SHA = re.compile(r'[0-9a-f]{7,40}\Z')
SCRIPT_ROOT = 'SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"'


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest(repo: Path, watched_paths: list[str] | None = None) -> str:
    """Hash selected workspace files, or all source changes in a source repository."""
    result = hashlib.sha256()
    result.update(git(repo, 'rev-parse', 'HEAD').encode())
    if watched_paths is not None:
        for name in sorted(watched_paths):
            path = repo / name
            result.update(name.encode() + b'\0')
            if path.is_symlink():
                result.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        result.update(block)
            else:
                result.update(b'[MISSING]')
        return result.hexdigest()
    diff = subprocess.run(['git', '-C', str(repo), 'diff', '--binary', 'HEAD'],
                          check=True, capture_output=True).stdout
    result.update(diff)
    untracked = subprocess.run(['git', '-C', str(repo), 'ls-files', '--others',
                                '--exclude-standard', '-z'], check=True, capture_output=True).stdout
    for name in sorted(filter(None, untracked.split(b'\0'))):
        path = repo / os.fsdecode(name)
        result.update(name + b'\0')
        if path.is_symlink():
            result.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    result.update(block)
    return result.hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


def repository(name: str) -> Path:
    repo = ROOT if name == 'workspace' else ROOT / name
    if not repo.is_dir() or Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo.resolve():
        raise ValueError(f'目标不是独立 Git 仓库: {repo}')
    return repo


def locate(card_id: str) -> Path:
    if not CARD_ID.fullmatch(card_id):
        raise ValueError('无效任务卡 ID')
    current = CARDS / card_id
    archive = CARDS / 'archive' / card_id
    found = [path for path in (current, archive) if path.is_dir()]
    if len(found) != 1:
        raise ValueError('未找到唯一任务卡')
    return found[0]


def render_card(card: dict) -> str:
    lines = [f"# {card['title']}", '', f"- ID：`{card['id']}`",
             f"- 状态：`{card['status']}`", f"- 目标仓库：`{card['repository']}`",
             f"- 目标分支：`{card['branch'] or '未指定'}`",
             f"- PR：`{card['pr'] or '未指定'}`",
             f"- 基线 SHA：`{card['baselineSha'] or '未指定'}`",
             f"- 候选 SHA：`{card['candidateSha'] or '未指定'}`",
             f"- vLLM SHA：`{card['vllmSha'] or '未指定'}`",
             f"- 节点：`{card['host'] or '就地'}`",
             f"- 容器 ID：`{card['containerId'] or '就地'}`",
             f"- 镜像 ID：`{card['imageId'] or '未记录'}`",
             f"- 允许操作：{', '.join(card['allowedOperations'])}",
             f"- 计划版本：`{card['planVersion']}`",
             f"- 已登记脚本 SHA256：`{card['sealedScriptSha256'] or '未登记'}`",
             f"- 已登记源码摘要：`{card.get('sealedSourceDigest') or '未登记'}`",
             f"- 模板：`{card['template'] or '无（普通任务卡）'}`",
             f"- 脚本来源：`{card['scriptSource']}`",
             f"- 监视文件：{', '.join(card.get('watchedPaths', [])) or '仅固定工作区 HEAD'}",
             f"- 最近阶段：`{card.get('currentStage') or '未运行'}`",
             f"- 最近操作：`{card.get('currentOperation') or '未运行'}`",
             f"- 最近运行目录：`{card.get('currentRun') or '未运行'}`", '',
             '## 本轮服务或测试计划', '',
             *[f"- {item}" for item in card['plannedChanges']], '',
             '## 执行阶段与提交', '',
             *[f"- `{name}`：`{sha}`" for name, sha in card['stages'].items()], '',
             '## 实际运行脚本', '',
             '先修改任务计划和 `run.sh`，再执行 `seal` 登记脚本摘要。',
             '每次运行前复制到 `runs/<阶段>/executed.sh`，只执行该快照。',
             '运行目录保存脚本 SHA、日志、退出码。脚本变更后须更新任务卡并重新 `seal`。',
             '密钥仅通过环境变量传入。',
             '', '## 本轮范围', '', card['goal'] or card['title'], '']
    return '\n'.join(lines)


def refresh_card(path: Path, card: dict) -> None:
    write_json(path / 'task.json', card)
    (path / 'TASK.md').write_text(render_card(card))


def parse_watches(values: list[str]) -> list[str]:
    paths = []
    for value in values:
        candidate = Path(value)
        if candidate.is_absolute() or '..' in candidate.parts or not (ROOT / candidate).is_file():
            raise ValueError(f'监视路径必须是工作区内的现有文件: {value}')
        paths.append(candidate.as_posix())
    return sorted(set(paths))


def parse_stages(values: list[str]) -> dict[str, str]:
    stages = {}
    for value in values:
        name, separator, sha = value.partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or not SHA.fullmatch(sha):
            raise ValueError('阶段格式须为 名称=7到40位小写提交SHA')
        if name in stages:
            raise ValueError(f'重复阶段: {name}')
        stages[name] = sha
    return stages


def create(args: argparse.Namespace) -> int:
    repo = repository(args.repository)
    for value in (args.baseline_sha, args.candidate_sha, args.vllm_sha):
        if value and not SHA.fullmatch(value):
            raise ValueError('提交 SHA 必须是 7 到 40 位小写十六进制')
    stages = parse_stages(args.stage)
    template = None
    source = None
    if args.template:
        source_name = TEMPLATES[args.template]
        template = ROOT / 'templates' / (source_name + '.template')
        source = template
        content = source.read_text()
        if content.count(SCRIPT_ROOT) != 1:
            raise ValueError('脚本未使用工作区服务模板的 SCRIPT_DIR 定位方式')
        content = content.replace(SCRIPT_ROOT, f'SCRIPT_DIR={json.dumps(str(ROOT))}')
        # Keep service logs with the frozen executed script.
        content = content.replace('LOG_DIR="$SCRIPT_DIR/log"',
                                  'LOG_DIR="${VLLM_TASK_RUN_DIR:-$SCRIPT_DIR/log}"')
    else:
        content = ('#!/bin/bash\nset -euo pipefail\n\n'
                   f'SCRIPT_DIR={json.dumps(str(ROOT))}\n'
                   'cd "$SCRIPT_DIR"\n\n'
                   'echo "[ERROR] 请先在任务卡 run.sh 中写入本阶段的实际命令" >&2\n'
                   'exit 2\n')
    card_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    path = CARDS / card_id
    path.mkdir(parents=True)
    script = path / 'run.sh'
    script.write_text(content)
    script.chmod(0o700)
    card = {'id': card_id, 'title': args.title, 'goal': args.goal,
            'status': 'planned', 'createdAt': now(), 'updatedAt': now(),
            'repository': args.repository, 'repositoryPath': str(repo),
            'branch': args.branch, 'pr': args.pr,
            'baselineSha': args.baseline_sha, 'candidateSha': args.candidate_sha,
            'vllmSha': args.vllm_sha, 'host': args.host,
            'containerId': args.container_id, 'imageId': args.image_id,
            'watchedPaths': parse_watches(getattr(args, 'watch', [])),
            'allowedOperations': sorted(set(args.allow)),
            'plannedChanges': args.change, 'stages': stages,
            'planVersion': 1, 'sealedScriptSha256': None,
            'sealedSourceDigest': None, 'sealedRepositorySha': None,
            'sealedVllmDigest': None,
            'template': str(template.relative_to(ROOT)) if template else '',
            'templateSha256': digest(template) if template else '',
            'scriptSource': str(source.relative_to(ROOT)) if source else 'generated:task-card',
            'scriptSourceSha256': digest(source) if source else '',
            'createdRepositorySha': git(repo, 'rev-parse', 'HEAD'), 'runs': []}
    refresh_card(path, card)
    print(json.dumps({'id': card_id, 'directory': str(path), 'card': str(path / 'TASK.md'),
                      'script': str(script), 'scriptSource': card['scriptSource']}, ensure_ascii=False))
    return 0


def update(args: argparse.Namespace) -> int:
    path = locate(args.id)
    if path.parent != CARDS:
        raise ValueError('已归档任务不能修改')
    card = read_json(path / 'task.json')
    if card['status'] == 'running' and process_active(card):
        raise ValueError('运行中的任务不能修改计划')
    watches = parse_watches(args.watch) if getattr(args, 'watch', None) else None
    stages = parse_stages(args.stage) if args.stage else None
    revision = path / 'revisions' / f"plan-v{card['planVersion']}"
    revision.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path / 'task.json', revision / 'task.json')
    shutil.copy2(path / 'run.sh', revision / 'run.sh')
    if args.goal is not None:
        card['goal'] = args.goal
    if args.change:
        card['plannedChanges'].extend(args.change)
    if args.stage:
        card['stages'] = stages
    if args.allow:
        card['allowedOperations'] = sorted(set(args.allow))
    if getattr(args, 'clear_watch', False):
        card['watchedPaths'] = []
    elif getattr(args, 'watch', None):
        card['watchedPaths'] = watches
    card['planVersion'] += 1
    card['sealedScriptSha256'] = None
    card['sealedSourceDigest'] = None
    card['sealedRepositorySha'] = None
    card['sealedVllmDigest'] = None
    card['status'] = 'planned'
    card['updatedAt'] = now()
    refresh_card(path, card)
    print(json.dumps({'id': args.id, 'status': 'planned', 'planVersion': card['planVersion'],
                      'directory': str(path)}, ensure_ascii=False))
    return 0


def seal(args: argparse.Namespace) -> int:
    path = locate(args.id)
    if path.parent != CARDS:
        raise ValueError('已归档任务不能登记脚本')
    card = read_json(path / 'task.json')
    if card['status'] == 'running' and process_active(card):
        raise ValueError('运行中的任务不能登记新脚本')
    if not card['plannedChanges'] or not card['stages']:
        raise ValueError('任务卡必须先列出计划修改和执行阶段')
    if card['sealedScriptSha256']:
        raise ValueError('任务卡已 seal；脚本或源码变化必须先 update')
    script = path / 'run.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    card['sealedScriptSha256'] = digest(script)
    repo = repository(card['repository'])
    card['sealedRepositorySha'] = git(repo, 'rev-parse', 'HEAD')
    scope = card.get('watchedPaths', []) if card['repository'] == 'workspace' else None
    card['sealedSourceDigest'] = source_digest(repo, scope)
    card['sealedVllmDigest'] = source_digest(repository('vllm')) if card['vllmSha'] else None
    card['status'] = 'ready'
    card['updatedAt'] = now()
    refresh_card(path, card)
    print(json.dumps({'id': args.id, 'status': 'ready',
                      'scriptSha256': card['sealedScriptSha256'], 'directory': str(path)}, ensure_ascii=False))
    return 0


def pid_start_time(pid: int) -> str | None:
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()
        return fields[19]
    except (IndexError, OSError):
        return None


def process_active(card: dict) -> bool:
    pid = card.get('pid')
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    observed = pid_start_time(pid)
    return bool(observed and observed == card.get('pidStartTime'))


def run(args: argparse.Namespace) -> int:
    path = locate(args.id)
    if path.parent != CARDS:
        raise ValueError('已归档任务不能运行')
    card = read_json(path / 'task.json')
    if not card['sealedScriptSha256']:
        raise ValueError('任务卡未 seal；先更新计划并登记实际脚本')
    if digest(path / 'run.sh') != card['sealedScriptSha256']:
        raise ValueError('run.sh 与任务卡登记的摘要不一致；先更新任务卡并重新 seal')
    if args.operation not in card['allowedOperations']:
        raise ValueError(f'任务卡未授权 {args.operation} 操作')
    for field, value in (('host', args.host), ('containerId', args.container_id),
                         ('imageId', args.image_id)):
        if value and card[field] != value:
            raise ValueError(f'任务卡 {field} 与当前执行目标不匹配')
    if card['status'] == 'running' and process_active(card):
        raise ValueError('任务卡已有运行中的脚本')
    repo = repository(card['repository'])
    expected = card['stages'].get(args.stage)
    if not expected:
        raise ValueError('执行阶段未写入任务卡；先 update 再 seal')
    actual = git(repo, 'rev-parse', 'HEAD')
    if not actual.startswith(expected):
        raise ValueError(f'目标仓库 SHA 不匹配：预期 {expected}，当前 {actual}')
    if actual != card['sealedRepositorySha']:
        raise ValueError('目标仓库提交与任务卡 seal 时不同；先更新任务卡并重新 seal')
    scope = card.get('watchedPaths', []) if card['repository'] == 'workspace' else None
    if (scope is None or scope) and source_digest(repo, scope) != card['sealedSourceDigest']:
        raise ValueError('目标仓库源码与任务卡 seal 时不同；先更新任务卡并重新 seal')
    if card['vllmSha']:
        vllm_actual = git(repository('vllm'), 'rev-parse', 'HEAD')
        if not vllm_actual.startswith(card['vllmSha']):
            raise ValueError(f'vLLM SHA 不匹配：预期 {card["vllmSha"]}，当前 {vllm_actual}')
        if source_digest(repository('vllm')) != card['sealedVllmDigest']:
            raise ValueError('vLLM 源码与任务卡 seal 时不同；先更新任务卡并重新 seal')
    stage = re.sub(r'[^A-Za-z0-9_.-]', '_', args.stage)
    if not stage or stage in ('.', '..'):
        raise ValueError('无效阶段名')
    run_id = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    directory = path / 'runs' / (stage + '-' + run_id)
    directory.mkdir(parents=True)
    snapshot = directory / 'executed.sh'
    shutil.copy2(path / 'run.sh', snapshot)
    snapshot.chmod(0o700)
    if digest(snapshot) != card['sealedScriptSha256']:
        raise ValueError('执行快照与任务卡登记的脚本摘要不一致')
    subprocess.run(['bash', '-n', str(snapshot)], check=True)
    record = {'stage': stage, 'operation': args.operation, 'runId': run_id, 'startedAt': now(),
              'repositorySha': actual, 'planVersion': card['planVersion'],
              'script': str(snapshot.relative_to(path)),
              'scriptSha256': digest(snapshot), 'log': str((directory / 'run.log').relative_to(path)),
              'status': 'running'}
    write_json(directory / 'run.json', record)
    card['runs'].append(str(directory.relative_to(path)))
    card['currentStage'] = stage
    card['currentOperation'] = args.operation
    card['currentRun'] = str(directory.relative_to(path))
    card['status'] = 'running'
    card['pid'] = os.getpid()
    card['pidStartTime'] = pid_start_time(os.getpid())
    card['updatedAt'] = now()
    refresh_card(path, card)
    print(json.dumps({'id': card['id'], 'stage': stage, 'script': str(snapshot),
                      'scriptSha256': record['scriptSha256'], 'log': str(directory / 'run.log')}, ensure_ascii=False), flush=True)
    code = 1
    try:
        env = os.environ.copy()
        env['VLLM_TASK_RUN_DIR'] = str(directory)
        with (directory / 'run.log').open('ab', buffering=0) as log:
            code = subprocess.run(['bash', str(snapshot)], cwd=directory, env=env,
                                  stdout=log, stderr=subprocess.STDOUT).returncode
    except OSError as exc:
        (directory / 'run.log').open('ab').write((str(exc) + '\n').encode())
        code = 127
    finally:
        record.update({'status': 'complete' if code == 0 else 'failed',
                       'exitCode': code, 'finishedAt': now()})
        try:
            record['postRepositorySha'] = git(repo, 'rev-parse', 'HEAD')
            record['postSourceDigest'] = source_digest(repo, scope)
        except (OSError, subprocess.CalledProcessError) as exc:
            record['postSourceError'] = type(exc).__name__
        write_json(directory / 'run.json', record)
        card['status'] = record['status']
        card.pop('pid', None)
        card.pop('pidStartTime', None)
        card['updatedAt'] = now()
        refresh_card(path, card)
    return code


def show(args: argparse.Namespace) -> int:
    path = locate(args.id)
    card = read_json(path / 'task.json')
    if card['status'] == 'running' and not process_active(card):
        card['status'] = 'unknown_after_disconnect'
    print(json.dumps({'directory': str(path), **card}, ensure_ascii=False, indent=2))
    return 0


def list_cards(args: argparse.Namespace) -> int:
    folders = [CARDS] if not args.all else [CARDS, CARDS / 'archive']
    rows = []
    for folder in folders:
        for path in sorted(folder.glob('*/task.json')):
            card = read_json(path)
            status = ('unknown_after_disconnect' if card['status'] == 'running' and
                      not process_active(card) else card['status'])
            rows.append({'id': card['id'], 'title': card['title'], 'status': status,
                         'stage': card.get('currentStage'), 'operation': card.get('currentOperation'),
                         'host': card['host'], 'repository': card['repository'],
                         'directory': str(path.parent)})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def archive(args: argparse.Namespace) -> int:
    path = locate(args.id)
    if path.parent != CARDS:
        raise ValueError('任务卡已经归档')
    card = read_json(path / 'task.json')
    if card['status'] == 'running' and process_active(card):
        raise ValueError('运行中的任务不能归档')
    card['status'] = args.outcome
    card['summary'] = args.summary
    card['archivedAt'] = now()
    card['updatedAt'] = now()
    card.pop('pid', None)
    card.pop('pidStartTime', None)
    refresh_card(path, card)
    destination = CARDS / 'archive' / args.id
    destination.parent.mkdir(parents=True, exist_ok=True)
    path.rename(destination)
    print(json.dumps({'id': args.id, 'directory': str(destination),
                      'status': args.outcome}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create_parser = commands.add_parser('create', help='建立 vLLM 服务或模型测试任务卡')
    create_parser.add_argument('--title', required=True)
    create_parser.add_argument('--goal', default='')
    create_parser.add_argument('--repository', choices=('workspace', 'vllm', 'vllm-ascend', 'benchmark'), required=True)
    create_parser.add_argument('--branch', default='')
    create_parser.add_argument('--pr', default='')
    create_parser.add_argument('--baseline-sha', default='')
    create_parser.add_argument('--candidate-sha', default='')
    create_parser.add_argument('--vllm-sha', default='')
    create_parser.add_argument('--host', default='')
    create_parser.add_argument('--container-id', default='')
    create_parser.add_argument('--image-id', default='')
    create_parser.add_argument('--watch', action='append', default=[], help='工作区卡片需要校验的脚本或配置文件')
    create_parser.add_argument('--allow', action='append', choices=OPERATIONS, required=True)
    create_parser.add_argument('--change', action='append', required=True, help='本轮服务或测试计划；可重复')
    create_parser.add_argument('--stage', action='append', required=True, help='名称=预期目标仓库SHA；可重复')
    create_parser.add_argument('--template', choices=TEMPLATES, help='启动 vLLM 服务时选择现有服务脚本模板；测试命令省略')
    update_parser = commands.add_parser('update', help='更新服务或测试计划，然后修改卡内运行脚本')
    update_parser.add_argument('id')
    update_parser.add_argument('--goal')
    update_parser.add_argument('--change', action='append')
    update_parser.add_argument('--stage', action='append')
    update_parser.add_argument('--allow', action='append', choices=OPERATIONS)
    update_parser.add_argument('--watch', action='append', help='重新指定需要校验的工作区文件')
    update_parser.add_argument('--clear-watch', action='store_true', help='只固定工作区 HEAD 和卡内脚本')
    seal_parser = commands.add_parser('seal', help='登记实际运行脚本的 SHA256')
    seal_parser.add_argument('id')
    run_parser = commands.add_parser('run', help='冻结并执行 run.sh')
    run_parser.add_argument('id')
    run_parser.add_argument('--stage', required=True)
    run_parser.add_argument('--operation', choices=OPERATIONS, default='test')
    run_parser.add_argument('--host', default='')
    run_parser.add_argument('--container-id', default='')
    run_parser.add_argument('--image-id', default='')
    show_parser = commands.add_parser('show', help='显示任务卡和状态')
    show_parser.add_argument('id')
    list_parser = commands.add_parser('list', help='列出当前任务卡')
    list_parser.add_argument('--all', action='store_true')
    archive_parser = commands.add_parser('archive', help='归档已结束任务')
    archive_parser.add_argument('id')
    archive_parser.add_argument('--outcome', choices=('complete', 'failed', 'cancelled'), required=True)
    archive_parser.add_argument('--summary', default='')
    args = parser.parse_args()
    return {'create': create, 'update': update, 'seal': seal, 'run': run,
            'show': show, 'list': list_cards, 'archive': archive}[args.action](args)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
