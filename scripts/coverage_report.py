"""Reproducible coverage run + report for iChat Pro (P4 Test T12 / #193).

Usage (from the repository root, with the project venv):

    .\\.venv\\Scripts\\python.exe scripts\\coverage_report.py

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

import argparse
import importlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_PATH = REPO_ROOT / 'docs' / 'quality' / 'coverage-report.md'

# ``--pattern``'s argparse default: it matches every test file, so only an
# explicitly different value narrows the run.
_DEFAULT_PATTERN = 'test*.py'

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
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')
    if not django.conf.settings.configured:
        django.setup()
    return Command().create_parser('manage.py', 'test')


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
    """True when the parsed arguments narrow the suite to a partial run.

    Thin boolean wrapper over ``_subset_reason`` (the reason itself is what the
    refusal message needs); the precedence between the individual checks lives
    there, in one place.
    """
    return _subset_reason(options, unknown) is not None


def _subset_is_unpublishable(options: argparse.Namespace, unknown: list[str]) -> bool:
    """True when this is a *subset* that ``--allow-subset`` must not publish.

    Plain filters (``--tag``, ``-k``, labels) are a deliberate, understood
    subset: the run succeeded and the historical exit-0 behaviour is kept.
    ``--parallel`` already exits 2 elsewhere. A swapped settings module or
    ``TEST_RUNNER`` is different on two counts:

    * the operator asked for a full snapshot and silently got none, which is
      how a stale snapshot got mistaken for a fresh one -- so this exits
      non-zero and CI can see it;
    * ``--allow-subset`` must not override it, because the runner -- not the
      operator -- decided how much of the suite to run, and the command line
      cannot tell how much that was.

    A full run is never unpublishable, whatever options were spelled.
    """
    if not _is_subset_run(options, unknown):
        return False
    if unknown or getattr(options, 'args', None):
        return False
    if getattr(options, 'tags', None) or getattr(options, 'exclude_tags', None):
        return False
    if getattr(options, 'test_name_patterns', None):
        return False
    pattern = getattr(options, 'pattern', None)
    if pattern is not None and pattern != _DEFAULT_PATTERN:
        return False
    # Anything left is runner/settings-driven, or an explicit --testrunner.
    return True


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


def _subset_reason(options: argparse.Namespace, unknown: list[str]) -> str | None:
    """Why this run cannot speak for the whole suite, or ``None`` if it can.

    The boolean ``_is_subset_run`` exists for callers that only need the
    decision; the snapshot guard needs the *reason* as well, because a refusal
    the operator cannot understand gets worked around instead of fixed.
    """
    if unknown:
        return f'无法识别的测试参数（Django 会以 exit 2 拒绝）：{" ".join(unknown)}'
    if getattr(options, 'args', None):
        return f'命令行指定了测试标签：{" ".join(str(a) for a in options.args)}'
    if getattr(options, 'tags', None):
        return f'--tag 只运行带指定标签的用例：{", ".join(options.tags)}'
    if getattr(options, 'exclude_tags', None):
        return f'--exclude-tag 排除了带指定标签的用例：{", ".join(options.exclude_tags)}'
    if getattr(options, 'test_name_patterns', None):
        return f'-k 只运行匹配 {options.test_name_patterns} 的用例'
    pattern = getattr(options, 'pattern', None)
    if pattern is not None and pattern != _DEFAULT_PATTERN:
        return f'--pattern={pattern} 会改变发现范围（默认 {_DEFAULT_PATTERN}）'
    if getattr(options, 'testrunner', None):
        return (
            f'显式 --testrunner={options.testrunner} 可能只运行部分用例；'
            f'快照必须来自默认 runner（{_default_test_runner()}）'
        )

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


def _is_subset_run(options: argparse.Namespace, unknown: list[str]) -> bool:
    """True when the run cannot speak for the whole suite.

    Judged on *parsed* values, so every spelling argparse accepts is handled by
    construction: labels, ``--tag``/``--exclude-tag``/``-k`` in any spelling,
    an explicitly given ``--pattern`` (whose default ``test*.py`` matches
    everything), any unrecognised option (which Django itself would reject with
    exit 2), and -- the QA-02 fix -- an effective settings module or
    ``TEST_RUNNER`` that differs from the project defaults.
    """
    return _subset_reason(options, unknown) is not None


def _subset_refusal(options: argparse.Namespace, unknown: list[str]) -> str | None:
    """Operator-facing refusal text for a subset run, or ``None`` for a full run."""
    reason = _subset_reason(options, unknown)
    if reason is None:
        return None
    return (
        f'拒绝写入 docs/quality/coverage-report.md：{reason}\n'
        '本次运行只代表子集，覆盖率数字不能当作全量快照。\n'
        f'如需全量统计，请去掉上述选项后重跑（默认 settings 为 {_DEFAULT_SETTINGS_MODULE}）。'
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


def _run_coverage(extra_args: list[str]) -> tuple[int, int, int]:
    """Run the suite under coverage.

    Returns ``(exit_code, test_count)``. The test count is parsed from Django's
    own ``Ran N test(s)`` line and is used as an independent guard: an option
    that filters the suite without being recognised as a filter would still
    show up as a suspiciously small count.
    """
    command = [
        sys.executable,
        '-m',
        'coverage',
        'run',
        'manage.py',
        'test',
        *extra_args,
    ]
    print(f'$ {" ".join(command)}', flush=True)

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
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

    ``subset_is_unpublishable`` marks the settings/runner substitutions, whose
    suite size is not knowable from the command line at all. ``--allow-subset``
    acknowledges a subset the operator *chose*; it must not bless a runner that
    decided on its own how much to run (same reasoning as ``--parallel``).
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
        detail = f'\n原因：{subset_reason}' if subset_reason else ''
        hint = (
            '\n这类替代无法从命令行判断实际跑了多少用例，'
            '--allow-subset 也不能绕过。'
            if subset_is_unpublishable
            else '\n如需强制写入，加 --allow-subset。'
        )
        return (
            'refuse',
            f'本次只运行了 {test_count} 个用例，未写入 docs/quality/coverage-report.md。'
            f'{detail}{hint}',
        )
    return 'write', ''


def _collect_json() -> dict:
    """Export the current .coverage database to JSON and parse it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / 'coverage.json'
        subprocess.run(
            [sys.executable, '-m', 'coverage', 'json', '-o', str(target), '-q'],
            cwd=REPO_ROOT,
            check=True,
        )
        return json.loads(target.read_text(encoding='utf-8'))


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
        completed = subprocess.run(
            ['git', 'rev-parse', '--short=12', 'HEAD'],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return 'unknown'
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else 'unknown'


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
        '> 本文件由 `scripts/coverage_report.py` 生成，**不要手工编辑**。',
        f'> 生成时间：{generated_at}',
        f'> 代码版本：`{_git_sha()}`（被测代码所在提交；本报告随其后的提交一并'
        '入库，两者 accounts/chat/ichat_pro 代码一致）',
        f'> 环境：{_environment_line()}',
        f'> 用例数：{test_count if test_count is not None else "未知"}'
        f'（skip {skipped}）',
        f'> 测试命令：`python -m coverage run manage.py test'
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
        '本任务只负责**建立可重复统计**，阈值门禁属 P4 T46（#173）。',
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

    extra = [item for item in args.test_args if item != '--']
    if not extra:
        extra = ['--parallel', '1']

    # Classify the pass-through arguments with Django's own parser, so every
    # argparse spelling (abbreviations, bundled short options, int coercions)
    # means exactly what it means in the real command.
    options, unknown = _parse_test_args(extra)
    subset_reason = _subset_reason(options, unknown)
    is_subset = subset_reason is not None
    unpublishable = _subset_is_unpublishable(options, unknown)
    parallel = _parallel_requested(options)

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
        exit_code, _test_count, _skipped = _run_coverage(extra)
        return exit_code if exit_code != 0 else 2

    exit_code, test_count, skipped = _run_coverage(extra)
    if exit_code != 0:
        print(f'\n测试未通过（退出码 {exit_code}），不生成覆盖率报告。', file=sys.stderr)
        return exit_code

    report = subprocess.run(
        [sys.executable, '-m', 'coverage', 'report', '-m'],
        cwd=REPO_ROOT,
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
        subset_is_unpublishable=unpublishable,
    )
    if decision == 'refuse':
        _safe_echo(f'\n{reason}\n', sys.stderr)
        return 2 if unpublishable else 0

    data = _collect_json()
    MARKDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_PATH.write_text(
        _render_markdown(data, exit_code, extra, is_subset, test_count, skipped),
        encoding='utf-8',
        newline='\n',
    )
    print(f'\n已写入 {MARKDOWN_PATH.relative_to(REPO_ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
