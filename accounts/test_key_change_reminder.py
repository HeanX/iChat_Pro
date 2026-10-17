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
        self.assertEqual(row['last_endorsed_key_version'], 1)
        self.assertEqual(row['last_endorsed_trust_status'], 'verified')

    def test_previous_and_last_endorsed_diverge_after_consecutive_rotations(self):
        """v1 verified -> v2 -> v3: previous is v2, the warning is about v1."""
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self._bob_uploads(_key_payload(3))

        row = self._trust_list_row()
        self.assertEqual(row['key_version'], 3)
        self.assertEqual(
            row['previous_key_version'],
            2,
            'previous_* must be the immediately preceding version, not the endorsed one',
        )
        self.assertEqual(row['previous_trust_status'], 'untrusted')
        self.assertEqual(
            row['last_endorsed_key_version'],
            1,
            'the warning is about the last endorsed fingerprint (v1)',
        )
        self.assertEqual(row['last_endorsed_trust_status'], 'verified')
        self.assertTrue(row['key_changed'])

    def test_contact_fingerprints_after_rotation_reports_change(self):
        """The endpoint chat.js consults must agree with the trust list."""
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)

        data = self._contact_keys()
        self.assertTrue(data['key_changed'])
        self.assertEqual(data['active_key']['key_fingerprint'], self.v2['key_fingerprint'])
        self.assertEqual(data['previous_key_version'], 1)
        self.assertEqual(data['last_endorsed_key_version'], 1)
        self.assertEqual(data['keys'][0]['trust_status'], 'untrusted')

    def test_contact_fingerprints_without_key_is_not_a_change(self):
        data = self._contact_keys()
        self.assertIsNone(data['active_key'])
        self.assertFalse(data['key_changed'])
        self.assertIsNone(data['previous_key_fingerprint'])
        self.assertIsNone(data['last_endorsed_key_fingerprint'])

    def test_delete_trust_only_targets_the_active_fingerprint(self):
        """DELETE keeps the audit trail; the reminder clears on re-verification.

        The contract in docs/handoff/accounts-auth.md #4 says superseded trust
        records are preserved for audit, so DELETE must not wipe the contact's
        history. A post-rotation reminder is resolved by verifying the new key.
        """
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self.assertTrue(self._trust_list_row()['key_changed'])

        response = self.alice_client.delete(reverse('key-trust', args=[self.bob.pk]))
        self.assertEqual(response.status_code, 200)
        # There is no row for v2 yet, so nothing is deleted and the v1 audit
        # row survives -- the reminder stays until the user acts on the new key.
        self.assertFalse(response.json()['deleted'])
        self.assertTrue(
            KeyTrust.objects.filter(
                user=self.alice,
                contact=self.bob,
                key_fingerprint=self.v1['key_fingerprint'],
            ).exists(),
            'the superseded verdict must be preserved for audit',
        )
        self.assertTrue(self._trust_list_row()['key_changed'])

        # Verifying the new key is what clears it.
        self.assertEqual(self._trust('verified').status_code, 200)
        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertEqual(row['key_version'], 2)
        self.assertEqual(
            KeyTrust.objects.filter(user=self.alice, contact=self.bob).count(),
            2,
            'both fingerprints keep their verdict',
        )

    def test_delete_trust_removes_only_the_active_row(self):
        """The happy path: both rows exist, DELETE clears v2 and keeps v1."""
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self._trust('verified')
        self.assertEqual(KeyTrust.objects.filter(user=self.alice, contact=self.bob).count(), 2)

        response = self.alice_client.delete(reverse('key-trust', args=[self.bob.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])

        remaining = list(
            KeyTrust.objects
            .filter(user=self.alice, contact=self.bob)
            .values_list('key_fingerprint', flat=True)
        )
        self.assertEqual(
            remaining,
            [self.v1['key_fingerprint']],
            'only the active fingerprint loses its verdict; the audit row survives',
        )
        self.assertEqual(KeyTrust.objects.get(user=self.alice, contact=self.bob).trust_status,
                         KeyTrust.TrustStatus.VERIFIED)

        # Losing the verdict for the current key brings the reminder back.
        row = self._trust_list_row()
        self.assertTrue(row['key_changed'])
        self.assertEqual(row['trust_status'], 'untrusted')
        self.assertEqual(row['last_endorsed_key_version'], 1)

    def test_last_endorsed_survives_reverification(self):
        """The endorsed history is reported even after the new key is verified.

        ``last_endorsed_*`` describes the fingerprint a rotation moved *away*
        from, so it stays available once the warning is resolved; only the
        never-endorsed case is null.
        """
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self.assertTrue(self._trust_list_row()['key_changed'])

        self.assertEqual(self._trust('verified').status_code, 200)
        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertEqual(row['trust_status'], 'verified')
        self.assertEqual(
            row['last_endorsed_key_version'],
            1,
            'the previously endorsed v1 is still reported after re-verifying v2',
        )
        self.assertEqual(row['last_endorsed_key_fingerprint'], self.v1['key_fingerprint'])
        self.assertEqual(row['last_endorsed_trust_status'], 'verified')

    def test_last_endorsed_is_null_when_nothing_else_was_endorsed(self):
        """A first key with its own untrusted verdict has no earlier endorsement."""
        self._bob_uploads(self.v1)
        self._trust('untrusted')

        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertIsNone(row['last_endorsed_key_version'])
        self.assertIsNone(row['last_endorsed_key_fingerprint'])

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
        self.assertIsNone(
            row['last_endorsed_key_version'],
            'nothing was ever endorsed, so there is nothing to warn about',
        )

    def test_last_endorsed_persists_when_new_key_is_explicitly_untrusted(self):
        """key_changed and last_endorsed_* answer different questions.

        Marking the rotated key 'untrusted' silences the reminder, but the
        earlier endorsement of v1 is history and keeps being reported.
        """
        self._bob_uploads(self.v1)
        self._trust('verified')
        self._bob_uploads(self.v2)
        self.assertTrue(self._trust_list_row()['key_changed'])

        self.assertEqual(self._trust('untrusted').status_code, 200)
        row = self._trust_list_row()
        self.assertFalse(row['key_changed'])
        self.assertEqual(row['trust_status'], 'untrusted')
        self.assertEqual(
            row['last_endorsed_key_version'],
            1,
            'the earlier endorsement stays visible even though the warning is off',
        )
        self.assertEqual(row['last_endorsed_key_fingerprint'], self.v1['key_fingerprint'])

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

    def test_trust_post_rejects_malformed_json_with_400(self):
        """A bad body must be a JSON 400, not a 500 HTML error page."""
        self._bob_uploads(self.v1)
        url = reverse('key-trust', args=[self.bob.pk])

        for body in ('not json', '[1, 2]', 'null', '"text"'):
            with self.subTest(body=body):
                response = self.alice_client.post(
                    url, data=body, content_type='application/json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {'error': 'invalid_json'})

        self.assertFalse(
            KeyTrust.objects.filter(user=self.alice, contact=self.bob).exists(),
            'a rejected body must not write trust rows',
        )
