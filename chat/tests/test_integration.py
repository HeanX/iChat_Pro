"""
T20 integration tests — fill coverage gaps for conversation list,
permission enforcement, ciphertext integrity, and edge cases.
"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import Contact, UserPublicKey
from chat.models import (
    Conversation,
    ConversationMember,
    EncryptedMessage,
    GroupMessage,
    GroupMessageRecipient,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Conversation list API — group conversations
# ---------------------------------------------------------------------------

class ConversationListGroupTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.bob = User.objects.create_user(username='bob', password='pw')
        self.carol = User.objects.create_user(username='carol', password='pw')
        self.client.force_login(self.alice)

    def test_list_includes_group_conversations(self):
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name='Test Group',
            created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=group, user=self.alice, role=ConversationMember.Role.OWNER
        )
        ConversationMember.objects.create(
            conversation=group, user=self.bob, role=ConversationMember.Role.MEMBER
        )

        response = self.client.get(reverse('api_conversations'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ids = [c['id'] for c in payload['conversations']]
        self.assertIn(group.pk, ids)

    def test_list_shows_group_fields(self):
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP,
            name='Secret Team',
            created_by=self.alice,
            last_message_at=None,
        )
        ConversationMember.objects.create(
            conversation=group, user=self.alice, role=ConversationMember.Role.OWNER
        )

        response = self.client.get(reverse('api_conversations'))
        result = response.json()['conversations'][0]
        self.assertEqual(result['type'], 'group')
        self.assertEqual(result['name'], 'Secret Team')
        self.assertIn('member_count', result)
        self.assertIn('membership_version', result)

    def test_list_excludes_deleted_conversations(self):
        active = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=active, user=self.alice)
        ConversationMember.objects.create(conversation=active, user=self.bob)

        deleted = Conversation.objects.create(
            type=Conversation.Type.SINGLE,
            created_by=self.alice,
            status=Conversation.Status.DELETED,
        )
        ConversationMember.objects.create(conversation=deleted, user=self.alice)
        ConversationMember.objects.create(conversation=deleted, user=self.bob)

        response = self.client.get(reverse('api_conversations'))
        ids = [c['id'] for c in response.json()['conversations']]
        self.assertIn(active.pk, ids)
        self.assertNotIn(deleted.pk, ids)

    def test_list_excludes_left_membership(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.SINGLE, created_by=self.alice
        )
        ConversationMember.objects.create(conversation=conv, user=self.alice)
        ConversationMember.objects.create(conversation=conv, user=self.bob)

        # Bob leaves
        bob_member = ConversationMember.objects.get(conversation=conv, user=self.bob)
        bob_member.status = ConversationMember.Status.LEFT
        bob_member.save()

        # Alice still sees the conversation
        response = self.client.get(reverse('api_conversations'))
        ids = [c['id'] for c in response.json()['conversations']]
        self.assertIn(conv.pk, ids)


# ---------------------------------------------------------------------------
# Private conversation creation — contact enforcement
# ---------------------------------------------------------------------------

class ConversationCreateContactEnforcementTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.bob = User.objects.create_user(username='bob', password='pw')
        self.mallory = User.objects.create_user(username='mallory', password='pw')
        self.client.force_login(self.alice)

    def test_cannot_create_without_contact(self):
        """Non-contacts cannot create private conversations."""
        response = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': self.mallory.pk}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_cannot_create_with_self(self):
        response = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': self.alice.pk}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_create_with_invalid_user(self):
        response = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({'peer_id': 99999}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_create_requires_peer_id(self):
        response = self.client.post(
            reverse('api_conversation_create'),
            json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_requires_post(self):
        response = self.client.get(reverse('api_conversation_create'))
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# Ciphertext integrity — model-level guarantees
# ---------------------------------------------------------------------------

class CiphertextIntegrityTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.bob = User.objects.create_user(username='bob', password='pw')
        self.conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)

    def test_encrypted_message_has_no_plaintext_field(self):
        """EncryptedMessage model must not expose any plaintext column."""
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            ciphertext='aGVsbG8=',
            nonce='AAECAwQFBgcICQoLDA0ODw==',
            auth_tag='EBESExQVFhcYGRobHB0eHw==',
            algorithm='AES-256-GCM',
        )
        # Verify the message is stored without plaintext
        self.assertEqual(msg.ciphertext, 'aGVsbG8=')
        self.assertFalse(hasattr(msg, 'plaintext'))

    def test_group_recipient_stores_per_user_ciphertext(self):
        """Each group recipient gets an independent ciphertext row."""
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Test', created_by=self.alice
        )
        ConversationMember.objects.create(conversation=group, user=self.alice)
        ConversationMember.objects.create(conversation=group, user=self.bob)
        gmsg = GroupMessage.objects.create(
            conversation=group, sender=self.alice
        )
        r1 = GroupMessageRecipient.objects.create(
            group_message=gmsg,
            receiver=self.alice,
            ciphertext='YWxpY2VfY3R4dA==',
            nonce='AAECAwQFBgcICQoLDA0ODw==',
            auth_tag='EBESExQVFhcYGRobHB0eHw==',
            algorithm='AES-256-GCM',
        )
        r2 = GroupMessageRecipient.objects.create(
            group_message=gmsg,
            receiver=self.bob,
            ciphertext='Ym9iX2N0eHQ=',
            nonce='Hh4fICEiIyQlJicoKSorLC0=',
            auth_tag='Li8wMTIzNDU2Nzg5Ojs8PT4=',
            algorithm='AES-256-GCM',
        )
        self.assertNotEqual(r1.ciphertext, r2.ciphertext)

    def test_message_without_ciphertext_is_allowed(self):
        """System messages may have no ciphertext."""
        msg = EncryptedMessage.objects.create(
            conversation=self.conv,
            sender=self.alice,
            receiver=self.bob,
            message_type='system',
            ciphertext=None,
            nonce=None,
            auth_tag=None,
        )
        self.assertIsNone(msg.ciphertext)


# ---------------------------------------------------------------------------
# Message history — permission enforcement
# ---------------------------------------------------------------------------

class MessageHistoryPermissionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.bob = User.objects.create_user(username='bob', password='pw')
        self.mallory = User.objects.create_user(username='mallory', password='pw')

    def test_private_history_requires_membership(self):
        conv = Conversation.objects.create(type=Conversation.Type.SINGLE)
        ConversationMember.objects.create(conversation=conv, user=self.alice)
        ConversationMember.objects.create(conversation=conv, user=self.bob)
        EncryptedMessage.objects.create(
            conversation=conv, sender=self.alice, receiver=self.bob,
            ciphertext='xxx', nonce='AAECAwQFBgcICQoLDA0ODw==',
            auth_tag='EBESExQVFhcYGRobHB0eHw==', algorithm='AES-256-GCM',
        )

        self.client.force_login(self.mallory)
        response = self.client.get(
            reverse('api_conversation_messages', args=[conv.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_group_history_requires_membership(self):
        group = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Test', created_by=self.alice
        )
        ConversationMember.objects.create(conversation=group, user=self.alice)
        ConversationMember.objects.create(conversation=group, user=self.bob)
        gmsg = GroupMessage.objects.create(conversation=group, sender=self.alice)
        GroupMessageRecipient.objects.create(
            group_message=gmsg, receiver=self.alice,
            ciphertext='xxx', nonce='AAECAwQFBgcICQoLDA0ODw==',
            auth_tag='EBESExQVFhcYGRobHB0eHw==', algorithm='AES-256-GCM',
        )

        self.client.force_login(self.mallory)
        response = self.client.get(
            reverse('api_group_messages', args=[group.pk])
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Public key conflict / dedup
# ---------------------------------------------------------------------------

class PublicKeyEdgeCasesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pw')
        self.client.force_login(self.alice)

    def test_upload_same_key_twice_is_idempotent(self):
        key_data = {
            'identity_public_key': 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE' * 2,
        }
        first = self.client.post(
            reverse('upload-public-key'),
            json.dumps(key_data),
            content_type='application/json',
        )
        second = self.client.post(
            reverse('upload-public-key'),
            json.dumps(key_data),
            content_type='application/json',
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)  # already exists
        # Still only one active key
        self.assertEqual(
            UserPublicKey.objects.filter(user=self.alice, is_active=True).count(), 1
        )

    def test_key_version_increments_on_new_key(self):
        k1 = {
            'identity_public_key': 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE' + 'A' * 64,
        }
        k2 = {
            'identity_public_key': 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE' + 'B' * 64,
        }
        self.client.post(
            reverse('upload-public-key'), json.dumps(k1), content_type='application/json'
        )
        self.client.post(
            reverse('upload-public-key'), json.dumps(k2), content_type='application/json'
        )
        keys = UserPublicKey.objects.filter(user=self.alice).order_by('key_version')
        self.assertEqual(keys.count(), 2)
        self.assertEqual(keys[0].key_version, 1)
        self.assertEqual(keys[1].key_version, 2)
        self.assertFalse(keys[0].is_active)
        self.assertTrue(keys[1].is_active)
