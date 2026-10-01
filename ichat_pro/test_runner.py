"""Test runner that neutralizes channels' per-call close_old_connections().

``channels.db.database_sync_to_async`` runs ``close_old_connections()`` on
the calling thread before and after every wrapped call. Under Django's
``TestCase`` the shared connection sits inside the test's atomic block with
``autocommit=False``, so ``close_if_unusable_or_obsolete`` treats it as an
unrestored autocommit setting and closes the connection mid-transaction
(``closed_in_transaction=True``); every later statement then fails with
psycopg "the connection is closed".

Django's own request-started/finished signalling is not involved here, so
this bypasses the framework's usual test-time protection. Production is
unaffected: those calls happen outside atomic blocks where autocommit
matches the settings. Tests rely on the framework-managed connection
exclusively, so disabling the channel-layer hygiene call is safe.
"""

from django.test.runner import DiscoverRunner


class IChatTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        import channels.db as channels_db

        channels_db.close_old_connections = lambda: None
