"""Reproducible coverage run + report for iChat Pro (P4 Test T12 / #193).

Usage (from the repository root, with the project venv):

    .\\.venv\\Scripts\\python.exe -I scripts\\coverage_report.py

    # opt out of writing the Markdown snapshot
    .\\.venv\\Scripts\\python.exe scripts\\coverage_report.py --no-markdown

    # pass extra arguments through to ``manage.py test``
    .\\.venv\\Scripts\\python.exe scripts\\coverage_report.py -- --parallel 1 chat.tests.test_protocol

Both statement and branch coverage are collected (``branch = True`` in
``.coveragerc``). Platform-independent on purpose so the same command can be
wired into CI later without a PowerShell dependency.

Exit codes:
  * tests fail            -> the test run's exit code, no snapshot written
  * --parallel >1 / auto  -> 2, the run still executes but is not traceable
  * subset without opt-in -> 0, snapshot untouched
  * success (full run)    -> 0, snapshot updated

A run is a "subset" when anything about it can narrow the suite or redirect
which settings/runner decide what "the whole suite" is -- including a
``--settings`` (or ``DJANGO_SETTINGS_MODULE``) pointing anywhere but
``ichat_pro.settings``, and a ``TEST_RUNNER`` other than the project's own.
"""

from __future__ import annotations

import os
import sys

# Enter an isolated process before importing Django or the coverage tool.
# Keep the original environment for diagnostics; measurement children receive
# a separate, explicitly constructed environment below.
if __name__ == '__main__' and not sys.flags.isolated:
    # Windows' CRT exec overlay does not propagate the replacement process's
    # exit status reliably. Wait explicitly, and import the launcher only from
    # the interpreter's libraries, without the caller's PYTHONPATH entries.
    _trusted_prefixes = tuple(os.path.normcase(os.path.abspath(prefix)) + os.sep
                              for prefix in (sys.prefix, sys.base_prefix))
    sys.path[:] = [entry for entry in sys.path
                   if os.path.normcase(os.path.abspath(entry)).startswith(_trusted_prefixes)]
    import subprocess as _bootstrap_subprocess

    raise SystemExit(_bootstrap_subprocess.call(
        [sys.executable, '-I', '-X', 'utf8', os.path.abspath(__file__), *sys.argv[1:]]))

import argparse
import fnmatch
import importlib
import json
import math
import platform
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_PATH = REPO_ROOT / 'docs' / 'quality' / 'coverage-report.md'

#: The measurement basis. Coverage reads this implicitly when it is called by
#: hand, but the tooling names it explicitly on every invocation so the run
#: cannot be re-pointed at a different rc behind the script's back (P11).
REPO_COVERAGERC = REPO_ROOT / '.coveragerc'

# ``--pattern``'s argparse default: it matches every test file, so only an
# explicitly different value narrows the run.
_DEFAULT_PATTERN = 'test*.py'

# Only operating-system, application and test-service settings cross the
# measurement boundary. New Python/coverage configuration variables are not
# inherited merely because this version of the tool has not heard of them.
_CHILD_ENV_KEYS = frozenset({
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC',
    'TEMP', 'TMP', 'TMPDIR', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
    'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'USER', 'USERNAME', 'LOGNAME',
    'CI', 'GITHUB_ACTIONS', 'RUNNER_OS', 'ICHAT_TEST_BASH',
    'DATABASE_URL', 'REDIS_URL', 'DJANGO_SECRET_KEY', 'DJANGO_DEBUG',
    'DJANGO_ALLOWED_HOSTS', 'DJANGO_STATIC_ROOT', 'DJANGO_MEDIA_ROOT',
    'DJANGO_CSRF_TRUSTED_ORIGINS', 'CSP_ALLOW_UNSAFE_INLINE_SCRIPT',
    'CSP_ALLOW_UNSAFE_INLINE_STYLE',
})
# The published basis may never shrink. A tolerance here is not a safety margin,
# it is an attack surface: the baseline is read from the committed snapshot and
# publishing rewrites that snapshot, so any positive allowance compounds across
# commits.
#
# The allowance has to be *underused*, not saturated: 5383 * 0.8 floors to 4306,
# and 4306 / 5383 = 0.79993, which a 0.8 floor already refuses. The attack that
# actually worked was a 10% cut per release -- 5383 -> 4844 -> 4359, publishing
# 74.94% -> 83.28% -> 92.54% "target met" while the real covered-line count never
# moved (review R4 F-1; recomputed against this function in
# .review/phase4-remediation-20261026/evidence/audit-cc89c11-ratchet-twostage.py,
# and reproduced through the real CLI in evidence/ratchet-stage1.log).
#
# If the measurement basis genuinely has to change (a source tree really did
# shrink), that is a reviewed decision: edit the `coverage-basis` comment in
# docs/quality/coverage-report.md by hand, record why, and COMMIT it. This
# function reads the snapshot from HEAD, not from the working tree, so an
# uncommitted edit has no effect and the refusal message would be telling the
# operator to do what they just did.
_MIN_BASIS_FRACTION = 1.0
_COVERAGE_BOOTSTRAP = (
    'import sys; from coverage.cmdline import main; '
    'sys.path.insert(0, sys.argv.pop(1)); raise SystemExit(main(sys.argv[1:]))'
)

# The settings module the published snapshot is produced from. Anything else
# (via ``--settings`` or ``DJANGO_SETTINGS_MODULE``) is not comparable: a
# different module can install a different TEST_RUNNER, database or app set,
# which is exactly how a one-test run used to masquerade as the full suite.
_DEFAULT_SETTINGS_MODULE = 'ichat_pro.settings'

# Django's own default, used only if the project settings cannot be imported.
_FRAMEWORK_TEST_RUNNER = 'django.test.runner.DiscoverRunner'

