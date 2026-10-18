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
import os
import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
