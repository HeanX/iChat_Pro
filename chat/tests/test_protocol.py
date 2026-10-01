"""P4 T31 tests: error-code registry, API 401 middleware (R-06), protocol
version gate, and the multi-connection presence counter (R-05)."""

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.test.client import Client

from chat.consumers import ChatConsumer
from chat.errors import REGISTRY, get_error
from chat.models import Conversation, ConversationMember, UserPresence
from ichat_pro.asgi import application


def _session_headers(client):
    from django.conf import settings as dj_settings

    session_id = client.cookies[dj_settings.SESSION_COOKIE_NAME].value
    return [
        (b"origin", b"http://testserver"),
        (b"cookie", f"{dj_settings.SESSION_COOKIE_NAME}={session_id}".encode()),
    ]


class ErrorRegistryTests(SimpleTestCase):
    def test_known_codes_have_contract(self):
        self.assertEqual(REGISTRY["invalid_payload"].http_status, 400)
        self.assertEqual(REGISTRY["conversation_not_found"].http_status, 404)
        self.assertEqual(REGISTRY["conversation_forbidden"].http_status, 403)
        self.assertEqual(REGISTRY["membership_conflict"].http_status, 409)
        self.assertEqual(REGISTRY["authentication_required"].http_status, 401)
        self.assertEqual(REGISTRY["idempotency_conflict"].http_status, 409)
        self.assertFalse(REGISTRY["idempotency_conflict"].retryable)
        self.assertTrue(REGISTRY["storage_unavailable"].retryable)
        self.assertTrue(REGISTRY["database_busy"].retryable)

    def test_unknown_code_falls_back_defensively(self):
        error = get_error("never_registered_code")
        self.assertEqual(error.http_status, 400)
        self.assertFalse(error.retryable)
        self.assertEqual(error.code, "never_registered_code")

    def test_database_busy_is_renamed_storage_unavailable(self):
        self.assertIn("storage_unavailable", REGISTRY)
        # legacy code kept registered only during the 1.0 compat window
        self.assertIn("database_busy", REGISTRY)


class ApiAuthMiddlewareTests(TestCase):
    """R-06: unauthenticated /api/ requests get 401 JSON, never HTML."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("proto_user", password="pass1234")

    def test_unauthenticated_api_gets_401_json(self):
        response = self.client.get("/api/conversations/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "authentication_required")
        self.assertEqual(response.json()["detail"], REGISTRY["authentication_required"].message)

    def test_unauthenticated_non_api_paths_still_render_html(self):
        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])

    def test_authenticated_api_does_not_get_401(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/conversations/")
        self.assertNotEqual(response.status_code, 401)


class ProtocolVersionGateTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("proto_ws", password="pass1234")
        client = Client()
        client.force_login(self.user)
        self.headers = _session_headers(client)

    def _communicator(self):
        return WebsocketCommunicator(application, "/ws/chat/", headers=self.headers)

    def test_request_without_version_is_accepted_as_1_0(self):
        async def run():
            comm = self._communicator()
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from()  # connection.ready
            await comm.send_json_to({"event": "connection.ping", "request_id": "p1"})
            pong = await comm.receive_json_from()
            self.assertEqual(pong["event"], "connection.pong")
            self.assertEqual(pong["protocol_version"], "1.0")
            await comm.disconnect()

        async_to_sync(run)()

    def test_explicit_supported_version_is_accepted(self):
        async def run():
            comm = self._communicator()
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from()
            await comm.send_json_to({
                "protocol_version": "1.0",
                "event": "connection.ping",
                "request_id": "p2",
            })
            pong = await comm.receive_json_from()
            self.assertEqual(pong["event"], "connection.pong")
            await comm.disconnect()

        async_to_sync(run)()

    def test_unsupported_version_gets_error_and_close_4003(self):
        async def run():
            comm = self._communicator()
            connected, _ = await comm.connect()
            self.assertTrue(connected)
            await comm.receive_json_from()
            await comm.send_json_to({
                "protocol_version": "9.9",
                "event": "connection.ping",
                "request_id": "p3",
            })
            error = await comm.receive_json_from()
            self.assertEqual(error["event"], "error")
            self.assertEqual(error["data"]["code"], "unsupported_protocol_version")
            self.assertFalse(error["data"]["retryable"])
            with self.assertRaises(Exception):
                await comm.receive_json_from(timeout=2)

        async_to_sync(run)()


class MultiConnectionPresenceTests(TransactionTestCase):
    """R-05: presence flips only on the first connect / last disconnect."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("proto_multi", password="pass1234")
        client = Client()
        client.force_login(self.user)
        self.headers = _session_headers(client)

    def _presence(self):
        return UserPresence.objects.get(user=self.user)

    def test_extra_tabs_do_not_fake_offline(self):
        async def run():
            comm1 = WebsocketCommunicator(application, "/ws/chat/", headers=self.headers)
            connected, _ = await comm1.connect()
            self.assertTrue(connected)
            await comm1.receive_json_from()

            comm2 = WebsocketCommunicator(application, "/ws/chat/", headers=self.headers)
            connected, _ = await comm2.connect()
            self.assertTrue(connected)
            await comm2.receive_json_from()

            await comm2.disconnect()
            presence = await database_sync_to_async(self._presence)()
            self.assertTrue(presence.is_online)

            await comm1.disconnect()
            presence = await database_sync_to_async(self._presence)()
            self.assertFalse(presence.is_online)

        async_to_sync(run)()

    def test_connection_counter_is_balanced_after_reconnect_cycle(self):
        async def run():
            for _ in range(3):
                comm = WebsocketCommunicator(application, "/ws/chat/", headers=self.headers)
                connected, _ = await comm.connect()
                self.assertTrue(connected)
                await comm.receive_json_from()
                await comm.disconnect()
                presence = await database_sync_to_async(self._presence)()
                self.assertFalse(presence.is_online)
            key = ChatConsumer._connection_count_key(self.user.pk)
            self.assertIsNone(cache.get(key))

        async_to_sync(run)()