# The runner ``ichat_pro/settings.py`` ships with. Pinned instead of read back
# from the settings module on purpose: if the two ever disagree, the tooling
# must refuse to publish rather than quietly accept whatever runner the module
# happens to name. Changing the project's runner therefore requires changing
# this constant in the same commit -- a deliberate, reviewable act.
_EXPECTED_TEST_RUNNER = 'ichat_pro.test_runner.IChatTestRunner'


# Sentinel for "Django picks its own process count" (bare ``--parallel`` or the
# value ``auto``). Always larger than any real process count.
_AUTO_PARALLEL = 1_000


def _django_test_parser():
    """The exact argparse parser ``manage.py test`` uses.

    Reusing it is the whole point: hand-rolled argv classification kept losing
    to argparse syntax (prefix abbreviations, bundled short options, ``int()``
    coercions) across four review rounds. Parsing with Django's own parser
    makes every spelling behave identically to the real command.
    """
    import django
    from django.core.management.commands.test import Command

    # ``create_parser`` reads settings.TEST_RUNNER, which needs configured
    # settings -- the script normally runs before Django is set up.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    previous = os.environ.get('DJANGO_SETTINGS_MODULE')
    os.environ['DJANGO_SETTINGS_MODULE'] = _DEFAULT_SETTINGS_MODULE
    try:
        if not django.conf.settings.configured:
            django.setup()
        return Command().create_parser('manage.py', 'test')
    finally:
        if previous is None:
            os.environ.pop('DJANGO_SETTINGS_MODULE', None)
        else:
            os.environ['DJANGO_SETTINGS_MODULE'] = previous


