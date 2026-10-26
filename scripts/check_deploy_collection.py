"""Prove that the deploy/ops test modules are really collected (QA-01).

Why this exists
---------------
``deploy/tests/`` is a normal package but ``deploy/`` was a PEP 420 namespace
package, and Django's default ``manage.py test`` only recurses into directories
that are packages. The whole cloud/ops regression suite (P4 T12/T23/T49/T50)
was therefore invisible to the default command: 404 tests collected,
``deploy_test_ids == []`` (review evidence
``.review/phase4-full-review-20261024/cloud-ops-default-discovery.json``).
Naming the five modules explicitly collects 82.

This script is the CI guard *around* that: it does not rely on implicit
discovery, and it fails loudly instead of quietly collecting nothing. It

  1. imports each of the five modules by name -- a missing module is a
     failure, never a silent zero;
  2. requires bash both on ``PATH`` and as each module's own ``HAS_BASH``
     flag, because those modules ``skipUnless(HAS_BASH)``. A skipped suite
     reports green, which is exactly the false evidence the ops review called
     out ("Bash 缺失导致 skip 不算通过");
  3. builds the suite through Django's own ``DiscoverRunner`` and requires the
     collected ``deploy.*`` ids to be non-empty (the count itself is *not*
     hardcoded -- it changes every time a regression is added);
  4. prints a JSON summary so the CI log carries the real numbers.

Run locally (from the repository root, with the project venv):

    python scripts/check_deploy_collection.py

Exit codes: 0 = all five modules collected with bash present, 1 = refused.
"""

from __future__ import annotations

import json
import os
import shutil
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ichat_pro.settings')

    if '--print-modules' in argv:
        # CI asks for the module list instead of hardcoding it a second time:
        # the five required modules plus anything the ops tree added later.
        print(' '.join(list(DEPLOY_TEST_MODULES) + extra_deploy_modules()))
        return 0

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
            '\nQA-01: 运维测试没有被显式收集。五种可能：\n'
            '  * 本分支还没并入 A 的 deploy/tests/ 与 deploy/__init__.py'
            '（归属 A，C 不重复造）；\n'
            '  * 五个模块之一被改名或删除；\n'
            '  * bash 缺失，运维用例会整体 skip。\n'
            'CI 不会用隐式发现来代替它们，也不会把 skip 当成通过。',
            file=sys.stderr,
        )
        return 1

    # bash is a hard requirement: every module skips itself without it.
    bash = os.environ.get('ICHAT_TEST_BASH') or shutil.which('bash')
    if not bash or not Path(bash).is_file():
        problems.append(
            'bash 不在 PATH 上（也未设置 ICHAT_TEST_BASH）；'
            '五个运维模块会整体 skip，skip 不算通过'
        )
    else:
        for name, module in modules.items():
            has_bash = getattr(module, 'HAS_BASH', None)
            if has_bash is False:
                problems.append(f'{name}: HAS_BASH 为 False，shell 用例会被 skip')

    runner = DiscoverRunner(verbosity=0, interactive=False)
    requested = list(DEPLOY_TEST_MODULES) + extras
    collected = list(flatten(runner.build_suite(requested)))
    deploy_ids = [test_id for test_id in collected if test_id.startswith('deploy.')]

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

    # Informational only: this branch may not carry A's `deploy/__init__.py`
    # yet, so default discovery is reported, not asserted. The main agent
    # re-checks it after A's commit is integrated.
    default_ids = [
        test_id for test_id in flatten(runner.build_suite([]))
        if test_id.startswith('deploy.')
    ]

    report = {
        'modules': list(DEPLOY_TEST_MODULES),
        'extra_modules': extras,
        'bash': bash,
        'explicit_collected_total': len(collected),
        'deploy_test_ids': len(deploy_ids),
        'per_module': per_module,
        'default_discovery_deploy_ids': len(default_ids),
        'default_discovery_covers_ops_suite': len(default_ids) >= len(deploy_ids),
        'problems': problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if problems:
        for problem in problems:
            print(f'ERROR {problem}', file=sys.stderr)
        return 1
    extra_note = f'，另有 {len(extras)} 个新增模块一并收集' if extras else ''
    print(
        f'\nQA-01 OK: {len(deploy_ids)} 个 deploy.* 用例已显式收集{extra_note}'
        f'（默认发现当前收集到 {len(default_ids)} 个）。'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
