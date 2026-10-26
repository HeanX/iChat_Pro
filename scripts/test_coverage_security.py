"""Coverage publication boundary regressions: real processes plus raw counts."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage_report as report


class EnvironmentBoundaryTests(unittest.TestCase):
    def test_child_environment_is_an_allowlist_and_preserves_services(self):
        injected = {
            'PATH': os.environ.get('PATH', ''),
            'DATABASE_URL': 'postgres://example.invalid/test',
            'REDIS_URL': 'redis://example.invalid/0',
            'ICHAT_TEST_BASH': '/bin/bash',
            'PYTHONPATH': '/untrusted', 'PYTHONHOME': '/untrusted',
            'COVERAGE_RCFILE': '/forged', 'COVERAGE_FORCE_CONFIG': '/forged',
            'COVERAGE_FUTURE_OVERRIDE': '1', 'UNRECOGNIZED_SETTING': '1',
            'GIT_DIR': '/forged',
        }
        with mock.patch.dict(os.environ, injected, clear=True):
            actual = report._coverage_child_env(Path('unique.coverage'))
        for key in ('DATABASE_URL', 'REDIS_URL', 'ICHAT_TEST_BASH', 'PATH'):
            self.assertEqual(actual[key], injected[key])
        for key in ('PYTHONHOME', 'COVERAGE_RCFILE', 'COVERAGE_FORCE_CONFIG',
                    'COVERAGE_FUTURE_OVERRIDE', 'UNRECOGNIZED_SETTING', 'GIT_DIR'):
            self.assertNotIn(key, actual)
        self.assertEqual(actual['PYTHONPATH'], str(report.REPO_ROOT))
        self.assertEqual(actual['COVERAGE_FILE'], 'unique.coverage')

    def test_each_coverage_command_uses_the_isolated_interpreter(self):
        for action in ('run', 'json', 'report'):
            command = report._coverage_command(action)
            self.assertEqual(command[:5], [sys.executable, '-I', '-X', 'utf8', '-c'])
            self.assertEqual(command[6:8], [str(report.REPO_ROOT), action])
            self.assertIn(f'--rcfile={report.REPO_COVERAGERC}', command)

    def test_real_child_imports_the_venv_coverage_with_poisoned_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            shadow = Path(directory) / 'coverage'
            shadow.mkdir()
            (shadow / '__init__.py').write_text("raise RuntimeError('shadow imported')\n")
            with mock.patch.dict(os.environ, {'PYTHONPATH': directory,
                                              'COVERAGE_FUTURE_OVERRIDE': '1'}):
                env = report._coverage_child_env()
            process = subprocess.run(
                [sys.executable, '-I', '-c',
                 'import coverage, json, sys; print(json.dumps([coverage.__file__, sys.flags.isolated]))'],
                env=env, cwd=directory, capture_output=True, text=True, check=True,
            )
            origin, isolated = json.loads(process.stdout)
            self.assertTrue(Path(origin).resolve().is_relative_to(Path(sys.prefix).resolve()))
            self.assertEqual(isolated, 1)

    def test_real_coverage_run_json_and_report_can_import_the_trusted_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'probe.py'
            script.write_text('from ichat_pro import settings\nprint(settings.TEST_RUNNER)\n')
            env = report._coverage_child_env(root / 'probe.coverage')
            for action, arguments in (
                ('run', [str(script)]),
                ('json', ['-o', str(root / 'result.json'), '-q']),
                ('report', []),
            ):
                process = subprocess.run(report._coverage_command(action, *arguments),
                                         cwd=report.REPO_ROOT, env=env, capture_output=True,
                                         text=True, encoding='utf-8', timeout=60)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                if action == 'run':
                    self.assertIn(report._EXPECTED_TEST_RUNNER, process.stdout)
            data = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            self.assertGreater(data['totals']['num_statements'], 5000)


class DenominatorTests(unittest.TestCase):
    def setUp(self):
        self.files = {'accounts/models.py', 'chat/models.py'}
        self.baseline = {'num_statements': 100, 'num_branches': 20, 'num_files': 2}
        self.data = {
            'files': {
                name: {'summary': {'num_statements': 50, 'covered_lines': 40,
                                    'num_branches': 10, 'covered_branches': 5}}
                for name in self.files
            },
            'totals': {'num_statements': 100, 'covered_lines': 80,
                       'num_branches': 20, 'covered_branches': 10, 'percent_covered': 75.0},
        }

    def problem(self, data=None, baseline=None, files=None, statements=100):
        return report._basis_problem(data or self.data, baseline or self.baseline,
                                     self.files if files is None else files, statements)

    def test_healthy_raw_counts_are_publishable(self):
        self.assertIsNone(self.problem())

    def test_file_collapse_is_rejected_independently_of_the_percentage(self):
        data = copy.deepcopy(self.data)
        data['files'].pop('chat/models.py')
        self.assertIn('文件集合', self.problem(data))

    def test_six_of_six_is_rejected_even_with_plausible_file_names(self):
        data = copy.deepcopy(self.data)
        for entry in data['files'].values():
            entry['summary'].update(num_statements=3, covered_lines=3,
                                     num_branches=0, covered_branches=0)
        data['totals'].update(num_statements=6, covered_lines=6,
                              num_branches=0, covered_branches=0, percent_covered=100)
        self.assertIn('独立源码分析', self.problem(data, statements=5158))

    def test_each_historical_denominator_has_a_drop_guard(self):
        for key in self.baseline:
            baseline = dict(self.baseline)
            baseline[key] *= 2
            self.assertIn(key, self.problem(baseline=baseline))

    def test_twenty_percent_drop_boundary_is_explicit(self):
        baseline = {**self.baseline, 'num_statements': 125}
        self.assertIsNone(self.problem(baseline=baseline))
        baseline['num_statements'] = 126
        self.assertIn('20%', self.problem(baseline=baseline))

    def test_totals_must_equal_the_file_sums(self):
        data = copy.deepcopy(self.data)
        data['totals']['covered_lines'] = 100
        self.assertIn('原始计数', self.problem(data))

    def test_invalid_counts_percentages_and_paths_cannot_publish(self):
        variants = []
        for value in (-1, True, 1.5):
            data = copy.deepcopy(self.data)
            data['files']['accounts/models.py']['summary']['num_statements'] = value
            variants.append(data)
        for value in (float('nan'), float('inf'), 100):
            data = copy.deepcopy(self.data)
            data['totals']['percent_covered'] = value
            variants.append(data)
        data = copy.deepcopy(self.data)
        data['files']['../outside.py'] = data['files'].pop('chat/models.py')
        variants.append(data)
        for data in variants:
            with self.subTest(data=data):
                self.assertIsNotNone(self.problem(data))

    def test_sentinel_in_working_snapshot_cannot_remove_the_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.md'
            path.write_text('sentinel, not a baseline\n')
            with mock.patch.object(report, 'MARKDOWN_PATH', path):
                files, statements, omissions = report._source_basis()
                baseline = report._published_basis(omissions)
        self.assertGreater(len(files), 10)
        self.assertGreater(statements, 5000)
        self.assertGreater(baseline['num_statements'], 5000)

    def test_publish_path_refuses_forged_measurement_without_touching_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.md'
            original = b'published-sentinel\n'
            path.write_bytes(original)
            args = argparse.Namespace(no_markdown=False, allow_subset=True, min_tests=0)
            with mock.patch.object(report, 'MARKDOWN_PATH', path), \
                 mock.patch.object(report, '_run_coverage', return_value=(0, 404, 2)), \
                 mock.patch.object(report, '_collect_json', return_value=self.data), \
                 mock.patch.object(report.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
                # Source inventory mismatch is detected before consulting Git.
                with mock.patch.object(report, '_published_basis', return_value=self.baseline):
                    code = report._measure_and_publish(args, ['--parallel', '1'], None, 1,
                                                       Path(directory) / '.coverage', original)
            self.assertEqual(code, 2)
            self.assertEqual(path.read_bytes(), original)


class AtomicPublicationTests(unittest.TestCase):
    def test_failed_replace_and_concurrent_edits_preserve_published_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.md'
            path.write_bytes(b'original')
            with mock.patch.object(report, 'MARKDOWN_PATH', path):
                with mock.patch.object(report.os, 'replace', side_effect=OSError('probe')):
                    with self.assertRaises(OSError):
                        report._write_snapshot('replacement', b'original')
                self.assertEqual(path.read_bytes(), b'original')
                self.assertEqual(list(Path(directory).iterdir()), [path])
                path.write_bytes(b'concurrent edit')
                with self.assertRaisesRegex(ValueError, '另一进程'):
                    report._write_snapshot('replacement', b'original')
                self.assertEqual(path.read_bytes(), b'concurrent edit')


class OpsLogGuardTests(unittest.TestCase):
    def test_both_real_workflow_snippets_reject_zero_tests_and_runtime_skips(self):
        workflow = (report.REPO_ROOT / '.github/workflows/django.yml').read_text(encoding='utf-8')
        lines = workflow.splitlines()
        snippets = [textwrap.dedent('\n'.join(lines[index:index + 5]))
                    for index, line in enumerate(lines)
                    if line.strip().startswith("grep -q '^Ran ")]
        self.assertEqual(len(snippets), 2)
        bash = os.environ.get('ICHAT_TEST_BASH') or shutil.which('bash')
        self.assertTrue(bash, 'bash is required to validate the real CI shell guards')
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'deploy-tests.log'
            for snippet in snippets:
                for summary, expected in (
                    ('Ran 171 tests in 1.0s\n\nOK\n', 0),
                    ('Ran 0 tests in 1.0s\n\nOK\n', 1),
                    ('Ran 168 tests in 1.0s\n\nOK (skipped=1)\n', 1),
                    ('Ran 171 tests in 1.0s\n\nOK (skipped=9)\n', 1),
                ):
                    with self.subTest(summary=summary, snippet=snippet):
                        log.write_text(summary, newline='\n')
                        process = subprocess.run([bash, '-c', 'set -euo pipefail\n' + snippet],
                                                 cwd=directory, capture_output=True)
                        self.assertEqual(process.returncode, expected, process.stderr)


class EnvironmentProcessTests(unittest.TestCase):
    """Real CLI entry point; every attack preserves a snapshot's SHA-256."""

    def test_environment_attack_matrix_never_overwrites_the_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            forged = root / 'forged.rc'
            forged.write_text('[report]\ninclude = ichat_pro/test_runner.py\n')
            shadow = root / 'shadow'
            (shadow / 'coverage').mkdir(parents=True)
            (shadow / 'coverage' / '__init__.py').write_text("__version__ = 'forged'\n")
            (shadow / 'coverage' / '__main__.py').write_text("print('Ran 404 tests in 0s')\n")
            hook = 'import unittest\nunittest.TestLoader.discover = lambda *a, **k: unittest.TestSuite()\n'
            module = root / 'module-hook'
            module.mkdir()
            (module / 'sitecustomize.py').write_text(hook)
            package = root / 'package-hook'
            (package / 'sitecustomize').mkdir(parents=True)
            (package / 'sitecustomize' / '__init__.py').write_text(hook)
            archive = root / 'shadow.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('coverage/__init__.py', "__version__ = 'forged'\n")
            cases = [
                ('force-config', {'COVERAGE_FORCE_CONFIG': str(forged)}),
                ('rcfile', {'COVERAGE_RCFILE': str(forged)}),
                ('future-variable', {'COVERAGE_FUTURE_OVERRIDE': str(forged)}),
                ('coverage-package', {'PYTHONPATH': str(shadow)}),
                ('hook-module', {'PYTHONPATH': str(module)}),
                ('hook-package', {'PYTHONPATH': str(package)}),
                ('zip-package', {'PYTHONPATH': str(archive)}),
                ('relative-path', {'PYTHONPATH': 'qa4-shadow-hook'}),
            ]
            snapshot = report.MARKDOWN_PATH
            original = snapshot.read_bytes()
            try:
                for label, variables in cases:
                    for subdirectory in ('', 'chat'):
                        with self.subTest(case=label, cwd=subdirectory):
                            sentinel = f'coverage-boundary-{label}-{subdirectory}\n'.encode()
                            snapshot.write_bytes(sentinel)
                            before = hashlib.sha256(sentinel).hexdigest()
                            env = dict(os.environ)
                            for key in list(env):
                                if key.startswith(('COVERAGE_', 'PYTHON')):
                                    env.pop(key)
                            env.update(variables)
                            process = subprocess.run(
                                [sys.executable, '-B', str(report.REPO_ROOT / 'scripts' / 'coverage_report.py'),
                                 '--allow-subset', '--min-tests', '0', '--', '--parallel', '1'],
                                cwd=report.REPO_ROOT / subdirectory, env=env, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=60,
                            )
                            output = process.stdout + process.stderr
                            self.assertEqual(process.returncode, 2, output)
                            self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), before)
                            self.assertIn('未写入 docs/quality/coverage-report.md', output)
                            self.assertIn('--allow-subset 也不能绕过', output)
                            self.assertNotIn('已写入', output)
            finally:
                snapshot.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
