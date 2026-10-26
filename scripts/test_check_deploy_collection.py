"""Unit tests for ``scripts/check_deploy_collection.py`` (QA-01).

The guard itself is mostly I/O against a real checkout, so what is pinned here
is the part that can silently rot: the list of required modules, the parser
that reports them to CI, and the refusal text. The end-to-end proof (all five
modules collected, bash present) is a process-level probe, not this file --
these tests must stay fast and runnable on any branch, including one that does
not carry ``deploy/tests/`` at all.

Run with the project venv:

    .\\.venv\\Scripts\\python.exe -m unittest scripts.test_check_deploy_collection -v
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_deploy_collection as guard  # noqa: E402


class RequiredModulesTests(unittest.TestCase):
    """The five modules are the contract; renaming one must fail loudly."""

    def test_the_five_review_named_modules_are_required(self):
        self.assertEqual(
            guard.DEPLOY_TEST_MODULES,
            (
                'deploy.tests.test_deploy_gate',
                'deploy.tests.test_isolation_check',
                'deploy.tests.test_monitor',
                'deploy.tests.test_release_audit',
                'deploy.tests.test_smoke_gate',
            ),
        )

    def test_print_modules_lists_every_required_module(self):
        # This is what CI feeds to ``manage.py test``, so it must never lose a
        # module silently -- and it must not print names that do not exist.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = guard.main(['--print-modules'])
        if code == 0:
            for name in guard.DEPLOY_TEST_MODULES:
                self.assertIn(name, out.getvalue().split())
        else:
            # Review P4: on a tree without deploy/tests/ this must refuse
            # instead of handing CI unimportable labels.
            self.assertEqual(out.getvalue(), '')
            self.assertIn('deploy/tests/__init__.py', err.getvalue())

    def test_extras_are_discovered_from_disk_not_hardcoded(self):
        # A module added to deploy/tests/ later must be picked up. On a branch
        # without deploy/tests/ the list is simply empty -- never an error.
        extras = guard.extra_deploy_modules()
        self.assertIsInstance(extras, list)
        tests_dir = REPO_ROOT / 'deploy' / 'tests'
        if tests_dir.is_dir():
            on_disk = {
                f'deploy.tests.{path.stem}'
                for path in tests_dir.glob('test_*.py')
            }
            self.assertEqual(
                set(extras) | set(guard.DEPLOY_TEST_MODULES), on_disk | set(guard.DEPLOY_TEST_MODULES)
            )
            for name in guard.DEPLOY_TEST_MODULES:
                self.assertNotIn(name, extras, 'required modules are not extras')
        else:
            self.assertEqual(extras, [])

    def test_flatten_walks_nested_suites(self):
        import unittest as unittest_module

        class Leaf(unittest_module.TestCase):
            def __init__(self, name):
                super().__init__('runTest')
                self._name = name

            def id(self):
                return self._name

            def runTest(self):
                pass

        suite = unittest_module.TestSuite([
            Leaf('a'),
            unittest_module.TestSuite([Leaf('b')]),
            Leaf('c'),
        ])
        self.assertEqual(list(guard.flatten(suite)), ['a', 'b', 'c'])


class NamespacePackageRegressionTests(unittest.TestCase):
    """P2 (review round 2): the guard must be red when default discovery is 0.

    Round 1's guard computed ``default_ids`` and then ignored them, so a repo
    that lost ``deploy/__init__.py`` -- the exact QA-01 regression -- printed
    "QA-01 OK ... 0" and exited 0.

    Two throwaway trees differ in one thing: whether ``deploy/__init__.py``
    exists. Both carry ``deploy/tests/`` with the five real module names. The
    guard's own ``main()`` is executed in a real subprocess against each, with
    cwd and ``sys.path`` pointed at that tree.
    """

    SETTINGS = (
        "SECRET_KEY = 'qa01-test'\n"
        'INSTALLED_APPS = []\n'
        'DATABASES = {}\n'
        'USE_TZ = True\n'
    )

    MODULE = (
        'import shutil\n'
        'import unittest\n\n'
        "BASH = shutil.which('bash')\n"
        'HAS_BASH = BASH is not None\n\n\n'
        'class {cls}(unittest.TestCase):\n'
        '    def test_one(self):\n'
        '        self.assertTrue(True)\n'
    )

    MODULES = {
        'test_deploy_gate': 'DeployGate',
        'test_isolation_check': 'IsolationCheck',
        'test_monitor': 'Monitor',
        'test_release_audit': 'ReleaseAudit',
        'test_smoke_gate': 'SmokeGate',
    }

    DRIVER = '''
import importlib.util, io, json, os, sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

tree = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("guard", sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.REPO_ROOT = tree
os.chdir(tree)
sys.path.insert(0, str(tree))
out, err = io.StringIO(), io.StringIO()
with redirect_stdout(out), redirect_stderr(err):
    code = module.main([])
print(json.dumps({"exit_code": code, "stdout": out.getvalue(),
                  "stderr": err.getvalue()}, ensure_ascii=False))
'''

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _build(self, name, *, regular_package):
        root = self.tmp / name
        (root / 'deploy' / 'tests').mkdir(parents=True)
        (root / 'probe_settings.py').write_text(self.SETTINGS, encoding='utf-8')
        if regular_package:
            (root / 'deploy' / '__init__.py').write_text('', encoding='utf-8')
        (root / 'deploy' / 'tests' / '__init__.py').write_text('', encoding='utf-8')
        for module, cls in self.MODULES.items():
            (root / 'deploy' / 'tests' / f'{module}.py').write_text(
                self.MODULE.format(cls=cls), encoding='utf-8'
            )
        return root

    def _run_guard(self, tree: Path) -> dict:
        env = dict(os.environ)
        env['PYTHONPATH'] = str(tree)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['DJANGO_SETTINGS_MODULE'] = 'probe_settings'
        env.pop('ICHAT_TEST_BASH', None)
        # bash must be findable, otherwise the guard fails for the shell reason
        # and the namespace question is masked.
        bash_dir = r'C:\Program Files\Git\bin'
        if Path(bash_dir, 'bash.exe').is_file():
            env['PATH'] = env.get('PATH', '') + os.pathsep + bash_dir
        completed = subprocess.run(
            [sys.executable, '-B', '-c', self.DRIVER, str(tree),
             str(REPO_ROOT / 'scripts' / 'check_deploy_collection.py')],
            cwd=str(tree), env=env, text=True, encoding='utf-8',
            errors='replace', capture_output=True, timeout=300,
        )
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_regular_package_is_green(self):
        # The sanity control: if this ever fails, the probe is broken and the
        # red case below proves nothing.
        result = self._run_guard(self._build('regular', regular_package=True))
        self.assertEqual(result['exit_code'], 0, result['stderr'])
        self.assertIn('QA-01 OK', result['stdout'])

    def test_namespace_package_is_red(self):
        # The regression QA-01 is about: deploy/ has no __init__.py, so
        # `manage.py test` never recurses into deploy/tests/.
        result = self._run_guard(self._build('namespace', regular_package=False))
        self.assertNotEqual(
            result['exit_code'], 0,
            'a namespace deploy/ must fail the guard, not print OK\n'
            + result['stdout'],
        )
        self.assertNotIn('QA-01 OK', result['stdout'])
        self.assertIn('默认发现', result['stderr'])
        self.assertIn('namespace package', result['stderr'])


if __name__ == '__main__':
    unittest.main()
