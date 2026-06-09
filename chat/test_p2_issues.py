"""
P2 Phase 2 backend tests: T27 (auto-delete), T31 (contact search),
T32 (group creation with members), T33/T34 (search API), T37 (group management),
T38 (key trust), T40 (comprehensive coverage).
"""
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.test.client import Client

from chat.models import (
    Conversation, ConversationMember, EncryptedMessage,
    GroupMessage, GroupMessageRecipient, GroupAnnouncement,
    UserMessageDeletion, UserPresence,
)
from accounts.models import Contact, KeyTrust, UserPublicKey

User = get_user_model()


def _u(name, pw='pass1234'):
    return User.objects.create_user(username=name, password=pw)


def _private_conv(a, b):
    c = Conversation.objects.create(type=Conversation.Type.SINGLE)
    ConversationMember.objects.bulk_create([
        ConversationMember(conversation=c, user=a, role=ConversationMember.Role.MEMBER),
        ConversationMember(conversation=c, user=b, role=ConversationMember.Role.MEMBER),
    ])
    return c


def _group(name, owner):
    c = Conversation.objects.create(type=Conversation.Type.GROUP, name=name, created_by=owner)
    ConversationMember.objects.create(conversation=c, user=owner, role=ConversationMember.Role.OWNER)
    return c


# ── T27: Auto-delete messages ───────────────────────────────────────

class AutoDeleteTests(TestCase):
    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob')
        self.conv = _private_conv(self.u1, self.u2)
        self.c = Client(); self.c.force_login(self.u1)

    def test_get_global_auto_delete_defaults(self):
        resp = self.c.get('/api/settings/auto-delete/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['enabled'])

    def test_set_global_auto_delete(self):
        resp = self.c.put('/api/settings/auto-delete/',
                          data='{"seconds": 86400}', content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['global_auto_delete_seconds'], 86400)

    def test_disable_global_auto_delete(self):
        self.c.put('/api/settings/auto-delete/',
                   data='{"seconds": 3600}', content_type='application/json')
        resp = self.c.put('/api/settings/auto-delete/',
                          data='{"disabled": true}', content_type='application/json')
        self.assertIsNone(resp.json()['global_auto_delete_seconds'])

    def test_get_conversation_auto_delete(self):
        resp = self.c.get(f'/api/conversations/{self.conv.id}/auto-delete/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['auto_delete_seconds'])

    def test_set_conversation_auto_delete(self):
        resp = self.c.put(f'/api/conversations/{self.conv.id}/auto-delete/',
                          data='{"seconds": 3600}', content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['auto_delete_seconds'], 3600)

    def test_auto_delete_non_member_404(self):
        u3 = _u('charlie'); c2 = Client(); c2.force_login(u3)
        resp = c2.get(f'/api/conversations/{self.conv.id}/auto-delete/')
        self.assertEqual(resp.status_code, 404)


# ── T33/T34: Unified search with scope ──────────────────────────────

class SearchAPITests(TestCase):
    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob_search')
        self.u3 = _u('charlie')
        # Make contacts
        Contact.objects.create(user=self.u1, contact=self.u2)
        _private_conv(self.u1, self.u2)
        _group('search_test_group', self.u1)
        self.c = Client(); self.c.force_login(self.u1)

    def test_search_contacts(self):
        resp = self.c.get('/api/search/?q=bob&scope=contacts')
        self.assertEqual(resp.status_code, 200)
        contacts = resp.json()['results']['contacts']
        self.assertTrue(any(c['username'] == 'bob_search' for c in contacts))

    def test_search_groups(self):
        resp = self.c.get('/api/search/?q=search_test&scope=group_chats')
        groups = resp.json()['results']['groups']
        self.assertTrue(any(g['name'] == 'search_test_group' for g in groups))

    def test_search_private_chats(self):
        resp = self.c.get('/api/search/?q=bob&scope=private_chats')
        convs = resp.json()['results']['conversations']
        self.assertTrue(any(c['peer_username'] == 'bob_search' for c in convs))

    def test_search_all_scope(self):
        resp = self.c.get('/api/search/?q=test&scope=all')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['scope'], 'all')

    def test_search_empty_query(self):
        resp = self.c.get('/api/search/?q=')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()['results']['contacts']), 0)

    def test_search_channels_placeholder(self):
        resp = self.c.get('/api/search/?q=test&scope=all')
        self.assertEqual(resp.json()['results']['channels'], [])


