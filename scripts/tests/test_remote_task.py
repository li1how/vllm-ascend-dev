"""Remote workflow checks without SSH, Docker, installs, or NPU jobs."""
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control = load('remote_task', 'remote-task.py')
worker = load('remote_worker', 'remote-worker.py')


class ControlTests(unittest.TestCase):
    def test_container_identity_rejects_wrong_mount_and_config(self):
        cid = 'a' * 64
        args = Namespace(container_id=cid, workspace_folder='/home/work', config='/home/work/.devcontainer/devcontainer.json')
        item = {'id': cid, 'state': 'running', 'image': 'ascend:dev', 'imageId': 'sha256:' + 'b' * 64,
                'workspace': '/home/work', 'config': args.config,
                'mounts': [{'type': 'bind', 'source': '/home/work', 'destination': '/workspace'}]}
        with patch.object(control, 'ssh_call', return_value=Mock(stdout=json.dumps(item))) as ssh:
            self.assertEqual(control.container_identity(args)[1], '/workspace')
            self.assertEqual(ssh.call_args.args[1][:4], ['docker', 'inspect', '--type', 'container'])
            item['config'] = '/wrong'
            ssh.return_value.stdout = json.dumps(item)
            with self.assertRaisesRegex(ValueError, '配置路径'):
                control.container_identity(args)
            item['config'] = args.config
            item['mounts'] = []
            ssh.return_value.stdout = json.dumps(item)
            with self.assertRaisesRegex(ValueError, '绑定'):
                control.container_identity(args)


    def test_worker_forwarding_uses_devcontainer_exec_and_preserves_arguments(self):
        args = Namespace(container_id='a' * 64, workspace_folder='/host/work space',
                         config='/host/work space/.devcontainer/devcontainer.json')
        with patch.object(control, 'ssh_call', return_value=Mock(returncode=0)) as ssh:
            self.assertEqual(control.worker_call(args, '/container/work space',
                                                 ['run', '--case', 'smoke', '--', '/bin/echo', 'two words']), 0)
            command = ssh.call_args.args[1]
            self.assertEqual(command[:3], ['devcontainer', 'exec', '--container-id'])
            self.assertIn('/host/work space', command)
            self.assertEqual(command[-3:], ['--', '/bin/echo', 'two words'])


class WorkerTests(unittest.TestCase):
    def test_python_only_change_skips_install_and_reports_unknown_stamp(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, 'STATE', Path(temp)):
            info = {name: {'sha': 'new', 'nativeDigest': 'same', 'origin': {'ok': True}}
                    for name in ('vllm', 'vllm-ascend')}
            env = {'imageId': 'image'}
            stamp = {name: {'nativeDigest': 'same', 'environment': env}
                     for name in info}
            (Path(temp) / 'install-stamp.json').write_text(json.dumps(stamp))
            args = Namespace(image_id='image', jobs=None, tmp_dir=None)
            with patch.object(worker, 'repos', return_value=info), patch.object(worker, 'environment', return_value=env), \
                 patch.object(worker, 'cache_state', return_value={'exists': False}), \
                 patch.object(worker.subprocess, 'run') as run, patch.object(worker, 'emit') as emit:
                self.assertEqual(worker.prepare(args), 0)
                self.assertEqual(emit.call_args.args[0]['decisions'], {'vllm': 'skip', 'vllm-ascend': 'skip'})
                run.assert_not_called()
                (Path(temp) / 'install-stamp.json').unlink()
                self.assertEqual(worker.prepare(args), 0)
                self.assertEqual(emit.call_args.args[0]['decisions'],
                                 {'vllm': 'unverified_skip', 'vllm-ascend': 'unverified_skip'})

    def test_cache_mismatch_blocks_install_without_deleting_cache(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, 'STATE', Path(temp)):
            info = {name: {'sha': 'new', 'nativeDigest': 'new', 'origin': {'ok': False}}
                    for name in ('vllm', 'vllm-ascend')}
            args = Namespace(image_id='image', jobs=32, tmp_dir=None)
            with patch.object(worker, 'repos', return_value=info), patch.object(worker, 'environment', return_value={}), \
                 patch.object(worker, 'cache_state', return_value={'cannMismatches': ['ACL_INC_DIR=old']}), \
                 patch.object(worker.subprocess, 'run') as run, patch.object(worker, 'emit') as emit:
                self.assertEqual(worker.prepare(args), 3)
                self.assertIn('失配', emit.call_args.args[0]['error'])
                run.assert_not_called()

    def test_manifest_redacts_command_and_starts_from_case_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, 'STATE', Path(temp)):
            args = Namespace(host='node', image_id='image', container_id='a' * 64,
                             config='/config', discovery_source='mcp', case='smoke', command=['echo', 'safe'])
            with patch.object(worker, 'repos', return_value={}), patch.object(worker.subprocess, 'Popen', return_value=Mock(pid=123)) as popen, \
                 patch.object(worker, 'emit'):
                self.assertEqual(worker.run(args), 0)
                manifest = json.loads(next(Path(temp).glob('smoke-*/manifest.json')).read_text())
                self.assertEqual(manifest['command'], ['echo', 'safe'])
                self.assertEqual(manifest['discoverySource'], 'mcp')
                self.assertEqual(worker.redact(['echo', '--token', 'abc']), ['echo', '--token', '[REDACTED]'])
                self.assertEqual(popen.call_args.kwargs['cwd'], next(Path(temp).glob('smoke-*')))

    def test_run_rejects_secret_arguments(self):
        args = Namespace(host='node', image_id='image', container_id='a' * 64,
                         config='/config', discovery_source='ssh', case='smoke',
                         command=['echo', '--token', 'secret-value'])
        with self.assertRaisesRegex(ValueError, '凭据'):
            worker.run(args)

    def test_run_resolves_workspace_script_then_preserves_output_cwd(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, 'STATE', Path(temp)):
            script = Path(temp) / 'my-script.sh'
            script.write_text('#!/bin/sh\n')
            with patch.object(worker, 'ROOT', Path(temp)), patch.object(worker, 'repos', return_value={}), \
                 patch.object(worker.subprocess, 'Popen', return_value=Mock(pid=10)) as popen, \
                 patch.object(worker, 'emit'):
                args = Namespace(host='node', image_id='image', container_id='a' * 64,
                                 config='/config', discovery_source='ssh', case='smoke', command=['./my-script.sh'])
                worker.run(args)
                self.assertEqual(popen.call_args.args[0][-1], str(script))
                manifest = json.loads(next(Path(temp).glob('smoke-*/manifest.json')).read_text())
                self.assertEqual(manifest['discoverySource'], 'ssh')

    def test_weight_inventory_marks_missing_path_without_search(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, 'ROOT', Path(temp)):
            path = Path(temp) / 'model'
            path.mkdir()
            args = Namespace(host='node', operation='record', path=str(path), model='m', purpose='smoke', mount='/models')
            with patch.object(worker, 'emit'):
                worker.weight(args)
                path.rmdir()
                args.operation = 'verify'
                with patch.object(worker, 'emit') as emit:
                    worker.weight(args)
                    self.assertFalse(emit.call_args.args[0]['weights'][0]['exists'])


if __name__ == '__main__':
    unittest.main()
