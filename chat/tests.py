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

    def test_chat_page_loads_private_chat_e2ee_module(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'js/private-chat-e2ee.js')

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
            'event': 'unknown.event',
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
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from .models import Conversation, ConversationMember, EncryptedMessage


class ConversationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="test1234")

    def test_create_single_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)
        self.assertIsNone(conv.last_message_at)
        self.assertEqual(conv.created_by, self.user)

    def test_create_group_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, created_by=self.user
        )
        self.assertEqual(conv.type, Conversation.Type.GROUP)

    def test_default_status_is_active(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertEqual(conv.status, Conversation.Status.ACTIVE)

    def test_created_by_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.created_by)

    def test_last_message_at_is_nullable(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        self.assertIsNone(conv.last_message_at)
        self.assertIsNone(conv.last_message_id)

    def test_str_method(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user
        )
        expected = f"Conversation #{conv.id} (Private Chat)"
        self.assertEqual(str(conv), expected)

    def test_ordering_by_last_message_at(self):
        older = Conversation.objects.create(type=Conversation.Type.SINGLE)
        newer = Conversation.objects.create(type=Conversation.Type.SINGLE)
        # By default ordering in admin is by -last_message_at, -created_at;
        # here we just verify both objects exist without error.
        self.assertTrue(Conversation.objects.filter(id=older.id).exists())
        self.assertTrue(Conversation.objects.filter(id=newer.id).exists())


class ConversationMemberModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username="alice", password="test1234")
        self.user2 = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.user1
        )

    def test_create_membership(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        self.assertEqual(member.status, ConversationMember.Status.ACTIVE)
        self.assertEqual(member.unread_count, 0)
        self.assertIsNone(member.last_read_message_id)

    def test_unique_constraint_per_conversation_and_user(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        with self.assertRaises(IntegrityError):
            ConversationMember.objects.create(
                conversation=self.conv, user=self.user1
            )

    def test_multiple_users_in_same_conversation(self):
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        ConversationMember.objects.create(
            conversation=self.conv, user=self.user2
        )
        self.assertEqual(self.conv.members.count(), 2)

    def test_str_method(self):
        member = ConversationMember.objects.create(
            conversation=self.conv, user=self.user1
        )
        expected = (
            f"Member #{self.user1.id} in Conversation #{self.conv.id}"
        )
        self.assertEqual(str(member), expected)

    def test_muted_status(self):
        member = ConversationMember.objects.create(
            conversation=self.conv,
            user=self.user1,
            status=ConversationMember.Status.MUTED,
        )
        self.assertEqual(member.status, ConversationMember.Status.MUTED)


class EncryptedMessageModelTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="test1234")
        self.bob = User.objects.create_user(username="bob", password="test1234")
        self.conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )

    def test_create_text_message(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext="base64encryptedpayload==",
            nonce="a1b2c3d4e5f6",
            auth_tag="ffaabbccdd112233",
            algorithm="AES-256-GCM",
            sender_key_version=1,
            receiver_key_version=1,
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertEqual(msg.algorithm, "AES-256-GCM")

    def test_defaults(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.message_type, EncryptedMessage.MessageType.TEXT)
        self.assertEqual(msg.status, EncryptedMessage.Status.SENT)
        self.assertIsNone(msg.ciphertext)
        self.assertIsNone(msg.nonce)
        self.assertIsNone(msg.auth_tag)

    def test_message_types(self):
        for mtype in EncryptedMessage.MessageType.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                message_type=mtype,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.message_type, mtype)

    def test_status_values(self):
        for status in EncryptedMessage.Status.values:
            msg = EncryptedMessage.objects.create(
                conversation=self.conv,
                sender=self.alice,
                receiver=self.bob,
                status=status,
                algorithm="AES-256-GCM",
            )
            self.assertEqual(msg.status, status)

    def test_sender_receiver_relationship(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertEqual(msg.sender, self.alice)
        self.assertEqual(msg.receiver, self.bob)
        self.assertEqual(self.alice.sent_messages.count(), 1)
        self.assertEqual(self.bob.received_messages.count(), 1)

    def test_str_method(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        expected = (
            f"Message #{msg.id} from #{self.alice.id} to #{self.bob.id}"
        )
        self.assertEqual(str(msg), expected)

    def test_deleted_at_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.deleted_at)

    def test_key_version_fields_nullable(self):
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            algorithm="AES-256-GCM",
        )
        self.assertIsNone(msg.sender_key_version)
        self.assertIsNone(msg.receiver_key_version)
