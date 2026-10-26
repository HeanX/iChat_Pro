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
import sys
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
        # module silently.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = guard.main(['--print-modules'])
        self.assertEqual(code, 0)
        for name in guard.DEPLOY_TEST_MODULES:
            self.assertIn(name, out.getvalue().split())

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


if __name__ == '__main__':
    unittest.main()
