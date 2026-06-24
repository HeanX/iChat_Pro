(function () {
  const ALGORITHM = 'ECDH-P256';
  const STORAGE_PREFIX = 'ichat_identity_key:';
  const DB_NAME = 'ichat-e2ee-keys';
  const DB_VERSION = 1;
  const KEY_STORE = 'identity_private_keys';
  const PENDING_BACKUP_PREFIX = 'ichat_identity_key_pending_backup:';

  function currentUserId() {
    const script = document.currentScript || document.getElementById('ichat-key-manager-script');
    return script ? script.dataset.currentUserId : null;
  }

  function storageKey(userId) {
    return `${STORAGE_PREFIX}${userId}`;
  }

  function pendingBackupKey(userId) {
    return `${PENDING_BACKUP_PREFIX}${userId}`;
  }

  function privateKeyStoreKey(userId, keyVersion = null) {
    return keyVersion ? `${userId}:v${keyVersion}` : String(userId);
  }

  function rememberPendingBackup(record) {
    if (!record || !record.private_key) return;
    sessionStorage.setItem(pendingBackupKey(record.user_id), JSON.stringify(record));
  }

  function idbRequest(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function idbTransaction(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  }

  function openKeyDb() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(KEY_STORE)) db.createObjectStore(KEY_STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function savePrivateKey(userId, privateKey, keyVersion = null) {
    const db = await openKeyDb();
    const tx = db.transaction(KEY_STORE, 'readwrite');
    tx.objectStore(KEY_STORE).put(privateKey, privateKeyStoreKey(userId));
    if (keyVersion) {
      tx.objectStore(KEY_STORE).put(privateKey, privateKeyStoreKey(userId, keyVersion));
    }
    await idbTransaction(tx);
    db.close();
  }

  async function loadPrivateKey(userId, keyVersion = null) {
    const db = await openKeyDb();
    const tx = db.transaction(KEY_STORE, 'readonly');
    const store = tx.objectStore(KEY_STORE);
    let key = null;
    if (keyVersion) {
      key = await idbRequest(store.get(privateKeyStoreKey(userId, keyVersion)));
      db.close();
      return key || null;
    }
    if (!key) {
      key = await idbRequest(store.get(privateKeyStoreKey(userId)));
    }
    db.close();
    return key || null;
  }

  async function importPrivateKey(privateKeyJwk, extractable) {
    return window.crypto.subtle.importKey(
      'jwk',
      privateKeyJwk,
      { name: 'ECDH', namedCurve: 'P-256' },
      !!extractable,
      ['deriveBits']
    );
  }

  function arrayBufferToBase64(buffer) {
    let binary = '';
    for (const byte of new Uint8Array(buffer)) {
      binary += String.fromCharCode(byte);
    }
    return btoa(binary);
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

  async function fingerprint(publicKeySpki) {
    const digest = await window.crypto.subtle.digest('SHA-256', publicKeySpki);
    return Array.from(new Uint8Array(digest))
      .map(byte => byte.toString(16).padStart(2, '0'))
      .join('')
      .toUpperCase();
  }

  async function generateRecord(userId) {
    const keyPair = await window.crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      ['deriveKey', 'deriveBits']
    );
    const publicKey = await window.crypto.subtle.exportKey('jwk', keyPair.publicKey);
    const privateKeyJwk = await window.crypto.subtle.exportKey('jwk', keyPair.privateKey);
    const publicKeySpki = await window.crypto.subtle.exportKey('spki', keyPair.publicKey);
    await savePrivateKey(userId, await importPrivateKey(privateKeyJwk, false));
    const backupRecord = {
      format_version: 1,
      user_id: String(userId),
      algorithm: ALGORITHM,
      private_key: privateKeyJwk,
      public_key: publicKey,
      identity_public_key: arrayBufferToBase64(publicKeySpki),
      key_fingerprint: await fingerprint(publicKeySpki),
      saved_at: new Date().toISOString()
    };
    rememberPendingBackup(backupRecord);
    const record = { ...backupRecord };
    delete record.private_key;
    return record;
  }

  function loadRecord(userId) {
    const raw = localStorage.getItem(storageKey(userId));
    if (!raw) return null;
    try {
      const record = JSON.parse(raw);
      return record.user_id === String(userId) ? record : null;
    } catch (error) {
      console.warn('Ignoring invalid local E2EE key record.', error);
      return null;
    }
  }

  function saveRecord(record) {
    const persisted = { ...record };
    delete persisted.private_key;
    localStorage.setItem(storageKey(record.user_id), JSON.stringify(persisted));
    localStorage.removeItem('ichat_ecdh_private_key');
    localStorage.removeItem('ichat_ecdh_public_key');
  }

  async function migrateLegacyPrivateKey(record) {
    if (!record || !record.private_key) return record;
    const privateKey = await importPrivateKey(record.private_key, false);
    await savePrivateKey(record.user_id, privateKey);
    rememberPendingBackup(record);
    delete record.private_key;
    saveRecord(record);
    return record;
  }

  async function uploadPublicKey(record) {
    const response = await fetch('/api/keys/upload/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
      },
      body: JSON.stringify({
        identity_public_key: record.identity_public_key,
        key_fingerprint: record.key_fingerprint,
        algorithm: record.algorithm
      })
    });
    if (!response.ok) {
      throw new Error(`Public-key registration failed (${response.status}).`);
    }
    const payload = await response.json();
    record.key_version = payload.key.key_version;
    const privateKey = await loadPrivateKey(record.user_id);
    if (privateKey) {
      await savePrivateKey(record.user_id, privateKey, record.key_version);
    }
    saveRecord(record);
    return record;
  }

  async function fetchServerKey(userId, keyVersion = null) {
    const suffix = keyVersion === null ? '' : `${encodeURIComponent(keyVersion)}/`;
    const response = await fetch(`/api/keys/${encodeURIComponent(userId)}/${suffix}`);
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`Unable to check server key (${response.status}).`);
    const payload = await response.json();
    return payload.key || null;
  }

  async function hasServerKey(userId) {
    const response = await fetch(`/api/keys/${encodeURIComponent(userId)}/`);
    if (response.status === 404) return false;
    if (!response.ok) throw new Error(`Unable to check server key (${response.status}).`);
    return true;
  }

  async function initialize() {
    const userId = currentUserId();
    if (!userId) throw new Error('Current user ID is unavailable.');

    let record = loadRecord(userId);
    if (!record) {
      const serverHasKey = await hasServerKey(userId);
      if (serverHasKey) {
        window.dispatchEvent(new CustomEvent('ichat:key-missing'));
        throw new Error('Local private key is missing. Import your key backup to decrypt existing messages.');
      }
      record = await generateRecord(userId);
      saveRecord(record);
      return uploadPublicKey(record);
    }
    record = await migrateLegacyPrivateKey(record);
    const privateKey = await loadPrivateKey(userId);
    if (!privateKey) {
      window.dispatchEvent(new CustomEvent('ichat:key-missing'));
      throw new Error('Local private key is missing. Import your key backup to decrypt existing messages.');
    }

    const activeKey = await fetchServerKey(userId);
    if (!activeKey) {
      return uploadPublicKey(record);
    }

    if (activeKey.identity_public_key === record.identity_public_key) {
      record.key_version = activeKey.key_version;
      await savePrivateKey(userId, privateKey, record.key_version);
      saveRecord(record);
      return record;
    }

    if (record.key_version) {
      const historicalKey = await fetchServerKey(userId, record.key_version);
      if (historicalKey && historicalKey.identity_public_key === record.identity_public_key) {
        await savePrivateKey(userId, privateKey, record.key_version);
        return record;
      }
    }

    window.dispatchEvent(new CustomEvent('ichat:key-missing'));
    throw new Error('Local private key does not match the server encryption identity. Import the matching key backup or rotate keys explicitly.');
  }

  function exportBackup() {
    const userId = currentUserId();
    const record = loadRecord(userId);
    const pendingBackup = sessionStorage.getItem(pendingBackupKey(userId));
    if (!pendingBackup && (!record || !record.private_key)) {
      throw new Error('This device stores its private key as non-extractable. A JSON backup can only be exported immediately after key creation, import, or legacy migration.');
    }
    const backupRecord = pendingBackup ? JSON.parse(pendingBackup) : record;
    const blob = new Blob([JSON.stringify(backupRecord, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `ichat-e2ee-key-backup-${userId}.json`;
    link.click();
    URL.revokeObjectURL(url);
    sessionStorage.removeItem(pendingBackupKey(userId));
  }

  async function importBackup(file) {
    const userId = currentUserId();
    const record = JSON.parse(await file.text());
    if (
      record.format_version !== 1 ||
      record.user_id !== String(userId) ||
      record.algorithm !== ALGORITHM ||
      !record.private_key ||
      !record.public_key ||
      !record.identity_public_key ||
      !record.key_fingerprint
    ) {
      throw new Error('This backup is invalid or belongs to another account.');
    }
    const privateKey = await importPrivateKey(record.private_key, false);
    const importedPublicKey = await window.crypto.subtle.importKey(
      'jwk',
      record.public_key,
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      []
    );
    const publicKeySpki = await window.crypto.subtle.exportKey('spki', importedPublicKey);
    const calculatedFingerprint = await fingerprint(publicKeySpki);
    if (
      record.private_key.x !== record.public_key.x ||
      record.private_key.y !== record.public_key.y ||
      record.identity_public_key !== arrayBufferToBase64(publicKeySpki) ||
      record.key_fingerprint !== calculatedFingerprint
    ) {
      throw new Error('This backup contains mismatched key material.');
    }
    await savePrivateKey(userId, privateKey);
    delete record.private_key;
    saveRecord(record);
    return uploadPublicKey(record);
  }

  window.iChatKeyManager = {
    initialize,
    exportBackup,
    importBackup,
    resetIdentityKey: async () => {
      const userId = currentUserId();
      if (!userId) throw new Error('Current user ID is unavailable.');
      const record = await generateRecord(userId);
      saveRecord(record);
      return uploadPublicKey(record);
    },
    uploadCurrentRecord: () => {
      const record = loadRecord(currentUserId());
      if (!record) throw new Error('No local private key is available to upload.');
      return uploadPublicKey(record);
    },
    loadCurrentRecord: () => loadRecord(currentUserId()),
    getCurrentPrivateKey: keyVersion => loadPrivateKey(currentUserId(), keyVersion)
  };
})();
