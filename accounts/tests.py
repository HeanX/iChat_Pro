import base64
import hashlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import UserPublicKey


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
