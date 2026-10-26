"""Prove that the deploy/ops test modules are really collected (QA-01).

Why this exists
---------------
``deploy/tests/`` is a normal package but ``deploy/`` was a PEP 420 namespace
package, and Django's default ``manage.py test`` only recurses into directories
that are packages. The whole cloud/ops regression suite (P4 T12/T23/T49/T50)
was therefore invisible to the default command: 404 tests collected,
``deploy_test_ids == []`` (review evidence
``.review/phase4-full-review-20261024/cloud-ops-default-discovery.json``).
Naming the modules explicitly collects them.

What it checks, and why each one is a *hard* failure
---------------------------------------------------
1. Every required module imports by name. A missing module is a failure, never
   a silent zero.
2. bash is on ``PATH`` (or ``ICHAT_TEST_BASH``). Without a shell the ops
   regressions cannot run, and a skipped check must never look green.
3. No collected test is already marked to skip. This is measured on the
   collected objects (``__unittest_skip__``), not on an attribute name, so
   renaming a module's ``HAS_BASH`` cannot dodge it (review P5).
4. Explicitly naming the modules collects a non-zero ``deploy.*`` count, and
   every required module contributes at least one test.
5. **Default discovery collects them too.** This is the acceptance
   requirement: ``manage.py test`` with no labels is what CI runs and what the
   review measured. Round 1 computed this number and only printed it, so the
   guard said "QA-01 OK ... 0" for the exact regression it exists to catch --
   ``deploy/`` losing ``__init__.py`` (review P2). It is now a failure.

``--print-modules`` prints the module list CI should run, and refuses when the
package layout is incomplete (review P4), so CI can use the output directly
without a second, weaker check.

Run locally (from the repository root, with the project venv):

    python scripts/check_deploy_collection.py

Exit codes: 0 = default discovery and explicit collection both see the suite
with bash present; 1 = refused, with a readable reason on stderr.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The ops regression suite that must exist. Kept in one place so CI and this
# guard cannot drift. These five are *required*: a missing one is a failure.
DEPLOY_TEST_MODULES = (
    'deploy.tests.test_deploy_gate',
    'deploy.tests.test_isolation_check',
    'deploy.tests.test_monitor',
    'deploy.tests.test_release_audit',
    'deploy.tests.test_smoke_gate',
)


def extra_deploy_modules() -> list[str]:
    """Every other ``deploy.tests.test_*`` module that exists on disk.

    The five above are the suite this guard was written for, but the ops tree
    keeps growing (e.g. ``test_deploy_config``). Naming only the original five
    would silently stop running a module added later, which is the same class
    of blind spot as implicit discovery -- just with a shorter list. Anything
    module-shaped in ``deploy/tests/`` is therefore collected too.
    """
    tests_dir = REPO_ROOT / 'deploy' / 'tests'
    if not tests_dir.is_dir():
        return []
    found = []
    for path in sorted(tests_dir.glob('test_*.py')):
        module = f'deploy.tests.{path.stem}'
        if module not in DEPLOY_TEST_MODULES:
            found.append(module)
    return found


def flatten(suite):
    """Yield every test id in a (possibly nested) unittest suite.

    Recursion is keyed on ``unittest.TestSuite`` rather than on "has
    ``__iter__``": a test case that happens to define ``__iter__`` (or any
    other iterable passed in by mistake) would otherwise be descended into and
    silently contribute no ids at all.
    """
    import unittest

    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item.id()


def _skip_marked_tests(suite) -> list[tuple[str, str]]:
    """Collected tests that are already marked to skip, with the reason.

    Review P5: the round-1 guard sniffed a ``HAS_BASH`` attribute by name, so a
    module that renamed it looked fine while still skipping. Attribute names are
    an implementation detail; the skip itself is not. ``unittest.skipUnless``
    sets ``__unittest_skip__`` on the test (or its class) when the condition is
    false at import time, so looking at the *collected objects* measures the
    real thing and cannot be dodged by renaming.

    This is why the check runs after collection: it is the collection result
    that carries the marks.
    """
    import unittest

    marked: list[tuple[str, str]] = []

    def walk(items):
        for item in items:
            if isinstance(item, unittest.TestSuite):
                walk(item)
                continue
            method = getattr(item, getattr(item, '_testMethodName', ''), None)
            if getattr(item, '__unittest_skip__', False):
                marked.append(
                    (item.id(), str(getattr(item, '__unittest_skip_why__', '')))
                )
            elif getattr(method, '__unittest_skip__', False):
                marked.append(
                    (item.id(), str(getattr(method, '__unittest_skip_why__', '')))
                )

    walk(suite)
    return marked


def _print_modules() -> int:
    """Print the module list CI should run, or fail if the suite is missing.

    Review P4: this used to print the five names and exit 0 even when
    ``deploy/tests/`` did not exist, so it could not stand in as evidence that
    the modules are there. It now verifies the package layout first and refuses
    with a readable reason, which means CI's ``MODULES=$(...)`` fails fast
    instead of handing ``manage.py test`` labels that cannot be imported.
    """
    tests_dir = REPO_ROOT / 'deploy' / 'tests'
    missing = []
    if not (REPO_ROOT / 'deploy' / '__init__.py').is_file():
        missing.append(
            'deploy/__init__.py 不存在，deploy 仍是 PEP 420 namespace package，'
            '默认发现不会递归进 deploy/tests/'
        )
    if not (tests_dir / '__init__.py').is_file():
        missing.append('deploy/tests/__init__.py 不存在（缺少测试包）')
    for name in DEPLOY_TEST_MODULES:
        module_path = REPO_ROOT / (name.replace('.', '/') + '.py')
        if not module_path.is_file():
            missing.append(f'{name} 不存在（{module_path}）')

    if missing:
        for problem in missing:
            print(f'ERROR {problem}', file=sys.stderr)
        print(
            '\nQA-01: 运维测试模块清单不完整，不能把不存在的模块交给 manage.py test。',
            file=sys.stderr,
        )
        return 1

    print(' '.join(list(DEPLOY_TEST_MODULES) + extra_deploy_modules()))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')

    if '--print-modules' in argv:
        return _print_modules()

    import django
    from django.test.runner import DiscoverRunner

    django.setup()

    import importlib

    problems: list[str] = []
    modules = {}
    for name in DEPLOY_TEST_MODULES:
        try:
            modules[name] = importlib.import_module(name)
        except Exception as error:  # noqa: BLE001 - the message is the point
            problems.append(f'{name}: 无法导入（{type(error).__name__}: {error}）')

    # Modules the ops tree added later are collected as well, but their absence
    # is not an error -- only the five above are the required contract.
    extras = []
    for name in extra_deploy_modules():
        try:
            modules[name] = importlib.import_module(name)
            extras.append(name)
        except Exception as error:  # noqa: BLE001 - surfaced, not fatal
            problems.append(f'{name}: 无法导入（{type(error).__name__}: {error}）')

    if problems:
        for problem in problems:
            print(f'ERROR {problem}', file=sys.stderr)
        print(
            '\nQA-01: 运维测试模块没有被全部显式收集。常见原因：\n'
            '  * 本分支还没并入 A 的 deploy/tests/ 与 deploy/__init__.py'
            '（归属 A，C 不重复造）；\n'
            '  * 五个必需模块之一被改名或删除；\n'
            '  * bash 缺失，依赖 shell 的运维用例会 skip。\n'
            'CI 不会用隐式发现来代替它们，也不会把 skip 当成通过。',
            file=sys.stderr,
        )
        return 1

    # The current ops helpers honour ICHAT_TEST_BASH even outside PATH. Check
    # that the selected interpreter really executes, rather than assuming its
    # presence means the shell regressions can run.
    bash = os.environ.get('ICHAT_TEST_BASH') or shutil.which('bash')
    if not bash or not Path(bash).is_file():
        problems.append(
            'bash 不在 PATH 上（也未设置 ICHAT_TEST_BASH）；'
            '依赖 shell 的运维用例会 skip，skip 不算通过。'
            '请安装 bash 或设置 ICHAT_TEST_BASH 为可执行文件的完整路径'
        )
    else:
        try:
            probe = subprocess.run([bash, '-c', 'exit 0'], capture_output=True, timeout=30)
            if probe.returncode:
                problems.append(f'bash 探测失败，退出码 {probe.returncode}；skip 不算通过')
        except (OSError, subprocess.SubprocessError) as error:
            problems.append(f'bash 无法执行：{error}；skip 不算通过')

    # Review P14: Django's default discovery starts at the *current directory*
    # (`build_suite([])` passes `start_dir='.'`), while REPO_ROOT comes from this
    # file's location. Running the guard from `chat/` therefore reported
    # "默认发现 0" for a perfectly good tree. `top_level` alone does not fix it,
    # so the cwd is pinned for the duration of the collection and restored
    # afterwards.
    runner = DiscoverRunner(verbosity=0, interactive=False)
    requested = list(DEPLOY_TEST_MODULES) + extras
    previous_cwd = os.getcwd()
    try:
        os.chdir(REPO_ROOT)
        explicit_suite = runner.build_suite(requested)
        discovered_default = list(flatten(runner.build_suite([])))
    finally:
        os.chdir(previous_cwd)

    collected = list(flatten(explicit_suite))
    deploy_ids = [test_id for test_id in collected if test_id.startswith('deploy.')]

    # Skip detection on the collected objects (review P5): a test that is
    # already marked to skip when the runner builds the suite will never run,
    # whatever attribute the module happens to export. Attribute sniffing was
    # the round-1 bug -- `test_deploy_gate` never exported HAS_BASH at all.
    for test_id, why in _skip_marked_tests(explicit_suite):
        problems.append(
            f'{test_id}: 收集时已被标记 skip（{why or "无原因"}），skip 不算通过'
        )

    per_module = {
        name: sum(1 for test_id in deploy_ids if test_id.startswith(name + '.'))
        for name in requested
    }
    empty_modules = [
        name for name in DEPLOY_TEST_MODULES if per_module.get(name, 0) == 0
    ]

    if not deploy_ids:
        problems.append(
            '显式指定运维模块后收集到的 deploy.* 用例数为 0；'
            'CI 不会把“收集不到”当成通过'
        )
    for name in empty_modules:
        problems.append(f'{name}: 收集到 0 个用例')

    # Default discovery is the acceptance requirement, not a nice-to-have.
    # `manage.py test` with no labels is what CI runs and what the review
    # measured; the regression this guard exists for (`deploy/` losing its
    # `__init__.py`, so `deploy.tests` becomes a namespace package) is visible
    # *only* here. Review P2: it used to be reported and ignored, so the guard
    # printed "QA-01 OK ... 0" for exactly the failure it was written to catch.
    # (`discovered_default` was collected above with the cwd pinned to REPO_ROOT.)
    default_deploy_ids = [
        test_id for test_id in discovered_default
        if test_id.startswith('deploy.')
    ]
    default_roots = {test_id.split('.')[0] for test_id in discovered_default}
    missing_from_default = [
        name for name in DEPLOY_TEST_MODULES
        if not any(
            test_id.startswith(name + '.') for test_id in default_deploy_ids
        )
    ]
    if not default_deploy_ids:
        problems.append(
            f'默认发现（manage.py test，无标签）收集到的 deploy.* 用例数为 0'
            f'（{REPO_ROOT / "deploy" / "__init__.py"} 是否存在：'
            f'{(REPO_ROOT / "deploy" / "__init__.py").is_file()}）。'
            'deploy/ 若是 PEP 420 namespace package，默认发现不会递归进 '
            'deploy/tests/，运维套件永远不会在默认 CI 入口执行'
        )
    for name in missing_from_default:
        problems.append(
            f'{name}: 默认发现没有收集到（显式点名能收到，说明是发现范围问题）'
        )

    report = {
        'modules': list(DEPLOY_TEST_MODULES),
        'extra_modules': extras,
        'bash': bash,
        'explicit_collected_total': len(collected),
        'deploy_test_ids': len(deploy_ids),
        'per_module': per_module,
        'default_discovery_deploy_ids': len(default_deploy_ids),
        'default_discovery_roots': sorted(default_roots),
        'default_discovery_missing_modules': missing_from_default,
        'deploy_package_is_regular': (
            REPO_ROOT / 'deploy' / '__init__.py'
        ).is_file(),
        'problems': problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if problems:
        for problem in problems:
            print(f'ERROR {problem}', file=sys.stderr)
        print(
            '\nQA-01: 默认 CI 入口没有完整收集运维测试。'
            '显式点名不算完成验收——`manage.py test`（无标签）必须自己收到它们。',
            file=sys.stderr,
        )
        return 1
    extra_note = f'，另有 {len(extras)} 个新增模块一并收集' if extras else ''
    print(
        f'\nQA-01 OK: 显式 {len(deploy_ids)} 个 deploy.* 用例{extra_note}；'
        f'默认发现同样收集到 {len(default_deploy_ids)} 个。'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
