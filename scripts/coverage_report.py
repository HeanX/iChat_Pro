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

Exit code is the test run's exit code, so a failing suite still fails the
caller. The Markdown snapshot is only written when the tests pass, so a red
run can never overwrite a green baseline with misleading numbers.
"""

from __future__ import annotations

import argparse
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

# ``manage.py test`` options that narrow the suite to a subset instead of
# merely configuring the run. ``--tag=x`` and ``--tag x`` are both matched.
_FILTER_FLAGS = {
    '--tag',
    '--exclude-tag',
    '--pattern',
    '-p',
    '-k',
}

# Options that consume the following token as their mandatory value, so that a
# value such as ``2`` in ``-v 2`` is not mistaken for a test label.
# Keep in sync with ``manage.py test --help``.
_VALUE_FLAGS = {
    '-v', '--verbosity',
    '-t', '--top-level-directory',
    '--pattern',
    '-p',
    '--tag',
    '--exclude-tag',
    '-k',
    '--settings',
    '--pythonpath',
    '--testrunner',
    '--durations',
}

# Options whose value is optional (``nargs='?'``). Django reads the next token
# as the value only when it is not itself an option; ``--shuffle 1234`` and
# ``--parallel 1`` both do so, and a real test label never looks numeric.
_OPTIONAL_VALUE_FLAGS = {
    '--shuffle',
    '--parallel',
}

# Short flags that always take a value. argparse accepts both ``-k value`` and
# the attached form ``-kvalue``, so both spellings must be recognised.
_SHORT_OPTION_MAP = {
    '-v': '--verbosity',
    '-t': '--top-level-directory',
    '-p': '--pattern',
    '-k': '-k',
}


def _is_optional_value(token: str) -> bool:
    """True when ``token`` is plausibly the optional value of the previous flag."""
    return token.isdigit()


def _classify(item: str) -> tuple[str, bool]:
    """Classify one argv token.

    Returns ``(kind, consumed)`` where ``kind`` is one of ``'label'``,
    ``'filter'`` or ``'option'`` and ``consumed`` says whether this token
    itself also carried an option's value (so no further token is needed).
    """
    if not item.startswith('-') or item == '-':
        return 'label', True

    if item.startswith('--'):
        name, sep, _ = item.partition('=')
        if name in _FILTER_FLAGS:
            return 'filter', True
        return 'option', bool(sep)

    # Short options: ``-k``, ``-k=value`` and ``-kvalue`` are all valid.
    flag = item[:2]
    rest = item[2:]
    canonical = _SHORT_OPTION_MAP.get(flag, flag)
    if canonical in _FILTER_FLAGS or flag in _FILTER_FLAGS:
        return 'filter', True
    if rest:
        # Attached value, e.g. ``-v2`` / ``-ktest_x``.
        return 'option', True
    return 'option', False


def _has_test_labels(extra_args: list[str]) -> bool:
    """True when the arguments narrow the run instead of exercising everything.

    Returns True for explicit test labels (``chat.tests.test_protocol``) and
    for any filtering option (``--tag``, ``--exclude-tag``, ``-k``,
    ``--pattern``, including the attached forms ``-kfoo`` / ``--tag=foo``).
    Both produce a partial measurement, so neither may feed the published
    snapshot.
    """
    pending_mandatory = False
    pending_optional = False
    for item in extra_args:
        if pending_mandatory:
            pending_mandatory = False
            continue
        if pending_optional:
            pending_optional = False
            if _is_optional_value(item):
                continue
            return True

        kind, consumed = _classify(item)
        if kind == 'label':
            return True
        if kind == 'filter':
            return True
        name = item.partition('=')[0] if item.startswith('--') else _SHORT_OPTION_MAP.get(item[:2], item[:2])
        if consumed:
            continue
        if name in _VALUE_FLAGS:
            pending_mandatory = True
        elif name in _OPTIONAL_VALUE_FLAGS:
            pending_optional = True
    return False


def _parallel_requested(extra_args: list[str]) -> int:
    """Largest ``--parallel`` value in the arguments (0 when absent)."""
    requested = 0
    for index, item in enumerate(extra_args):
        if item.startswith('--parallel='):
            value = item.partition('=')[2]
        elif item == '--parallel':
            value = extra_args[index + 1] if index + 1 < len(extra_args) else ''
        else:
            continue
        if value.isdigit():
            requested = max(requested, int(value))
    return requested


def _safe_echo(text: str) -> None:
    """Write to stdout without dying on a legacy console code page.

    Django's test output can contain non-ASCII characters (e.g. the Greek
    epsilon in ``failfast``'s help). Windows consoles are frequently GBK, which
    raises ``UnicodeEncodeError`` on those. Coverage collection must never fail
    because of how a log line renders.
    """
    stream = sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoded = text.encode(stream.encoding or 'utf-8', errors='replace')
        stream.write(encoded.decode(stream.encoding or 'utf-8', errors='replace'))
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
        f'> 代码版本：`{_git_sha()}`（提交前生成时该值为工作区 HEAD）',
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
        '| 模块 | 语句 | 分支 | 合计 |',
        '| --- | ---: | ---: | ---: |',
    ]
    for package, bucket in _per_module(data):
        module_stmt = _percent(bucket['covered_lines'], bucket['num_statements'])
        module_branch = _percent(bucket['covered_branches'], bucket['num_branches'])
        module_total = _percent(
            bucket['covered_lines'] + bucket['covered_branches'],
            bucket['num_statements'] + bucket['num_branches'],
        )
        lines.append(
            f'| `{package}` | {module_stmt:.2f}% | {module_branch:.2f}% '
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
        '- 必须用 `--parallel 1`（默认）。`--parallel >1` 时测试在子进程运行，'
        'coverage 采集不到，脚本会拒绝写快照。',
        '',
    ]
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            '注意：--no-markdown / --allow-subset 必须写在 -- 之前；'
            '-- 之后的参数会原样透传给 manage.py test。'
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

    # A run that names test labels or passes a filtering option measures only
    # a slice of the suite. Publishing that as "the" coverage number would be
    # misleading, so the documented snapshot is reserved for full runs.
    is_subset = _has_test_labels(extra)

    # ``--parallel >1`` forks test processes that coverage cannot see, so the
    # collected totals collapse to a fraction of the real suite. That is not a
    # "subset" -- the tests all run -- it is silently wrong data, so it blocks
    # the snapshot outright (``--allow-subset`` does not override it).
    parallel = _parallel_requested(extra)
    if parallel > 1:
        print(
            f'\n拒绝写入：--parallel {parallel} 会在子进程中运行测试，coverage 采不到覆盖率。\n'
            '请改用 --parallel 1（默认）以获得可信数字。',
            file=sys.stderr,
        )
        exit_code, test_count, skipped = _run_coverage(extra)
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
    # look full. A suspiciously small test count refuses the snapshot as well.
    # This is a hard minimum: ``--allow-subset`` acknowledges a *known* subset
    # but never authorises publishing an implausibly small run.
    too_few = test_count < args.min_tests

    if args.no_markdown:
        return 0

    if too_few:
        print(
            f'\n本次只运行了 {test_count} 个用例（低于 --min-tests {args.min_tests}），'
            '未写入 docs/quality/coverage-report.md。',
            file=sys.stderr,
        )
        return 0

    if is_subset and not args.allow_subset:
        print(
            f'\n本次只运行了 {test_count} 个用例，未写入 docs/quality/coverage-report.md。\n'
            '如需强制写入，加 --allow-subset。',
            file=sys.stderr,
        )
        return 0

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
