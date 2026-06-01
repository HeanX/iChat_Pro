"""Tests for authentication views: registration, login, and logout."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Contact, FriendRequest


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
        # After registration the user should be authenticated
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
        # Should redirect to login since user is logged out
        self.assertRedirects(response, f'{self.LOGIN_URL}?next={reverse("index")}')


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

    # ── contact list page ──────────────────────────────────────────

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

    # ── send friend request ────────────────────────────────────────

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

    # ── accept / reject ────────────────────────────────────────────

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

    # ── delete contact ─────────────────────────────────────────────

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

    # ── search users (JSON endpoint) ───────────────────────────────

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

    # ── full flow integration ──────────────────────────────────────

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