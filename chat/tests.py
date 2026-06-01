from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from ichat_pro.asgi import application


class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='websocket-user',
            password='test-password',
        )
        self.session_cookie_header = self._session_cookie_header()

    def test_authenticated_user_can_connect(self):
        async_to_sync(self._assert_authenticated_user_can_connect)()

    def test_anonymous_user_is_rejected(self):
        async_to_sync(self._assert_anonymous_user_is_rejected)()

    def test_ping_returns_pong(self):
        async_to_sync(self._assert_ping_returns_pong)()

    def test_unimplemented_event_returns_error(self):
        async_to_sync(self._assert_unimplemented_event_returns_error)()

    def test_invalid_json_returns_error(self):
        async_to_sync(self._assert_invalid_json_returns_error)()

    def test_cross_site_origin_is_rejected(self):
        async_to_sync(self._assert_cross_site_origin_is_rejected)()

    def test_disconnect_removes_user_group(self):
        async_to_sync(self._assert_disconnect_removes_user_group)()

    def _session_cookie_header(self):
        self.client.force_login(self.user)
        session_id = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        return [
            (b'origin', b'http://testserver'),
            (b'cookie', f'{settings.SESSION_COOKIE_NAME}={session_id}'.encode()),
        ]

    async def _assert_authenticated_user_can_connect(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        ready = await communicator.receive_json_from()
        self.assertEqual(ready['protocol_version'], '1.0')
        self.assertEqual(ready['event'], 'connection.ready')
        self.assertEqual(ready['data']['user_id'], self.user.pk)
        self.assertEqual(ready['data']['heartbeat_interval_seconds'], 30)

        await communicator.disconnect()

    async def _assert_anonymous_user_is_rejected(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=[(b'origin', b'http://testserver')],
        )
        connected, close_code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)

    async def _assert_ping_returns_pong(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_json_to({
            'protocol_version': '1.0',
            'event': 'connection.ping',
            'request_id': 'ping-request',
            'data': {},
        })

        pong = await communicator.receive_json_from()
        self.assertEqual(pong['event'], 'connection.pong')
        self.assertEqual(pong['request_id'], 'ping-request')
        self.assertEqual(pong['data'], {})

        await communicator.disconnect()

    async def _assert_unimplemented_event_returns_error(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_json_to({
            'protocol_version': '1.0',
            'event': 'message.single.send',
            'request_id': 'message-request',
            'data': {},
        })

        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertEqual(error['request_id'], 'message-request')
        self.assertEqual(error['data']['code'], 'not_implemented')
        self.assertFalse(error['data']['retryable'])

        await communicator.disconnect()

    async def _assert_cross_site_origin_is_rejected(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=[(b'origin', b'https://attacker.example')],
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def _assert_disconnect_removes_user_group(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        channel_layer = get_channel_layer()
        user_group_name = f'user_{self.user.pk}'
        self.assertIn(user_group_name, channel_layer.groups)

        await communicator.disconnect()
        self.assertNotIn(user_group_name, channel_layer.groups)

    async def _assert_invalid_json_returns_error(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/chat/',
            headers=self.session_cookie_header,
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()

        await communicator.send_to(text_data='{')

        error = await communicator.receive_json_from()
        self.assertEqual(error['event'], 'error')
        self.assertIsNone(error['request_id'])
        self.assertEqual(error['data']['code'], 'invalid_payload')
        self.assertFalse(error['data']['retryable'])

        await communicator.disconnect()
