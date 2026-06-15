"""
Phase 2 backend tests: T19 (conversation management), T20 (message operations),
T21 (message status model), T22 (online presence).
"""
import json

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
from accounts.models import Contact, UserPrivacySettings

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
        Contact.objects.create(user=self.u1, contact=self.u2)
        self.client = Client()
        self.client.force_login(self.u1)
        self.msg = EncryptedMessage.objects.create(
            conversation=self.conv, sender=self.u1, receiver=self.u2,
            ciphertext='source-ciphertext', nonce='source-nonce', auth_tag='source-tag',
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

    def test_forward_private_message_creates_target_message(self):
        u3 = _create_user('carol')
        Contact.objects.create(user=self.u1, contact=u3)
        target = _create_private_conversation(self.u1, u3)

        resp = self.client.post(
            f'/api/conversations/{target.id}/messages/forward/',
            data=json.dumps({
                'original_message_id': self.msg.pk,
                'original_conversation_id': self.conv.pk,
                'peer_id': u3.pk,
                'client_message_id': 'forward-private-1',
                'message_type': 'text',
                'ciphertext': 'forward-ciphertext',
                'nonce': 'forward-nonce',
                'auth_tag': 'forward-tag',
                'algorithm': 'AES-256-GCM',
                'sender_key_version': 1,
                'receiver_key_version': 1,
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 201)
        payload = resp.json()
        self.assertIn('message_id', payload)
        forwarded = EncryptedMessage.objects.get(pk=payload['message_id'])
        self.assertEqual(forwarded.conversation, target)
        self.assertEqual(forwarded.sender, self.u1)
        self.assertEqual(forwarded.receiver, u3)
        self.assertEqual(forwarded.reply_to_message_id, self.msg.pk)
        self.assertEqual(forwarded.ciphertext, 'forward-ciphertext')

    def test_forward_group_message_creates_recipient_copies(self):
        u3 = _create_user('carol')
        group = _create_group(self.u1, name='Forward Target')
        ConversationMember.objects.create(
            conversation=group,
            user=self.u2,
            role=ConversationMember.Role.MEMBER,
        )
        ConversationMember.objects.create(
            conversation=group,
            user=u3,
            role=ConversationMember.Role.MEMBER,
        )

        recipients = [
            {
                'receiver_id': user.pk,
                'ciphertext': f'cipher-{user.pk}',
                'nonce': f'nonce-{user.pk}',
                'auth_tag': f'tag-{user.pk}',
                'algorithm': 'AES-256-GCM',
                'sender_key_version': 1,
                'receiver_key_version': 1,
            }
            for user in (self.u1, self.u2, u3)
        ]
        resp = self.client.post(
            f'/api/conversations/{group.id}/messages/forward/',
            data=json.dumps({
                'original_message_id': self.msg.pk,
                'original_conversation_id': self.conv.pk,
                'client_message_id': 'forward-group-1',
                'message_type': 'text',
                'membership_version': group.membership_version,
                'recipients': recipients,
            }),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 201)
        group_message = GroupMessage.objects.get(pk=resp.json()['message_id'])
        self.assertEqual(group_message.conversation, group)
        self.assertEqual(group_message.sender, self.u1)
        self.assertEqual(group_message.reply_to_message_id, self.msg.pk)
        self.assertEqual(
            set(group_message.recipients.values_list('receiver_id', flat=True)),
            {self.u1.pk, self.u2.pk, u3.pk},
        )


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


class StorageSettingsAPITests(TestCase):
    """Data & storage settings and cache-clearing API."""

    def setUp(self):
        self.alice = _create_user('storage_alice')
        self.bob = _create_user('storage_bob')
        self.client = Client()
        self.client.login(username='storage_alice', password='pass1234')

    def test_storage_settings_are_persisted(self):
        response = self.client.get('/api/storage/settings/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['settings']['auto_download_enabled'])
        self.assertEqual(response.json()['settings']['cache_retention_days'], 7)

        response = self.client.post(
            '/api/storage/settings/',
            data=json.dumps({
                'auto_download_enabled': False,
                'file_size_limit_mb': {'files': 12},
                'cache_retention_days': 30,
                'cache_max_size_mb': 100,
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        settings = self.client.get('/api/storage/settings/').json()['settings']
        self.assertFalse(settings['auto_download_enabled'])
        self.assertEqual(settings['file_size_limit_mb']['files'], 12)
        self.assertEqual(settings['cache_retention_days'], 30)
        self.assertEqual(settings['cache_max_size_mb'], 100)

    def test_storage_clear_updates_server_stats(self):
        conv = _create_private_conversation(self.alice, self.bob)
        EncryptedMessage.objects.create(
            conversation=conv,
            sender=self.alice,
            receiver=self.bob,
            message_type=EncryptedMessage.MessageType.IMAGE,
            algorithm='AES-256-GCM',
            client_message_id='storage-image-1',
        )

        before = self.client.get('/api/storage/stats/').json()
        self.assertGreater(before['categories']['images']['size_bytes'], 0)

        response = self.client.post(
            '/api/storage/clear/',
            data=json.dumps({'categories': ['images']}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        after = response.json()['stats']
        self.assertEqual(after['categories']['images']['size_bytes'], 0)
        self.assertGreater(after['categories']['images']['cleared_baseline_bytes'], 0)


class PrivacySecurityAPITests(TestCase):
    """Privacy settings API and enforcement for existing chat flows."""

    def setUp(self):
        self.alice = _create_user('privacy_alice')
        self.bob = _create_user('privacy_bob')
        self.carol = _create_user('privacy_carol')
        self.client = Client()
        self.client.login(username='privacy_alice', password='pass1234')

    def test_extended_privacy_settings_are_persisted(self):
        response = self.client.post(
            '/api/privacy/settings/',
            data=json.dumps({
                'birthday_visibility': 'nobody',
                'gifts_visibility': 'contacts',
                'saved_music_visibility': 'nobody',
                'who_can_add_me_to_groups': 'contacts',
                'passkey_enabled': True,
                'login_email': 'privacy@example.com',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        settings = self.client.get('/api/privacy/settings/').json()['settings']
        self.assertEqual(settings['birthday_visibility'], 'nobody')
        self.assertEqual(settings['gifts_visibility'], 'contacts')
        self.assertEqual(settings['saved_music_visibility'], 'nobody')
        self.assertEqual(settings['who_can_add_me_to_groups'], 'contacts')
        self.assertTrue(settings['passkey_enabled'])
        self.assertEqual(settings['login_email'], 'privacy@example.com')

    def test_group_invite_respects_target_privacy(self):
        group = _create_group(self.alice)
        self.client.force_login(self.bob)
        self.client.post(
            '/api/privacy/settings/',
            data=json.dumps({'who_can_add_me_to_groups': 'contacts'}),
            content_type='application/json',
        )
        self.client.force_login(self.alice)

        response = self.client.post(
            f'/api/groups/{group.id}/invite/',
            data=json.dumps({'user_id': self.bob.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

        Contact.objects.create(user=self.bob, contact=self.alice)
        response = self.client.post(
            f'/api/groups/{group.id}/invite/',
            data=json.dumps({'user_id': self.bob.id}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

    def test_private_profile_payload_respects_visibility_settings(self):
        _create_private_conversation(self.alice, self.bob)
        self.bob.profile.bio = 'hidden bio'
        self.bob.profile.phone_number = '+10086'
        self.bob.profile.birthday = '2000-01-02'
        self.bob.profile.save()
        UserPrivacySettings.objects.update_or_create(
            user=self.bob,
            defaults={
                'bio_visibility': 'nobody',
                'phone_number_visibility': 'nobody',
                'birthday_visibility': 'nobody',
            },
        )

        response = self.client.get('/api/conversations/')
        self.assertEqual(response.status_code, 200)
        conversation = response.json()['conversations'][0]
        self.assertEqual(conversation['peer_bio'], '')
        self.assertEqual(conversation['peer_phone_number'], '')
        self.assertEqual(conversation['peer_birthday'], '')

    def test_private_send_respects_receiver_message_privacy(self):
        conv = _create_private_conversation(self.alice, self.bob)
        payload = {
            'receiver_id': self.bob.id,
            'message_type': EncryptedMessage.MessageType.TEXT,
            'ciphertext': 'aGVsbG8=',
            'nonce': 'AAAAAAAAAAAAAAAA',
            'auth_tag': 'AAAAAAAAAAAAAAAAAAAAAA==',
            'algorithm': 'AES-256-GCM',
            'sender_key_version': 1,
            'receiver_key_version': 1,
            'client_message_id': 'privacy-send-1',
        }

        response = self.client.post(
            f'/api/conversations/{conv.id}/messages/send/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

        UserPrivacySettings.objects.update_or_create(
            user=self.bob,
            defaults={'who_can_send_messages': 'everyone'},
        )
        payload['client_message_id'] = 'privacy-send-2'
        response = self.client.post(
            f'/api/conversations/{conv.id}/messages/send/',
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

    def test_auto_delete_privacy_setting_hides_expired_private_messages(self):
        conv = _create_private_conversation(self.alice, self.bob)
        old_message = EncryptedMessage.objects.create(
            conversation=conv,
            sender=self.bob,
            receiver=self.alice,
            ciphertext='aGVsbG8=',
            nonce='AAAAAAAAAAAAAAAA',
            auth_tag='AAAAAAAAAAAAAAAAAAAAAA==',
            algorithm='AES-256-GCM',
            sender_key_version=1,
            receiver_key_version=1,
        )
        EncryptedMessage.objects.filter(pk=old_message.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=2),
        )
        UserPrivacySettings.objects.update_or_create(
            user=self.alice,
            defaults={'auto_delete_messages_days': 1},
        )

        response = self.client.get(f'/api/conversations/{conv.id}/messages/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'], [])
        self.assertTrue(
            UserMessageDeletion.objects.filter(
                user=self.alice,
                conversation=conv,
                message_type=UserMessageDeletion.MessageType.PRIVATE,
                message_id=old_message.id,
            ).exists()
        )


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
