"""Verification helpers for the deploy tooling.

These modules measure things about the test suite itself (for example whether
the ops tests are reachable through the default ``manage.py test`` discovery
entry point). They are not tests and are not collected by unittest discovery:
the package has no ``test*`` modules.
"""
