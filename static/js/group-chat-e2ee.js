(function () {
  'use strict';

  const IDENTITY_ALGORITHM = 'ECDH-P256';
  const MESSAGE_ALGORITHM = 'AES-256-GCM';
  const HKDF_INFO = 'chat-message-encryption-v1';

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
      throw new GroupChatCryptoError('invalid_metadata', `${field} must be a positive integer.`);
    }
    return parsed;
  }

  function currentRecord() {
    const record = window.iChatKeyManager && window.iChatKeyManager.loadCurrentRecord();
    if (!record || !record.private_key || !record.key_version) {
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

  async function deriveGroupSessionKey(localPrivateKey, remoteIdentityPublicKey, metadata) {
    const contextBytes = new TextEncoder().encode(groupContext(metadata));
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

    const privateKey = await importPrivateKey(local.private_key);
    const senderId = requireInteger(local.user_id, 'local_user_id');
    const senderKeyVersion = requireInteger(local.key_version, 'local_key_version');

    const recipients = [];
    for (const receiverId of uniqueMemberIds) {
      const receiverKey = keyMap[String(receiverId)];
      const metadata = {
        group_id: groupId,
        membership_version: membershipVersion,
        sender_id: senderId,
        receiver_id: receiverId,
        sender_key_version: senderKeyVersion,
        receiver_key_version: receiverKey.key_version
      };
      const sessionKey = await deriveGroupSessionKey(privateKey, receiverKey.identity_public_key, metadata);
      const encrypted = await encryptText(plaintext, sessionKey);
      recipients.push({
        receiver_id: receiverId,
        receiver_key_version: receiverKey.key_version,
        ciphertext: encrypted.ciphertext,
        nonce: encrypted.nonce,
        auth_tag: encrypted.auth_tag
      });
    }

    return {
      algorithm: MESSAGE_ALGORITHM,
      sender_key_version: senderKeyVersion,
      membership_version: membershipVersion,
      recipients
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
    if (receiverKeyVersion !== requireInteger(local.key_version, 'local_key_version')) {
      throw new GroupChatCryptoError(
        'local_key_changed',
        'Your current device key cannot decrypt this message. Import the matching key backup.'
      );
    }

    const senderKeyVersion = requireInteger(payload.sender_key_version, 'sender_key_version');
    const senderId = requireInteger(payload.sender_id, 'sender_id');
    const senderKey = await fetchPublicKey(senderId, senderKeyVersion);

    const privateKey = await importPrivateKey(local.private_key);
    const metadata = {
      group_id: requireInteger(payload.group_id, 'group_id'),
      membership_version: requireInteger(payload.membership_version, 'membership_version'),
      sender_id: senderId,
      receiver_id: receiverId,
      sender_key_version: senderKeyVersion,
      receiver_key_version: receiverKeyVersion
    };
    const sessionKey = await deriveGroupSessionKey(privateKey, senderKey.identity_public_key, metadata);
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
    fetchBatchPublicKeys
  };
})();