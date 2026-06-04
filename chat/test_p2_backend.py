"""
Phase 2 backend tests: T19 (conversation management), T20 (message operations),
T21 (message status model), T22 (online presence).
"""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test.client import Client

from chat.models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
    UserMessageDeletion,
    UserPresence,
)
from chat.consumers import ChatConsumer
from ichat_pro.asgi import application
from chat.views import RECALL_LIMIT_MINUTES

User = get_user_model()


# ── Helpers ─────────────────────────────────────────────────────────


def _session_headers(client, user):
    """Return HTTP headers carrying the Django session for WebSocket auth."""
    session_id = client.cookies[settings.SESSION_COOKIE_NAME].value
    return [
        (b'origin', b'http://testserver'),
        (b'cookie', f'{settings.SESSION_COOKIE_NAME}={session_id}'.encode()),
    ]


def _create_user(username, password='pass1234'):
    return User.objects.create_user(username=username, password=password)


def _create_private_conversation(user_a, user_b):
    conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
    ConversationMember.objects.bulk_create([
        ConversationMember(conversation=conv, user=user_a, role=ConversationMember.Role.MEMBER),
        ConversationMember(conversation=conv, user=user_b, role=ConversationMember.Role.MEMBER),
    ])
    return conv


def _create_group(user, name='Test Group'):
    conv = Conversation.objects.create(type=Conversation.Type.GROUP, name=name, created_by=user)
    ConversationMember.objects.create(conversation=conv, user=user, role=ConversationMember.Role.OWNER)
    return conv


# ── T21: Message Status Model Tests ──────────────────────────────────


class MessageStatusModelTests(TestCase):
    """Test new statuses and models added for T21."""

    def test_encrypted_message_recalled_status(self):
        u1 = _create_user('alice')
        u2 = _create_user('bob')
        conv = _create_private_conversation(u1, u2)
        msg = EncryptedMessage.objects.create(
            conversation=conv, sender=u1, receiver=u2,
            algorithm='AES-256-GCM', client_message_id='test-1',
        )
        msg.status = EncryptedMessage.Status.RECALLED
        msg.recalled_at = timezone.now()
        msg.save()
        self.assertEqual(msg.status, 'recalled')

    def test_group_message_recalled_status(self):
        u1 = _create_user('alice')
        conv = _create_group(u1)
        gm = GroupMessage.objects.create(
            conversation=conv, sender=u1, client_message_id='test-2',
        )
        gm.status = GroupMessage.Status.RECALLED
        gm.recalled_at = timezone.now()
        gm.save()
        self.assertEqual(gm.status, 'recalled')

    def test_user_message_deletion_unique(self):
        u1 = _create_user('alice')
        conv = _create_private_conversation(u1, _create_user('bob'))
        UserMessageDeletion.objects.create(
            user=u1, conversation=conv,
            message_type=UserMessageDeletion.MessageType.PRIVATE,
            message_id=42,
        )
        # Duplicate should fail
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            UserMessageDeletion.objects.create(
                user=u1, conversation=conv,
                message_type=UserMessageDeletion.MessageType.PRIVATE,
                message_id=42,
            )

    def test_group_recipient_failed_status(self):
        self.assertIn('failed', GroupMessageRecipient.Status.values)

    def test_reply_to_message_id_on_private(self):
        u1 = _create_user('alice')
        u2 = _create_user('bob')
        conv = _create_private_conversation(u1, u2)
        msg = EncryptedMessage.objects.create(
            conversation=conv, sender=u1, receiver=u2,
            algorithm='AES-256-GCM', client_message_id='test-3',
            reply_to_message_id=10,
        )
        self.assertEqual(msg.reply_to_message_id, 10)


# ── T19: Conversation Management API Tests ────────────────────────────


