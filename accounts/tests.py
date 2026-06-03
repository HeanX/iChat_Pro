"""Tests for accounts: registration, login, contacts, profile, groups, E2EE keys."""

import base64
import hashlib

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from chat.models import Conversation, ConversationMember

from .models import (
    Contact,
    FriendRequest,
    UserProfile,
    UserPublicKey,
)
# Group & GroupMember consolidated into chat.Conversation (T22)


# ─── E2EE multi-version public-key API ─────────────────────────────────


class UserPublicKeyApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='alice',
            password='test-password',
        )
        self.other_user = get_user_model().objects.create_user(
            username='bob',
            password='test-password',
        )
        self.client.force_login(self.user)

    def _payload(self, key_bytes=b'public-key-v1'):
        return {
            'identity_public_key': base64.b64encode(key_bytes).decode(),
            'key_fingerprint': hashlib.sha256(key_bytes).hexdigest().upper(),
            'algorithm': UserPublicKey.ALGORITHM_ECDH_P256,
        }

    def test_upload_creates_public_key_without_private_material(self):
        response = self.client.post(
            reverse('upload-public-key'),
            self._payload(),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        public_key = UserPublicKey.objects.get(user=self.user)
        self.assertEqual(public_key.key_version, 1)
        self.assertNotIn('private', response.json()['key'])
        self.assertFalse(hasattr(public_key, 'private_key'))

    def test_upload_same_key_is_idempotent(self):
        payload = self._payload()
        first = self.client.post(reverse('upload-public-key'), payload, content_type='application/json')
        second = self.client.post(reverse('upload-public-key'), payload, content_type='application/json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(UserPublicKey.objects.filter(user=self.user).count(), 1)

    def test_upload_new_key_rotates_active_version(self):
        self.client.post(reverse('upload-public-key'), self._payload(), content_type='application/json')
        response = self.client.post(
            reverse('upload-public-key'),
            self._payload(b'public-key-v2'),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['key']['key_version'], 2)
        self.assertEqual(UserPublicKey.objects.filter(user=self.user, is_active=True).count(), 1)
        self.assertFalse(UserPublicKey.objects.get(user=self.user, key_version=1).is_active)

    def test_upload_rejects_private_key_material(self):
        payload = self._payload()
        payload['private_key'] = 'must-never-reach-server'

        response = self.client.post(
            reverse('upload-public-key'),
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'private_key_material_not_allowed')
        self.assertFalse(UserPublicKey.objects.exists())

    def test_upload_rejects_incorrect_fingerprint(self):
        payload = self._payload()
        payload['key_fingerprint'] = '0' * 64

        response = self.client.post(
            reverse('upload-public-key'),
            payload,
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'fingerprint_mismatch')

    def test_key_query_batch_and_fingerprint_endpoints_return_active_key(self):
        upload = self.client.post(
            reverse('upload-public-key'),
            self._payload(),
            content_type='application/json',
        )
        expected = upload.json()['key']

        detail = self.client.get(reverse('public-key', args=[self.user.pk]))
        batch = self.client.post(
            reverse('batch-public-keys'),
            {'user_ids': [self.user.pk, self.other_user.pk]},
            content_type='application/json',
        )
        fingerprint = self.client.get(reverse('public-key-fingerprint', args=[self.user.pk]))

        self.assertEqual(detail.json()['key'], expected)
        self.assertEqual(batch.json()['keys'], [expected])
        self.assertEqual(fingerprint.json()['key_fingerprint'], expected['key_fingerprint'])
        self.assertEqual(fingerprint.json()['key_version'], 1)

    def test_key_version_endpoint_returns_inactive_historical_key(self):
        first = self.client.post(
            reverse('upload-public-key'),
            self._payload(),
            content_type='application/json',
        ).json()['key']
        self.client.post(
            reverse('upload-public-key'),
            self._payload(b'public-key-v2'),
            content_type='application/json',
        )

        response = self.client.get(reverse('public-key-version', args=[self.user.pk, 1]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key']['identity_public_key'], first['identity_public_key'])
        self.assertFalse(response.json()['key']['is_active'])

    def test_anonymous_user_cannot_access_key_api(self):
        self.client.logout()

        response = self.client.get(reverse('public-key', args=[self.user.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class UserPublicKeyModelTests(TestCase):
    """Test UserPublicKey multi-version model constraints."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='p')

    def _make(self, user, version, key_bytes=b'k'):
        return UserPublicKey.objects.create(
            user=user,
            identity_public_key=base64.b64encode(key_bytes).decode(),
            key_fingerprint=hashlib.sha256(key_bytes).hexdigest().upper(),
            key_version=version,
        )

    def test_create_public_key(self):
        pk = self._make(self.alice, 1)
        self.assertEqual(UserPublicKey.objects.count(), 1)
        self.assertEqual(pk.algorithm, 'ECDH-P256')
        self.assertTrue(pk.is_active)

    def test_unique_user_version(self):
        self._make(self.alice, 1, b'k1')
        with self.assertRaises(Exception):
            self._make(self.alice, 1, b'k2')

    def test_multiple_versions_per_user_allowed(self):
        self._make(self.alice, 1, b'k1')
        self._make(self.alice, 2, b'k2')
        self.assertEqual(UserPublicKey.objects.filter(user=self.alice).count(), 2)

    def test_str_contains_username_and_version(self):
        pk = self._make(self.alice, 3)
        self.assertIn('alice', str(pk))
        self.assertIn('v3', str(pk))


# ─── Registration ─────────────────────────────────────────────────────


class RegistrationViewTests(TestCase):
    """Test registration validation and flow."""

    REGISTER_URL = reverse('register')
    INDEX_URL = reverse('index')

    VALID_DATA = {
        'username': 'newuser',
        'email': 'new@example.com',
        'password1': 'secure1234',
        'password2': 'secure1234',
    }

    def test_register_page_loads(self):
        response = self.client.get(self.REGISTER_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/register.html')

    def test_register_with_valid_data(self):
        response = self.client.post(self.REGISTER_URL, self.VALID_DATA)
        self.assertRedirects(response, self.INDEX_URL)
        self.assertTrue(User.objects.filter(username='newuser').exists())

    def test_register_creates_user(self):
        self.client.post(self.REGISTER_URL, self.VALID_DATA)
        user = User.objects.get(username='newuser')
        self.assertEqual(user.email, 'new@example.com')
        self.assertTrue(user.is_active)

    def test_register_logs_user_in(self):
        response = self.client.post(self.REGISTER_URL, self.VALID_DATA)
        self.assertRedirects(response, self.INDEX_URL)
        response = self.client.get(self.INDEX_URL)
        self.assertEqual(response.status_code, 200)

    def test_register_username_too_short(self):
        data = {**self.VALID_DATA, 'username': 'ab'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='ab').exists())

    def test_register_password_too_short(self):
        data = {**self.VALID_DATA, 'password1': 'Ab1', 'password2': 'Ab1'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_password_no_letter(self):
        data = {**self.VALID_DATA, 'password1': '12345678', 'password2': '12345678'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_password_no_digit(self):
        data = {**self.VALID_DATA, 'password1': 'abcdefgh', 'password2': 'abcdefgh'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_passwords_do_not_match(self):
        data = {**self.VALID_DATA, 'password2': 'different1'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_duplicate_username(self):
        User.objects.create_user(username='newuser', password='testpass')
        response = self.client.post(self.REGISTER_URL, self.VALID_DATA)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='newuser').count(), 1)

    def test_register_duplicate_username_case_insensitive(self):
        User.objects.create_user(username='NewUser', password='testpass')
        data = {**self.VALID_DATA, 'username': 'newuser'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username__iexact='newuser').count(), 1)

    def test_register_duplicate_email(self):
        User.objects.create_user(
            username='existing', email='new@example.com', password='testpass',
        )
        response = self.client.post(self.REGISTER_URL, self.VALID_DATA)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_missing_email(self):
        data = {**self.VALID_DATA, 'email': ''}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_invalid_email(self):
        data = {**self.VALID_DATA, 'email': 'not-an-email'}
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newuser').exists())

    def test_register_redirects_when_authenticated(self):
        User.objects.create_user(username='loggedin', password='testpass')
        self.client.login(username='loggedin', password='testpass')
        response = self.client.get(self.REGISTER_URL)
        self.assertRedirects(response, self.INDEX_URL)

    def test_register_password_similar_to_username(self):
        data = {
            **self.VALID_DATA,
            'username': 'secure1234',
            'password1': 'secure1234',
            'password2': 'secure1234',
        }
        response = self.client.post(self.REGISTER_URL, data)
        self.assertEqual(response.status_code, 200)


# ─── Login / Logout ───────────────────────────────────────────────────


class LoginViewTests(TestCase):
    """Test login error handling, disabled accounts, and success flow."""

    LOGIN_URL = reverse('login')
    INDEX_URL = reverse('index')

    @classmethod
    def setUpTestData(cls):
        cls.active_user = User.objects.create_user(
            username='alice',
            email='alice@example.com',
            password='correctpass1',
        )
        cls.disabled_user = User.objects.create_user(
            username='bob',
            email='bob@example.com',
            password='correctpass1',
        )
        cls.disabled_user.is_active = False
        cls.disabled_user.save()

    def test_login_page_loads(self):
        response = self.client.get(self.LOGIN_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/login.html')

    def test_login_with_valid_credentials(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'alice',
            'password': 'correctpass1',
        })
        self.assertRedirects(response, self.INDEX_URL)

    def test_login_sets_session(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'alice',
            'password': 'correctpass1',
        })
        self.assertRedirects(response, self.INDEX_URL)
        response = self.client.get(self.INDEX_URL)
        self.assertEqual(response.status_code, 200)

    def test_login_wrong_password(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'alice',
            'password': 'wrongpassword1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid password')

    def test_login_nonexistent_user(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'ghost',
            'password': 'whatever1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No account found')

    def test_login_disabled_account(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'bob',
            'password': 'correctpass1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'disabled')

    def test_login_redirects_when_authenticated(self):
        self.client.login(username='alice', password='correctpass1')
        response = self.client.get(self.LOGIN_URL)
        self.assertRedirects(response, self.INDEX_URL)

    def test_login_blank_username(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': '',
            'password': 'somepass1',
        })
        self.assertEqual(response.status_code, 200)

    def test_login_blank_password(self):
        response = self.client.post(self.LOGIN_URL, {
            'username': 'alice',
            'password': '',
        })
        self.assertEqual(response.status_code, 200)


class LogoutViewTests(TestCase):
    """Test logout behavior."""

    LOGIN_URL = reverse('login')
    LOGOUT_URL = reverse('logout')

    def setUp(self):
        User.objects.create_user(
            username='alice', password='correctpass1',
        )
        self.client.login(username='alice', password='correctpass1')

    def test_logout_redirects_to_login(self):
        response = self.client.post(self.LOGOUT_URL)
        self.assertRedirects(response, self.LOGIN_URL)

    def test_logout_clears_session(self):
        self.client.post(self.LOGOUT_URL)
        response = self.client.get(reverse('index'))
        self.assertRedirects(response, f'{self.LOGIN_URL}?next={reverse("index")}')


# ─── Contacts ─────────────────────────────────────────────────────────


class ContactModelTests(TestCase):
    """Test Contact and FriendRequest model constraints."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='p')
        cls.bob = User.objects.create_user(username='bob', password='p')

    def test_create_contact(self):
        contact = Contact.objects.create(user=self.alice, contact=self.bob)
        self.assertEqual(Contact.objects.count(), 1)
        self.assertIn(contact, Contact.objects.filter(user=self.alice))

    def test_unique_contact_pair(self):
        Contact.objects.create(user=self.alice, contact=self.bob)
        with self.assertRaises(Exception):
            Contact.objects.create(user=self.alice, contact=self.bob)

    def test_friend_request_str(self):
        req = FriendRequest.objects.create(
            sender=self.alice, receiver=self.bob,
        )
        self.assertIn('alice', str(req))
        self.assertIn('bob', str(req))

    def test_unique_pending_request(self):
        FriendRequest.objects.create(
            sender=self.alice, receiver=self.bob, status='pending',
        )
        with self.assertRaises(Exception):
            FriendRequest.objects.create(
                sender=self.alice, receiver=self.bob, status='pending',
            )


class ContactViewTests(TestCase):
    """Test contact list, search, and CRUD flows."""

    CONTACTS_URL = reverse('contacts')
    LOGIN_URL = reverse('login')

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username='alice', password='alicepass',
        )
        cls.bob = User.objects.create_user(
            username='bob', password='bobpass',
        )
        cls.charlie = User.objects.create_user(
            username='charlie', password='charliepass',
        )
        cls.dave = User.objects.create_user(username='dave', password='davepass')

    def setUp(self):
        self.client.login(username='alice', password='alicepass')

    def test_contacts_page_requires_login(self):
        self.client.post(reverse('logout'))
        response = self.client.get(self.CONTACTS_URL)
        self.assertRedirects(
            response,
            f'{self.LOGIN_URL}?next={self.CONTACTS_URL}',
        )

    def test_contacts_page_loads(self):
        response = self.client.get(self.CONTACTS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/contacts.html')

    def test_contacts_page_shows_no_contacts_initially(self):
        response = self.client.get(self.CONTACTS_URL)
        self.assertContains(response, 'No contacts yet')

    def test_send_friend_request(self):
        response = self.client.post(
            reverse('friend_request_send'),
            {'username': 'bob'},
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertTrue(
            FriendRequest.objects.filter(
                sender=self.alice, receiver=self.bob, status='pending',
            ).exists(),
        )

    def test_send_duplicate_request(self):
        self.client.post(
            reverse('friend_request_send'), {'username': 'bob'},
        )
        response = self.client.post(
            reverse('friend_request_send'), {'username': 'bob'},
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertEqual(
            FriendRequest.objects.filter(
                sender=self.alice, receiver=self.bob, status='pending',
            ).count(),
            1,
        )

    def test_send_request_to_self(self):
        response = self.client.post(
            reverse('friend_request_send'), {'username': 'alice'},
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertFalse(
            FriendRequest.objects.filter(
                sender=self.alice, receiver=self.alice,
            ).exists(),
        )

    def test_send_request_to_existing_contact(self):
        Contact.objects.create(user=self.alice, contact=self.bob)
        response = self.client.post(
            reverse('friend_request_send'), {'username': 'bob'},
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertEqual(
            FriendRequest.objects.filter(
                sender=self.alice, receiver=self.bob,
            ).count(),
            0,
        )

    def test_send_request_blank_username(self):
        response = self.client.post(
            reverse('friend_request_send'), {'username': ''},
        )
        self.assertRedirects(response, self.CONTACTS_URL)

    def test_accept_friend_request_creates_contact(self):
        req = FriendRequest.objects.create(
            sender=self.bob, receiver=self.alice,
        )
        response = self.client.post(
            reverse('friend_request_accept', args=[req.id]),
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        req.refresh_from_db()
        self.assertEqual(req.status, 'accepted')
        self.assertTrue(
            Contact.objects.filter(
                user=self.bob, contact=self.alice,
            ).exists(),
        )

    def test_cannot_accept_others_request(self):
        req = FriendRequest.objects.create(
            sender=self.bob, receiver=self.charlie,
        )
        response = self.client.post(
            reverse('friend_request_accept', args=[req.id]),
        )
        self.assertEqual(response.status_code, 404)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_reject_friend_request(self):
        req = FriendRequest.objects.create(
            sender=self.bob, receiver=self.alice,
        )
        response = self.client.post(
            reverse('friend_request_reject', args=[req.id]),
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        req.refresh_from_db()
        self.assertEqual(req.status, 'rejected')

    def test_cannot_reject_others_request(self):
        req = FriendRequest.objects.create(
            sender=self.bob, receiver=self.charlie,
        )
        response = self.client.post(
            reverse('friend_request_reject', args=[req.id]),
        )
        self.assertEqual(response.status_code, 404)
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')

    def test_delete_contact(self):
        contact = Contact.objects.create(user=self.alice, contact=self.bob)
        response = self.client.post(
            reverse('contact_delete', args=[contact.id]),
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertFalse(Contact.objects.filter(id=contact.id).exists())

    def test_cannot_delete_others_contact(self):
        contact = Contact.objects.create(user=self.bob, contact=self.charlie)
        response = self.client.post(
            reverse('contact_delete', args=[contact.id]),
        )
        self.assertRedirects(response, self.CONTACTS_URL)
        self.assertTrue(Contact.objects.filter(id=contact.id).exists())

    def test_search_users_finds_match(self):
        response = self.client.get(
            reverse('search_users'), {'q': 'bob'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['username'], 'bob')

    def test_search_excludes_self(self):
        response = self.client.get(
            reverse('search_users'), {'q': 'alice'},
        )
        data = response.json()
        usernames = [r['username'] for r in data['results']]
        self.assertNotIn('alice', usernames)

    def test_search_empty_query(self):
        response = self.client.get(
            reverse('search_users'), {'q': ''},
        )
        data = response.json()
        self.assertEqual(len(data['results']), 0)

    def test_full_friend_flow(self):
        # Alice sends request to Charlie
        self.client.post(
            reverse('friend_request_send'), {'username': 'charlie'},
        )
        self.assertTrue(
            FriendRequest.objects.filter(
                sender=self.alice, receiver=self.charlie, status='pending',
            ).exists(),
        )

        # Charlie logs in and accepts
        self.client.login(username='charlie', password='charliepass')
        req = FriendRequest.objects.get(
            sender=self.alice, receiver=self.charlie, status='pending',
        )
        self.client.post(reverse('friend_request_accept', args=[req.id]))
        self.assertTrue(
            Contact.objects.filter(
                user=self.alice, contact=self.charlie,
            ).exists(),
        )

        # Alice removes the contact
        self.client.login(username='alice', password='alicepass')
        contact = Contact.objects.get(user=self.alice, contact=self.charlie)
        self.client.post(reverse('contact_delete', args=[contact.id]))
        self.assertFalse(
            Contact.objects.filter(
                user=self.alice, contact=self.charlie,
            ).exists(),
        )


# ─── Profile ──────────────────────────────────────────────────────────


class ProfileEditTests(TestCase):
    """Test profile editing."""

    PROFILE_EDIT_URL = reverse('profile_edit')
    INDEX_URL = reverse('index')

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(
            username='alice', password='alicepass',
        )

    def setUp(self):
        self.client.login(username='alice', password='alicepass')

    def test_profile_edit_page_loads(self):
        response = self.client.get(self.PROFILE_EDIT_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/profile_edit.html')

    def test_profile_edit_requires_login(self):
        self.client.post(reverse('logout'))
        response = self.client.get(self.PROFILE_EDIT_URL)
        self.assertEqual(response.status_code, 302)

    def test_profile_edit_creates_profile(self):
        self.assertFalse(
            UserProfile.objects.filter(user=self.alice).exists(),
        )
        self.client.post(self.PROFILE_EDIT_URL, {
            'nickname': 'Ally',
            'bio': 'Hello world',
        })
        self.assertTrue(
            UserProfile.objects.filter(user=self.alice).exists(),
        )

    def test_profile_edit_updates_nickname(self):
        UserProfile.objects.create(user=self.alice, nickname='Old')
        self.client.post(self.PROFILE_EDIT_URL, {
            'nickname': 'NewName',
            'bio': '',
        })
        self.alice.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.nickname, 'NewName')

    def test_profile_edit_success_redirect(self):
        response = self.client.post(self.PROFILE_EDIT_URL, {
            'nickname': 'Ally',
            'bio': 'Hi',
        })
        self.assertRedirects(response, self.INDEX_URL)


# ─── Groups ───────────────────────────────────────────────────────────


class GroupModelTests(TestCase):
    """Test chat.Conversation and ConversationMember as canonical group models (T22)."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='p')
        cls.bob = User.objects.create_user(username='bob', password='p')

    def test_create_group_conversation(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Test', created_by=self.alice,
        )
        self.assertEqual(conv.type, Conversation.Type.GROUP)
        self.assertEqual(conv.name, 'Test')
        self.assertEqual(conv.members.count(), 0)

    def test_add_member(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='G1', created_by=self.alice,
        )
        cm = ConversationMember.objects.create(
            conversation=conv, user=self.bob, role=ConversationMember.Role.MEMBER,
        )
        self.assertEqual(conv.members.count(), 1)
        self.assertIn(cm, conv.members.all())

    def test_unique_member_constraint(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='G2', created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.bob,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            ConversationMember.objects.create(
                conversation=conv, user=self.bob,
            )

    def test_group_str(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='MyGroup', created_by=self.alice,
        )
        expected = f'Conversation #{conv.id} (Group Chat)'
        self.assertEqual(str(conv), expected)


class GroupViewTests(TestCase):
    """Test group CRUD views using chat.Conversation (T22)."""

    GROUPS_URL = reverse('groups')

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='p')
        cls.bob = User.objects.create_user(username='bob', password='q')
        Contact.objects.create(user=cls.alice, contact=cls.bob)

    def setUp(self):
        self.client.login(username='alice', password='p')

    def test_groups_page_loads(self):
        response = self.client.get(self.GROUPS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pages/groups.html')

    def test_groups_requires_login(self):
        self.client.post(reverse('logout'))
        response = self.client.get(self.GROUPS_URL)
        self.assertEqual(response.status_code, 302)

    def test_create_group(self):
        self.client.post(reverse('group_create'), {'name': 'Alpha'})
        self.assertTrue(
            Conversation.objects.filter(
                name='Alpha', type=Conversation.Type.GROUP,
            ).exists(),
        )
        conv = Conversation.objects.get(name='Alpha', type=Conversation.Type.GROUP)
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=conv, user=self.alice, role='owner',
            ).exists(),
        )

    def test_create_group_empty_name(self):
        self.client.post(reverse('group_create'), {'name': ''})
        self.assertFalse(
            Conversation.objects.filter(type=Conversation.Type.GROUP).exists(),
        )

    def test_group_detail_loads(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Beta', created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        response = self.client.get(
            reverse('group_detail', args=[conv.id]),
        )
        self.assertEqual(response.status_code, 200)

    def test_group_detail_redirects_non_member(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Secret', created_by=self.bob,
        )
        response = self.client.get(
            reverse('group_detail', args=[conv.id]),
        )
        self.assertRedirects(response, self.GROUPS_URL)

    def test_add_member_to_group(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Gamma', created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        response = self.client.post(
            reverse('group_add_member', args=[conv.id]),
            {'username': 'bob'},
        )
        self.assertRedirects(
            response, reverse('group_detail', args=[conv.id]),
        )
        self.assertTrue(
            ConversationMember.objects.filter(
                conversation=conv, user=self.bob,
            ).exists(),
        )

    def test_add_non_contact_to_group(self):
        stranger = User.objects.create_user(username='stranger', password='x')
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Delta', created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.alice, role=ConversationMember.Role.OWNER,
        )
        response = self.client.post(
            reverse('group_add_member', args=[conv.id]),
            {'username': 'stranger'},
        )
        self.assertFalse(
            ConversationMember.objects.filter(
                conversation=conv, user=stranger,
            ).exists(),
        )

    def test_leave_group(self):
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Epsilon', created_by=self.alice,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.alice,
        )
        response = self.client.post(
            reverse('group_leave', args=[conv.id]),
        )
        self.assertRedirects(response, self.GROUPS_URL)
        membership = ConversationMember.objects.get(
            conversation=conv, user=self.alice,
        )
        self.assertEqual(membership.status, ConversationMember.Status.LEFT)

    # ── T23: authorization tests ───────────────────────────────────

    def test_non_member_cannot_add_member(self):
        """Outsider cannot add members by guessing a group ID."""
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Outsider', created_by=self.bob,
        )
        # alice is not a member
        response = self.client.post(
            reverse('group_add_member', args=[conv.id]),
            {'username': 'bob'},
        )
        self.assertFalse(
            ConversationMember.objects.filter(
                conversation=conv, user=self.bob,
            ).exists(),
        )

    def test_regular_member_cannot_add_member(self):
        """Regular member without admin role cannot add others."""
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Regular', created_by=self.bob,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.bob, role=ConversationMember.Role.OWNER,
        )
        ConversationMember.objects.create(
            conversation=conv, user=self.alice, role=ConversationMember.Role.MEMBER,
        )
        self.client.login(username='alice', password='p')
        response = self.client.post(
            reverse('group_add_member', args=[conv.id]),
            {'username': 'stranger'},
        )
        # alice is a member but not an admin — should be blocked
        stranger = User.objects.filter(username='stranger').first()
        self.assertFalse(
            ConversationMember.objects.filter(
                conversation=conv, user=stranger,
            ).exists(),
        )

    def test_add_member_requires_login(self):
        """Unauthenticated user cannot add members."""
        conv = Conversation.objects.create(
            type=Conversation.Type.GROUP, name='Auth', created_by=self.alice,
        )
        self.client.post(reverse('logout'))
        response = self.client.post(
            reverse('group_add_member', args=[conv.id]),
            {'username': 'bob'},
        )
        self.assertTrue(response.status_code in (301, 302))
