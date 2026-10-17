"""P4 Test T08 (#189): key-fingerprint and key-change reminders.

Covers the acceptance item "新密钥不自动继承旧信任状态" for the server-side
trust API. The client mirror of the same rule is exercised by
``chat/tests/js/key_change_reminder.test.js``.

Invariants under test:

* a rotated key starts as ``untrusted`` and never inherits the old verdict;
* the old fingerprint keeps its historical record for audit;
* the "key changed" warning is raised only while the *current* fingerprint
  carries a verdict that was given for a *different* fingerprint.
"""

import base64
import hashlib
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import KeyTrust

User = get_user_model()

ALGORITHM = 'ECDH-P256'


def _key_payload(seed):
    """Render a deterministic, base64-decodable pseudo public key."""
    raw = b'\x04' + bytes([seed]) * 64
    return {
        'identity_public_key': base64.b64encode(raw).decode(),
        'key_fingerprint': hashlib.sha256(raw).hexdigest().upper(),
        'algorithm': ALGORITHM,
    }


class KeyChangeReminderTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='test-password')
        self.bob = User.objects.create_user(username='bob', password='test-password')

        # Alice is the viewer; Bob is the contact who rotates his key.
        self.alice_client = self.client_class()
        self.alice_client.force_login(self.alice)
        self.bob_client = self.client_class()
        self.bob_client.force_login(self.bob)

        self.v1 = _key_payload(1)
        self.v2 = _key_payload(2)

    # ── helpers ──────────────────────────────────────────────────────

    def _bob_uploads(self, payload):
        return self.bob_client.post(
            reverse('upload-public-key'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def _trust(self, status):
        return self.alice_client.post(
            reverse('key-trust', args=[self.bob.pk]),
            data=json.dumps({'trust_status': status}),
            content_type='application/json',
        )

    def _trust_list_row(self):
        response = self.alice_client.get(reverse('key-trust-list'))
        self.assertEqual(response.status_code, 200)
        rows = [r for r in response.json()['trusts'] if r['contact_id'] == self.bob.pk]
        self.assertEqual(len(rows), 1, 'exactly one row per contact is expected')
        return rows[0]

    def _contact_keys(self):
        response = self.alice_client.get(reverse('contact-fingerprints', args=[self.bob.pk]))
        self.assertEqual(response.status_code, 200)
        return response.json()

    # ── trust is never inherited by the new key version ──────────────

    def test_rotated_key_is_untrusted_and_old_record_is_kept(self):
        self.assertEqual(self._bob_uploads(self.v1).status_code, 201)
        self.assertEqual(self._trust('verified').status_code, 200)

        # Bob rotates: same account, brand-new fingerprint, version 2.
        rotated = self._bob_uploads(self.v2)
        self.assertEqual(rotated.status_code, 201)
        self.assertEqual(rotated.json()['key']['key_version'], 2)

        data = self._contact_keys()
        self.assertEqual(data['active_key']['key_fingerprint'], self.v2['key_fingerprint'])
        self.assertEqual(data['active_key']['key_version'], 2, 'rotation must bump the version')

        # The new fingerprint carries no verdict ...
        by_fingerprint = {k['key_fingerprint']: k for k in data['keys']}
        self.assertEqual(by_fingerprint[self.v2['key_fingerprint']]['trust_status'], 'untrusted')
        # ... while the historical verdict survives for audit.
        self.assertEqual(
            by_fingerprint[self.v1['key_fingerprint']]['trust_status'],
            'verified',
        )
        self.assertTrue(
            KeyTrust.objects.filter(
                user=self.alice,
                contact=self.bob,
                key_fingerprint=self.v1['key_fingerprint'],
                trust_status=KeyTrust.TrustStatus.VERIFIED,
            ).exists(),
            'the v1 audit row must not be deleted or downgraded by a rotation',
        )
        self.assertFalse(
            KeyTrust.objects.filter(
                user=self.alice,
                contact=self.bob,
                key_fingerprint=self.v2['key_fingerprint'],
            ).exists(),
            'the new fingerprint must not receive an implicit trust row',
        )

    def test_trust_post_binds_verdict_to_the_active_key(self):
        self.assertEqual(self._bob_uploads(self.v1).status_code, 201)
        self.assertEqual(self._bob_uploads(self.v2).status_code, 201)
        self.assertEqual(self._trust('verified').status_code, 200)

        stored = KeyTrust.objects.get(user=self.alice, contact=self.bob)
        self.assertEqual(stored.key_fingerprint, self.v2['key_fingerprint'])
        self.assertEqual(stored.key_version, 2)
        self.assertFalse(
            KeyTrust.objects.filter(
                user=self.alice,
                contact=self.bob,
                key_fingerprint=self.v1['key_fingerprint'],
            ).exists(),
            'a post-rotation trust action must not retroactively trust v1',
        )

    # ── the change warning must describe the current key ─────────────

    def test_trust_list_after_rotation_reports_current_key(self):
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)

        row = self._trust_list_row()
        self.assertEqual(
            row['key_version'],
            2,
            'the row must describe the active key version, not the stale v1 record',
        )
        self.assertEqual(row['key_fingerprint'], self.v2['key_fingerprint'])
        self.assertEqual(
            row['trust_status'],
            'untrusted',
            'the new key must surface as untrusted, never as the inherited verdict',
        )
        self.assertTrue(row['key_changed'], 'a verified v1 replaced by an unverified v2 is a change')
        self.assertEqual(row['previous_key_fingerprint'], self.v1['key_fingerprint'])
        self.assertEqual(row['previous_key_version'], 1)
        self.assertEqual(row['previous_trust_status'], 'verified')

    def test_trust_list_not_changed_when_current_key_is_untrusted(self):
        self._bob_uploads(self.v1)
        self._trust('untrusted')
        self._bob_uploads(self.v2)

        row = self._trust_list_row()
        self.assertFalse(
            row['key_changed'],
            'rotating away from a key the user never endorsed is not a security event',
        )
        self.assertEqual(row['trust_status'], 'untrusted')
        self.assertEqual(row['previous_trust_status'], 'untrusted')

    def test_trust_list_not_changed_after_reverification(self):
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self.assertTrue(self._trust_list_row()['key_changed'])

        # The user re-verifies the new fingerprint: the warning must clear.
        self.assertEqual(self._trust('verified').status_code, 200)
        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertEqual(row['key_version'], 2)
        self.assertEqual(row['trust_status'], 'verified')

    def test_trust_list_without_rotation_is_not_flagged(self):
        self._bob_uploads(self.v1)
        self._trust('verified')

        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertEqual(row['key_version'], 1)
        self.assertEqual(row['trust_status'], 'verified')
        self.assertFalse(
            KeyTrust.objects.filter(
                user=self.alice,
                contact=self.bob,
                key_fingerprint=self.v2['key_fingerprint'],
            ).exists(),
            'reading the trust list must never create trust rows',
        )