class ConversationManagementAPITests(TestCase):
    """Test pin, mute, archive, hide, clear, read/unread endpoints."""

    def setUp(self):
        self.u1 = _create_user('alice')
        self.u2 = _create_user('bob')
        self.conv = _create_private_conversation(self.u1, self.u2)
        self.client = Client()
        self.client.force_login(self.u1)
        self.member = _get_active_member(self.conv.id, self.u1)

    def test_pin_conversation(self):
        resp = self.client.post(f'/api/conversations/{self.conv.id}/pin/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['is_pinned'])

    def test_unpin_conversation(self):
        self.client.post(f'/api/conversations/{self.conv.id}/pin/')
        resp = self.client.delete(f'/api/conversations/{self.conv.id}/pin/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['is_pinned'])

    def test_mute_conversation(self):
        resp = self.client.post(
            f'/api/conversations/{self.conv.id}/mute/',
            data='{"duration_minutes": 120}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNotNone(data['muted_until'])

    def test_unmute_conversation(self):
        self.client.post(
            f'/api/conversations/{self.conv.id}/mute/',
            data='{"duration_minutes": 60}',
            content_type='application/json',
        )
        resp = self.client.delete(f'/api/conversations/{self.conv.id}/mute/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['muted_until'])

    def test_archive_conversation(self):
        resp = self.client.post(f'/api/conversations/{self.conv.id}/archive/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()['archived_at'])

    def test_unarchive_conversation(self):
        self.client.post(f'/api/conversations/{self.conv.id}/archive/')
        resp = self.client.post(f'/api/conversations/{self.conv.id}/unarchive/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['archived_at'])

    def test_hide_conversation(self):
        resp = self.client.delete(f'/api/conversations/{self.conv.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()['hidden_at'])

    def test_clear_conversation(self):
        resp = self.client.post(f'/api/conversations/{self.conv.id}/clear/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()['cleared_at'])

    def test_read_conversation(self):
        resp = self.client.post(f'/api/conversations/{self.conv.id}/read/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['unread_count'], 0)

    def test_unread_conversation(self):
        resp = self.client.post(
            f'/api/conversations/{self.conv.id}/unread/',
            data='{"unread_count": 3}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['unread_count'], 3)

    def test_non_member_gets_404(self):
        u3 = _create_user('charlie')
        client2 = Client()
        client2.force_login(u3)
        resp = client2.post(f'/api/conversations/{self.conv.id}/pin/')
        self.assertEqual(resp.status_code, 404)

    def test_pinned_ordering(self):
        # Create a second conversation
        u3 = _create_user('charlie')
        conv2 = _create_private_conversation(self.u1, u3)
        # Pin the first one
        self.client.post(f'/api/conversations/{self.conv.id}/pin/')
        # List conversations
        resp = self.client.get('/api/conversations/')
        convs = resp.json()['conversations']
        self.assertTrue(len(convs) >= 2)
        # Pinned conversation should come first
        self.assertTrue(convs[0]['is_pinned'])


def _get_active_member(conv_id, user):
    return ConversationMember.objects.get(
        conversation_id=conv_id, user=user, status=ConversationMember.Status.ACTIVE,
    )


# ── T20: Message Operations API Tests ──────────────────────────────────


class MessageOperationsAPITests(TestCase):
    """Test recall, delete, status, and forward endpoints."""

    def setUp(self):
        self.u1 = _create_user('alice')
        self.u2 = _create_user('bob')
        self.conv = _create_private_conversation(self.u1, self.u2)
        self.client = Client()
        self.client.force_login(self.u1)
        self.msg = EncryptedMessage.objects.create(
            conversation=self.conv, sender=self.u1, receiver=self.u2,
            algorithm='AES-256-GCM', client_message_id='test-ops-1',
        )

    def test_recall_own_message(self):
        resp = self.client.post(
            f'/api/conversations/{self.conv.id}/messages/{self.msg.pk}/recall/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'recalled')
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.status, 'recalled')

    def test_recall_other_users_message_403(self):
        client2 = Client()
        client2.force_login(self.u2)
        resp = client2.post(
            f'/api/conversations/{self.conv.id}/messages/{self.msg.pk}/recall/',
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_message_per_user(self):
        resp = self.client.delete(
            f'/api/conversations/{self.conv.id}/messages/{self.msg.pk}/',
        )
        self.assertEqual(resp.status_code, 200)
        # Verify deletion record exists for u1 but not u2
        self.assertTrue(
            UserMessageDeletion.objects.filter(
                user=self.u1, message_type='private', message_id=self.msg.pk,
            ).exists()
        )
        self.assertFalse(
            UserMessageDeletion.objects.filter(
                user=self.u2, message_type='private', message_id=self.msg.pk,
            ).exists()
        )

    def test_message_status_endpoint(self):
        resp = self.client.get(
            f'/api/conversations/{self.conv.id}/messages/{self.msg.pk}/status/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'sent')

    def test_message_status_permission(self):
        u3 = _create_user('charlie')
        client3 = Client()
        client3.force_login(u3)
        resp = client3.get(
            f'/api/conversations/{self.conv.id}/messages/{self.msg.pk}/status/',
        )
        # Non-member gets 404 (membership check before permission)
        self.assertEqual(resp.status_code, 404)


# ── T22: Presence API Tests ────────────────────────────────────────────


class PresenceAPITests(TestCase):
    """Test UserPresence model and API."""

    def setUp(self):
        self.u1 = _create_user('alice')
        self.u2 = _create_user('bob')
        self.client = Client()
        self.client.force_login(self.u1)

    def test_presence_model_defaults(self):
        presence = UserPresence.objects.create(user=self.u1)
        self.assertFalse(presence.is_online)
        self.assertEqual(presence.status, 'online')

    def test_get_own_presence(self):
        presence = UserPresence.objects.create(user=self.u1, is_online=True, status='online')
        resp = self.client.get(f'/api/users/{self.u1.pk}/presence/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['is_online'])
        self.assertEqual(data['status'], 'online')

    def test_get_other_presence_visibility_nobody(self):
        UserPresence.objects.create(
            user=self.u2, is_online=True, status='online',
            presence_visibility=UserPresence.Visibility.NOBODY,
        )
        resp = self.client.get(f'/api/users/{self.u2.pk}/presence/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['is_online'])
        self.assertEqual(resp.json()['status'], 'offline')

    def test_update_presence(self):
        resp = self.client.put(
            '/api/users/presence/',
            data='{"status": "away", "presence_visibility": "contacts"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'away')
        self.assertEqual(data['presence_visibility'], 'contacts')

    def test_user_without_presence_returns_offline(self):
        resp = self.client.get(f'/api/users/{self.u2.pk}/presence/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['is_online'])


# ── T22: WebSocket Presence and Typing Tests ──────────────────────────


class WebSocketPresenceTests(TransactionTestCase):
    """Test presence broadcast on connect/disconnect and typing indicators."""

    def setUp(self):
        self.u1 = _create_user('alice')
        self.u2 = _create_user('bob')
        self.conv = _create_private_conversation(self.u1, self.u2)
        client = Client()
        client.force_login(self.u1)
        self.u1_headers = _session_headers(client, self.u1)
        client2 = Client()
        client2.force_login(self.u2)
        self.u2_headers = _session_headers(client2, self.u2)

    def test_presence_set_on_connect(self):
        async_to_sync(self._assert_presence_on_connect)()

    async def _assert_presence_on_connect(self):
        comm = WebsocketCommunicator(application, '/ws/chat/', headers=self.u1_headers)
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        # Consume connection.ready
        ready = await comm.receive_json_from()
        self.assertEqual(ready['event'], 'connection.ready')

        # Verify DB presence
        presence = await self._get_presence(self.u1.pk)
        self.assertTrue(presence.is_online)
        await comm.disconnect()

    def test_presence_offline_on_disconnect(self):
        async_to_sync(self._assert_presence_offline)()

    async def _assert_presence_offline(self):
        comm = WebsocketCommunicator(application, '/ws/chat/', headers=self.u1_headers)
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        await comm.receive_json_from()  # connection.ready
        await comm.disconnect()

        # Verify DB shows offline
        presence = await self._get_presence(self.u1.pk)
        self.assertFalse(presence.is_online)
        self.assertEqual(presence.status, 'offline')

    def test_typing_start_broadcast(self):
        async_to_sync(self._assert_typing_start)()

    async def _assert_typing_start(self):
        # Connect both users
        comm1 = WebsocketCommunicator(application, '/ws/chat/', headers=self.u1_headers)
        comm2 = WebsocketCommunicator(application, '/ws/chat/', headers=self.u2_headers)
        await comm1.connect()
        await comm2.connect()
        await comm1.receive_json_from()  # connection.ready
        await comm2.receive_json_from()  # connection.ready

        # u1 starts typing in conversation with u2
        await comm1.send_json_to({
            'protocol_version': '1.0',
            'event': 'typing.start',
            'request_id': 'r-1',
            'sent_at': '2026-06-04T00:00:00Z',
            'data': {'conversation_id': self.conv.id},
        })
        # u1 gets ack
        ack = await comm1.receive_json_from()
        self.assertEqual(ack['event'], 'typing.start.ack')

        # u2 gets typing indicator
        typing_event = await comm2.receive_json_from()
        self.assertEqual(typing_event['event'], 'typing')
        self.assertEqual(typing_event['data']['action'], 'typing')
        self.assertEqual(typing_event['data']['user_id'], self.u1.pk)

        await comm1.disconnect()
        await comm2.disconnect()

    def test_typing_stop_broadcast(self):
        async_to_sync(self._assert_typing_stop)()

    async def _assert_typing_stop(self):
        comm1 = WebsocketCommunicator(application, '/ws/chat/', headers=self.u1_headers)
        comm2 = WebsocketCommunicator(application, '/ws/chat/', headers=self.u2_headers)
        await comm1.connect()
        await comm2.connect()
        await comm1.receive_json_from()
        await comm2.receive_json_from()

        await comm1.send_json_to({
            'protocol_version': '1.0',
            'event': 'typing.stop',
            'request_id': 'r-2',
            'sent_at': '2026-06-04T00:00:00Z',
            'data': {'conversation_id': self.conv.id},
        })
        ack = await comm1.receive_json_from()
        self.assertEqual(ack['event'], 'typing.stop.ack')

        typing_event = await comm2.receive_json_from()
        self.assertEqual(typing_event['event'], 'typing')
        self.assertEqual(typing_event['data']['action'], 'stop')

        await comm1.disconnect()
        await comm2.disconnect()

    # ── Async DB helpers ──

    @database_sync_to_async
    def _get_presence(self, user_id):
        return UserPresence.objects.get(user_id=user_id)