def _parse_test_args(extra_args: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse pass-through arguments with Django's own ``test`` parser.

    Returns ``(namespace, unknown)``. Unknown options are reported instead of
    raising, so a caller can refuse them rather than guess.
    """
    parser = _django_test_parser()
    # ``create_parser`` wires Django's add_subparsers-style handling for
    # ``--settings`` etc.; add_argument_defaults keeps this list short.
    parser.add_argument('--ichat-coverage-marker', action='store_true',
                        help=argparse.SUPPRESS)
    return parser.parse_known_args(list(extra_args))


def _is_subset_run(options: argparse.Namespace, unknown: list[str]) -> bool:
    """True when the run cannot speak for the whole suite.

    Thin boolean wrapper over ``_subset_reason`` (the reason itself is what the
    refusal message needs); the precedence between the individual checks lives
    there, in one place.
    """
    return _subset_reason(options, unknown) is not None


def _subset_is_unpublishable(options: argparse.Namespace, unknown: list[str]) -> bool:
    """True when ``--allow-subset`` must NOT be able to publish this run.

    ``--allow-subset`` means "I know I narrowed the run; publish it anyway".
    That is a legitimate statement only for filters that choose *which of the
    discovered tests* to run -- a label, ``--tag``, ``--exclude-tag``, ``-k``.

    It is not a statement about anything that changes the **basis** the numbers
    are computed on:

    * a swapped settings module, ``TEST_RUNNER`` or ``--pythonpath`` (the
      *runner* decides how much to run, and argv cannot even tell how much);
    * ``--pattern`` / ``-t``: they change what discovery reaches at all, so the
      denominator shrinks with the numerator (review P13);
    * a replacement coverage configuration (review P11).

    For those, "I know it is a subset" is not something the operator can know,
    so ``--allow-subset`` must not launder them into the published snapshot
    (review P7). A full run is never unpublishable, whatever options were
    spelled.
    """
    return (
        _coverage_basis_reason() is not None
        or _basis_replacement_reason(options, unknown) is not None
    )


def _pythonpath_is_repo_root(path: str) -> bool:
    """Whether a path points at the repository root itself.

    ``--pythonpath .`` or ``-t <repo>`` insert the directory this script
    already imports from, so they cannot swap ``ichat_pro`` for a shadow copy
    and are not substitutions. Anything else is.
    """
    try:
        candidate = Path(str(path))
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        return candidate.resolve() == REPO_ROOT
    except (OSError, RuntimeError):
        return False


def _coverage_basis_reason(environ=None) -> str | None:
    """Refuse measurement overrides by category, before importing Django.

    The repository root and this interpreter's own library directories are
    redundant PYTHONPATH entries. Every other entry is a substitution, whether
    it contains a file, a package, a zip archive, or no startup hook at all.
    """
    env = os.environ if environ is None else environ
    for key, value in sorted(env.items()):
        name = key.upper()
        if not value or not name.startswith('COVERAGE_') or name == 'COVERAGE_FILE':
            continue
        if name == 'COVERAGE_RCFILE':
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = REPO_ROOT / candidate
            if candidate.resolve() == REPO_COVERAGERC:
                continue
        return f'环境变量 {key} 替换了覆盖率测量基准；请移除该覆盖率配置'
    trusted_paths = {
        REPO_ROOT.resolve(),
        (Path(sys.prefix) / 'Lib' / 'site-packages').resolve(),
        (Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}'
         / 'site-packages').resolve(),
    }
    for entry in (env.get('PYTHONPATH') or '').split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        if candidate.resolve() not in trusted_paths:
            return (f'PYTHONPATH={entry} 替换了覆盖率测量基准；只接受仓库根和当前 '
                    'venv 的 site-packages。影子 coverage 包或 sitecustomize.py / '
                    'sitecustomize 包都不能作为发布基准')
    return None


def _basis_replacement_reason(options: argparse.Namespace, unknown: list[str]) -> str | None:
    """Why the measurement basis (not just the selection) has been replaced.

    Covers everything that changes *what the numbers mean* rather than *which
    already-discovered tests run*: runner/settings/pythonpath/top-level
    substitutions, a changed discovery pattern, and a replaced coverage
    configuration. Kept separate from ``_subset_reason`` so that "is this a
    subset" and "may this subset be published" cannot drift apart again.
    """
    return _substitution_reason(options) or _discovery_basis_reason(options)


def _discovery_basis_reason(options: argparse.Namespace) -> str | None:
    """Why discovery itself would reach a different set of files than default.

    ``--pattern`` is not a filter over discovered tests: it is the glob
    discovery uses, so ``--pattern chat/tests/test_protocol.py`` shrinks the
    denominator along with the numerator. Treating it as a benign filter let
    ``--allow-subset`` publish a one-file "coverage" number (review P13).
    """
    pattern = getattr(options, 'pattern', None)
    if pattern is not None and pattern != _DEFAULT_PATTERN:
        return (
            f'--pattern={pattern} 改变了用例发现基准（默认 {_DEFAULT_PATTERN}），'
            '分母会随之缩小，不能由 --allow-subset 授权发布'
        )
    return None


def _substitution_reason(options: argparse.Namespace) -> str | None:
    """Why the effective settings/runner differs from the project default.

    Independent of any narrowing filter on purpose: an operator may combine a
    swapped runner with a label, and the swapped runner must still block the
    snapshot (review P7). ``None`` means "the defaults are in force".
    """
    explicit_runner = getattr(options, 'testrunner', None)
    if explicit_runner and explicit_runner != _default_test_runner():
        return (
            f'显式 --testrunner={explicit_runner} 替换了默认 runner'
            f'（{_default_test_runner()}）'
        )

    pythonpath = getattr(options, 'pythonpath', None)
    if pythonpath and not _pythonpath_is_repo_root(pythonpath):
        return (
            f'--pythonpath={pythonpath} 会把该目录插到 sys.path 最前面，'
            '子进程解析到的 ichat_pro 包可能不是本仓库里的那一份'
            '（同名 settings/runner 的字符串完全相同，仅比对名字看不出来）'
        )

    top_level = getattr(options, 'top_level', None)
    if top_level and str(top_level) not in ('.', ''):
        # ``-t <repo>`` is the same "import from the repo root" as
        # ``--pythonpath <repo>``; only a *different* root changes discovery.
        # Symmetry with `_pythonpath_is_repo_root` (review P18).
        if not _pythonpath_is_repo_root(top_level):
            return f'--top-level-directory={top_level} 改变了用例发现根目录'

    module, source = _effective_settings_module(options)
    if module != _DEFAULT_SETTINGS_MODULE:
        # Even a module that imports * from ichat_pro.settings is refused: the
        # point of the guard is that the published numbers come from one known
        # configuration, and settings can hide a TEST_RUNNER that narrows the
        # suite (that is precisely how the 1-test snapshot was published).
        return (
            f'生效的 settings 模块是 {module}（来自 {source}），'
            f'不是默认的 {_DEFAULT_SETTINGS_MODULE}。'
            '自定义 settings 会用其中的 TEST_RUNNER 决定跑哪些用例，'
            '本次运行不能代表全量测试。'
        )

    expected_runner = _default_test_runner()
    effective_runner = _test_runner_for(module)
    if effective_runner is None:
        return (
            f'无法导入生效的 settings 模块 {module}，因此无法确认本次运行使用的 '
            f'TEST_RUNNER 是否与默认的 {expected_runner} 一致。'
        )
    if effective_runner != expected_runner:
        return (
            f'生效的 TEST_RUNNER 是 {effective_runner}，与默认的 {expected_runner} '
            '不同；自定义 runner 可以静默地只收集部分用例，本次运行不能代表全量测试。'
        )
    return None


def _subset_reason(options: argparse.Namespace, unknown: list[str]) -> str | None:
    """Why this run cannot speak for the whole suite, or ``None`` if it can.

    The boolean ``_is_subset_run`` exists for callers that only need the
    decision; the snapshot guard needs the *reason* as well, because a refusal
    the operator cannot understand gets worked around instead of fixed.

    Order matters for the message only, not for the decision: the basis is
    checked first so that ``--settings=<alt> chat`` reports the dangerous cause
    (swapped runner) rather than the benign one (a label), and
    ``--pattern=x --tag=y`` reports the changed discovery basis.
    """
    if unknown:
        return f'无法识别的测试参数（Django 会以 exit 2 拒绝）：{" ".join(unknown)}'
    coverage_basis = _coverage_basis_reason()
    if coverage_basis is not None:
        return coverage_basis
    basis = _basis_replacement_reason(options, unknown)
    if basis is not None:
        return basis
    if getattr(options, 'args', None):
        return f'命令行指定了测试标签：{" ".join(str(a) for a in options.args)}'
    if getattr(options, 'tags', None):
        return f'--tag 只运行带指定标签的用例：{", ".join(options.tags)}'
    if getattr(options, 'exclude_tags', None):
        return f'--exclude-tag 排除了带指定标签的用例：{", ".join(options.exclude_tags)}'
    if getattr(options, 'test_name_patterns', None):
        return f'-k 只运行匹配 {options.test_name_patterns} 的用例'
    return None


def _effective_settings_module(options: argparse.Namespace) -> tuple[str, str]:
    """The settings module this run would really use, and where it came from.

    ``manage.py`` exports ``DJANGO_SETTINGS_MODULE`` before Django is set up, so
    the environment variable is the effective value. An explicit ``--settings``
    is what Django's ``handle_default_options`` overwrites it with, so it wins.

    Returns ``(module, source)`` where source is ``'--settings'``,
    ``'DJANGO_SETTINGS_MODULE'`` or ``'default'``. The source matters to the
    refusal message: it tells the operator which knob to stop turning.
    """
    explicit = getattr(options, 'settings', None)
    if explicit:
        return str(explicit), '--settings'
    from_env = os.environ.get('DJANGO_SETTINGS_MODULE')
    if from_env and from_env != _DEFAULT_SETTINGS_MODULE:
        return from_env, 'DJANGO_SETTINGS_MODULE'
    return _DEFAULT_SETTINGS_MODULE, 'default'


def _test_runner_for(settings_module: str) -> str | None:
    """``TEST_RUNNER`` as the *effective* settings module defines it.

    The parser only ever sees ``--testrunner``; a ``TEST_RUNNER`` baked into a
    custom settings module is invisible to argv. That gap is the whole QA-02
    defect: ``--settings=some_alt_settings`` whose ``TEST_RUNNER`` builds a
    single-case suite ran one test, looked like a full run, and overwrote the
    published snapshot. Importing the module read-only (no ``django.setup()``,
    no app loading) and reading the attribute closes it.

    Returns ``None`` when the module cannot be imported here -- the caller must
    then treat the run as unverifiable rather than as a full run.
    """
    if settings_module not in sys.modules and str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        module = importlib.import_module(settings_module)
    except Exception:  # noqa: BLE001 - any import failure is "cannot verify"
        return None
    value = getattr(module, 'TEST_RUNNER', None)
    return str(value) if value else None


def _default_test_runner() -> str:
    """The project's own runner, as the snapshot expects it.

    The pinned constant is authoritative so a settings-module edit cannot
    redefine "the default" in the same breath as it swaps the runner. It falls
    back to what ``ichat_pro.settings`` really says, then to Django's default,
    only to keep the refusal message accurate when the constant drifts.
    """
    if _EXPECTED_TEST_RUNNER:
        return _EXPECTED_TEST_RUNNER
    return _test_runner_for(_DEFAULT_SETTINGS_MODULE) or _FRAMEWORK_TEST_RUNNER


def _subset_refusal(options: argparse.Namespace, unknown: list[str]) -> str | None:
    """Operator-facing refusal text for a subset run, or ``None`` for a full run.

    Delegates to ``_refusal_text`` so this and ``main()`` cannot drift apart
    (review P16); ``test_count`` is left out because no tests have run yet.
    """
    reason = _subset_reason(options, unknown)
    if reason is None:
        return None
    return _refusal_text(
        reason,
        unpublishable=_subset_is_unpublishable(options, unknown),
    )


def _parallel_requested(options: argparse.Namespace) -> int:
    """How many test processes the parsed arguments ask for (0 when absent).

    Values come straight from Django's ``parallel_type`` conversion, so
    ``+2``, ``2_0`` and ``auto`` all behave exactly like the real command.
    ``auto`` (bare ``--parallel`` or the literal string) is reported as a
    sentinel because Django then picks its own count, which is >1.
    """
    value = getattr(options, 'parallel', 0)
    if value in (None, 0):
        return 0
    if value == 'auto':
        return _AUTO_PARALLEL
    return int(value)


def _safe_echo(text: str, stream=None) -> None:
    """Write to a console stream without dying on a legacy code page.

    Django's test output and the guard messages contain non-ASCII characters
    that GBK consoles cannot encode. A refusal reason is the one thing the
    operator must be able to read, so both stdout and stderr go through here.

    When the stream cannot represent the text, the bytes are written as UTF-8
    instead of being replaced with ``?``: a redirected (piped) stream has no
    code page of its own, so ASCII-only mangling would turn the whole refusal
    into mojibake the operator cannot act on. Decoding that UTF-8 instead of
    the locale is what ``*_echo`` consumers should do.
    """
    stream = stream or sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        buffer = getattr(stream, 'buffer', None)
        if buffer is not None:
            buffer.write(text.encode('utf-8'))
        else:  # e.g. StringIO under test: keep it legible, never raise
            encoding = stream.encoding or 'utf-8'
            stream.write(
                text.encode(encoding, errors='replace').decode(encoding, errors='replace')
            )
    stream.flush()


def _coverage_child_env(data_file: Path | None = None) -> dict:
    """Construct the measurement environment from an explicit allowlist."""
    env = {key: value for key, value in os.environ.items()
           if key.upper() in _CHILD_ENV_KEYS}
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONNOUSERSITE'] = '1'
    env['DJANGO_SETTINGS_MODULE'] = _DEFAULT_SETTINGS_MODULE
    if data_file is not None:
        # Pin the data file to this run: a stale .coverage elsewhere would be
        # read back as if it were this run's numbers.
        env['COVERAGE_FILE'] = str(data_file)
    return env


def _coverage_command(action: str, *arguments: str) -> list[str]:
    # Resolve coverage from the isolated venv first, then add exactly the
    # trusted repository root for manage.py/Django. -I's safe_path otherwise
    # prevents coverage's script runner from adding even this trusted path.
    return [sys.executable, '-I', '-X', 'utf8', '-c', _COVERAGE_BOOTSTRAP,
            str(REPO_ROOT), action,
            f'--rcfile={REPO_COVERAGERC}', *arguments]


def _run_coverage(extra_args: list[str], data_file: Path | None = None) -> tuple[int, int, int]:
    """Run the suite under coverage.

    Returns ``(exit_code, test_count, skipped)``. The test count is parsed from
    Django's own ``Ran N test(s)`` line and is used as an independent guard: an
    option that filters the suite without being recognised as a filter would
    still show up as a suspiciously small count.

    ``--pythonpath`` is refused before this runs (see ``_substitution_reason``):
    it changes which ``ichat_pro`` package the child resolves, so the child may
    execute a different runner than the one this process inspected.

    ``--rcfile`` is passed explicitly (review P11). Refusing a foreign
    ``COVERAGE_RCFILE`` is the primary guard, but the rc also decides *what is
    measured*; pinning it here means a future relaxed check cannot silently
    change the basis, and the three coverage commands cannot disagree.
    """
    command = _coverage_command('run', 'manage.py', 'test', *extra_args)
    print(f'$ {" ".join(command)}', flush=True)

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=_coverage_child_env(data_file or REPO_ROOT / '.coverage'),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    assert process.stdout is not None
    ran_pattern = re.compile(r'^Ran (\d+) tests? in ', re.MULTILINE)
    skip_pattern = re.compile(r'^OK \(skipped=(\d+)\)', re.MULTILINE)
    collected: list[str] = []
    for line in process.stdout:
        # Echo through a UTF-8-safe writer: the console may be a legacy code
        # page (e.g. GBK on Windows) that cannot encode Django's output.
        _safe_echo(line)
        collected.append(line)
    exit_code = process.wait()

    output = ''.join(collected)
    match = ran_pattern.search(output)
    test_count = int(match.group(1)) if match else 0
    skip_match = skip_pattern.search(output)
    skipped = int(skip_match.group(1)) if skip_match else 0
    return exit_code, test_count, skipped


def _refusal_text(reason: str, *, unpublishable: bool, test_count: int | None = None) -> str:
    """The one place that words a snapshot refusal.

    Review P16: ``main()`` and ``_subset_refusal()`` used to build two slightly
    different messages, so a fix in one path could miss the other. Both now go
    through here, and the wording depends only on *why* the run cannot publish.
    """
    lead = (
        f'本次只运行了 {test_count} 个用例，'
        if test_count is not None
        else ''
    )
    tail = (
        '\n这类改动改变了覆盖率数字的基准，无法从命令行判断实际统计范围，'
        '--allow-subset 也不能绕过。'
        if unpublishable
        else '\n如需强制写入，加 --allow-subset。'
    )
    return (
        f'{lead}未写入 docs/quality/coverage-report.md。\n'
        f'原因：{reason}{tail}'
    )


def _snapshot_decision(
    *,
    is_subset: bool,
    allow_subset: bool,
    test_count: int,
    min_tests: int,
    parallel: int = 1,
    subset_reason: str = '',
    subset_is_unpublishable: bool = False,
) -> tuple[str, str]:
    """Decide whether a run may overwrite the published coverage snapshot.

    Pure so the guards can be tested without running the whole suite.

    Returns ``('write', '')`` or ``('refuse', reason)``. The ``min_tests`` floor
    is absolute: it protects against a run that executed nothing at all, so
    neither ``--allow-subset`` nor ``--min-tests 0`` can get past it. A
    ``parallel`` above 1 is refused outright -- the tests do run, but coverage
    cannot see the worker processes, so the totals would be silently wrong.

    ``subset_reason`` is the readable *why* produced by ``_subset_reason``; it
    is embedded in the refusal so the operator learns which option made the run
    untrustworthy instead of only that it was refused.

    ``subset_is_unpublishable`` marks the runs whose *basis* changed (settings,
    runner, pythonpath, pattern, coverage config), whose statistical scope is
    not knowable from the command line at all. ``--allow-subset`` acknowledges
    a subset the operator *chose*; it must not bless a run that decided on its
    own how much to measure (same reasoning as ``--parallel``).
    """
    if parallel > 1:
        return (
            'refuse',
            f'拒绝写入：--parallel {parallel} 会在子进程中运行测试，'
            'coverage 采不到覆盖率。\n请改用 --parallel 1（默认）以获得可信数字。',
        )
    floor = max(min_tests, 1)
    if test_count < floor:
        return (
            'refuse',
            f'本次只运行了 {test_count} 个用例（低于下限 {floor}），'
            '未写入 docs/quality/coverage-report.md。',
        )
    if is_subset and (subset_is_unpublishable or not allow_subset):
        return (
            'refuse',
            _refusal_text(
                subset_reason or '本次运行只覆盖了部分用例',
                unpublishable=subset_is_unpublishable,
                test_count=test_count,
            ),
        )
    return 'write', ''


def _collect_json(data_file: Path | None = None) -> dict:
    """Export the current .coverage database to JSON and parse it.

    ``--rcfile`` is pinned and the environment is constructed for the same
    reason as in ``_run_coverage``: this step decides which recorded files end
    up in the published totals, so it must read the same basis and the same
    data file the run wrote.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / 'coverage.json'
        subprocess.run(
            _coverage_command('json', '-o', str(target), '-q'),
            cwd=REPO_ROOT,
            env=_coverage_child_env(data_file or REPO_ROOT / '.coverage'),
            check=True,
        )
        return json.loads(target.read_text(encoding='utf-8'))


def _git_read(*arguments: str) -> str:
    return subprocess.run(
        ['git', *arguments], cwd=REPO_ROOT, env=_coverage_child_env(),
        capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def _source_basis() -> tuple[set[str], int, list[str]]:
    """Independently count statements in the configured production sources.

    ``analysis2`` reads source syntax; it does not trust the exported JSON's
    totals or the set of files reached by the test runner.
    """
    import coverage

    analyzer = coverage.Coverage(config_file=str(REPO_COVERAGERC), data_file=None)
    sources = analyzer.get_option('run:source')
    if set(sources or ()) != {'accounts', 'chat', 'ichat_pro'}:
        raise ValueError('覆盖率 source 必须是 accounts/chat/ichat_pro')
    omissions = analyzer.get_option('run:omit') or []
    paths = [path for source in sources for path in (REPO_ROOT / source).rglob('*.py')
             if not _omitted(path.relative_to(REPO_ROOT).as_posix(), omissions)]
    files = {path.relative_to(REPO_ROOT).as_posix() for path in paths}
    try:
        statements = sum(len(analyzer.analysis2(str(path))[1]) for path in paths)
    finally:
        analyzer.get_data().close(force=True)
    return files, statements, omissions


def _omitted(relative: str, patterns: list[str]) -> bool:
    absolute = (REPO_ROOT / relative).as_posix()
    return any(fnmatch.fnmatchcase(relative, pattern.replace('\\', '/'))
               or fnmatch.fnmatchcase(absolute, pattern.replace('\\', '/'))
               for pattern in patterns)


def _published_basis(omissions: list[str]) -> dict[str, int]:
    """Read the committed baseline, never a mutable working-tree snapshot.

    The snapshot is read from ``HEAD`` rather than the working tree so that a
    half-written or hand-edited copy cannot move the baseline. Because
    publishing rewrites this same file, the baseline therefore advances one
    commit at a time — which is exactly why the comparison below is a hard
    floor (``_MIN_BASIS_FRACTION = 1.0``) instead of a tolerance: any allowance
    would compound across releases (review R4 F-1).

    Consequence for the documented escape hatch: an operator who edits the
    ``coverage-basis`` comment but does not commit it still sees the old
    baseline, and therefore still gets refused. That is deliberate (it fails
    closed and forces a reviewable diff), but it means the procedure has to say
    "commit it" — see docs/quality/coverage-integrity.md.
    """
    text = _git_read('show', 'HEAD:docs/quality/coverage-report.md')
    match = re.search(r'<!-- coverage-basis: (.+?) -->', text)
    if match:
        basis = json.loads(match.group(1))
    else:
        # Older snapshots contain raw statement/branch totals but no file
        # count. Recover that count from the same committed source tree.
        basis = {}
        for label, key in (('语句', 'num_statements'), ('分支', 'num_branches')):
            match = re.search(r'^\| ' + label + r' \| \d+ / (\d+) \|', text, re.M)
            if match is None:
                raise ValueError('已发布快照缺少可核验的原始分母')
            basis[key] = int(match.group(1))
        names = _git_read('ls-tree', '-r', '--name-only', 'HEAD', '--',
                          'accounts', 'chat', 'ichat_pro').splitlines()
        basis['num_files'] = sum(name.endswith('.py') and not _omitted(name, omissions)
                                 for name in names)
    for key in ('num_statements', 'num_branches', 'num_files'):
        if type(basis.get(key)) is not int or basis[key] < 0:
            raise ValueError(f'已发布快照的 {key} 无效')
    if not basis['num_statements'] or not basis['num_files']:
        raise ValueError('已发布快照的语句数/文件数不能为零')
    return basis


def _basis_problem(data: dict, baseline: dict, files: set[str], statements: int) -> str | None:
    """Validate raw counts, source inventory and the historical denominator."""
    try:
        totals = data['totals']
        measured = set()
        sums = dict.fromkeys(('num_statements', 'covered_lines',
                              'num_branches', 'covered_branches'), 0)
        for filename, result in data['files'].items():
            path = Path(filename.replace('\\', '/'))
            if not path.is_absolute():
                path = REPO_ROOT / path
            relative = path.resolve().relative_to(REPO_ROOT).as_posix()
            if relative in measured:
                raise ValueError('JSON 含重复文件')
            measured.add(relative)
            summary = result['summary']
            for key in sums:
                value = summary[key]
                if type(value) is not int or value < 0:
                    raise ValueError(f'{key} 必须是非负整数')
                sums[key] += value
            if (summary['covered_lines'] > summary['num_statements']
                    or summary['covered_branches'] > summary['num_branches']):
                raise ValueError('覆盖数不能大于分母')
        if measured != files:
            return f'统计文件集合与源码不符（应有 {len(files)}，实际 {len(measured)}）'
        if sums['num_statements'] != statements or not statements:
            return f'语句分母与独立源码分析不符（应有 {statements}，实际 {sums["num_statements"]}）'
        for key, value in sums.items():
            if type(totals[key]) is not int or totals[key] != value:
                return f'汇总 {key} 与逐文件原始计数不一致'
        current = {**sums, 'num_files': len(measured)}
        for key in ('num_statements', 'num_branches', 'num_files'):
            if current[key] < baseline[key] * _MIN_BASIS_FRACTION:
                return (f'{key} 分母从已发布的 {baseline[key]} 降至 {current[key]}；'
                        '统计范围不允许缩小（哪怕 1%）。若源码确实变少了，'
                        '请人工修改 docs/quality/coverage-report.md 的 coverage-basis '
                        '注释、记录理由并**提交**（本工具读的是 git HEAD 中的快照，'
                        '未提交的修改不生效），不要用本工具静默改写基线')
        actual = _percent(sums['covered_lines'] + sums['covered_branches'],
                          sums['num_statements'] + sums['num_branches'])
        percent = float(totals['percent_covered'])
        if not math.isfinite(percent) or abs(percent - actual) > 0.02:
            return '汇总百分比与原始计数不一致'
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as error:
        return f'覆盖率 JSON 不可核验：{error}'
    return None


def _denominator_reason(data: dict) -> str | None:
    try:
        files, statements, omissions = _source_basis()
        return _basis_problem(data, _published_basis(omissions), files, statements)
    except Exception as error:
        # Missing Git history/config/source analysis is not permission to
        # replace a published snapshot with unverifiable numbers.
        return f'无法核验覆盖率分母：{type(error).__name__}: {error}'


def _write_snapshot(text: str, original: bytes | None) -> None:
    MARKDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = MARKDOWN_PATH.read_bytes() if MARKDOWN_PATH.exists() else None
    if current != original:
        raise ValueError('快照在测量期间已被另一进程修改，拒绝覆盖')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=MARKDOWN_PATH.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, MARKDOWN_PATH)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _percent(covered: int, total: int) -> float:
    return 100.0 * covered / total if total else 100.0


def _non_negative_int(value: str) -> int:
    """argparse type for counts that must not be negative."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f'需要整数，收到 {value!r}') from error
    if parsed < 0:
        raise argparse.ArgumentTypeError(f'不能为负数，收到 {parsed}')
    return parsed


def _per_module(data: dict) -> list[tuple[str, dict]]:
    """Group per-file totals by top-level package (accounts/chat/ichat_pro)."""
    buckets: dict[str, dict[str, int]] = {}
    for filename, entry in data['files'].items():
        rel = Path(filename)
        try:
            relative = rel.relative_to(REPO_ROOT)
        except ValueError:
            relative = rel
        package = relative.parts[0] if relative.parts else '(root)'
        bucket = buckets.setdefault(
            package,
            {'num_statements': 0, 'covered_lines': 0, 'num_branches': 0, 'covered_branches': 0},
        )
        summary = entry['summary']
        for key in ('num_statements', 'covered_lines', 'num_branches', 'covered_branches'):
            bucket[key] += summary[key]
    return sorted(buckets.items())


def _git_sha() -> str:
    """Short HEAD SHA, or ``unknown`` outside a git checkout."""
    try:
        value = _git_read('rev-parse', '--short=12', 'HEAD').strip()
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return value or 'unknown'


def _environment_line() -> str:
    """Python / Django / coverage / database backend, recorded for re-runs."""
    import django

    import coverage

    backend = 'unknown'
    try:
        # When run as ``python scripts/coverage_report.py``, sys.path[0] is the
        # script directory, so the project package is not importable yet.
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')
        django.setup()
        from django.conf import settings

        backend = settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]
    except Exception:  # noqa: BLE001 - reporting must not fail the script
        backend = 'unknown'
    return (
        f'Python {platform.python_version()} / Django {django.get_version()} '
        f'/ coverage {coverage.__version__} / DB `{backend}`'
    )


def _render_markdown(
    data: dict,
    exit_code: int,
    extra_args: list[str],
    is_subset: bool = False,
    test_count: int | None = None,
    skipped: int = 0,
) -> str:
    totals = data['totals']
    stmt = _percent(totals['covered_lines'], totals['num_statements'])
    branch = _percent(totals['covered_branches'], totals['num_branches'])
    combined = totals['percent_covered']
    generated_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    lines = [
        '# 覆盖率报告（P4 Test T12 / #193）',
        '',
        '<!-- coverage-basis: ' + json.dumps({
            'num_statements': totals['num_statements'],
            'num_branches': totals['num_branches'], 'num_files': len(data['files']),
        }, sort_keys=True) + ' -->',
        '',
        '> 本文件由 `scripts/coverage_report.py` 生成，**不要手工编辑**。',
        f'> 生成时间：{generated_at}',
        f'> 代码版本：`{_git_sha()}`（被测代码所在提交；本报告随其后的提交一并'
        '入库，两者 accounts/chat/ichat_pro 代码一致）',
        f'> 环境：{_environment_line()}',
        f'> 用例数：{test_count if test_count is not None else "未知"}'
        f'（skip {skipped}）',
        f'> 测试命令：`python -I scripts/coverage_report.py'
        f'{" --allow-subset" if is_subset else ""} --'
        f'{" " + " ".join(extra_args) if extra_args else ""}`',
        f'> 测试结果：退出码 {exit_code}',
        '',
    ]
    if is_subset:
        lines += [
            '> ⚠️ 本次为**部分用例**运行（`--allow-subset`），数字不代表全量覆盖率。',
            '',
        ]
    lines += [
        '## 汇总',
        '',
        '| 指标 | 覆盖 / 总数 | 覆盖率 |',
        '| --- | ---: | ---: |',
        f'| 语句 | {totals["covered_lines"]} / {totals["num_statements"]} | {stmt:.2f}% |',
        f'| 分支 | {totals["covered_branches"]} / {totals["num_branches"]} | {branch:.2f}% |',
        f'| **合计（语句+分支）** | — | **{combined:.2f}%** |',
        '',
        '## 分模块',
        '',
        '| 模块 | 语句 covered/total | 语句 | 分支 covered/total | 分支 | 合计 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for package, bucket in _per_module(data):
        module_stmt = _percent(bucket['covered_lines'], bucket['num_statements'])
        module_branch = _percent(bucket['covered_branches'], bucket['num_branches'])
        module_total = _percent(
            bucket['covered_lines'] + bucket['covered_branches'],
            bucket['num_statements'] + bucket['num_branches'],
        )
        lines.append(
            f'| `{package}` '
            f'| {bucket["covered_lines"]}/{bucket["num_statements"]} | {module_stmt:.2f}% '
            f'| {bucket["covered_branches"]}/{bucket["num_branches"]} | {module_branch:.2f}% '
            f'| {module_total:.2f}% |'
        )

    lines += [
        '',
        '## 与需求目标对照',
        '',
        '目标取自 `docs/phase4/requirements.md`（NEW-TST-006/007/008）。'
        '**#193 的验收包含达到评审目标**；阈值门禁的实现另属 P4 T46（#173），'
        '不能据此将 #193 的未达标状态改写为完成。',
        '',
        '| 需求 | 指标 | 目标 | 实测 | 结论 |',
        '| --- | --- | ---: | ---: | --- |',
        f'| NEW-TST-006 | 核心后端语句覆盖率 | ≥85% | {stmt:.2f}% | '
        f'{"已达标" if stmt >= 85 else "**未达标**"} |',
        f'| NEW-TST-007 | 核心后端分支覆盖率 | ≥75% | {branch:.2f}% | '
        f'{"已达标" if branch >= 75 else "**未达标**"} |',
        '| NEW-TST-008 | 高风险逻辑覆盖 | ≥90% | 未统计 | **未验证**（高风险面清单尚未定义，需 P4 Test T11） |',
        '',
        '## 口径说明',
        '',
        '- 统计口径见仓库根目录 `.coveragerc`：`branch = True`，来源为 `accounts`/`chat`/`ichat_pro`。',
        '- 已排除：`*/migrations/*`（生成代码）、`*/tests/*`、`*/tests.py`（测试自身）、'
        '`manage.py`、`ichat_pro/wsgi.py`、`ichat_pro/asgi.py`。',
        '- `ichat_pro/test_runner.py` 是线上启用的 `TEST_RUNNER`，属产品代码，**计入**统计。',
        '- 仅本机 SQLite 结果；PostgreSQL 并发用例在 SQLite 下静默 skip，'
        '因此并发分支的真实覆盖需在 CI 的 PostgreSQL job 中复核。',
        '- 必须用 `--parallel 1`（默认）。`--parallel`（含 `auto`、缩写 `--para=N`）'
        '会让测试在子进程运行，coverage 采集不到，脚本会拒绝写快照。',
        '- 带测试标签或过滤选项（`--tag`/`--exclude-tag`/`-k`/`--pattern`，含 '
        '`--pat` 这类前缀缩写与 `-kfoo` 这类紧凑写法）或自定义 `--testrunner` '
        '的运行只代表子集，默认不写快照。',
        '- 分支覆盖率原始数见"分模块"表的 covered/total 列，可单独核算。',
        '- 发布前独立核对源码文件集合与语句数；语句/分支/文件分母**一律不得小于**'
        'Git 中已发布快照（不接受任何百分比容差，因为发布本身会改写基线，容差会跨发布'
        '复利），`--allow-subset` 也不能豁免。统计范围确需缩小时须人工修改本文件的'
        '`coverage-basis` 注释、记录理由并提交（工具读 git HEAD，未提交不生效）。',
        '',
    ]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            '注意：所有脚本自有选项（--no-markdown / --allow-subset / --min-tests）'
            '必须写在 -- 之前且不带位置参数；-- 之后的内容原样透传给 manage.py test。'
            '写错位置会被 Django 以 exit 2 拒绝，不会写快照。'
        ),
    )
    parser.add_argument(
        '--no-markdown',
        action='store_true',
        help='只跑覆盖率并打印，不写 docs/quality/coverage-report.md',
    )
    parser.add_argument(
        '--allow-subset',
        action='store_true',
        help='即使只跑了部分测试用例也写 Markdown（默认只在全量运行时写）',
    )
    parser.add_argument(
        '--min-tests',
        type=_non_negative_int,
        default=1,
        metavar='N',
        help=(
            '写入 Markdown 所需的最少测试数（默认 1）。这是硬性下限，'
            '--allow-subset 不能绕过它，用于拦截被过滤成全量假象的运行。'
        ),
    )
    parser.add_argument(
        'test_args',
        nargs=argparse.REMAINDER,
        help='透传给 manage.py test 的参数（前面加 -- 分隔）',
    )
    args = parser.parse_args()

    # Refuse an untrusted environment before constructing Django's parser:
    # parser creation itself imports settings and the configured test runner.
    environment_reason = _coverage_basis_reason()
    if environment_reason:
        _safe_echo(_refusal_text(environment_reason, unpublishable=True) + '\n', sys.stderr)
        return 2
    os.chdir(REPO_ROOT)

    extra = [item for item in args.test_args if item != '--']
    if not extra:
        extra = ['--parallel', '1']

    # Classify the pass-through arguments with Django's own parser, so every
    # argparse spelling (abbreviations, bundled short options, int coercions)
    # means exactly what it means in the real command.
    options, unknown = _parse_test_args(extra)
    subset_reason = _subset_reason(options, unknown)
    unpublishable = _subset_is_unpublishable(options, unknown)
    parallel = _parallel_requested(options)

    if unpublishable:
        _safe_echo(_refusal_text(subset_reason or '覆盖率测量基准已改变',
                                 unpublishable=True) + '\n', sys.stderr)
        return 2
    original = MARKDOWN_PATH.read_bytes() if MARKDOWN_PATH.exists() else None
    with tempfile.TemporaryDirectory(prefix='ichat-coverage-') as directory:
        return _measure_and_publish(args, extra, subset_reason, parallel,
                                    Path(directory) / '.coverage', original)


def _measure_and_publish(args, extra, subset_reason, parallel, data_file, original) -> int:
    is_subset = subset_reason is not None

    # ``--parallel >1`` forks test processes that coverage cannot see, so the
    # collected totals collapse to a fraction of the real suite. That is not a
    # "subset" -- the tests all run -- it is silently wrong data, so it blocks
    # the snapshot outright (``--allow-subset`` does not override it). The run
    # is still executed so the user sees the real test result.
    if parallel > 1:
        _decision, reason = _snapshot_decision(
            is_subset=is_subset,
            allow_subset=args.allow_subset,
            test_count=0,
            min_tests=args.min_tests,
            parallel=parallel,
            subset_reason=subset_reason or '',
        )
        _safe_echo(f'\n{reason}\n', sys.stderr)
        exit_code, _test_count, _skipped = _run_coverage(extra, data_file)
        return exit_code if exit_code != 0 else 2

    exit_code, test_count, skipped = _run_coverage(extra, data_file)
    if exit_code != 0:
        print(f'\n测试未通过（退出码 {exit_code}），不生成覆盖率报告。', file=sys.stderr)
        return exit_code

    report = subprocess.run(
        _coverage_command('report', '-m'),
        cwd=REPO_ROOT,
        env=_coverage_child_env(data_file),
        check=False,
    )
    if report.returncode != 0:
        return report.returncode

    # Second, independent guard: an unrecognised filter can make a partial run
    # look full, so the snapshot decision is a pure function that both the
    # script and its tests call.
    if args.no_markdown:
        return 0

    decision, reason = _snapshot_decision(
        is_subset=is_subset,
        allow_subset=args.allow_subset,
        test_count=test_count,
        min_tests=args.min_tests,
        parallel=parallel,
        subset_reason=subset_reason or '',
        subset_is_unpublishable=False,
    )
    if decision == 'refuse':
        _safe_echo(f'\n{reason}\n', sys.stderr)
        return 0

    data = _collect_json(data_file)
    reason = _denominator_reason(data)
    if reason:
        _safe_echo(_refusal_text(reason, unpublishable=True) + '\n', sys.stderr)
        return 2
    try:
        _write_snapshot(_render_markdown(data, exit_code, extra, is_subset,
                                         test_count, skipped), original)
    except (OSError, ValueError) as error:
        _safe_echo(f'未写入 docs/quality/coverage-report.md：{error}\n', sys.stderr)
        return 2
    print(f'\n已写入 {MARKDOWN_PATH.relative_to(REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