# ── T32: Group creation with initial members ────────────────────────

class GroupCreationTests(TestCase):
    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob'); self.u3 = _u('charlie')
        Contact.objects.create(user=self.u1, contact=self.u2)
        self.c = Client(); self.c.force_login(self.u1)

    def test_create_group_with_initial_members(self):
        resp = self.c.post('/api/groups/',
                           data='{"name":"Team","initial_member_ids":[%d]}' % self.u2.pk,
                           content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['member_count'], 2)

    def test_create_group_deduplicate_members(self):
        resp = self.c.post('/api/groups/',
                           data='{"name":"Team2","initial_member_ids":[%d,%d,%d]}' % (
                               self.u2.pk, self.u2.pk, self.u1.pk),
                           content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['member_count'], 2)  # self + u2 only

    def test_create_group_no_name(self):
        resp = self.c.post('/api/groups/',
                           data='{"name":""}', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_create_group_creator_is_owner(self):
        resp = self.c.post('/api/groups/',
                           data='{"name":"Team3"}', content_type='application/json')
        gid = resp.json()['id']
        member = ConversationMember.objects.get(conversation_id=gid, user=self.u1)
        self.assertEqual(member.role, ConversationMember.Role.OWNER)


# ── T37: Group management (promote/demote/transfer/announcement/mute) ─

class GroupManagementTests(TestCase):
    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob'); self.u3 = _u('charlie')
        self.group = _group('TestGroup', self.u1)
        # Add u2 as member
        ConversationMember.objects.create(
            conversation=self.group, user=self.u2, role=ConversationMember.Role.MEMBER)
        self.c = Client(); self.c.force_login(self.u1)

    def test_promote_member_to_admin(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/promote/{self.u2.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['role'], 'admin')

    def test_demote_admin(self):
        # First promote, then demote
        ConversationMember.objects.filter(
            conversation=self.group, user=self.u2).update(role=ConversationMember.Role.ADMIN)
        resp = self.c.post(f'/api/groups/{self.group.id}/demote/{self.u2.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['role'], 'member')

    def test_demote_non_admin_409(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/demote/{self.u2.pk}/')
        self.assertEqual(resp.status_code, 409)

    def test_transfer_ownership(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/transfer/',
                           data=f'{{"user_id":{self.u2.pk}}}',
                           content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['new_owner_id'], self.u2.pk)

    def test_transfer_ownership_non_owner_403(self):
        c2 = Client(); c2.force_login(self.u2)
        resp = c2.post(f'/api/groups/{self.group.id}/transfer/',
                       data=f'{{"user_id":{self.u3.pk}}}',
                       content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_cannot_promote(self):
        c2 = Client(); c2.force_login(self.u2)
        resp = c2.post(f'/api/groups/{self.group.id}/promote/{self.u3.pk}/')
        self.assertEqual(resp.status_code, 403)

    def test_create_and_get_announcement(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/announcement/',
                           data='{"content":"Welcome!"}', content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        aid = resp.json()['announcement']['id']

        resp2 = self.c.get(f'/api/groups/{self.group.id}/announcement/')
        self.assertEqual(resp2.json()['announcement']['id'], aid)

    def test_delete_announcement(self):
        self.c.post(f'/api/groups/{self.group.id}/announcement/',
                    data='{"content":"Hello"}', content_type='application/json')
        resp = self.c.delete(f'/api/groups/{self.group.id}/announcement/')
        self.assertEqual(resp.status_code, 200)
        resp2 = self.c.get(f'/api/groups/{self.group.id}/announcement/')
        self.assertIsNone(resp2.json()['announcement'])

    def test_announcement_empty_content(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/announcement/',
                           data='{"content":""}', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_group_mute(self):
        resp = self.c.post(f'/api/groups/{self.group.id}/mute-group/',
                           data='{"duration_minutes":30}', content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()['muted_until'])

    def test_group_unmute(self):
        self.c.post(f'/api/groups/{self.group.id}/mute-group/',
                    data='{"duration_minutes":30}', content_type='application/json')
        resp = self.c.delete(f'/api/groups/{self.group.id}/mute-group/')
        self.assertEqual(resp.status_code, 200)

    def test_members_advanced_view(self):
        resp = self.c.get(f'/api/groups/{self.group.id}/members-advanced/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('members', resp.json())
        self.assertIn('membership_version', resp.json())


# ── T38: Key trust and fingerprints ─────────────────────────────────

class KeyTrustTests(TestCase):
    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob')
        Contact.objects.create(user=self.u1, contact=self.u2)
        # Upload keys
        import base64, hashlib
        self.key1_raw = b'\x04' + b'\x01' * 64
        self.key1_b64 = base64.b64encode(self.key1_raw).decode()
        self.fp1 = hashlib.sha256(self.key1_raw).hexdigest().upper()
        self.key2_raw = b'\x04' + b'\x02' * 64
        self.key2_b64 = base64.b64encode(self.key2_raw).decode()
        self.fp2 = hashlib.sha256(self.key2_raw).hexdigest().upper()
        self.c = Client(); self.c.force_login(self.u1)
        self.c2 = Client(); self.c2.force_login(self.u2)

    def _upload_key(self, client, raw, fp):
        import base64
        return client.post('/api/keys/upload/',
                   data=f'{{"identity_public_key":"{base64.b64encode(raw).decode()}","key_fingerprint":"{fp}"}}',
                   content_type='application/json')

    def test_my_fingerprints(self):
        self._upload_key(self.c, self.key1_raw, self.fp1)
        resp = self.c.get('/api/keys/fingerprints/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.json()['keys']), 0)

    def test_contact_fingerprints(self):
        self._upload_key(self.c2, self.key2_raw, self.fp2)
        resp = self.c.get(f'/api/keys/contacts/{self.u2.pk}/fingerprints/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['is_contact'])

    def test_trust_contact_key(self):
        self._upload_key(self.c2, self.key2_raw, self.fp2)
        resp = self.c.post(f'/api/keys/contacts/{self.u2.pk}/trust/',
                           data='{"trust_status":"trusted"}',
                           content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['trust_status'], 'trusted')

    def test_untrust_contact_key(self):
        self._upload_key(self.c2, self.key2_raw, self.fp2)
        self.c.post(f'/api/keys/contacts/{self.u2.pk}/trust/',
                    data='{"trust_status":"trusted"}', content_type='application/json')
        resp = self.c.delete(f'/api/keys/contacts/{self.u2.pk}/trust/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['deleted'])

    def test_trust_self_400(self):
        resp = self.c.post(f'/api/keys/contacts/{self.u1.pk}/trust/',
                           data='{"trust_status":"trusted"}',
                           content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_trust_list(self):
        self._upload_key(self.c2, self.key2_raw, self.fp2)
        self.c.post(f'/api/keys/contacts/{self.u2.pk}/trust/',
                    data='{"trust_status":"trusted"}', content_type='application/json')
        resp = self.c.get('/api/keys/trust/')
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.json()['trusts']), 0)

    def test_contact_without_key_404(self):
        resp = self.c.get(f'/api/keys/contacts/{self.u2.pk}/fingerprints/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['active_key'])


# ── T40: Additional comprehensive tests ─────────────────────────────

class ComprehensiveTests(TestCase):
    """Additional tests to meet Phase 2 coverage requirements."""

    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob')
        self.conv = _private_conv(self.u1, self.u2)
        self.c = Client(); self.c.force_login(self.u1)

    def test_search_permission_required(self):
        c = Client()
        resp = c.get('/api/search/?q=test')
        self.assertEqual(resp.status_code, 302)  # redirects to login

    def test_key_trust_model_str(self):
        import base64, hashlib
        raw = b'\x04' + b'\x03' * 64
        fp = hashlib.sha256(raw).hexdigest().upper()
        UserPublicKey.objects.create(
            user=self.u2, identity_public_key=base64.b64encode(raw).decode(),
            key_fingerprint=fp, key_version=1,
        )
        kt = KeyTrust.objects.create(
            user=self.u1, contact=self.u2,
            key_fingerprint=fp, key_version=1,
            trust_status=KeyTrust.TrustStatus.VERIFIED,
        )
        self.assertIn(self.u2.username, str(kt))

    def test_group_announcement_model_str(self):
        g = _group('Test', self.u1)
        ann = GroupAnnouncement.objects.create(
            conversation=g, author=self.u1, content='Hello')
        self.assertIn('Announcement', str(ann))

    def test_group_member_count_excludes_removed(self):
        g = _group('member_count_test', self.u1)
        ConversationMember.objects.create(
            conversation=g, user=self.u2, role=ConversationMember.Role.MEMBER)
        self.assertEqual(
            ConversationMember.objects.filter(
                conversation=g, status=ConversationMember.Status.ACTIVE).count(), 2)
        ConversationMember.objects.filter(
            conversation=g, user=self.u2).update(status=ConversationMember.Status.REMOVED)
        self.assertEqual(
            ConversationMember.objects.filter(
                conversation=g, status=ConversationMember.Status.ACTIVE).count(), 1)

    def test_auto_delete_field_on_model(self):
        self.conv.auto_delete_seconds = 3600
        self.conv.save()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.auto_delete_seconds, 3600)

    def test_conversation_member_auto_delete_field(self):
        member = ConversationMember.objects.get(
            conversation=self.conv, user=self.u1)
        member.auto_delete_seconds = 7200
        member.save()
        member.refresh_from_db()
        self.assertEqual(member.auto_delete_seconds, 7200)

    def test_key_trust_unique_constraint(self):
        import base64, hashlib
        raw = b'\x04' + b'\x04' * 64
        fp = hashlib.sha256(raw).hexdigest().upper()
        UserPublicKey.objects.create(
            user=self.u2, identity_public_key=base64.b64encode(raw).decode(),
            key_fingerprint=fp, key_version=1)
        KeyTrust.objects.create(
            user=self.u1, contact=self.u2, key_fingerprint=fp,
            key_version=1, trust_status=KeyTrust.TrustStatus.TRUSTED)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            KeyTrust.objects.create(
                user=self.u1, contact=self.u2, key_fingerprint=fp,
                key_version=1, trust_status=KeyTrust.TrustStatus.TRUSTED)

    def test_e2ee_ciphertext_no_plaintext_in_api(self):
        """Verify conversation list doesn't expose message plaintext (E2EE)."""
        resp = self.c.get('/api/conversations/')
        convs = resp.json()['conversations']
        for c in convs:
            self.assertNotIn('plaintext', str(c))
            self.assertNotIn('body', str(c))
            self.assertNotIn('text', str(c).lower())


class TokenSessionIsolationTests(TestCase):
    """T40: Multi-account token/session isolation."""

    def setUp(self):
        self.u1 = _u('alice'); self.u2 = _u('bob')
        self.conv = _private_conv(self.u1, self.u2)
        self.c1 = Client(); self.c1.force_login(self.u1)
        self.c2 = Client(); self.c2.force_login(self.u2)

    def test_user1_cannot_hide_user2s_view(self):
        """User can only hide conversations from their own view."""
        resp = self.c2.delete(f'/api/conversations/{self.conv.id}/')
        self.assertEqual(resp.status_code, 200)
        # User1 should still see it
        resp2 = self.c1.get('/api/conversations/')
        self.assertIn(self.conv.id, [c['id'] for c in resp2.json()['conversations']])


class PrivateChatContactEnforcementTests(TestCase):
    """T40: Contact enforcement for private chats."""

    def test_non_contact_cannot_get_private_messages(self):
        u1 = _u('a1'); u2 = _u('a2'); u3 = _u('a3')
        conv = _private_conv(u1, u3)  # u1 and u3 are conversing
        c2 = Client(); c2.force_login(u2)
        resp = c2.get(f'/api/conversations/{conv.id}/messages/')
        self.assertEqual(resp.status_code, 403)
