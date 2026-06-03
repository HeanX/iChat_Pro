(function () {
  const IDENTITY_ALGORITHM = 'ECDH-P256';
  const MESSAGE_ALGORITHM = 'AES-256-GCM';
  const HKDF_INFO = 'chat-message-encryption-v1';
  const TRUST_STORAGE_PREFIX = 'ichat_peer_identity:';

  class PrivateChatCryptoError extends Error {
    constructor(code, message) {
      super(message);
      this.name = 'PrivateChatCryptoError';
      this.code = code;
    }
  }

  function requireInteger(value, field) {
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
      throw new PrivateChatCryptoError('invalid_metadata', `${field} must be a positive integer.`);
    }
    return parsed;
  }

  function currentRecord() {
    const record = window.iChatKeyManager && window.iChatKeyManager.loadCurrentRecord();
    if (!record || !record.private_key || !record.key_version) {
      throw new PrivateChatCryptoError(
        'local_key_missing',
        'Local private key is missing. Import your key backup or initialize a new identity key.'
      );
    }
    return record;
  }

  function bytesToBase64(bytes) {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }

  function base64ToBytes(value, field) {
    if (typeof value !== 'string' || !value) {
      throw new PrivateChatCryptoError('invalid_ciphertext', `${field} must be Base64 text.`);
    }
    try {
      const binary = atob(value);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      if (bytesToBase64(bytes) !== value) throw new Error('Non-canonical Base64.');
      return bytes;
    } catch (error) {
      throw new PrivateChatCryptoError('invalid_ciphertext', `${field} must be valid Base64 text.`);
    }
  }

  async function importPrivateKey(privateKeyJwk) {
    return window.crypto.subtle.importKey(
      'jwk',
      privateKeyJwk,
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveBits']
    );
  }

  async function importPublicKey(identityPublicKey) {
    return window.crypto.subtle.importKey(
      'spki',
      base64ToBytes(identityPublicKey, 'identity_public_key'),
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      []
    );
  }

  function privateContext(metadata) {
    return [
      'single',
      requireInteger(metadata.conversation_id, 'conversation_id'),
      requireInteger(metadata.sender_id, 'sender_id'),
      requireInteger(metadata.receiver_id, 'receiver_id'),
      requireInteger(metadata.sender_key_version, 'sender_key_version'),
      requireInteger(metadata.receiver_key_version, 'receiver_key_version')
    ].join(':');
  }

  async function derivePrivateSessionKey(localPrivateKey, remoteIdentityPublicKey, metadata) {
    const contextBytes = new TextEncoder().encode(privateContext(metadata));
    const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);
    const remotePublicKey = await importPublicKey(remoteIdentityPublicKey);
    const sharedSecret = await window.crypto.subtle.deriveBits(
      { name: 'ECDH', public: remotePublicKey },
      localPrivateKey,
      256
    );
    const hkdfKey = await window.crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveKey']);
    return window.crypto.subtle.deriveKey(
      {
        name: 'HKDF',
        hash: 'SHA-256',
        salt,
        info: new TextEncoder().encode(HKDF_INFO)
      },
      hkdfKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  async function encryptText(plaintext, sessionKey) {
    if (typeof plaintext !== 'string' || !plaintext) {
      throw new PrivateChatCryptoError('invalid_plaintext', 'Message text cannot be empty.');
    }
    const nonce = window.crypto.getRandomValues(new Uint8Array(12));
    const encrypted = new Uint8Array(await window.crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: nonce, tagLength: 128 },
      sessionKey,
      new TextEncoder().encode(plaintext)
    ));
    const tagStart = encrypted.length - 16;
    return {
      ciphertext: bytesToBase64(encrypted.slice(0, tagStart)),
      nonce: bytesToBase64(nonce),
      auth_tag: bytesToBase64(encrypted.slice(tagStart)),
      algorithm: MESSAGE_ALGORITHM
    };
  }

  async function decryptText(encryptedPayload, sessionKey) {
    if (encryptedPayload.algorithm !== MESSAGE_ALGORITHM) {
      throw new PrivateChatCryptoError('unsupported_algorithm', 'Unsupported private-message algorithm.');
    }
    const ciphertext = base64ToBytes(encryptedPayload.ciphertext, 'ciphertext');
    const nonce = base64ToBytes(encryptedPayload.nonce, 'nonce');
    const authTag = base64ToBytes(encryptedPayload.auth_tag, 'auth_tag');
    if (nonce.length !== 12 || authTag.length !== 16) {
      throw new PrivateChatCryptoError('invalid_ciphertext', 'Encrypted message metadata is malformed.');
    }
    const encrypted = new Uint8Array(ciphertext.length + authTag.length);
    encrypted.set(ciphertext);
    encrypted.set(authTag, ciphertext.length);
    try {
      const plaintext = await window.crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: nonce, tagLength: 128 },
        sessionKey,
        encrypted
      );
      return new TextDecoder().decode(plaintext);
    } catch (error) {
      throw new PrivateChatCryptoError(
        'damaged_ciphertext',
        'This message cannot be decrypted because its key changed or its ciphertext was damaged.'
      );
    }
  }

  async function fetchPublicKey(userId, keyVersion = null) {
    const suffix = keyVersion === null ? '' : `${requireInteger(keyVersion, 'key_version')}/`;
    const response = await fetch(`/api/keys/${encodeURIComponent(userId)}/${suffix}`);
    if (response.status === 404) {
      throw new PrivateChatCryptoError('peer_key_missing', 'The contact has not initialized an encryption key.');
    }
    if (!response.ok) {
      throw new PrivateChatCryptoError('peer_key_unavailable', 'Unable to load the contact encryption key.');
    }
    const payload = await response.json();
    const key = payload.key;
    if (
      !key ||
      requireInteger(key.user_id, 'user_id') !== requireInteger(userId, 'user_id') ||
      (keyVersion !== null && requireInteger(key.key_version, 'key_version') !== requireInteger(keyVersion, 'key_version')) ||
      key.algorithm !== IDENTITY_ALGORITHM
    ) {
      throw new PrivateChatCryptoError('invalid_peer_key', 'The contact identity-key response is invalid or unsupported.');
    }
    return key;
  }

  function loadPeerTrust(userId) {
    const storageKey = `${TRUST_STORAGE_PREFIX}${userId}`;
    const previous = localStorage.getItem(storageKey);
    if (!previous) return null;
    let trusted;
    try {
      trusted = JSON.parse(previous);
    } catch (error) {
      throw new PrivateChatCryptoError(
        'peer_trust_invalid',
        'The saved contact security-key record is damaged. Clear it and verify the fingerprint again.'
      );
    }
    if (!trusted || typeof trusted !== 'object') {
      throw new PrivateChatCryptoError(
        'peer_trust_invalid',
        'The saved contact security-key record is damaged. Clear it and verify the fingerprint again.'
      );
    }
    if (!trusted.versions) {
      trusted.versions = {};
      if (trusted.key_version && trusted.key_fingerprint) {
        trusted.versions[String(trusted.key_version)] = trusted.key_fingerprint;
      }
    }
    return trusted;
  }

  function rememberPeerIdentity(key) {
    const trusted = loadPeerTrust(key.user_id);
    if (trusted) {
      const versionStr = String(key.key_version);
      const trustedFingerprint = trusted.versions[versionStr];
      if (trustedFingerprint) {
        if (trustedFingerprint !== key.key_fingerprint) {
          throw new PrivateChatCryptoError(
            'peer_key_changed',
            'The contact security key changed. Verify the new fingerprint before sending or decrypting messages.'
          );
        }
      } else {
        throw new PrivateChatCryptoError(
          'peer_key_changed',
          'The contact security key changed. Verify the new fingerprint before sending or decrypting messages.'
        );
      }
    } else {
      const trusted = {
        user_id: key.user_id,
        versions: {
          [String(key.key_version)]: key.key_fingerprint
        }
      };
      const storageKey = `${TRUST_STORAGE_PREFIX}${key.user_id}`;
      localStorage.setItem(storageKey, JSON.stringify(trusted));
    }
  }

  function trustPeerKey(key) {
    let trusted = loadPeerTrust(key.user_id);
    if (!trusted) {
      trusted = {
        user_id: key.user_id,
        versions: {}
      };
    }
    trusted.versions[String(key.key_version)] = key.key_fingerprint;
    const storageKey = `${TRUST_STORAGE_PREFIX}${key.user_id}`;
    localStorage.setItem(storageKey, JSON.stringify(trusted));
  }

  async function encryptPrivateMessage({ plaintext, conversationId, receiverId }) {
    const local = currentRecord();
    const receiverKey = await fetchPublicKey(requireInteger(receiverId, 'receiver_id'));
    rememberPeerIdentity(receiverKey);
    const metadata = {
      conversation_id: requireInteger(conversationId, 'conversation_id'),
      sender_id: requireInteger(local.user_id, 'sender_id'),
      receiver_id: requireInteger(receiverKey.user_id, 'receiver_id'),
      sender_key_version: requireInteger(local.key_version, 'sender_key_version'),
      receiver_key_version: requireInteger(receiverKey.key_version, 'receiver_key_version')
    };
    const privateKey = await importPrivateKey(local.private_key);
    const sessionKey = await derivePrivateSessionKey(privateKey, receiverKey.identity_public_key, metadata);
    return { ...metadata, ...await encryptText(plaintext, sessionKey) };
  }

  async function decryptPrivateMessage(payload) {
    const local = currentRecord();
    if (payload.algorithm !== MESSAGE_ALGORITHM) {
      throw new PrivateChatCryptoError('unsupported_algorithm', 'Unsupported private-message algorithm.');
    }
    const localUserId = requireInteger(local.user_id, 'user_id');
    const senderId = requireInteger(payload.sender_id, 'sender_id');
    const receiverId = requireInteger(payload.receiver_id, 'receiver_id');
    let remoteUserId;
    let remoteKeyVersion;
    let localKeyVersion;

    if (receiverId === localUserId) {
      remoteUserId = senderId;
      remoteKeyVersion = requireInteger(payload.sender_key_version, 'sender_key_version');
      localKeyVersion = requireInteger(payload.receiver_key_version, 'receiver_key_version');
    } else if (senderId === localUserId) {
      remoteUserId = receiverId;
      remoteKeyVersion = requireInteger(payload.receiver_key_version, 'receiver_key_version');
      localKeyVersion = requireInteger(payload.sender_key_version, 'sender_key_version');
    } else {
      throw new PrivateChatCryptoError('wrong_receiver', 'This encrypted message belongs to another user.');
    }

    if (localKeyVersion !== requireInteger(local.key_version, 'local_key_version')) {
      throw new PrivateChatCryptoError(
        'local_key_changed',
        'Your current device key cannot decrypt this message. Import the matching key backup.'
      );
    }

    const remoteKey = await fetchPublicKey(remoteUserId, remoteKeyVersion);
    if (requireInteger(remoteKey.key_version, 'remote_key_version') !== remoteKeyVersion) {
      throw new PrivateChatCryptoError('peer_key_changed', 'The contact security key changed after this message was encrypted.');
    }
    rememberPeerIdentity(remoteKey);
    const privateKey = await importPrivateKey(local.private_key);
    const sessionKey = await derivePrivateSessionKey(privateKey, remoteKey.identity_public_key, payload);
    return decryptText(payload, sessionKey);
  }

  window.iChatPrivateE2EE = {
    MESSAGE_ALGORITHM,
    PrivateChatCryptoError,
    privateContext,
    derivePrivateSessionKey,
    encryptText,
    decryptText,
    encryptPrivateMessage,
    decryptPrivateMessage,
    trustPeerKey
  };
})();
