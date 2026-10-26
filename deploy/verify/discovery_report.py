"""Report what Django's DEFAULT discovery collects, including deploy tests.

This is the measurement behind the QA-01 / C11 half of the finding: the new
ops suite only counts as wired into CI if `manage.py test` (no labels) picks
it up. Before deploy/__init__.py existed the script would report
deploy_test_ids: [] for a tree whose five modules pass 82 tests when named.

Run with the project on PYTHONPATH and DJANGO_SETTINGS_MODULE set (manage.py
does the latter); this module is invoked as `python -m deploy.verify...` from
the repo root, mirroring how CI calls the test runner.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ichat_pro.settings")
    repo = Path(__file__).resolve().parents[2]
    os.chdir(repo)
    sys.path.insert(0, str(repo))
    import django

    django.setup()

    from django.test.utils import get_runner

    runner_class = get_runner(django.conf.settings)
    runner = runner_class(verbosity=0, interactive=False)
    runner.setup_test_environment()
    suite = runner.build_suite([])
    ids = [test.id() for test in flatten(suite)]
    deploy_ids = [i for i in ids if i.startswith("deploy.")]

    report = {
        "total_collected": len(ids),
        "deploy_collected": len(deploy_ids),
        "deploy_modules": sorted({i.rsplit(".", 2)[0] for i in deploy_ids}),
        "deploy_test_ids_sample": deploy_ids[:5],
    }
    print(json.dumps(report, indent=2))

    out = repo / ".review" / "phase4-remediation-20261026" / "evidence" / "ops-discovery.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not deploy_ids:
        print(
            "FAIL: default discovery collected NO deploy tests; the ops suite "
            "is not wired into `manage.py test`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
