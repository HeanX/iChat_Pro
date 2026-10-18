"""Unit tests for ``scripts/coverage_report.py`` (P4 Test T12 / #193).

Run with the project venv:

    .\\.venv\\Scripts\\python.exe -m unittest scripts.test_coverage_report -v

These tests exist because the subset guard in the reporting script has twice
regressed in review (first ``--tag``/``--pattern``, then attached short forms
``-kfoo``/``-pfoo``), silently letting a partial run overwrite the published
coverage snapshot. Every spelling the argument parser accepts is pinned here.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage_report as report  # noqa: E402


class HasTestLabelsTests(unittest.TestCase):
    """``_has_test_labels`` must flag every narrowed run, and only those."""

    def test_plain_full_run_arguments_are_not_subsets(self):
        full_runs = [
            [],
            ['--parallel', '1'],
            ['--parallel=1'],
            ['--parallel'],
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
            ['--parallel', '2', '--shuffle', '99'],
            ['-t', '.'],
            ['--top-level-directory', '.'],
            ['--settings', 'ichat_pro.settings'],
            ['--pythonpath', '.'],
        ]
        for arguments in full_runs:
            with self.subTest(arguments=arguments):
                self.assertFalse(report._has_test_labels(arguments))

    def test_explicit_test_labels_are_subsets(self):
        for arguments in (
            ['chat'],
            ['chat.tests.test_protocol'],
            ['--parallel', '1', 'chat.tests.test_protocol'],
            ['accounts', 'chat'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(report._has_test_labels(arguments))

    def test_long_filter_flags_are_subsets(self):
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
                self.assertTrue(report._has_test_labels(arguments))

    def test_short_filter_flags_are_subsets_in_every_spelling(self):
        # The regression that slipped through review twice: attached values.
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
                self.assertTrue(report._has_test_labels(arguments))

    def test_filter_value_is_not_mistaken_for_a_label(self):
        # ``--tag slow`` consumes ``slow``; it must not be read as a label and
        # short-circuit before the filter check.
        self.assertTrue(report._has_test_labels(['--exclude-tag', 'slow']))

    def test_argparse_abbreviations_are_subsets(self):
        # argparse accepts unambiguous prefixes of long options, so every
        # prefix Django accepts must be treated as a filter. ``--pat`` was the
        # exact spelling that slipped through round three's review.
        for arguments in (
            ['--pat=test_protocol*.py'],
            ['--patt=test_protocol*.py'],
            ['--patt', 'test_protocol*.py'],
            ['--ta=slow'],
            ['--exclu=slow'],
            ['--exclude=slow'],
        ):
            with self.subTest(arguments=arguments):
                self.assertTrue(report._has_test_labels(arguments))

    def test_abbreviation_of_a_non_filter_option_is_not_a_subset(self):
        for arguments in (
            ['--keepdb'],
            ['--noin'],
            ['--para=1'],
            ['--verb=2'],
            ['--shuff=1234'],
        ):
            with self.subTest(arguments=arguments):
                self.assertFalse(report._has_test_labels(arguments))


class ParallelRequestedTests(unittest.TestCase):
    """``_parallel_requested`` feeds the guard against unusable coverage data."""

    def test_detects_every_spelling(self):
        self.assertEqual(report._parallel_requested(['--parallel', '2']), 2)
        self.assertEqual(report._parallel_requested(['--parallel=2']), 2)
        self.assertEqual(report._parallel_requested(['--parallel', '4']), 4)
        # argparse allows abbreviations; ``--para=2`` really does run in parallel.
        self.assertEqual(report._parallel_requested(['--para=2']), 2)
        self.assertEqual(report._parallel_requested(['--para', '2']), 2)

    def test_absent_or_serial_is_zero_or_one(self):
        self.assertEqual(report._parallel_requested([]), 0)
        self.assertEqual(report._parallel_requested(['--parallel', '1']), 1)
        self.assertEqual(report._parallel_requested(['--keepdb']), 0)

    def test_bare_and_auto_mean_many(self):
        # Bare ``--parallel`` has const="auto"; Django then picks its own count.
        self.assertGreater(report._parallel_requested(['--parallel']), 1)
        self.assertGreater(report._parallel_requested(['--parallel=auto']), 1)
        self.assertGreater(report._parallel_requested(['--parallel', 'auto']), 1)
        self.assertGreater(report._parallel_requested(['--para=auto']), 1)

    def test_bare_parallel_also_counts_as_a_subset(self):
        # Guards the case where the value is "auto" rather than a number.
        self.assertTrue(report._has_test_labels(['--parallel', 'auto']))

    def test_takes_the_maximum(self):
        self.assertEqual(
            report._parallel_requested(['--parallel', '2', '--parallel=8']), 8
        )


class ResolveOptionTests(unittest.TestCase):
    """Prefix resolution must mirror argparse, not approximate it."""

    def test_unambiguous_prefixes_resolve(self):
        self.assertEqual(report._resolve_option('--pat'), '--pattern')
        self.assertEqual(report._resolve_option('--patt'), '--pattern')
        self.assertEqual(report._resolve_option('--parallel'), '--parallel')
        self.assertEqual(report._resolve_option('--para'), '--parallel')

    def test_ambiguous_or_unknown_prefixes_are_returned_as_is(self):
        # ``--pa`` is ambiguous between --parallel and --pattern.
        self.assertEqual(report._resolve_option('--pa'), '--pa')
        self.assertEqual(report._resolve_option('--nonsense'), '--nonsense')


class ClassifyTests(unittest.TestCase):
    """Pin the token classification the guards depend on."""

    def test_labels(self):
        for token in ('chat', 'chat.tests.test_protocol', '-'):
            with self.subTest(token=token):
                self.assertEqual(report._classify(token)[0], 'label')

    def test_filters(self):
        for token in ('--tag', '--tag=x', '--exclude-tag', '-k', '-kfoo', '-p', '-px*'):
            with self.subTest(token=token):
                self.assertEqual(report._classify(token)[0], 'filter')

    def test_plain_options(self):
        for token in ('--parallel', '--parallel=2', '-v', '-v2', '--keepdb'):
            with self.subTest(token=token):
                self.assertEqual(report._classify(token)[0], 'option')

    def test_attached_value_is_consumed(self):
        self.assertTrue(report._classify('-v2')[1])
        self.assertTrue(report._classify('--parallel=2')[1])
        self.assertFalse(report._classify('-v')[1])
        self.assertFalse(report._classify('--parallel')[1])


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


class ParallelGuardTests(unittest.TestCase):
    """``--parallel`` above 1 must never publish numbers coverage cannot see."""

    def test_any_parallel_above_one_is_detected(self):
        for arguments in (
            ['--parallel', '2'],
            ['--parallel=2'],
            ['--parallel', '8'],
            ['--para=4'],
            ['--parallel'],
            ['--parallel=auto'],
        ):
            with self.subTest(arguments=arguments):
                self.assertGreater(report._parallel_requested(arguments), 1)

    def test_serial_and_absent_are_allowed(self):
        for arguments in ([], ['--parallel', '1'], ['--parallel=1'], ['--keepdb']):
            with self.subTest(arguments=arguments):
                self.assertLessEqual(report._parallel_requested(arguments), 1)


if __name__ == '__main__':
    unittest.main()
