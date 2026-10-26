"""Unit tests for ``scripts/coverage_report.py`` (P4 Test T12 / #193).

Run with the project venv:

    .\\.venv\\Scripts\\python.exe -m unittest scripts.test_coverage_report -v

These tests exist because the subset guard in the reporting script repeatedly
regressed in review (``--tag``, then ``-kfoo``, then ``--pat=``, then
``--parallel=+2`` and bundled ``-rkfoo``): every attempt to classify argv by
hand lost to argparse syntax. The implementation now parses pass-through
arguments with Django's own parser, so these tests pin that behaviour at the
same level -- including the spellings that previously slipped through.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage_report as report  # noqa: E402


def _parsed(*arguments: str):
    """Parse arguments exactly like the script does."""
    return report._parse_test_args(list(arguments))


# ``Command.create_parser`` reads settings.TEST_RUNNER, so Django must be
# configured before any test touches the parser.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
import django  # noqa: E402

django.setup()


def _is_subset(*arguments: str) -> bool:
    options, unknown = _parsed(*arguments)
    return report._is_subset_run(options, unknown)


def _parallel(*arguments: str):
    options, _ = _parsed(*arguments)
    return report._parallel_requested(options)


class SubsetDetectionTests(unittest.TestCase):
    """The run must be flagged as a subset exactly when Django would narrow it.

    Judgement is made on arguments parsed by Django's own ``test`` parser, so
    every argparse spelling is covered by construction. Each spelling below is
    one that escaped a hand-written classifier in an earlier review round.
    """

    def test_plain_full_run_arguments_are_not_subsets(self):
        full_runs = [
            [],
            ['--parallel', '1'],
            ['--parallel=1'],
            ['-v', '2'],
            ['-v2'],
            ['--verbosity=2'],
            ['--shuffle', '1234'],
            ['--shuffle=1234'],
            ['--shuffle'],
            ['--keepdb'],
            ['--noinput'],
            ['--no-input'],
            ['--timing'],
            ['-r'],
            ['-b'],
            ['-d'],
            ['--failfast'],
            ['--durations', '10'],
            ['--durations=10'],
            ['--parallel', '1', '--keepdb', '-v', '2'],
            ['-t', '.'],
            ['--top-level-directory', '.'],
            ['--settings', 'ichat_pro.settings'],
            ['--pythonpath', '.'],
            # Abbreviations of *non-filter* options stay full runs.
            ['--noin'],
            ['--para=1'],
            ['--verb=2'],
            ['--shuff=1234'],
        ]
        for arguments in full_runs:
            with self.subTest(arguments=arguments):
                self.assertFalse(_is_subset(*arguments))

    def test_explicit_test_labels_are_subsets(self):
        for arguments in (
            ['chat'],
            ['chat.tests.test_protocol'],
            ['--parallel', '1', 'chat.tests.test_protocol'],
            ['accounts', 'chat'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_long_filter_flags_are_subsets_in_every_spelling(self):
        for arguments in (
            ['--tag', 'slow'],
            ['--tag=slow'],
            ['--exclude-tag', 'slow'],
            ['--exclude-tag=slow'],
            ['--pattern', 'test_*.py'],
            ['--pattern=test_*.py'],
            ['--tag', 'a', '--exclude-tag', 'b'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_short_filter_flags_are_subsets_in_every_spelling(self):
        for arguments in (
            ['-k', 'test_protocol'],
            ['-k=test_protocol'],
            ['-ktest_protocol'],
            ['-p', 'test_*.py'],
            ['-p=test_*.py'],
            ['-ptest_*.py'],
            ['--parallel', '1', '-ktest_protocol'],
            ['--parallel', '1', '-ptest_protocol*.py'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_bundled_short_options_containing_a_filter_are_subsets(self):
        # argparse bundles short options: ``-rkprotocol`` == ``-r -k protocol``.
        # A classifier that only looked at the first two characters missed this.
        for arguments in (
            ['-rkprotocol'],
            ['-rk', 'protocol'],
            ['-bkx'],
            ['-bkrprotocol'],
            ['--parallel', '1', '-rkprotocol'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_bundles_without_a_filter_stay_full_runs(self):
        for arguments in (
            ['-rb'],
            ['-bd'],
            ['--parallel', '1', '-rb'],
        ):
            with self.subTest(arguments=arguments):
                self.assertFalse(_is_subset(*arguments))

    def test_bundle_ending_in_a_valueless_k_raises_like_django(self):
        # ``-rbdk`` leaves -k without a value: Django itself exits 2 here, and
        # the helper surfaces that as CommandError rather than guessing.
        with self.assertRaises(Exception):
            _is_subset('-rbdk')

    def test_argparse_abbreviations_are_subsets(self):
        for arguments in (
            ['--pat=test_protocol*.py'],
            ['--patt=test_protocol*.py'],
            ['--patt', 'test_protocol*.py'],
            ['--ta=slow'],
            ['--exclu=slow'],
            ['--exclude=slow'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_custom_testrunner_is_treated_as_a_subset(self):
        # A swapped runner changes what "the whole suite" means, so an
        # explicit --testrunner must not feed the published snapshot.
        # Abbreviations of --testrunner must behave identically.
        for arguments in (
            ['--testrunner', 'xxx.YRunner'],
            ['--testrunner=xxx.YRunner'],
            ['--testr', 'xxx.YRunner'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))

    def test_unknown_options_are_treated_as_subsets(self):
        # Django itself would exit 2; refuse rather than guess.
        self.assertTrue(_is_subset('--definitely-not-an-option'))
        self.assertTrue(_is_subset('--definitely-not-an-option', 'chat'))

    def test_bare_parallel_is_not_a_subset_but_is_refused_by_parallel_guard(self):
        # Bare --parallel means "auto": the whole suite still runs, so it is
        # not a subset -- but _parallel_requested() reports it above 1 and the
        # snapshot guard refuses it there.
        self.assertFalse(_is_subset('--parallel'))
        self.assertFalse(_is_subset('--parallel=auto'))
        self.assertGreater(_parallel('--parallel'), 1)
        self.assertGreater(_parallel('--parallel=auto'), 1)


class SettingsAndRunnerDetectionTests(unittest.TestCase):
    """QA-02: the settings module decides the runner, and the runner the suite.

    The defect this pins: ``--settings=<alt>`` made Django use that module's
    ``TEST_RUNNER``, which built a single-case suite. ``_is_subset_run`` only
    looked at the argv ``testrunner`` dest, returned False, and the script
    overwrote the published snapshot with a 1-test coverage run.

    Every case here is pure: the settings module is a temporary file, and the
    environment is patched so no ambient ``DJANGO_SETTINGS_MODULE`` leaks in.
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop('DJANGO_SETTINGS_MODULE', None)
        self.addCleanup(self._env.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        if str(self.tmp) not in sys.path:
            sys.path.insert(0, str(self.tmp))
        self.addCleanup(lambda: sys.path.remove(str(self.tmp)) if str(self.tmp) in sys.path else None)

    def _write_settings(self, name, body):
        (self.tmp / f'{name}.py').write_text(body, encoding='utf-8')
        sys.modules.pop(name, None)
        self.addCleanup(sys.modules.pop, name, None)
        return name

    def _reason(self, *arguments):
        options, unknown = _parsed(*arguments)
        return report._subset_reason(options, unknown)

    def test_default_settings_module_is_not_a_subset(self):
        for arguments in (
            [],
            ['--settings', 'ichat_pro.settings'],
            ['--settings=ichat_pro.settings'],
            ['--sett=ichat_pro.settings'],
        ):
            with self.subTest(arguments=arguments):
                self.assertFalse(_is_subset(*arguments))
                self.assertIsNone(self._reason(*arguments))

    def test_any_other_settings_module_is_a_subset(self):
        # Even an `import *` clone: the runner can be overridden in it, which is
        # exactly how the one-test run was published.
        name = self._write_settings(
            'qa02_clone_settings', 'from ichat_pro.settings import *\n')
        for arguments in (
            [f'--settings={name}'],
            [f'--settings', name],
            [f'--sett={name}'],  # argparse abbreviation
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(_is_subset(*arguments))
                reason = self._reason(*arguments)
                self.assertTrue(reason)
                self.assertIn(name, reason, 'the refusal must name the module')
                self.assertIn('--settings', reason, 'and where it came from')

    def test_environment_settings_module_is_a_subset(self):
        name = self._write_settings(
            'qa02_env_settings', 'from ichat_pro.settings import *\n')
        with mock.patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': name}):
            self.assertTrue(_is_subset())
            reason = self._reason()
        self.assertIn(name, reason)
        self.assertIn('DJANGO_SETTINGS_MODULE', reason)

    def test_default_settings_in_the_environment_stays_a_full_run(self):
        # A CI job exporting the default module explicitly is not a subset.
        with mock.patch.dict(
            os.environ, {'DJANGO_SETTINGS_MODULE': 'ichat_pro.settings'}
        ):
            self.assertFalse(_is_subset())

    def test_missing_settings_module_is_refused_not_assumed_full(self):
        # The script cannot import it, so it cannot prove which runner would run:
        # an unverifiable run must never publish a snapshot.
        self.assertTrue(_is_subset('--settings=qa02_does_not_exist_settings'))
        reason = self._reason('--settings=qa02_does_not_exist_settings')
        self.assertTrue(reason)
        self.assertIn('qa02_does_not_exist_settings', reason)

    def test_settings_module_with_a_different_test_runner_is_refused(self):
        # The default module path, but the runner is swapped inside it. Done by
        # patching the settings module's attribute, which is what a settings
        # module that assigns TEST_RUNNER looks like after import.
        import ichat_pro.settings as project_settings

        with mock.patch.object(
            project_settings, 'TEST_RUNNER', 'qa02_one_case_runner.OneTestRunner'
        ):
            self.assertTrue(_is_subset())
            reason = self._reason()
        self.assertIn('qa02_one_case_runner.OneTestRunner', reason)
        self.assertIn('TEST_RUNNER', reason)

    def test_settings_module_with_the_same_runner_is_not_a_subset(self):
        import ichat_pro.settings as project_settings

        self.assertEqual(
            report._test_runner_for('ichat_pro.settings'),
            project_settings.TEST_RUNNER,
        )
        self.assertIsNone(self._reason())

    def test_refusal_is_unpublishable_for_runner_or_settings(self):
        # A plain filter is an understood subset (exit 0, snapshot untouched);
        # a hidden runner means the operator silently got no snapshot, so it
        # must be visible to CI as a non-zero exit.
        name = self._write_settings(
            'qa02_error_settings', 'from ichat_pro.settings import *\n')
        for arguments in (
            [f'--settings={name}'],
            ['--testrunner=qa02_one_case_runner.OneTestRunner'],
        ):
            with self.subTest(arguments=arguments):
                options, unknown = _parsed(*arguments)
                self.assertTrue(report._subset_is_unpublishable(options, unknown))
        for arguments in (['--tag', 'slow'], ['chat'], ['-kfoo']):
            with self.subTest(arguments=arguments):
                options, unknown = _parsed(*arguments)
                self.assertFalse(report._subset_is_unpublishable(options, unknown))
        # `--pattern` is *not* in this list: it changes the discovery basis
        # rather than filtering discovered tests (review P13).
        for arguments in (['--pattern=t_*.py'], ['-p', 't_*.py']):
            with self.subTest(arguments=arguments):
                options, unknown = _parsed(*arguments)
                self.assertTrue(report._subset_is_unpublishable(options, unknown))

    def test_allow_subset_cannot_publish_a_hidden_runner(self):
        # ``--allow-subset`` acknowledges a subset the operator *chose*. A
        # runner that decided on its own how much to run is not that, so it is
        # refused even with the flag -- same rule as ``--parallel``.
        decision, reason = report._snapshot_decision(
            is_subset=True,
            allow_subset=True,
            test_count=1,
            min_tests=1,
            subset_reason='生效的 TEST_RUNNER 是 X',
            subset_is_unpublishable=True,
        )
        self.assertEqual(decision, 'refuse')
        self.assertIn('--allow-subset 也不能绕过', reason)
        self.assertIn('TEST_RUNNER', reason)
        # A chosen subset still honours the flag.
        decision, _ = report._snapshot_decision(
            is_subset=True, allow_subset=True, test_count=11, min_tests=1,
        )
        self.assertEqual(decision, 'write')

    def test_refusal_text_is_readable_and_says_why(self):
        name = self._write_settings(
            'qa02_readable_settings',
            'from ichat_pro.settings import *\n'
            "TEST_RUNNER = 'qa02_one_case_runner.OneTestRunner'\n",
        )
        options, unknown = _parsed(f'--settings={name}')
        text = report._subset_refusal(options, unknown)
        self.assertIsNotNone(text)
        self.assertIn('coverage-report.md', text)
        self.assertIn(name, text)
        self.assertIn('TEST_RUNNER', text)
        self.assertNotIn('Traceback', text)
        # No refusal for a real full run.
        options, unknown = _parsed('--parallel', '1')
        self.assertIsNone(report._subset_refusal(options, unknown))


class ParallelRequestedTests(unittest.TestCase):
    """``--parallel`` must be read the way Django reads it.

    Django converts the value with ``int()``, so ``+2`` is 2 and ``2_0`` is 20.
    A classifier that used ``str.isdigit()`` accepted ``--parallel=+2`` and
    published coverage numbers from worker processes it never traced.
    """

    def test_detects_every_numeric_spelling(self):
        for arguments, expected in (
            (['--parallel', '2'], 2),
            (['--parallel=2'], 2),
            (['--parallel', '4'], 4),
            (['--para=2'], 2),
            (['--para', '2'], 2),
            # int() coercions a digit check would miss.
            (['--parallel=+2'], 2),
            (['--parallel', '+2'], 2),
            (['--para=+2'], 2),
            (['--parallel=2_0'], 20),
            (['--parallel', ' 3'], 3),
        ):
            with self.subTest(arguments=arguments, expected=expected):
                self.assertEqual(_parallel(*arguments), expected)

    def test_absent_or_serial_is_zero_or_one(self):
        self.assertEqual(_parallel(), 0)
        self.assertEqual(_parallel('--parallel', '1'), 1)
        self.assertEqual(_parallel('--keepdb'), 0)
        self.assertEqual(_parallel('--parallel=0'), 0)

    def test_bare_and_auto_mean_many(self):
        # Bare ``--parallel`` has const="auto"; Django then picks its own count.
        for arguments in (
            ['--parallel'],
            ['--parallel=auto'],
            ['--parallel', 'auto'],
            ['--para=auto'],
        ):
            with self.subTest(arguments=arguments):
                self.assertGreater(_parallel(*arguments), 1)

    def test_takes_the_maximum(self):
        self.assertGreaterEqual(
            _parallel('--parallel', '2', '--parallel=8'), 8
        )


class NonNegativeIntTests(unittest.TestCase):
    """``--min-tests`` must reject nonsense instead of silently accepting it."""

    def test_accepts_zero_and_positive(self):
        self.assertEqual(report._non_negative_int('0'), 0)
        self.assertEqual(report._non_negative_int('404'), 404)

    def test_rejects_negative(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            report._non_negative_int('-5')

    def test_rejects_non_numeric(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            report._non_negative_int('many')


class SnapshotDecisionTests(unittest.TestCase):
    """The guards that decide whether a run may overwrite the published report.

    These were previously untested: mutation-testing the ``too_few`` and
    ``is_subset`` branches left the whole suite green, which is how two silent
    snapshot-overwrite paths survived review.
    """

    def _decide(self, **kwargs):
        defaults = dict(
            is_subset=False, allow_subset=False, test_count=404, min_tests=1
        )
        defaults.update(kwargs)
        return report._snapshot_decision(**defaults)

    def test_full_run_writes(self):
        self.assertEqual(self._decide()[0], 'write')

    def test_zero_tests_never_writes(self):
        decision, _ = self._decide(test_count=0)
        self.assertEqual(decision, 'refuse')

    def test_min_tests_floor_refuses_small_runs(self):
        decision, _ = self._decide(test_count=11, min_tests=100)
        self.assertEqual(decision, 'refuse')

    def test_allow_subset_cannot_bypass_the_floor(self):
        # ``--allow-subset`` acknowledges a known subset but never authorises a
        # run that executed nothing.
        decision, _ = self._decide(
            is_subset=True, allow_subset=True, test_count=0, min_tests=0
        )
        self.assertEqual(decision, 'refuse')

    def test_min_tests_zero_cannot_publish_an_empty_run(self):
        decision, _ = self._decide(test_count=0, min_tests=0)
        self.assertEqual(decision, 'refuse')

    def test_subset_is_refused_by_default(self):
        decision, _ = self._decide(is_subset=True, test_count=11)
        self.assertEqual(decision, 'refuse')

    def test_allow_subset_permits_a_known_subset(self):
        decision, _ = self._decide(is_subset=True, allow_subset=True, test_count=11)
        self.assertEqual(decision, 'write')

    def test_refusal_always_carries_a_reason(self):
        for kwargs in (
            {'test_count': 0},
            {'test_count': 5, 'min_tests': 10},
            {'is_subset': True, 'test_count': 11},
            {'parallel': 2},
        ):
            with self.subTest(kwargs=kwargs):
                decision, reason = self._decide(**kwargs)
                self.assertEqual(decision, 'refuse')
                self.assertTrue(reason)

    def test_parallel_above_one_is_always_refused(self):
        # The tests do run, but coverage cannot see the worker processes, so
        # the published totals would be silently wrong. --allow-subset must not
        # override this, and a healthy test count must not mask it either.
        for kwargs in (
            {'parallel': 2},
            {'parallel': 8},
            {'parallel': report._AUTO_PARALLEL},
            {'parallel': 2, 'allow_subset': True},
            {'parallel': 2, 'allow_subset': True, 'is_subset': True},
            {'parallel': 2, 'min_tests': 0},
        ):
            with self.subTest(kwargs=kwargs):
                decision, _ = self._decide(test_count=404, **kwargs)
                self.assertEqual(decision, 'refuse')

    def test_serial_parallel_is_allowed(self):
        for parallel in (0, 1):
            with self.subTest(parallel=parallel):
                decision, _ = self._decide(parallel=parallel)
                self.assertEqual(decision, 'write')


class SubstitutionScopeTests(unittest.TestCase):
    """P6/P7 (review round 2): what counts as replacing the runner.

    Two real bypasses survived round 1:

    * ``--pythonpath <dir>`` put a shadow ``ichat_pro`` package first on
      ``sys.path``. Its ``settings.py`` declared the *same* ``TEST_RUNNER``
      string, so comparing names saw nothing, while its runner built one case.
    * ``--allow-subset --settings=<alt> <label|--tag|-k>`` let a benign filter
      downgrade the settings swap into an authorisable "known subset".

    Both are pure classification questions, so they are pinned here; the real
    subprocess behaviour is pinned in ``ProcessLevelSnapshotTests`` below.
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop('DJANGO_SETTINGS_MODULE', None)
        self.addCleanup(self._env.stop)

    def _flags(self, *arguments):
        options, unknown = _parsed(*arguments)
        return (
            report._is_subset_run(options, unknown),
            report._subset_is_unpublishable(options, unknown),
            report._subset_reason(options, unknown),
        )

    def test_pythonpath_is_always_unpublishable(self):
        # Anything that is not the repo root can shadow ichat_pro. (The repo
        # root itself is covered by test_a_full_run_is_never_unpublishable.)
        for arguments in (
            ['--pythonpath', 'shadow'],
            ['--pythonpath=shadow'],
            ['--pythonp', 'shadow'],          # argparse abbreviation
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, reason = self._flags(*arguments)
                self.assertTrue(subset)
                self.assertTrue(unpublishable, '--pythonpath cannot be authorised')
                self.assertIn('pythonpath', reason)

    def test_top_level_directory_is_unpublishable_when_it_changes_the_root(self):
        subset, unpublishable, reason = self._flags('-t', 'chat')
        self.assertTrue(subset)
        self.assertTrue(unpublishable)
        self.assertIn('top-level-directory', reason)
        # The default value is not a narrowing.
        self.assertEqual(self._flags('-t', '.')[0], False)

    def test_a_benign_filter_cannot_launder_a_swapped_settings_module(self):
        # This is the P7 shape: the filter is real, but it must not demote the
        # dangerous cause. Every combination stays unpublishable.
        for arguments in (
            ['--settings=alt', 'chat'],
            ['--settings=alt', '--tag=x'],
            ['--settings=alt', '-kx'],
            ['--settings=alt', '--exclude-tag=y'],
            ['--settings=alt', '--pattern=t_*.py'],
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, reason = self._flags(*arguments)
                self.assertTrue(subset)
                self.assertTrue(unpublishable)
                self.assertIn('settings', reason,
                              'the reason must name the dangerous cause')

    def test_a_benign_filter_alone_stays_authorisable(self):
        # The opposite direction: an operator-chosen *selection* may still be
        # published with --allow-subset, which is a documented workflow.
        # `--pattern` is deliberately absent -- it is a basis change (P13).
        for arguments in (
            ['chat'],
            ['--tag=slow'],
            ['-kx'],
            ['--exclude-tag=y'],
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, _ = self._flags(*arguments)
                self.assertTrue(subset)
                self.assertFalse(unpublishable, 'chosen filters stay authorisable')

    def test_a_full_run_is_never_unpublishable(self):
        for arguments in (
            [],
            ['--parallel', '1'],
            ['--settings=ichat_pro.settings'],
            ['--keepdb', '--shuffle=1234'],
            # Pointing --pythonpath at the repo root it already imports from
            # cannot swap the package, so it is not a substitution.
            ['--pythonpath', '.'],
            ['--pythonpath', str(REPO_ROOT)],
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, reason = self._flags(*arguments)
                self.assertFalse(subset)
                self.assertFalse(unpublishable)
                self.assertIsNone(reason)

    def test_pythonpath_elsewhere_is_still_unpublishable(self):
        # The P6 shape: a directory that is not the repo root wins sys.path.
        for arguments in (
            ['--pythonpath', 'shadow'],
            ['--pythonpath', str(Path(tempfile.gettempdir()) / 'not-the-repo')],
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, reason = self._flags(*arguments)
                self.assertTrue(subset)
                self.assertTrue(unpublishable)
                self.assertIn('pythonpath', reason)


class CoverageBasisTests(unittest.TestCase):
    """P11 (round 3): the coverage configuration is part of the basis.

    ``COVERAGE_RCFILE`` pointed coverage at a forged rc whose ``[report]
    include`` kept only ``ichat_pro/test_runner.py``. With **no argv at all**
    the suite still ran 404 tests, exit 0, and the published snapshot said
    "6/6 statements, 0/0 branches, 100.00%, 已达标" -- turning an unmet 70.49%
    baseline into a satisfied one, in a committed artefact.

    ``PYTHONPATH`` entries outside the repo are the same class: a
    ``sitecustomize.py`` there is imported before any project code and can
    replace ``unittest.TestLoader.discover``.
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for key in ('COVERAGE_RCFILE', 'COVERAGE_FILE', 'DJANGO_SETTINGS_MODULE',
                    'PYTHONPATH'):
            os.environ.pop(key, None)
        self.addCleanup(self._env.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _flags(self, *arguments):
        options, unknown = _parsed(*arguments)
        return (
            report._is_subset_run(options, unknown),
            report._subset_is_unpublishable(options, unknown),
            report._subset_reason(options, unknown),
        )

    def test_foreign_coverage_rcfile_is_unpublishable(self):
        forged = str(Path(tempfile.gettempdir()) / 'forged.coveragerc')
        with mock.patch.dict(os.environ, {'COVERAGE_RCFILE': forged}):
            subset, unpublishable, reason = self._flags()
        self.assertTrue(subset, 'a foreign rc must make the run a subset')
        self.assertTrue(unpublishable, '--allow-subset must not launder it')
        self.assertIn('COVERAGE_RCFILE', reason)
        self.assertIn('测量基准', reason)

    def test_repo_coverage_rcfile_via_env_is_still_refused(self):
        # The guard refuses COVERAGE_RCFILE outright rather than comparing
        # paths: coverage also accepts .coveragerc, setup.cfg and tox.ini, so
        # "is this the repo's file" cannot be decided by name alone.
        with mock.patch.dict(
            os.environ, {'COVERAGE_RCFILE': str(report.REPO_COVERAGERC)}
        ):
            subset, unpublishable, _ = self._flags()
        self.assertTrue(subset)
        self.assertTrue(unpublishable)

    def test_foreign_pythonpath_with_a_startup_hook_is_unpublishable(self):
        shadow = self.tmp / 'hookdir'
        shadow.mkdir()
        (shadow / 'sitecustomize.py').write_text('', encoding='utf-8')
        with mock.patch.dict(
            os.environ,
            {'PYTHONPATH': os.pathsep.join([str(shadow), str(REPO_ROOT)])},
        ):
            subset, unpublishable, reason = self._flags()
        self.assertTrue(subset)
        self.assertTrue(unpublishable)
        self.assertIn('sitecustomize.py', reason)
        self.assertIn(str(shadow), reason)

    def test_plain_pythonpath_entries_are_not_a_substitution(self):
        # A normal venv/harness directory is not an attack: only a module that
        # Python imports at startup (sitecustomize/usercustomize) is.
        plain = self.tmp / 'plainlib'
        plain.mkdir()
        (plain / 'some_module.py').write_text('', encoding='utf-8')
        with mock.patch.dict(
            os.environ,
            {'PYTHONPATH': os.pathsep.join([str(plain), str(REPO_ROOT)])},
        ):
            subset, unpublishable, reason = self._flags()
        self.assertFalse(subset)
        self.assertFalse(unpublishable)
        self.assertIsNone(reason)

    def test_repo_only_pythonpath_is_not_a_substitution(self):
        with mock.patch.dict(os.environ, {'PYTHONPATH': str(REPO_ROOT)}):
            subset, unpublishable, reason = self._flags()
        self.assertFalse(subset)
        self.assertFalse(unpublishable)
        self.assertIsNone(reason)

    def test_coverage_file_is_pinned_rather_than_refused(self):
        # Moving the raw data file is a legitimate convenience; the script
        # pins it for its children instead, so it must NOT be refused.
        with mock.patch.dict(
            os.environ, {'COVERAGE_FILE': str(Path(tempfile.gettempdir()) / 'x.coverage')}
        ):
            self.assertIsNone(report._coverage_basis_reason())

    def test_child_env_drops_the_rcfile_override(self):
        with mock.patch.dict(os.environ, {'COVERAGE_RCFILE': 'C:/forged.rc'}):
            env = report._coverage_child_env(Path('C:/tmp/data.coverage'))
        self.assertNotIn('COVERAGE_RCFILE', env)
        self.assertEqual(env['COVERAGE_FILE'], str(Path('C:/tmp/data.coverage')))

    def test_pattern_is_a_basis_change_not_a_filter(self):
        # P13: `--pattern` is the glob discovery uses, so the denominator
        # shrinks with the numerator. It cannot be authorised.
        for arguments in (
            ['--pattern=chat/tests/test_protocol.py'],
            ['--pattern', 'chat/tests/test_protocol.py'],
            ['-p', 'chat/tests/test_protocol.py'],
        ):
            with self.subTest(arguments=arguments):
                subset, unpublishable, reason = self._flags(*arguments)
                self.assertTrue(subset)
                self.assertTrue(unpublishable)
                self.assertIn('--pattern', reason)

    def test_top_level_repo_root_is_not_a_substitution(self):
        # P18: symmetric with --pythonpath <repo>.
        self.assertFalse(self._flags('-t', str(REPO_ROOT))[0])
        subset, unpublishable, reason = self._flags('-t', 'chat')
        self.assertTrue(subset)
        self.assertTrue(unpublishable)
        self.assertIn('top-level-directory', reason)

    def test_refusal_text_has_one_source(self):
        # P16: `_subset_refusal` and `_snapshot_decision` must not drift.
        with mock.patch.dict(os.environ, {'COVERAGE_RCFILE': 'C:/forged.rc'}):
            options, unknown = _parsed()
            from_refusal = report._subset_refusal(options, unknown)
        from_decision = report._snapshot_decision(
            is_subset=True,
            allow_subset=False,
            test_count=404,
            min_tests=1,
            subset_reason=report._subset_reason(options, unknown) or '',
            subset_is_unpublishable=True,
        )[1]
        # Same wording apart from the "N tests" lead-in the decision adds.
        self.assertIn('未写入 docs/quality/coverage-report.md', from_refusal)
        self.assertIn('未写入 docs/quality/coverage-report.md', from_decision)
        self.assertIn('原因：', from_refusal)
        self.assertIn('原因：', from_decision)
        self.assertIn('--allow-subset 也不能绕过', from_refusal)
        self.assertIn('--allow-subset 也不能绕过', from_decision)


class ProcessLevelSnapshotTests(unittest.TestCase):
    """P8 (review round 2): the real script, in a real subprocess.

    Round 1's suite was 100% pure functions, so it could not observe either
    bypass: ``--pythonpath`` only misbehaves in the *child's* ``sys.path``, and
    the combination bug only appears through ``main()``'s decision path.

    Each case spawns ``scripts/coverage_report.py`` exactly as an operator
    would, with a throwaway settings module (and, for P6, a shadow ``ichat_pro``
    package) in a temp directory that is never committed. The published
    snapshot is backed up, replaced by a sentinel, and restored in ``finally``;
    the tests assert on the sentinel and on the hash, so a regression that
    writes the snapshot fails loudly *and* leaves the tree unchanged.

    The suite here is the project's own, so these runs execute one test through
    a one-case TEST_RUNNER -- deliberately tiny, the point is the guard.
    """

    #: A runner that builds exactly one test, used by the temp settings module.
    ONE_CASE_RUNNER = '''\
import unittest

from ichat_pro.test_runner import IChatTestRunner


class OneCase(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)


class OneTestRunner(IChatTestRunner):
    def build_suite(self, test_labels=None, extra_tests=None, **kwargs):
        return unittest.defaultTestLoader.loadTestsFromTestCase(OneCase)
'''

    SHADOW_SETTINGS = '''\
"""Shadow ichat_pro.settings: identical TEST_RUNNER string, one-case suite."""

DEBUG = True
SECRET_KEY = 'qa02-shadow-only'
ALLOWED_HOSTS = ['*']
INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TEST_RUNNER = 'ichat_pro.test_runner.IChatTestRunner'
'''

    SHADOW_RUNNER = '''\
import unittest

from django.test.runner import DiscoverRunner


class OneCase(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)


class IChatTestRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, extra_tests=None, **kwargs):
        return unittest.defaultTestLoader.loadTestsFromTestCase(OneCase)
'''

    def setUp(self):
        # A real subprocess of the real suite is slow; keep it to a few cases.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.snapshot = report.MARKDOWN_PATH
        self.original = (
            self.snapshot.read_bytes() if self.snapshot.exists() else b''
        )
        self.addCleanup(self._restore_snapshot)

        (self.tmp / 'qa02_test_runner.py').write_text(
            self.ONE_CASE_RUNNER, encoding='utf-8'
        )
        (self.tmp / 'qa02_alt_settings.py').write_text(
            "from ichat_pro.settings import *  # noqa: F401,F403\n"
            "TEST_RUNNER = 'qa02_test_runner.OneTestRunner'\n",
            encoding='utf-8',
        )
        shadow = self.tmp / 'shadow'
        (shadow / 'ichat_pro').mkdir(parents=True)
        (shadow / 'ichat_pro' / '__init__.py').write_text('', encoding='utf-8')
        (shadow / 'ichat_pro' / 'settings.py').write_text(
            self.SHADOW_SETTINGS, encoding='utf-8'
        )
        (shadow / 'ichat_pro' / 'test_runner.py').write_text(
            self.SHADOW_RUNNER, encoding='utf-8'
        )
        self.shadow = shadow

    def _restore_snapshot(self):
        self.snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot.write_bytes(self.original)

    def _sentinel(self, label: str) -> str:
        text = f'qa02-process-test-sentinel-{label}\n'
        self.snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot.write_text(text, encoding='utf-8', newline='\n')
        return text

    def _run(self, forwarded, *, own=()):
        """Run the real script against the real worktree, one case at a time.

        The temp directory goes on ``PYTHONPATH`` so ``--settings=<temp>``
        resolves. That is not a P11 bypass: the guard only refuses a
        ``PYTHONPATH`` entry that contains a startup hook, and this directory
        deliberately contains none.
        """
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join([str(self.tmp), str(REPO_ROOT)])
        env['PYTHONIOENCODING'] = 'utf-8'
        env.pop('DJANGO_SETTINGS_MODULE', None)
        env.pop('COVERAGE_RCFILE', None)
        command = [
            sys.executable, '-B', str(REPO_ROOT / 'scripts' / 'coverage_report.py'),
            *own, '--', *[str(arg).replace('{shadow}', str(self.shadow))
                          for arg in forwarded],
        ]
        completed = subprocess.run(
            command, cwd=str(REPO_ROOT), env=env, text=True,
            encoding='utf-8', errors='replace', capture_output=True,
            timeout=900,
        )
        return completed, completed.stdout + completed.stderr

    def _assert_refused(self, label, forwarded, *, own=()):
        sentinel = self._sentinel(label)
        before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        completed, output = self._run(forwarded, own=own)
        after = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        text = self.snapshot.read_text(encoding='utf-8')
        self.assertNotEqual(
            completed.returncode, 0,
            f'{label}: a refused snapshot must exit non-zero\n{output[-2000:]}',
        )
        self.assertEqual(
            before, after,
            f'{label}: the published snapshot was overwritten\n{output[-2000:]}',
        )
        self.assertEqual(text, sentinel, f'{label}: snapshot content changed')
        self.assertNotIn('已写入', output, f'{label}: script claimed a write')
        self.assertIn(
            '未写入 docs/quality/coverage-report.md', output,
            f'{label}: refusal must say the snapshot was not written',
        )
        return output

    def test_pythonpath_shadow_package_cannot_publish_a_snapshot(self):
        # P6: the shadow declares the same TEST_RUNNER string but runs 1 test.
        output = self._assert_refused(
            'pythonpath-shadow', ['--pythonpath={shadow}', '--parallel', '1'],
        )
        self.assertIn('pythonpath', output)
        self.assertRegex(output, r'Ran 1 test')

    def test_pythonpath_cannot_be_laundered_by_allow_subset(self):
        output = self._assert_refused(
            'pythonpath-allow-subset',
            ['--pythonpath={shadow}', '--parallel', '1'],
            own=('--allow-subset',),
        )
        self.assertIn('--allow-subset 也不能绕过', output)

    def test_swapped_settings_with_a_filter_cannot_publish(self):
        # P7: the filter is real; it must not demote the settings swap.
        for label, forwarded in (
            ('settings-label', ['--settings=qa02_alt_settings', 'chat']),
            ('settings-tag', ['--settings=qa02_alt_settings', '--tag=x']),
            ('settings-k', ['--settings=qa02_alt_settings', '-kx']),
        ):
            with self.subTest(label=label):
                output = self._assert_refused(
                    label, forwarded, own=('--allow-subset',),
                )
                self.assertIn('qa02_alt_settings', output)
                self.assertIn('--allow-subset 也不能绕过', output)

    def test_swapped_settings_from_the_environment_cannot_publish(self):
        sentinel = self._sentinel('env-settings')
        before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join([str(self.tmp), str(REPO_ROOT)])
        env['PYTHONIOENCODING'] = 'utf-8'
        env['DJANGO_SETTINGS_MODULE'] = 'qa02_alt_settings'
        env.pop('COVERAGE_RCFILE', None)
        completed = subprocess.run(
            [sys.executable, '-B',
             str(REPO_ROOT / 'scripts' / 'coverage_report.py'),
             '--', '--parallel', '1'],
            cwd=str(REPO_ROOT), env=env, text=True, encoding='utf-8',
            errors='replace', capture_output=True, timeout=900,
        )
        output = completed.stdout + completed.stderr
        after = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        self.assertNotEqual(completed.returncode, 0, output[-2000:])
        self.assertEqual(before, after, 'snapshot overwritten via env settings')
        self.assertEqual(self.snapshot.read_text(encoding='utf-8'), sentinel)
        self.assertIn('DJANGO_SETTINGS_MODULE', output)

    # -- P11 / P13 (round 3): the measurement basis -------------------------

    FORGED_RC = (
        '[run]\n'
        'branch = True\n'
        'source = ichat_pro\n'
        '\n'
        '[report]\n'
        'include = ichat_pro/test_runner.py\n'
    )

    def test_foreign_coverage_rcfile_cannot_publish(self):
        """P11: one environment variable must not rewrite the baseline.

        The forged rc reports only ``ichat_pro/test_runner.py``, so a full
        404-test run publishes "6/6 statements, 0/0 branches, 100.00%" and
        marks NEW-TST-006/007 as met. No argv is suspicious at all.
        """
        rcfile = self.tmp / 'forged.coveragerc'
        rcfile.write_text(self.FORGED_RC, encoding='utf-8')
        sentinel = self._sentinel('rcfile')
        before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        env = dict(os.environ)
        env['PYTHONPATH'] = str(REPO_ROOT)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['COVERAGE_RCFILE'] = str(rcfile)
        env.pop('DJANGO_SETTINGS_MODULE', None)
        completed = subprocess.run(
            [sys.executable, '-B',
             str(REPO_ROOT / 'scripts' / 'coverage_report.py'),
             '--', '--parallel', '1'],
            cwd=str(REPO_ROOT), env=env, text=True, encoding='utf-8',
            errors='replace', capture_output=True, timeout=900,
        )
        output = completed.stdout + completed.stderr
        after = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        self.assertNotEqual(completed.returncode, 0,
                            f'a forged rc must fail the command\n{output[-2000:]}')
        self.assertEqual(before, after,
                         f'the published snapshot was overwritten\n{output[-2000:]}')
        self.assertEqual(self.snapshot.read_text(encoding='utf-8'), sentinel)
        self.assertNotIn('已写入', output)
        self.assertIn('COVERAGE_RCFILE', output)
        self.assertIn('测量基准', output)

    def test_pattern_allow_subset_cannot_publish(self):
        """P13: ``--pattern`` shrinks the denominator, so it is a basis change.

        The pattern below matches no discovered module, so the run also trips
        the ``--min-tests`` floor. Both refuse; what matters here is the exit
        code and the untouched snapshot -- the *reason* for an ambiguous
        pattern must not be the benign "--allow-subset would allow this".
        """
        for label, forwarded in (
            ('pattern-eq', ['--pattern=chat/tests/test_protocol.py']),
            ('pattern-short', ['-p', 'chat/tests/test_protocol.py']),
        ):
            with self.subTest(label=label):
                output = self._assert_refused(
                    label, forwarded, own=('--allow-subset',),
                )
                self.assertNotIn('如需强制写入，加 --allow-subset', output)
                self.assertIn('未写入', output)

    def test_pattern_basis_is_named_even_when_the_run_is_empty(self):
        """The refusal must name the basis change, not only the empty run."""
        options, unknown = _parsed('--pattern=chat/tests/test_protocol.py')
        self.assertIn('--pattern', report._subset_reason(options, unknown))
        self.assertTrue(report._subset_is_unpublishable(options, unknown))


if __name__ == '__main__':
    unittest.main()
