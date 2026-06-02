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

    class KeyManager {
        constructor() {
            this._ready = false;
        }

        hasExistingKeys() {
            return !!localStorage.getItem(STORAGE_KEY_PRIVATE);
        }

        async generateKeyPair() {
            const keyPair = await window.crypto.subtle.generateKey(
                { name: 'ECDH', namedCurve: 'P-256' },
                true,
                ['deriveKey', 'deriveBits'],
            );

            const spkiRaw = await window.crypto.subtle.exportKey(
                'spki', keyPair.publicKey,
            );
            const publicKeySpki = arrayBufferToBase64(spkiRaw);

            const pkcs8Raw = await window.crypto.subtle.exportKey(
                'pkcs8', keyPair.privateKey,
            );
            const privateKeyPkcs8 = arrayBufferToBase64(pkcs8Raw);

            const fingerprint = await this._computeFingerprint(spkiRaw);

            localStorage.setItem(STORAGE_KEY_PRIVATE, privateKeyPkcs8);
            localStorage.setItem(STORAGE_KEY_PUBLIC, publicKeySpki);
            localStorage.setItem(STORAGE_KEY_FINGERPRINT, fingerprint);

            this._ready = true;
            return { publicKeySpki, fingerprint };
        }

        loadKeys() {
            const priv = localStorage.getItem(STORAGE_KEY_PRIVATE);
            const pub = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);
            if (!priv || !pub || !fp) return null;
            this._ready = true;
            return { publicKeySpki: pub, fingerprint: fp, privateKeyPkcs8: priv };
        }

        async getPrivateCryptoKey() {
            const pkcs8B64 = localStorage.getItem(STORAGE_KEY_PRIVATE);
            if (!pkcs8B64) return null;
            const buffer = base64ToArrayBuffer(pkcs8B64);
            return window.crypto.subtle.importKey(
                'pkcs8', buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true, ['deriveKey', 'deriveBits'],
            );
        }

        async getPublicCryptoKey() {
            const spkiB64 = localStorage.getItem(STORAGE_KEY_PUBLIC);
            if (!spkiB64) return null;
            const buffer = base64ToArrayBuffer(spkiB64);
            return window.crypto.subtle.importKey(
                'spki', buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true, [],
            );
        }

        async importRemotePublicKey(spkiBase64) {
            const buffer = base64ToArrayBuffer(spkiBase64);
            return window.crypto.subtle.importKey(
                'spki', buffer,
                { name: 'ECDH', namedCurve: 'P-256' },
                true, [],
            );
        }

        async _computeFingerprint(spkiBuffer) {
            const digest = await window.crypto.subtle.digest('SHA-256', spkiBuffer);
            return arrayBufferToHex(digest);
        }

        async getFingerprint() {
            const spkiB64 = localStorage.getItem(STORAGE_KEY_PUBLIC);
            if (!spkiB64) return null;
            const buffer = base64ToArrayBuffer(spkiB64);
            return this._computeFingerprint(buffer);
        }

        async uploadPublicKey() {
            const spki = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);
            if (!spki || !fp) throw new Error('No local key pair found. Generate keys first.');
            const response = await fetch('/api/keys/upload/', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    identity_public_key: spki,
                    key_fingerprint: fp,
                    algorithm: 'ECDH-P256',
                }),
            });
            const data = await response.json();
            if (!response.ok || !data.key) {
                throw new Error(data.error || 'Upload failed.');
            }
            return data.key;
        }

        async fetchPublicKey(userId) {
            const response = await fetch(`/api/keys/${encodeURIComponent(userId)}/`);
            const data = await response.json();
            if (!response.ok || !data.key) {
                throw new Error(data.error || 'Public key not found.');
            }
            return {
                userId: data.key.user_id,
                identityPublicKey: data.key.identity_public_key,
                fingerprint: data.key.key_fingerprint,
                algorithm: data.key.algorithm,
                keyVersion: data.key.key_version,
                isActive: data.key.is_active,
            };
        }

        async fetchPublicKeyByVersion(userId, keyVersion) {
            const response = await fetch(
                `/api/keys/${encodeURIComponent(userId)}/${encodeURIComponent(keyVersion)}/`,
            );
            const data = await response.json();
            if (!response.ok || !data.key) {
                throw new Error(data.error || 'Public key version not found.');
            }
            return {
                userId: data.key.user_id,
                identityPublicKey: data.key.identity_public_key,
                fingerprint: data.key.key_fingerprint,
                algorithm: data.key.algorithm,
                keyVersion: data.key.key_version,
                isActive: data.key.is_active,
            };
        }

        async fetchPublicKeysBatch(userIds) {
            const response = await fetch('/api/keys/batch/', {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCsrfToken(),
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ user_ids: userIds }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Batch fetch failed.');
            }
            return data.keys || [];
        }

        async fetchFingerprint(userId) {
            const response = await fetch(
                `/api/keys/fingerprint/${encodeURIComponent(userId)}/`,
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Fingerprint not found.');
            }
            return {
                userId: data.user_id,
                fingerprint: data.key_fingerprint,
                keyVersion: data.key_version,
            };
        }

        exportBackup(filename = null) {
            const priv = localStorage.getItem(STORAGE_KEY_PRIVATE);
            const pub = localStorage.getItem(STORAGE_KEY_PUBLIC);
            const fp = localStorage.getItem(STORAGE_KEY_FINGERPRINT);
            if (!priv || !pub || !fp) throw new Error('No key pair to export.');
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

        async importBackup(file) {
            const text = await file.text();
            let backup;
            try { backup = JSON.parse(text); } catch { throw new Error('Invalid backup file: not valid JSON.'); }
            if (!backup.version || !backup.publicKeySpki || !backup.privateKeyPkcs8)
                throw new Error('Invalid backup file: missing required fields.');
            if (backup.algorithm !== 'ECDH-P256')
                throw new Error(`Unsupported algorithm: ${backup.algorithm}.`);
            const pkcs8Buf = base64ToArrayBuffer(backup.privateKeyPkcs8);
            await window.crypto.subtle.importKey(
                'pkcs8', pkcs8Buf,
                { name: 'ECDH', namedCurve: 'P-256' },
                true, ['deriveKey', 'deriveBits'],
            );
            const spkiBuf = base64ToArrayBuffer(backup.publicKeySpki);
            await window.crypto.subtle.importKey(
                'spki', spkiBuf,
                { name: 'ECDH', namedCurve: 'P-256' },
                true, [],
            );
            const computedFp = await this._computeFingerprint(spkiBuf);
            if (computedFp !== backup.fingerprint)
                throw new Error('Fingerprint mismatch: the backup may be corrupted.');
            localStorage.setItem(STORAGE_KEY_PRIVATE, backup.privateKeyPkcs8);
            localStorage.setItem(STORAGE_KEY_PUBLIC, backup.publicKeySpki);
            localStorage.setItem(STORAGE_KEY_FINGERPRINT, backup.fingerprint);
            this._ready = true;
            return { fingerprint: backup.fingerprint };
        }

        deleteLocalKeys() {
            localStorage.removeItem(STORAGE_KEY_PRIVATE);
            localStorage.removeItem(STORAGE_KEY_PUBLIC);
            localStorage.removeItem(STORAGE_KEY_FINGERPRINT);
            this._ready = false;
        }

        get isReady() { return this._ready; }
    }

    window.KeyManager = KeyManager;
})();
