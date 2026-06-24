(function () {
  'use strict';

  const IDENTITY_ALGORITHM = 'ECDH-P256';
  const MESSAGE_ALGORITHM = 'AES-256-GCM';
  const HKDF_INFO = 'chat-message-encryption-v1';
  const TRUST_STORAGE_PREFIX = 'ichat_peer_identity:';

  class GroupChatCryptoError extends Error {
    constructor(code, message) {
      super(message);
      this.name = 'GroupChatCryptoError';
      this.code = code;
    }
  }

  function requireInteger(value, field) {
    const parsed = Number(value);
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
      console.error(
        '[GroupE2EE] invalid_metadata:',
        `field="${field}"`,
        `value=${JSON.stringify(value)}`,
        `type=${typeof value}`,
        `parsed=${parsed}`,
      );
      throw new GroupChatCryptoError('invalid_metadata', `${field} must be a positive integer.`);
    }
    return parsed;
  }

  function currentRecord() {
    const record = window.iChatKeyManager && window.iChatKeyManager.loadCurrentRecord();
    if (!record || !record.key_version) {
      throw new GroupChatCryptoError(
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
      throw new GroupChatCryptoError('invalid_ciphertext', `${field} must be Base64 text.`);
    }
    try {
      const binary = atob(value);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      if (bytesToBase64(bytes) !== value) throw new Error('Non-canonical Base64.');
      return bytes;
    } catch (error) {
      throw new GroupChatCryptoError('invalid_ciphertext', `${field} must be valid Base64 text.`);
    }
  }

  async function importPrivateKey(privateKeyJwk) {
    if (privateKeyJwk && privateKeyJwk.type === 'private') return privateKeyJwk;
    return window.crypto.subtle.importKey(
      'jwk',
      privateKeyJwk,
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveBits']
    );
  }

  async function currentPrivateKey(localRecord, keyVersion = null) {
    if (
      window.iChatKeyManager &&
      typeof window.iChatKeyManager.getCurrentPrivateKey === 'function'
    ) {
      const key = await window.iChatKeyManager.getCurrentPrivateKey(keyVersion);
      if (key) return key;
    }
    if (
      localRecord.private_key &&
      (keyVersion === null || Number(localRecord.key_version) === Number(keyVersion))
    ) {
      return importPrivateKey(localRecord.private_key);
    }
    throw new GroupChatCryptoError(
      'local_key_missing',
      'Local private key is missing. Import your key backup or initialize a new identity key.'
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

  async function generateEphemeralKeyPair() {
    return window.crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveBits']
    );
  }

  async function exportPublicKeyBase64(publicKey) {
    return bytesToBase64(new Uint8Array(await window.crypto.subtle.exportKey('spki', publicKey)));
  }

  function groupContext(metadata) {
    return [
      'group',
      requireInteger(metadata.group_id, 'group_id'),
      requireInteger(metadata.membership_version, 'membership_version'),
      requireInteger(metadata.sender_id, 'sender_id'),
      requireInteger(metadata.receiver_id, 'receiver_id'),
      requireInteger(metadata.sender_key_version, 'sender_key_version'),
      requireInteger(metadata.receiver_key_version, 'receiver_key_version')
    ].join(':');
  }

  async function deriveGroupSessionKeyWithPublicKey(localPrivateKey, remotePublicKey, metadata) {
    const contextBytes = new TextEncoder().encode(groupContext(metadata));
    const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);
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

  async function deriveGroupSessionKey(localPrivateKey, remoteIdentityPublicKey, metadata) {
    return deriveGroupSessionKeyWithPublicKey(
      localPrivateKey,
      await importPublicKey(remoteIdentityPublicKey),
      metadata
    );
  }

  async function encryptText(plaintext, sessionKey) {
    if (typeof plaintext !== 'string' || !plaintext) {
      throw new GroupChatCryptoError('invalid_plaintext', 'Message text cannot be empty.');
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
      throw new GroupChatCryptoError('unsupported_algorithm', 'Unsupported group-message algorithm.');
    }
    const ciphertext = base64ToBytes(encryptedPayload.ciphertext, 'ciphertext');
    const nonce = base64ToBytes(encryptedPayload.nonce, 'nonce');
    const authTag = base64ToBytes(encryptedPayload.auth_tag, 'auth_tag');
    if (nonce.length !== 12 || authTag.length !== 16) {
      throw new GroupChatCryptoError('invalid_ciphertext', 'Encrypted message metadata is malformed.');
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
      throw new GroupChatCryptoError(
        'damaged_ciphertext',
        'This message cannot be decrypted because its key changed or its ciphertext was damaged.'
      );
    }
  }

  function getCookie(name) {
    for (const rawCookie of document.cookie.split(';')) {
      const cookie = rawCookie.trim();
      if (cookie.startsWith(`${name}=`)) {
        return decodeURIComponent(cookie.slice(name.length + 1));
      }
    }
    return '';
  }

  async function fetchPublicKey(userId, keyVersion) {
    // Check cache first
    const cached = _getCachedPublicKey(userId, keyVersion);
    if (cached) return cached;

    const suffix = keyVersion !== undefined ? `${requireInteger(keyVersion, 'key_version')}/` : '';
    const response = await fetch(`/api/keys/${encodeURIComponent(userId)}/${suffix}`);
    if (response.status === 404) {
      throw new GroupChatCryptoError('peer_key_missing', `User ${userId} has not initialized an encryption key.`);
    }
    if (!response.ok) {
      throw new GroupChatCryptoError('peer_key_unavailable', `Unable to load the encryption key for user ${userId}.`);
    }
    const payload = await response.json();
    const key = payload.key;
    if (
      !key ||
      requireInteger(key.user_id, 'user_id') !== requireInteger(userId, 'user_id') ||
      (keyVersion !== undefined && requireInteger(key.key_version, 'key_version') !== requireInteger(keyVersion, 'key_version')) ||
      key.algorithm !== IDENTITY_ALGORITHM
    ) {
      throw new GroupChatCryptoError('invalid_peer_key', 'The identity-key response is invalid or unsupported.');
    }
    // Cache the fetched key
    _cachePublicKey(key.user_id, key.key_version, key);
    return key;
  }

  async function fetchBatchPublicKeys(userIds) {
    const response = await fetch('/api/keys/batch/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
      },
      body: JSON.stringify({ user_ids: userIds })
    });
    if (!response.ok) {
      throw new GroupChatCryptoError('peer_key_unavailable', 'Unable to load member encryption keys.');
    }
    const payload = await response.json();
    const keyMap = {};
    for (const key of (payload.keys || [])) {
      if (key.algorithm === IDENTITY_ALGORITHM) {
        keyMap[String(key.user_id)] = key;
      }
    }
    return keyMap;
  }

  function loadPeerTrust(userId) {
    const storageKey = `${TRUST_STORAGE_PREFIX}${userId}`;
    const previous = localStorage.getItem(storageKey);
    if (!previous) return null;
    let trusted;
    try {
      trusted = JSON.parse(previous);
    } catch (error) {
      throw new GroupChatCryptoError(
        'peer_trust_invalid',
        'The saved member security-key record is damaged. Clear it and verify the fingerprint again.'
      );
    }
    if (!trusted || typeof trusted !== 'object') {
      throw new GroupChatCryptoError(
        'peer_trust_invalid',
        'The saved member security-key record is damaged. Clear it and verify the fingerprint again.'
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

  function rememberPeerIdentity(key, options = {}) {
    const trusted = loadPeerTrust(key.user_id);
    if (trusted) {
      const versionStr = String(key.key_version);
      const trustedFingerprint = trusted.versions[versionStr];
      if (trustedFingerprint !== key.key_fingerprint) {
        if (!trustedFingerprint && options.allowNewVersionForDecrypt) {
          if (typeof CustomEvent === 'function' && typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new CustomEvent('ichat:peer-key-observed', {
              detail: {
                user_id: key.user_id,
                key_version: key.key_version,
                key_fingerprint: key.key_fingerprint
              }
            }));
          }
          return;
        }
        const error = new GroupChatCryptoError(
          'peer_key_changed',
          'A group member security key changed. Verify the new fingerprint before sending or decrypting group messages.'
        );
        error.key_change_reason = trustedFingerprint ? 'fingerprint_mismatch' : 'new_key_version';
        error.user_id = key.user_id;
        error.key_version = key.key_version;
        error.key_fingerprint = key.key_fingerprint;
        error.key = {
          user_id: key.user_id,
          key_version: key.key_version,
          key_fingerprint: key.key_fingerprint
        };
        throw error;
      }
      return;
    }
    localStorage.setItem(`${TRUST_STORAGE_PREFIX}${key.user_id}`, JSON.stringify({
      user_id: key.user_id,
      versions: {
        [String(key.key_version)]: key.key_fingerprint
      }
    }));
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
    localStorage.setItem(`${TRUST_STORAGE_PREFIX}${key.user_id}`, JSON.stringify(trusted));
  }

  function forgetPeerKey(userId) {
    localStorage.removeItem(`${TRUST_STORAGE_PREFIX}${requireInteger(userId, 'user_id')}`);
  }

  async function encryptGroupMessage({ plaintext, groupId, membershipVersion, memberIds }) {
    const local = currentRecord();
    requireInteger(groupId, 'group_id');
    requireInteger(membershipVersion, 'membership_version');
    if (!Array.isArray(memberIds) || memberIds.length === 0) {
      throw new GroupChatCryptoError('invalid_members', 'Member list must be a non-empty array.');
    }

    const uniqueMemberIds = [...new Set(memberIds.map(id => requireInteger(id, 'member_id')))];
    const keyMap = await fetchBatchPublicKeys(uniqueMemberIds);

    const missingIds = uniqueMemberIds.filter(id => !keyMap[String(id)]);
    if (missingIds.length > 0) {
      throw new GroupChatCryptoError(
        'peer_key_missing',
        `Missing encryption keys for members: ${missingIds.join(', ')}`
      );
    }

    const senderId = requireInteger(local.user_id, 'local_user_id');
    const senderKeyVersion = requireInteger(local.key_version, 'local_key_version');

    const recipients = [];
    for (const receiverId of uniqueMemberIds) {
      const receiverKey = keyMap[String(receiverId)];
      if (receiverId !== senderId) rememberPeerIdentity(receiverKey);
      const metadata = {
        group_id: groupId,
        membership_version: membershipVersion,
        sender_id: senderId,
        receiver_id: receiverId,
        sender_key_version: senderKeyVersion,
        receiver_key_version: receiverKey.key_version
      };
      const ephemeralKeyPair = await generateEphemeralKeyPair();
      const senderEphemeralPublicKey = await exportPublicKeyBase64(ephemeralKeyPair.publicKey);
      const sessionKey = await deriveGroupSessionKey(
        ephemeralKeyPair.privateKey,
        receiverKey.identity_public_key,
        metadata
      );
      const encrypted = await encryptText(plaintext, sessionKey);
      recipients.push({
        receiver_id: receiverId,
        receiver_key_version: receiverKey.key_version,
        ciphertext: encrypted.ciphertext,
        nonce: encrypted.nonce,
        auth_tag: encrypted.auth_tag,
        sender_ephemeral_public_key: senderEphemeralPublicKey
      });
    }

    // ── sender_copy: encrypt for the sender's own identity key ─────
    // so the sender's other devices can decrypt their own group messages.
    let senderCopy = null;
    if (local.identity_public_key) {
      const selfMetadata = {
        group_id: groupId,
        membership_version: membershipVersion,
        sender_id: senderId,
        receiver_id: senderId,
        sender_key_version: senderKeyVersion,
        receiver_key_version: senderKeyVersion
      };
      const selfEphemeral = await generateEphemeralKeyPair();
      const selfSessionKey = await deriveGroupSessionKey(
        selfEphemeral.privateKey,
        local.identity_public_key,
        selfMetadata
      );
      const selfEncrypted = await encryptText(plaintext, selfSessionKey);
      senderCopy = {
        ciphertext: selfEncrypted.ciphertext,
        nonce: selfEncrypted.nonce,
        auth_tag: selfEncrypted.auth_tag,
        sender_ephemeral_public_key: await exportPublicKeyBase64(selfEphemeral.publicKey)
      };
    }

    return {
      algorithm: MESSAGE_ALGORITHM,
      sender_key_version: senderKeyVersion,
      membership_version: membershipVersion,
      recipients,
      sender_copy: senderCopy
    };
  }

  async function decryptGroupMessage(payload) {
    const local = currentRecord();
    if (payload.algorithm !== MESSAGE_ALGORITHM) {
      throw new GroupChatCryptoError('unsupported_algorithm', 'Unsupported group-message algorithm.');
    }

    const receiverId = requireInteger(payload.receiver_id, 'receiver_id');
    if (receiverId !== requireInteger(local.user_id, 'local_user_id')) {
      throw new GroupChatCryptoError('wrong_receiver', 'This encrypted group message belongs to another user.');
    }

    const receiverKeyVersion = requireInteger(payload.receiver_key_version, 'receiver_key_version');
    const privateKey = await currentPrivateKey(local, receiverKeyVersion);

    const senderKeyVersion = requireInteger(payload.sender_key_version, 'sender_key_version');
    const senderId = requireInteger(payload.sender_id, 'sender_id');
    const senderKey = await fetchPublicKey(senderId, senderKeyVersion);
    if (senderId !== requireInteger(local.user_id, 'local_user_id')) {
      rememberPeerIdentity(senderKey, { allowNewVersionForDecrypt: true });
    }

    const metadata = {
      group_id: requireInteger(payload.group_id, 'group_id'),
      membership_version: requireInteger(payload.membership_version, 'membership_version'),
      sender_id: senderId,
      receiver_id: receiverId,
      sender_key_version: senderKeyVersion,
      receiver_key_version: receiverKeyVersion
    };
    const sessionKey = payload.sender_ephemeral_public_key
      ? await deriveGroupSessionKey(
          privateKey,
          payload.sender_ephemeral_public_key,
          metadata
        )
      : await deriveGroupSessionKey(privateKey, senderKey.identity_public_key, metadata);
    return decryptText(payload, sessionKey);
  }

  async function fetchGroupMemberKeys(groupId) {
    const response = await fetch(`/api/groups/${encodeURIComponent(groupId)}/members/`);
    if (response.status === 404) {
      throw new GroupChatCryptoError('group_not_found', 'Group chat does not exist or is not available.');
    }
    if (!response.ok) {
      throw new GroupChatCryptoError('group_unavailable', 'Unable to load group member information.');
    }
    const payload = await response.json();
    if (!payload.members || !Array.isArray(payload.members)) {
      throw new GroupChatCryptoError('invalid_group_data', 'Group member data is invalid.');
    }
    return payload;
  }

  // ── File key wrapping (for file transfer) ────────────────────────

  const FILE_KEY_WRAP_HKDF_INFO = 'ichat-file-key-wrap-v1';

  // ── In-memory public key cache ─────────────────────────────────
  const _publicKeyCache = new Map();  // key: `${userId}:v${keyVersion}`
  const PUBLIC_KEY_CACHE_MAX = 200;

  function _cachePublicKey(userId, keyVersion, key) {
    const cacheKey = `${userId}:v${keyVersion}`;
    if (_publicKeyCache.size >= PUBLIC_KEY_CACHE_MAX) {
      const firstKey = _publicKeyCache.keys().next().value;
      _publicKeyCache.delete(firstKey);
    }
    _publicKeyCache.set(cacheKey, key);
  }

  function _getCachedPublicKey(userId, keyVersion) {
    const cacheKey = `${userId}:v${keyVersion}`;
    return _publicKeyCache.get(cacheKey) || null;
  }

  async function wrapFileKey(fileKeyBytes, fileId, holderId, metadata) {
    if (!metadata) {
      metadata = {
        group_id: window.activeChatId || 0,
        membership_version: window.activeMembershipVersion || 1,
        sender_id: window.myUserId || 0,
        receiver_id: holderId,
        sender_key_version: window.localKeyVersion || 1,
        receiver_key_version: 0,
      };
    }

    const remoteKey = await fetchPublicKey(holderId, metadata.receiver_key_version || undefined);
    metadata.receiver_key_version = requireInteger(remoteKey.key_version, 'remote_key_version');
    metadata.sender_key_version = requireInteger(currentRecord().key_version, 'local_key_version');
    const local = currentRecord();

    // Generate ephemeral key pair for forward secrecy
    const ephemeralKeyPair = await generateEphemeralKeyPair();
    const senderEphemeralPublicKeyB64 = await exportPublicKeyBase64(ephemeralKeyPair.publicKey);

    const contextBytes = new TextEncoder().encode(groupContext(metadata));
    const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);
    const remotePublicKey = await importPublicKey(remoteKey.identity_public_key);
    const sharedSecret = await window.crypto.subtle.deriveBits(
      { name: 'ECDH', public: remotePublicKey }, ephemeralKeyPair.privateKey, 256
    );
    const hkdfKey = await window.crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveKey']);
    const wrapSessionKey = await window.crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(FILE_KEY_WRAP_HKDF_INFO) },
      hkdfKey, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']
    );

    const nonce = window.crypto.getRandomValues(new Uint8Array(12));
    const aadStr = 'ichat-file-key-wrap-v1:' + fileId + ':' + holderId;
    const aad = new TextEncoder().encode(aadStr);
    const combined = new Uint8Array(
      await window.crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
        wrapSessionKey, fileKeyBytes
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
      membership_version: metadata.membership_version,
      sender_ephemeral_public_key: senderEphemeralPublicKeyB64,
    };
  }

  async function unwrapFileKey(wrapped, fileId, holderId, senderId, metadata) {
    const candidateIds = [];
    if (metadata && metadata.group_id) candidateIds.push(Number(metadata.group_id));
    if (window.activeChatId) candidateIds.push(Number(window.activeChatId));

    // Remove duplicates while preserving order, and filter to positive integers only
    const uniqueIds = [...new Set(candidateIds)].filter(id => Number.isInteger(id) && id > 0);

    console.log('[GroupE2EE.unwrapFileKey] Parameters:', {
      fileId,
      holderId,
      senderId,
      activeChatId: window.activeChatId,
      wrappedSenderKeyVer: wrapped.sender_key_version,
      wrappedReceiverKeyVer: wrapped.receiver_key_version,
      hasMetadata: !!metadata,
      candidateGroupIds: uniqueIds
    });

    const local = currentRecord();
    const localKeyVersion = wrapped.receiver_key_version || (metadata && metadata.receiver_key_version) || null;
    const privateKey = await currentPrivateKey(local, localKeyVersion);

    // Use sender's ephemeral public key (forward secrecy) if available;
    // fall back to sender identity key for legacy wrapped keys.
    let remotePublicKey;
    if (wrapped.sender_ephemeral_public_key) {
      remotePublicKey = await importPublicKey(wrapped.sender_ephemeral_public_key);
    } else {
      const senderKey = await fetchPublicKey(senderId, wrapped.sender_key_version || undefined);
      remotePublicKey = await importPublicKey(senderKey.identity_public_key);
    }

    console.log('[GroupE2EE.unwrapFileKey] Key details:', {
      localKeyVer: local.key_version,
      localKeyFingerprint: local.key_fingerprint,
      usingEphemeralKey: !!wrapped.sender_ephemeral_public_key,
    });

    const sharedSecret = await window.crypto.subtle.deriveBits(
      { name: 'ECDH', public: remotePublicKey }, privateKey, 256
    );
    const hkdfKey = await window.crypto.subtle.importKey('raw', sharedSecret, 'HKDF', false, ['deriveKey']);

    const ciphertext = base64ToBytes(wrapped.encrypted_file_key);
    const nonce = base64ToBytes(wrapped.nonce);
    const authTag = base64ToBytes(wrapped.auth_tag);
    const combined = new Uint8Array(ciphertext.length + authTag.length);
    combined.set(ciphertext, 0);
    combined.set(authTag, ciphertext.length);

    const aadStr = 'ichat-file-key-wrap-v1:' + fileId + ':' + holderId;
    console.log('[GroupE2EE.unwrapFileKey] AAD String:', aadStr);
    const aad = new TextEncoder().encode(aadStr);

    let decrypted = null;
    let lastError = null;

    for (const groupId of uniqueIds) {
      try {
        const testMetadata = {
          group_id: groupId,
          membership_version: wrapped.membership_version || (metadata && metadata.membership_version) || window.activeMembershipVersion || 1,
          sender_id: senderId,
          receiver_id: holderId,
          sender_key_version: wrapped.sender_key_version || 0,
          receiver_key_version: wrapped.receiver_key_version || 0,
        };

        const contextStr = groupContext(testMetadata);
        const contextBytes = new TextEncoder().encode(contextStr);
        const salt = await window.crypto.subtle.digest('SHA-256', contextBytes);

        const wrapSessionKey = await window.crypto.subtle.deriveKey(
          { name: 'HKDF', hash: 'SHA-256', salt, info: new TextEncoder().encode(FILE_KEY_WRAP_HKDF_INFO) },
          hkdfKey, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']
        );

        decrypted = await window.crypto.subtle.decrypt(
          { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
          wrapSessionKey, combined
        );
        console.log('[GroupE2EE.unwrapFileKey] Decrypted successfully using groupId = ' + groupId);
        break;
      } catch (err) {
        lastError = err;
        console.warn('[GroupE2EE.unwrapFileKey] Decryption failed for groupId = ' + groupId + '. Error:', err);
      }
    }

    if (!decrypted) {
      console.error('[GroupE2EE.unwrapFileKey] Decryption failed for all candidate group IDs.');
      throw lastError || new Error('Decryption failed for all candidate group IDs');
    }

    return new Uint8Array(decrypted);
  }

  async function wrapFileKeyForSelf(fileKeyBytes, fileId, holderId) {
    const local = currentRecord();
    const metadata = {
      group_id: window.activeChatId || 0,
      membership_version: window.activeMembershipVersion || 1,
      sender_id: holderId,
      receiver_id: holderId,
      sender_key_version: requireInteger(local.key_version, 'local_key_version'),
      receiver_key_version: requireInteger(local.key_version, 'local_key_version'),
    };
    return wrapFileKey(fileKeyBytes, fileId, holderId, metadata);
  }

  window.iChatGroupE2EE = {
    MESSAGE_ALGORITHM,
    GroupChatCryptoError,
    groupContext,
    deriveGroupSessionKey,
    encryptText,
    decryptText,
    encryptGroupMessage,
    decryptGroupMessage,
    fetchGroupMemberKeys,
    fetchBatchPublicKeys,
    trustPeerKey,
    forgetPeerKey,
    // File transfer
    wrapFileKey,
    unwrapFileKey,
    wrapFileKeyForSelf,
  };
})();
