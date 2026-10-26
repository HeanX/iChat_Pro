"""Deployment tooling for iChat Pro (scripts, templates and their tests).

This file exists so ``deploy`` is a regular package. Without it the directory
is a PEP 420 namespace package, and Django's default unittest discovery — which
only recurses into directories that have ``__init__.py`` — silently ignores
``deploy/tests/``. The ops regression suite (P4 T12/T23/T49/T50) would then
never run under ``manage.py test`` even though every individual module passes
when named explicitly (review finding QA-01 / C11, 2026-10-24).
"""
