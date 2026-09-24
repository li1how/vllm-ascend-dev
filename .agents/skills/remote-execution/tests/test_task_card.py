"""Task card lifecycle without Git writes, SSH, Dev Containers, or NPU workloads."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/task-card.py'
spec = importlib.util.spec_from_file_location('task_card', SCRIPT)
task_card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_card)


class TaskCardTests(unittest.TestCase):
    def test_source_digest_tracks_dirty_and_untracked_code(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
            source = repo / 'source.py'
            source.write_text('value = 1\n')
            subprocess.run(['git', '-C', str(repo), 'add', 'source.py'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test',
                            '-c', 'user.email=test@example.com', 'commit', '-qm', 'initial'], check=True)
            baseline = task_card.source_digest(repo)
            source.write_text('value = 2\n')
            changed = task_card.source_digest(repo)
            self.assertNotEqual(changed, baseline)
            (repo / 'new.py').write_text('new = True\n')
            self.assertNotEqual(task_card.source_digest(repo), changed)

    def test_scoped_workspace_digest_ignores_unrelated_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
            watched = repo / '.agents/skills/remote-execution/scripts/remote-task.py'
            watched.parent.mkdir(parents=True)
            watched.write_text('value = 1\n')
            unrelated = repo / 'README.md'
            unrelated.write_text('first\n')
            subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test',
                            '-c', 'user.email=test@example.com', 'commit', '-qm', 'initial'], check=True)
            scoped = task_card.source_digest(repo, ['.agents/skills/remote-execution/scripts/remote-task.py'])
            unrelated.write_text('second\n')
            self.assertEqual(task_card.source_digest(repo, ['.agents/skills/remote-execution/scripts/remote-task.py']), scoped)
            watched.write_text('value = 2\n')
            self.assertNotEqual(task_card.source_digest(repo, ['.agents/skills/remote-execution/scripts/remote-task.py']), scoped)

    def test_card_script_must_be_registered_before_run_and_can_be_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards = root / 'task-cards'
            create = Namespace(title='验证 Embedding 通信', goal='只验证 PCP',
                               repository='vllm-ascend', branch='perf/pcp',
                               pr='17561', baseline_sha='', candidate_sha='',
                               vllm_sha='', host='node', container_id='cid',
                               image_id='image', allow=['test'], change=['运行 PCP 冒烟'],
                               stage=['smoke=' + 'a' * 40], template=None,
                               source_script=None)
            with patch.object(task_card, 'ROOT', root), patch.object(task_card, 'CARDS', cards), \
                 patch.object(task_card, 'repository', return_value=root), \
                 patch.object(task_card, 'git', return_value='a' * 40), \
                 patch.object(task_card, 'source_digest', return_value='source'), \
                 patch('builtins.print'):
                self.assertEqual(task_card.create(create), 0)
                path = next(cards.glob('*/task.json')).parent
                card_id = path.name
                script = path / 'run.sh'
                self.assertIn('SCRIPT_DIR=', script.read_text())
                self.assertIn(str(root), script.read_text())
                args = Namespace(id=card_id, stage='smoke', operation='test',
                                 host='node', container_id='cid', image_id='image')
                with self.assertRaisesRegex(ValueError, '未 seal'):
                    task_card.run(args)
                script.write_text(script.read_text().replace('echo "[ERROR] 请先在任务卡 run.sh 中写入本阶段的实际命令" >&2\nexit 2', 'echo first'))
                self.assertEqual(task_card.seal(Namespace(id=card_id)), 0)
                with self.assertRaisesRegex(ValueError, '先 update'):
                    task_card.seal(Namespace(id=card_id))
                script.write_text(script.read_text().replace('echo first', 'echo unregistered'))
                with self.assertRaisesRegex(ValueError, '摘要不一致'):
                    task_card.run(args)
                self.assertEqual(task_card.update(Namespace(
                    id=card_id, goal=None, change=['运行更新后的 PCP 冒烟'],
                    stage=None, allow=None)), 0)
                script.write_text(script.read_text().replace('echo unregistered', 'echo registered'))
                self.assertEqual(task_card.seal(Namespace(id=card_id)), 0)
                self.assertEqual(task_card.run(args), 0)
                executed = next(path.glob('runs/*/executed.sh'))
                self.assertIn('echo registered', executed.read_text())
                self.assertIn('registered', next(path.glob('runs/*/run.log')).read_text())
                record = json.loads(next(path.glob('runs/*/run.json')).read_text())
                self.assertEqual(record['scriptSha256'], task_card.digest(executed))
                self.assertEqual(record['planVersion'], 2)
                self.assertEqual(task_card.archive(Namespace(
                    id=card_id, outcome='complete', summary='冒烟通过')), 0)
                self.assertFalse(path.exists())
                self.assertTrue((cards / 'archive' / card_id / 'TASK.md').exists())

    def test_service_card_uses_existing_server_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = root / 'templates/server.sh.template'
            template.parent.mkdir()
            template.write_text('#!/bin/bash\nSCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"\n'
                                'LOG_DIR="$SCRIPT_DIR/log"\nvllm_cmd=(vllm serve model)\n')
            private = root / 'scripts/server.sh'
            private.parent.mkdir()
            private.write_text('#!/bin/bash\necho wrong-source\n')
            cards = root / 'task-cards'
            create = Namespace(title='serve', goal='', repository='workspace', branch='', pr='',
                               baseline_sha='', candidate_sha='', vllm_sha='', host='',
                               container_id='', image_id='', allow=['serve'], change=['启动服务'],
                               stage=['serve=' + 'a' * 40], template='server')
            with patch.object(task_card, 'ROOT', root), patch.object(task_card, 'CARDS', cards), \
                 patch.object(task_card, 'repository', return_value=root), \
                 patch.object(task_card, 'git', return_value='a' * 40), patch('builtins.print'):
                task_card.create(create)
            path = next(cards.glob('*/task.json')).parent
            script = (path / 'run.sh').read_text()
            self.assertIn('vllm_cmd=(vllm serve model)', script)
            self.assertNotIn('wrong-source', script)
            self.assertIn('VLLM_TASK_RUN_DIR', script)
            card = json.loads((path / 'task.json').read_text())
            self.assertEqual(card['template'], 'templates/server.sh.template')
            self.assertEqual(card['templateSha256'], task_card.digest(template))

    def test_unlisted_stage_or_target_cannot_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cards = root / 'task-cards'
            create = Namespace(title='scope', goal='', repository='vllm-ascend',
                               branch='', pr='', baseline_sha='', candidate_sha='',
                               vllm_sha='', host='node', container_id='cid',
                               image_id='image', allow=['test'], change=['只运行 smoke'],
                               stage=['smoke=' + 'a' * 40], template=None,
                               source_script=None)
            with patch.object(task_card, 'ROOT', root), patch.object(task_card, 'CARDS', cards), \
                 patch.object(task_card, 'repository', return_value=root), \
                 patch.object(task_card, 'git', return_value='a' * 40), \
                 patch.object(task_card, 'source_digest', return_value='source'), patch('builtins.print'):
                task_card.create(create)
                card_id = next(cards.glob('*/task.json')).parent.name
                task_card.seal(Namespace(id=card_id))
                bad_stage = Namespace(id=card_id, stage='full', operation='test',
                                      host='node', container_id='cid', image_id='image')
                with self.assertRaisesRegex(ValueError, '未写入任务卡'):
                    task_card.run(bad_stage)
                wrong_node = Namespace(id=card_id, stage='smoke', operation='test',
                                       host='other', container_id='cid', image_id='image')
                with self.assertRaisesRegex(ValueError, 'host'):
                    task_card.run(wrong_node)


if __name__ == '__main__':
    unittest.main()
