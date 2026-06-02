/**
 * key_manager.js — Client-side ECDH P-256 Key Management
 *
 * Responsibilities:
 *  - Generate ECDH P-256 key pairs via Web Crypto API
 *  - Export public key as SPKI (Base64) and fingerprint (SHA-256 hex)
 *  - Keep private key in localStorage ONLY — never sent to server
 *  - Upload public key + fingerprint to Django backend
 *  - Backup: export key pair to downloadable JSON file
 *  - Import: restore key pair from JSON file
 *
 * Globals: window.KeyManager (class)
 */

(function () {
    'use strict';

    const STORAGE_KEY_PRIVATE = 'ichat_ecdh_private_pkcs8';
    const STORAGE_KEY_PUBLIC = 'ichat_ecdh_public_spki';
    const STORAGE_KEY_FINGERPRINT = 'ichat_ecdh_fingerprint';

    // ── helpers ────────────────────────────────────────────────────

    function arrayBufferToBase64(buffer) {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    function base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    function arrayBufferToHex(buffer) {
        return Array.from(new Uint8Array(buffer))
            .map(b => b.toString(16).padStart(2, '0'))
            .join('')
            .toUpperCase();
    }

    function getCsrfToken() {
        const match = document.cookie.match(
            /(?:^|;\s*)csrftoken=([^;]*)/,
        );
        return match ? decodeURIComponent(match[1]) : '';
    }

    // ── KeyManager ─────────────────────────────────────────────────

    class KeyManager {
        constructor() {
            this._ready = false;
        }

        /**
         * True if a private key exists in localStorage.
         */
        hasExistingKeys() {
            return !!localStorage.getItem(STORAGE_KEY_PRIVATE);
        }

        /**
         * Generate a fresh ECDH P-256 key pair and persist to localStorage.
         * Returns { publicKeySpki, fingerprint } — private key NEVER returned.
         */
        async generateKeyPair() {
            const keyPair = await window.crypto.subtle.generateKey(
                { name: 'ECDH', namedCurve: 'P-256' },
                true, // extractable
                ['deriveKey', 'deriveBits'],
            );

            // Export public key as SPKI (SubjectPublicKeyInfo)
            const spkiRaw = await window.crypto.subtle.exportKey(
                'spki',
                keyPair.publicKey,
            );
            const publicKeySpki = arrayBufferToBase64(spkiRaw);

            // Export private key as PKCS#8 — stored ONLY in localStorage
            const pkcs8Raw = await window.crypto.subtle.exportKey(
                'pkcs8',
                keyPair.privateKey,
            );
            const privateKeyPkcs8 = arrayBufferToBase64(pkcs8Raw);

            // Compute SHA-256 fingerprint of the SPKI public key
            const fingerprint = await this._computeFingerprint(spkiRaw);

            // Persist to localStorage
            localStorage.setItem(STORAGE_KEY_PRIVATE, privateKeyPkcs8);
            localStorage.setItem(STORAGE_KEY_PUBLIC, publicKeySpki);
            localStorage.setItem(STORAGE_KEY_FINGERPRINT, fingerprint);

            this._ready = true;
            return { publicKeySpki, fingerprint };
        }

        /**
         * Load existing keys from localStorage.
         * Returns { publicKeySpki, fingerprint, privateKeyPkcs8 } or null.
         */
        loadKeys() {
            const priv = localStorage.getItem(STORAGE_KEY_PRIVATE);
            const pub = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);

            if (!priv || !pub || !fp) return null;

            this._ready = true;
            return {
                publicKeySpki: pub,
                fingerprint: fp,
                privateKeyPkcs8: priv, // for backup only
            };
        }

        /**
         * Get the CryptoKey object for the local private key.
         */
        async getPrivateCryptoKey() {
            const pkcs8B64 = localStorage.getItem(STORAGE_KEY_PRIVATE);
            if (!pkcs8B64) return null;

            const buffer = base64ToArrayBuffer(pkcs8B64);
            return window.crypto.subtle.importKey(
                'pkcs8',
                buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true,
                ['deriveKey', 'deriveBits'],
            );
        }

        /**
         * Get the CryptoKey object for the local public key.
         */
        async getPublicCryptoKey() {
            const spkiB64 = localStorage.getItem(STORAGE_KEY_PUBLIC);
            if (!spkiB64) return null;

            const buffer = base64ToArrayBuffer(spkiB64);
            return window.crypto.subtle.importKey(
                'spki',
                buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true,
                [],
            );
        }

        /**
         * Import a remote user's public key from SPKI Base64 → CryptoKey.
         */
        async importRemotePublicKey(spkiBase64) {
            const buffer = base64ToArrayBuffer(spkiBase64);
            return window.crypto.subtle.importKey(
                'spki',
                buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true,
                [],
            );
        }

        /**
         * SHA-256 hex fingerprint of an ArrayBuffer (SPKI public key).
         */
        async _computeFingerprint(spkiBuffer) {
            const digest = await window.crypto.subtle.digest('SHA-256', spkiBuffer);
            return arrayBufferToHex(digest);
        }

        /**
         * Recompute fingerprint from stored SPKI (verification aid).
         */
        async getFingerprint() {
            const spkiB64 = localStorage.getItem(STORAGE_KEY_PUBLIC);
            if (!spkiB64) return null;

            const buffer = base64ToArrayBuffer(spkiB64);
            return this._computeFingerprint(buffer);
        }

        // ── server sync ────────────────────────────────────────────

        /**
         * Upload the public key + fingerprint to the Django backend.
         * Requires the user to be authenticated (CSRF cookie present).
         */
        async uploadPublicKey() {
            const spki = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);

            if (!spki || !fp) {
                throw new Error('No local key pair found. Generate keys first.');
            }

            const formData = new FormData();
            formData.append('public_key', spki);
            formData.append('fingerprint', fp);

            const response = await fetch('/keys/upload/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrfToken() },
                body: formData,
            });

            const data = await response.json();
            if (!data.ok) {
                throw new Error(data.error || 'Upload failed.');
            }
            return data;
        }

        /**
         * Fetch a user's public key from the server.
         */
        async fetchPublicKey(username) {
            const response = await fetch(`/keys/${encodeURIComponent(username)}/`);
            const data = await response.json();
            if (!data.ok) {
                throw new Error(data.error || 'Public key not found.');
            }
            return {
                username: data.username,
                publicKey: data.public_key,
                fingerprint: data.fingerprint,
                algorithm: data.algorithm,
            };
        }

        // ── backup / import ─────────────────────────────────────────

        /**
         * Export the local key pair as a downloadable JSON file.
         * The file contains the SPKI public key, PKCS#8 private key,
         * and fingerprint — meant for user-controlled backup.
         */
        exportBackup(filename = null) {
            const priv = localStorage.getItem(STORAGE_KEY_PRIVATE);
            const pub = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);

            if (!priv || !pub || !fp) {
                throw new Error('No key pair to export.');
            }

            const backup = {
                version: 1,
                algorithm: 'ECDH-P256',
                fingerprint: fp,
                publicKeySpki: pub,
                privateKeyPkcs8: priv,
                exportedAt: new Date().toISOString(),
            };

            const blob = new Blob(
                [JSON.stringify(backup, null, 2)],
                { type: 'application/json' },
            );

            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename || `ichat-keys-${fp.slice(0, 12)}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);

            return backup;
        }

        /**
         * Import a key pair from a backup JSON file.
         * @param {File} file — the JSON backup file from <input type="file">
         * @returns {Promise<{fingerprint: string}>}
         */
        async importBackup(file) {
            const text = await file.text();
            let backup;
            try {
                backup = JSON.parse(text);
            } catch {
                throw new Error('Invalid backup file: not valid JSON.');
            }

            if (!backup.version || !backup.publicKeySpki || !backup.privateKeyPkcs8) {
                throw new Error('Invalid backup file: missing required fields.');
            }

            if (backup.algorithm !== 'ECDH-P256') {
                throw new Error(
                    `Unsupported algorithm: ${backup.algorithm}. Expected ECDH-P256.`,
                );
            }

            // Validate that the private key parses correctly
            try {
                const pkcs8Buf = base64ToArrayBuffer(backup.privateKeyPkcs8);
                await window.crypto.subtle.importKey(
                    'pkcs8',
                    pkcs8Buf,
                    { name: 'ECDH', namedCurve: 'P-256' },
                    true,
                    ['deriveKey', 'deriveBits'],
                );
            } catch {
                throw new Error(
                    'Invalid backup: private key could not be imported.',
                );
            }

            // Validate that the public key parses correctly
            try {
                const spkiBuf = base64ToArrayBuffer(backup.publicKeySpki);
                await window.crypto.subtle.importKey(
                    'spki',
                    spkiBuf,
                    { name: 'ECDH', namedCurve: 'P-256' },
                    true,
                    [],
                );
            } catch {
                throw new Error(
                    'Invalid backup: public key could not be imported.',
                );
            }

            // Verify fingerprint matches
            const spkiBuf = base64ToArrayBuffer(backup.publicKeySpki);
            const computedFp = await this._computeFingerprint(spkiBuf);
            if (computedFp !== backup.fingerprint) {
                throw new Error(
                    'Fingerprint mismatch: the backup may be corrupted.',
                );
            }

            // Restore to localStorage
            localStorage.setItem(STORAGE_KEY_PRIVATE, backup.privateKeyPkcs8);
            localStorage.setItem(STORAGE_KEY_PUBLIC, backup.publicKeySpki);
            localStorage.setItem(STORAGE_KEY_FINGERPRINT, backup.fingerprint);

            this._ready = true;
            return { fingerprint: backup.fingerprint };
        }

        /**
         * Delete all locally stored keys (dangerous — user should
         * export a backup first).
         */
        deleteLocalKeys() {
            localStorage.removeItem(STORAGE_KEY_PRIVATE);
            localStorage.removeItem(STORAGE_KEY_PUBLIC);
            localStorage.removeItem(STORAGE_KEY_FINGERPRINT);
            this._ready = false;
        }

        /**
         * True after generateKeyPair() or loadKeys() has succeeded.
         */
        get isReady() {
            return this._ready;
        }
    }

    // Expose globally
    window.KeyManager = KeyManager;
})();
