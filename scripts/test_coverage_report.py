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


class ParallelRequestedTests(unittest.TestCase):
    """``_parallel_requested`` feeds the guard against unusable coverage data."""

    def test_detects_every_spelling(self):
        self.assertEqual(report._parallel_requested(['--parallel', '2']), 2)
        self.assertEqual(report._parallel_requested(['--parallel=2']), 2)
        self.assertEqual(report._parallel_requested(['--parallel', '4']), 4)

    def test_absent_or_serial_is_zero_or_one(self):
        self.assertEqual(report._parallel_requested([]), 0)
        self.assertEqual(report._parallel_requested(['--parallel', '1']), 1)
        self.assertEqual(report._parallel_requested(['--keepdb']), 0)

    def test_takes_the_maximum(self):
        self.assertEqual(
            report._parallel_requested(['--parallel', '2', '--parallel=8']), 8
        )


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


if __name__ == '__main__':
    unittest.main()
