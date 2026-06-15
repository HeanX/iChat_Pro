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
      console.error(
        '[PrivateE2EE] invalid_metadata:',
        `field="${field}"`,
        `value=${JSON.stringify(value)}`,
        `type=${typeof value}`,
        `parsed=${parsed}`,
      );
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

  function forgetPeerKey(userId) {
    const storageKey = `${TRUST_STORAGE_PREFIX}${requireInteger(userId, 'user_id')}`;
    localStorage.removeItem(storageKey);
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
      const historicalLocalKey = await fetchPublicKey(localUserId, localKeyVersion);
      if (historicalLocalKey.identity_public_key === local.identity_public_key) {
        // The same local key material may have been re-registered as a newer
        // version. Keep using the current private key for historical messages.
      } else {
        throw new PrivateChatCryptoError(
          'local_key_changed',
          'Your current device key cannot decrypt this message. Import the matching key backup.'
        );
      }
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

  // ── File key wrapping (for file transfer) ────────────────────────

  const FILE_KEY_WRAP_HKDF_INFO = 'ichat-file-key-wrap-v1';

  /**
   * Wrap raw file key bytes for a recipient using ECDH + HKDF + AES-GCM.
   *
   * @param {Uint8Array} fileKeyBytes - raw 32-byte file key
   * @param {number} fileId - server-assigned EncryptedFile.id
   * @param {number} holderId - recipient user ID (the key holder)
   * @param {object} [metadata] - {conversation_id, sender_id, receiver_id,
   *   sender_key_version, receiver_key_version}. If omitted, derives
   *   defaults from local state or active conversation.
   * @returns {Promise<{holder_id: number, encrypted_file_key: string, nonce: string,
   *   auth_tag: string, algorithm: string, sender_key_version: number,
   *   receiver_key_version: number}>}
   */
  async function wrapFileKey(fileKeyBytes, fileId, holderId, metadata) {
    if (!metadata) {
      // Try to infer metadata from active conversation state
      const convId = window.activeChatId || 0;
      const localId = window.myUserId || 0;
      metadata = {
        conversation_id: convId,
        sender_id: localId,
        receiver_id: holderId,
        sender_key_version: window.localKeyVersion || 1,
        receiver_key_version: 0,
      };
    }

    // Fetch recipient's public key
    const receiverKeyVersion = metadata.receiver_key_version || 0;
    const remoteKey = await fetchPublicKey(holderId, receiverKeyVersion || undefined);
    metadata.receiver_key_version = requireInteger(remoteKey.key_version, 'remote_key_version');
    metadata.sender_key_version = requireInteger(currentRecord().key_version, 'local_key_version');
    const local = currentRecord();
    const privateKey = await importPrivateKey(local.private_key);

    // Derive a dedicated wrap session key (separate HKDF info to avoid
    // domain crossing with message keys)
    const contextBytes = new TextEncoder().encode(privateContext(metadata));
    const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);
    const remotePublicKey = await importPublicKey(remoteKey.identity_public_key);
    const sharedSecret = await window.crypto.subtle.deriveBits(
      { name: 'ECDH', public: remotePublicKey },
      privateKey,
      256
    );
    const hkdfKey = await window.crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveKey']);
    const wrapSessionKey = await window.crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(FILE_KEY_WRAP_HKDF_INFO) },
      hkdfKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );

    // Encrypt the file key with AAD
    const nonce = window.crypto.getRandomValues(new Uint8Array(12));
    const aadStr = 'ichat-file-key-wrap-v1:' + fileId + ':' + holderId;
    const aad = new TextEncoder().encode(aadStr);
    const combined = new Uint8Array(
      await window.crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
        wrapSessionKey,
        fileKeyBytes
      )
    );
    const tagStart = combined.length - 16;

    return {
      holder_id: holderId,
      encrypted_file_key: bytesToBase64(combined.slice(0, tagStart)),
      nonce: bytesToBase64(nonce),
      auth_tag: bytesToBase64(combined.slice(tagStart)),
      algorithm: 'AES-256-GCM',
      sender_key_version: requireInteger(local.key_version, 'local_key_version'),
      receiver_key_version: requireInteger(remoteKey.key_version, 'remote_key_version'),
    };
  }

  /**
   * Unwrap a file key received from a sender.
   *
   * @param {object} wrapped - {encrypted_file_key, nonce, auth_tag, sender_key_version, receiver_key_version}
   * @param {number} fileId
   * @param {number} holderId - local user ID (the key holder)
   * @param {number} senderId - the user who sent the file
   * @param {object} [metadata]
   * @returns {Promise<Uint8Array>} raw 32-byte file key
   */
  async function unwrapFileKey(wrapped, fileId, holderId, senderId, metadata) {
    if (!metadata) {
      const convId = window.activeChatId || 0;
      metadata = {
        conversation_id: convId,
        sender_id: senderId,
        receiver_id: holderId,
        sender_key_version: wrapped.sender_key_version || 0,
        receiver_key_version: wrapped.receiver_key_version || 0,
      };
    }

    const remoteKeyVersion = wrapped.sender_key_version || metadata.sender_key_version || 0;
    const senderKey = await fetchPublicKey(senderId, remoteKeyVersion || undefined);
    const local = currentRecord();
    const privateKey = await importPrivateKey(local.private_key);

    const contextBytes = new TextEncoder().encode(privateContext(metadata));
    const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);
    const remotePublicKey = await importPublicKey(senderKey.identity_public_key);
    const sharedSecret = await window.crypto.subtle.deriveBits(
      { name: 'ECDH', public: remotePublicKey },
      privateKey,
      256
    );
    const hkdfKey = await window.crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveKey']);
    const wrapSessionKey = await window.crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(FILE_KEY_WRAP_HKDF_INFO) },
      hkdfKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );

    const ciphertext = base64ToBytes(wrapped.encrypted_file_key);
    const nonce = base64ToBytes(wrapped.nonce);
    const authTag = base64ToBytes(wrapped.auth_tag);
    const combined = new Uint8Array(ciphertext.length + authTag.length);
    combined.set(ciphertext, 0);
    combined.set(authTag, ciphertext.length);

    const aadStr = 'ichat-file-key-wrap-v1:' + fileId + ':' + holderId;
    const aad = new TextEncoder().encode(aadStr);

    return new Uint8Array(
      await window.crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
        wrapSessionKey,
        combined
      )
    );
  }

  /** Wrap file key for the sender themself (multi-device sync). */
  async function wrapFileKeyForSelf(fileKeyBytes, fileId, holderId) {
    // For self-wrapping, re-derive the session key with sender==receiver
    const local = currentRecord();
    const metadata = {
      conversation_id: window.activeChatId || 0,
      sender_id: holderId,
      receiver_id: holderId,
      sender_key_version: requireInteger(local.key_version, 'local_key_version'),
      receiver_key_version: requireInteger(local.key_version, 'local_key_version'),
    };
    return wrapFileKey(fileKeyBytes, fileId, holderId, metadata);
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
    trustPeerKey,
    forgetPeerKey,
    // File transfer
    wrapFileKey,
    unwrapFileKey,
    wrapFileKeyForSelf,
  };
})();
