"""Transport tests with fixture cache entries, no compiler, SSH or NPU."""
import copy
import fcntl
import hashlib
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / 'scripts/cache-transfer.py'
spec = importlib.util.spec_from_file_location('cache_transfer', TOOL)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'source'
        engine = self.workspace / 'vllm-ascend/csrc/scripts/build_cache.py'
        engine.parent.mkdir(parents=True)
        engine.write_text("SCHEMA_VERSION = 4\nARTIFACT_MODEL_BY_DOMAIN = {'third_party': 1, 'custom_operator': 2}\n")
        self.source = self.root / 'source-cache'
        self.target = self.root / 'target-cache'
        self.snapshot = self.root / 'snapshot'
        self.args = Namespace(host='node', image_id='sha256:image', soc='a3', include_build_state=False)
        self.env = {'architecture': 'aarch64', 'system': 'Linux', 'libc': ['glibc', '2.38'],
                    'osRelease': 'os', 'imageId': 'sha256:image', 'soc': 'a3',
                    'cannMetadata': {'version.info': 'cann'}, 'packages': {'torch': '2.10', 'torch-npu': '2.10'},
                    'tools': {'cxx': 'c++ version', 'cmake': 'cmake version'}}
        self.identity = {'host': 'node', 'workspace': '/workspaces/vllm-ascend-dev',
                         'cacheDir': '/workspaces/vllm-ascend-dev/.cache/native',
                         'nativeInputs': {'vllm': 'upstream', 'vllm-ascend': 'native'},
                         'environment': self.env, 'repos': {'vllm-ascend': {'sha': 'old', 'dirty': False}}}
        self.provenance = patch.object(cache, 'provenance', side_effect=lambda *a: copy.deepcopy(self.identity))
        self.provenance.start()
        self.addCleanup(self.provenance.stop)

    def entry(self, key='a' * 64):
        entry = self.source / 'third_party/protobuf' / key
        artifacts = entry / 'artifacts'
        artifacts.mkdir(parents=True)
        (artifacts / 'lib.a').write_bytes(b'compiled bytes')
        manifest = {'schema': 4, 'domain': 'third_party', 'artifact_model': 1, 'final_key': key,
                    'artifacts': [{'path': 'lib.a', 'sha256': cache.sha256(artifacts / 'lib.a')}]}
        cache.write_json(entry / 'manifest.json', manifest)
        return entry

    def export(self):
        return cache.export_snapshot(self.workspace, self.source, self.snapshot, self.args)

    def restore(self):
        return cache.restore_snapshot(self.workspace, self.target, self.snapshot, self.args)

    def test_roundtrip_ignores_locks_temp_and_unlisted_files_and_preserves_existing(self):
        entry = self.entry()
        (entry / 'secret-not-an-artifact').write_text('not copied')
        (self.source / '.temporary').mkdir()
        lock = self.source / 'third_party/.locks/old.lock'
        lock.parent.mkdir(parents=True)
        lock.touch()
        report = self.export()
        self.assertEqual(len(report['entries']), 1)
        self.assertFalse((self.snapshot / 'cache/third_party/.locks').exists())
        self.assertFalse((self.snapshot / 'cache' / entry.relative_to(self.source) / 'secret-not-an-artifact').exists())
        first = self.restore()
        dst = self.target / entry.relative_to(self.source) / 'artifacts/lib.a'
        before = dst.stat().st_mtime_ns
        second = self.restore()
        self.assertEqual(len(first['copied']), 1)
        self.assertEqual(len(second['existing']), 1)
        self.assertEqual(dst.stat().st_mtime_ns, before)

    def test_corrupt_source_and_snapshot_are_rejected(self):
        entry = self.entry()
        (entry / 'artifacts/lib.a').write_text('corrupt')
        self.assertEqual(len(self.export()['rejected']), 1)
        self.assertEqual(self.restore()['copied'], [])

    def test_corrupt_transferred_entry_does_not_enter_target(self):
        entry = self.entry()
        self.export()
        staged = self.snapshot / 'cache' / entry.relative_to(self.source)
        (staged / 'artifacts/lib.a').write_text('truncated transfer')
        result = self.restore()
        self.assertEqual(len(result['rejected']), 1)
        self.assertFalse((self.target / entry.relative_to(self.source)).exists())

    def test_invalid_json_object_and_artifact_escape_are_rejected(self):
        entry = self.entry()
        (entry / 'manifest.json').write_text('[]')
        result = self.export()
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['entries'], [])
        with self.assertRaises(ValueError):
            cache.contained(self.root, '../../outside')

    def test_valid_relative_symlink_is_preserved_and_external_link_is_rejected(self):
        entry = self.entry()
        link = entry / 'artifacts/libalias.a'
        link.symlink_to('lib.a')
        manifest = cache.read_json(entry / 'manifest.json')
        manifest['artifacts'].append({'path': 'libalias.a', 'kind': 'symlink', 'target': 'lib.a'})
        cache.write_json(entry / 'manifest.json', manifest)
        self.export()
        self.restore()
        self.assertEqual((self.target / entry.relative_to(self.source) / 'artifacts/libalias.a').readlink(), Path('lib.a'))
        link.unlink()
        link.symlink_to('/etc/passwd')
        manifest['artifacts'][-1]['target'] = '/etc/passwd'
        cache.write_json(entry / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            cache.validate_entry(entry, 4, {'third_party': 1})

    def test_busy_entry_is_skipped_without_waiting_or_copying_lock(self):
        entry = self.entry()
        lock = self.source / 'third_party/.locks' / (hashlib.sha256(str(entry).encode()).hexdigest() + '.lock')
        lock.parent.mkdir(parents=True)
        with lock.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            report = self.export()
        self.assertEqual(report['entries'], [])
        self.assertEqual(len(report['rejected']), 1)

    def build_state(self):
        self.args.include_build_state = True
        build = self.workspace / cache.BUILD_DIRS[0]
        build.mkdir(parents=True)
        (build / 'object.o').write_bytes(b'object')
        (build / 'stale.lock').touch()
        origin = self.workspace / 'log/last-native-build.json'
        origin.parent.mkdir(parents=True)
        cache.write_json(origin, self.identity)
        return build

    def test_build_state_restores_only_matching_input_environment_and_paths(self):
        self.entry()
        build = self.build_state()
        self.export()
        self.assertFalse((self.snapshot / 'build-state' / cache.BUILD_DIRS[0] / 'stale.lock').exists())
        import shutil
        shutil.rmtree(build)
        report = self.restore()
        self.assertEqual(report['buildState']['restored'], [cache.BUILD_DIRS[0]])
        self.assertEqual((build / 'object.o').read_bytes(), b'object')
        second = self.restore()
        self.assertIn('preserved', second['buildState']['skipped'][0])

    def test_build_state_path_native_and_environment_mismatches_leave_action_cache_usable(self):
        self.entry()
        self.build_state()
        self.export()
        for field, value in (('workspace', '/other'), ('nativeInputs', {'vllm': 'new'}),
                             ('environment', {**self.env, 'imageId': 'other image'})):
            with self.subTest(field=field):
                old = self.identity[field]
                self.identity[field] = value
                report = self.restore()
                self.assertIn(field + ' differs', report['buildState']['skipped'])
                self.assertEqual(len(report['copied']) + len(report['existing']), 1)
                self.identity[field] = old

    def test_unverified_build_state_is_skipped(self):
        self.entry()
        self.args.include_build_state = True
        build = self.workspace / cache.BUILD_DIRS[0]
        build.mkdir()
        report = self.export()
        self.assertEqual(report['buildState']['directories'], [])
        self.assertIn('successful build origin unavailable', report['buildState']['skipped'])

    def test_corrupt_build_origin_skips_state_and_keeps_incremental_entries(self):
        self.entry()
        self.build_state()
        (self.workspace / 'log/last-native-build.json').write_text('broken json')
        report = self.export()
        self.assertEqual(len(report['entries']), 1)
        self.assertEqual(report['buildState']['directories'], [])
        self.assertIn('invalid successful build origin', report['buildState']['skipped'][0])

    def test_snapshot_inside_build_directory_is_rejected_before_copy(self):
        snapshot = self.workspace / cache.BUILD_DIRS[0] / 'snapshot'
        with patch('builtins.print'):
            result = cache.main(['export', '--workspace', str(self.workspace),
                                 '--cache-dir', str(self.source), '--snapshot-dir', str(snapshot),
                                 '--include-build-state'])
        self.assertEqual(result, 1)
        self.assertFalse(snapshot.exists())
        report = cache.read_json(next((self.workspace / 'log/cache-transfer').glob('*-export.json')))
        self.assertEqual(report['exitCode'], 1)
        self.assertIn('must not overlap', report['error'])

    def test_schema_or_runtime_mismatch_rejects_restore(self):
        self.entry()
        self.export()
        self.env['architecture'] = 'x86_64'
        with self.assertRaisesRegex(ValueError, 'architecture'):
            self.restore()
        self.env['architecture'] = 'aarch64'
        manifest = cache.read_json(self.snapshot / 'manifest.json')
        manifest['cacheSchema'] = 3
        cache.write_json(self.snapshot / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.restore()

    def test_source_sha_change_alone_does_not_reject_incremental_cache(self):
        self.entry()
        self.export()
        self.identity['repos']['vllm-ascend']['sha'] = 'new-python-only-commit'
        self.assertEqual(len(self.restore()['copied']), 1)

    def test_event_summary_counts_results_and_ignores_other_events(self):
        path = self.root / 'events.jsonl'
        path.write_text('\n'.join(['{"event":"cache_result","status":"HIT"}',
                                   '{"event":"cache_result","status":"MISS"}',
                                   '{"event":"cache_result","status":"BYPASS"}',
                                   '{"event":"entry","status":"HIT"}', 'invalid']))
        self.assertEqual(cache.event_summary(path), {'HIT': 1, 'MISS': 1, 'BYPASS': 1})

    def test_inspect_does_not_create_reports_or_modify_cache(self):
        self.entry()
        with patch('builtins.print'):
            self.assertEqual(cache.main(['inspect', '--workspace', str(self.workspace),
                                         '--cache-dir', str(self.source)]), 0)
        self.assertFalse((self.workspace / 'log').exists())


if __name__ == '__main__':
    unittest.main()
