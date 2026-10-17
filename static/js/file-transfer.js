/**
 * iChat Pro — Encrypted File Transfer Module
 *
 * Provides chunked AES-256-GCM file encryption, upload session management,
 * and download-with-decryption.  Integrates with the existing E2EE modules
 * (private-chat-e2ee.js / group-chat-e2ee.js) for per-recipient file-key
 * wrapping.
 *
 * Exposed as ``window.iChatFileTransfer``.
 */
(function () {
    'use strict';

    const FILE_ALGORITHM = 'AES-256-GCM';
    const CHUNK_SIZE = 1024 * 1024;          // 1 MiB
    const MAX_FILE_SIZE = 100 * 1024 * 1024;  // 100 MiB
    const MAX_CONCURRENT_CHUNKS = 4;

    // ── Byte / Base64 helpers (duplicated here to avoid cross-module deps) ─

    function bytesToBase64(bytes) {
        let binary = '';
        for (let i = 0; i < bytes.length; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    function base64ToBytes(b64) {
        const binary = atob(b64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes;
    }

    function bytesToHex(bytes) {
        return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
    }

    function hexToBytes(hex) {
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < hex.length; i += 2) {
            bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
        }
        return bytes;
    }

    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, function (ch) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;',
            }[ch];
        });
    }

    function formatBytes(bytes) {
        if (!bytes) return '';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function getCurrentReplyId() {
        return window.replyToMessage && window.replyToMessage.id ? window.replyToMessage.id : null;
    }

    function clearReplyState() {
        if (window.MessageActions && typeof window.MessageActions.cancelReply === 'function') {
            window.MessageActions.cancelReply();
        } else {
            window.replyToMessage = null;
            const banner = document.getElementById('reply-quote-banner');
            if (banner) banner.style.display = 'none';
        }
    }

    // ── Crypto helpers ──────────────────────────────────────────────────

    /** Generate a random AES-256-GCM key for file encryption. */
    async function generateFileKey() {
        const key = await window.crypto.subtle.generateKey(
            { name: 'AES-GCM', length: 256 },
            true,  // extractable — needed to wrap for recipients
            ['encrypt', 'decrypt']
        );
        const rawBytes = new Uint8Array(
            await window.crypto.subtle.exportKey('raw', key)
        );
        return { fileKey: key, fileKeyBytes: rawBytes };
    }

    /** Import raw 32 bytes as an AES-GCM CryptoKey. */
    async function importFileKey(rawBytes) {
        return window.crypto.subtle.importKey(
            'raw', rawBytes,
            { name: 'AES-GCM', length: 256 },
            false,  // not extractable (already have raw bytes)
            ['encrypt', 'decrypt']
        );
    }

    /**
     * Encrypt one file chunk with AES-256-GCM.
     *
     * @param {Uint8Array} chunkBytes - plaintext chunk
     * @param {CryptoKey} fileKey
     * @param {string} clientFileId - stable client-generated file UUID (AAD)
     * @param {number} chunkIndex
     * @returns {{ciphertext: Uint8Array, nonce: Uint8Array, authTag: Uint8Array, ciphertextSha256: string}}
     */
    async function encryptFileChunk(chunkBytes, fileKey, clientFileId, chunkIndex) {
        const nonce = window.crypto.getRandomValues(new Uint8Array(12));
        const aadStr = 'ichat-file-chunk-v1:' + clientFileId + ':' + chunkIndex;
        const aad = new TextEncoder().encode(aadStr);

        // AES-GCM appends the 16-byte auth tag to the ciphertext
        const combined = new Uint8Array(
            await window.crypto.subtle.encrypt(
                { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
                fileKey,
                chunkBytes
            )
        );
        // Split: last 16 bytes = auth tag
        const tagStart = combined.length - 16;
        const ciphertext = combined.slice(0, tagStart);

        // Compute SHA-256 of ciphertext for integrity verification
        const ciphertextHash = await window.crypto.subtle.digest('SHA-256', ciphertext);
        const ciphertextSha256 = bytesToHex(new Uint8Array(ciphertextHash));

        return {
            ciphertext: ciphertext,
            nonce: nonce,
            authTag: combined.slice(tagStart),
            ciphertextSha256: ciphertextSha256,
        };
    }

    /**
     * Decrypt one file chunk with AES-256-GCM.
     */
    async function decryptFileChunk(encryptedPayload, fileKey, clientFileId, chunkIndex) {
        const nonce = encryptedPayload.nonce;
        const aadStr = 'ichat-file-chunk-v1:' + clientFileId + ':' + chunkIndex;
        const aad = new TextEncoder().encode(aadStr);

        // Recombine ciphertext + auth tag
        const combined = new Uint8Array(encryptedPayload.ciphertext.length + encryptedPayload.authTag.length);
        combined.set(encryptedPayload.ciphertext, 0);
        combined.set(encryptedPayload.authTag, encryptedPayload.ciphertext.length);

        return new Uint8Array(
            await window.crypto.subtle.decrypt(
                { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
                fileKey,
                combined
            )
        );
    }

    /**
     * Encrypt file metadata JSON with AES-256-GCM.
     *
     * @param {object} metadata - {original_name, mime_type, plain_size_bytes, ...}
     * @param {CryptoKey} fileKey
     * @param {string} clientFileId
     */
    async function encryptFileMetadata(metadata, fileKey, clientFileId) {
        const plaintext = new TextEncoder().encode(JSON.stringify(metadata));
        const nonce = window.crypto.getRandomValues(new Uint8Array(12));
        const aadStr = 'ichat-file-metadata-v1:' + clientFileId;
        const aad = new TextEncoder().encode(aadStr);

        const combined = new Uint8Array(
            await window.crypto.subtle.encrypt(
                { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
                fileKey,
                plaintext
            )
        );
        const tagStart = combined.length - 16;
        return {
            encrypted_metadata: bytesToBase64(combined.slice(0, tagStart)),
            metadata_nonce: bytesToBase64(nonce),
            metadata_auth_tag: bytesToBase64(combined.slice(tagStart)),
        };
    }

    /**
     * Decrypt file metadata JSON.
     */
    async function decryptFileMetadata(encryptedB64, nonceB64, authTagB64, fileKey, clientFileId) {
        const ciphertext = base64ToBytes(encryptedB64);
        const nonce = base64ToBytes(nonceB64);
        const authTag = base64ToBytes(authTagB64);
        const combined = new Uint8Array(ciphertext.length + authTag.length);
        combined.set(ciphertext, 0);
        combined.set(authTag, ciphertext.length);

        const aadStr = 'ichat-file-metadata-v1:' + clientFileId;
        const aad = new TextEncoder().encode(aadStr);

        const plainBytes = new Uint8Array(
            await window.crypto.subtle.decrypt(
                { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
                fileKey,
                combined
            )
        );
        return JSON.parse(new TextDecoder().decode(plainBytes));
    }

    // ── File key wrapping (per-recipient) ───────────────────────────────

    /**
     * Wrap raw file key bytes for one recipient using the existing E2EE session key.
     *
     * @param {Uint8Array} fileKeyBytes - raw 32-byte file key
     * @param {CryptoKey} sessionKey - derived via ECDH+HKDF for this recipient
     * @param {number} fileId - server-assigned file ID
     * @param {number} holderId - recipient user ID
     * @returns {{encrypted_file_key: string, nonce: string, auth_tag: string, algorithm: string}}
     */
    async function wrapFileKey(fileKeyBytes, sessionKey, fileId, holderId) {
        const nonce = window.crypto.getRandomValues(new Uint8Array(12));
        const aadStr = 'ichat-file-key-wrap-v1:' + fileId + ':' + holderId;
        const aad = new TextEncoder().encode(aadStr);

        const combined = new Uint8Array(
            await window.crypto.subtle.encrypt(
                { name: 'AES-GCM', iv: nonce, tagLength: 128, additionalData: aad },
                sessionKey,
                fileKeyBytes
            )
        );
        const tagStart = combined.length - 16;
        return {
            encrypted_file_key: bytesToBase64(combined.slice(0, tagStart)),
            nonce: bytesToBase64(nonce),
            auth_tag: bytesToBase64(combined.slice(tagStart)),
            algorithm: 'AES-256-GCM',
        };
    }

    /**
     * Unwrap the file key using the existing E2EE session key.
     *
     * @param {object} wrapped - {encrypted_file_key, nonce, auth_tag}
     * @param {CryptoKey} sessionKey
     * @param {number} fileId
     * @param {number} holderId
     * @returns {Uint8Array} raw 32-byte file key
     */
    async function unwrapFileKey(wrapped, sessionKey, fileId, holderId) {
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
                sessionKey,
                combined
            )
        );
    }

    // ── UploadSession ───────────────────────────────────────────────────

    class UploadSession {
        /**
         * @param {File} file - browser File object
         * @param {number} conversationId
         * @param {string} conversationType - 'single' | 'group'
         * @param {string} messageKind - 'image' | 'file' | 'sticker'
         */
        constructor(file, conversationId, conversationType, messageKind, options) {
            options = options || {};
            this.file = file;
            this.conversationId = conversationId;
            this.conversationType = conversationType;
            this.messageKind = messageKind;
            this.caption = String(options.caption || '').trim();
            this.replyToMessageId = options.replyToMessageId || null;
            this.clientFileId = crypto.randomUUID();
            this.chunkSize = CHUNK_SIZE;
            this.chunkCount = Math.ceil(file.size / this.chunkSize);
            this.fileId = null;
            this.uploadId = null;
            this.fileKey = null;
            this.fileKeyBytes = null;
            this.encryptedMetadata = null;
            this.uploadedChunks = new Set();
            this.status = 'pending';  // pending → uploading → completed → messaged
            this.onProgress = null;   // callback(percent: number)
            this._aborted = false;
        }

        /** Create the upload session, encrypt metadata, and prepare. */
        async init() {
            if (this.status !== 'pending') return;

            // 1. Generate file key
            const { fileKey, fileKeyBytes } = await generateFileKey();
            this.fileKey = fileKey;
            this.fileKeyBytes = fileKeyBytes;

            // 2. Encrypt metadata
            const meta = {
                original_name: this.file.name,
                mime_type: this.file.type || 'application/octet-stream',
                plain_size_bytes: this.file.size,
                plain_sha256: '',  // can be filled client-side if needed
                caption_present: Boolean(this.caption),
            };
            this.encryptedMetadata = await encryptFileMetadata(meta, this.fileKey, this.clientFileId);

            // 3. Create upload session via REST API
            const resp = await window.apiFetch('/api/files/uploads/', {
                method: 'POST',
                body: JSON.stringify({
                    client_file_id: this.clientFileId,
                    conversation_id: this.conversationId,
                    conversation_type: this.conversationType,
                    message_kind: this.messageKind,
                    total_size_bytes: this.file.size,
                    chunk_count: this.chunkCount,
                    chunk_size_bytes: this.chunkSize,
                    algorithm: FILE_ALGORITHM,
                    encrypted_metadata: this.encryptedMetadata.encrypted_metadata,
                    metadata_nonce: this.encryptedMetadata.metadata_nonce,
                    metadata_auth_tag: this.encryptedMetadata.metadata_auth_tag,
                }),
            });

            this.fileId = resp.file_id;
            this.uploadId = resp.upload_id;

            // If server returned existing uploaded chunks (idempotent resume)
            if (resp.uploaded_chunks && resp.uploaded_chunks.length > 0) {
                for (const idx of resp.uploaded_chunks) {
                    this.uploadedChunks.add(idx);
                }
            }
            this.status = 'uploading';
        }

        /** Upload all chunks with concurrency control. */
        async uploadAll() {
            if (this.status !== 'uploading') throw new Error('Session not in uploading state');

            const pending = [];
            for (let i = 0; i < this.chunkCount; i++) {
                if (!this.uploadedChunks.has(i)) {
                    pending.push(i);
                }
            }

            if (pending.length === 0) {
                this._reportProgress(100);
                return;
            }

            // Process in batches of MAX_CONCURRENT_CHUNKS
            for (let b = 0; b < pending.length; b += MAX_CONCURRENT_CHUNKS) {
                if (this._aborted) throw new Error('Upload aborted');
                const batch = pending.slice(b, b + MAX_CONCURRENT_CHUNKS);
                await Promise.all(batch.map(i => this._uploadChunk(i)));
                this._reportProgress(
                    Math.round((this.uploadedChunks.size / this.chunkCount) * 100)
                );
            }
        }

        async _uploadChunk(chunkIndex) {
            const start = chunkIndex * this.chunkSize;
            const end = Math.min(start + this.chunkSize, this.file.size);
            const plainChunk = new Uint8Array(
                await this.file.slice(start, end).arrayBuffer()
            );

            // Encrypt
            const encrypted = await encryptFileChunk(
                plainChunk, this.fileKey, this.clientFileId, chunkIndex
            );

            // Build FormData for multipart upload
            const formData = new FormData();
            formData.append('chunk', new Blob([encrypted.ciphertext]), 'chunk.bin');
            formData.append('nonce', bytesToBase64(encrypted.nonce));
            formData.append('auth_tag', bytesToBase64(encrypted.authTag));
            formData.append('ciphertext_sha256', encrypted.ciphertextSha256 || '');
            formData.append('size_bytes', String(encrypted.ciphertext.length));

            const url = '/api/files/uploads/' + encodeURIComponent(this.uploadId) +
                        '/chunks/' + chunkIndex + '/';

            const csrf = getCookie('csrftoken');
            const resp = await fetch(url, {
                method: 'PUT',
                headers: { 'X-CSRFToken': csrf },
                body: formData,
            });

            if (!resp.ok) {
                let detail = resp.statusText;
                try {
                    const body = await resp.json();
                    detail = body.error || body.detail || detail;
                } catch (_) {}
                throw new Error('Chunk upload failed: ' + detail);
            }

            this.uploadedChunks.add(chunkIndex);
        }

        /** Complete the upload — merge chunks server-side. */
        async complete() {
            if (this.status !== 'uploading') throw new Error('Session not in uploading state');
            if (this.uploadedChunks.size !== this.chunkCount) {
                throw new Error('Not all chunks uploaded');
            }

            // Server computes and stores merged ciphertext SHA-256 during chunk assembly
            const resp = await window.apiFetch(
                '/api/files/uploads/' + encodeURIComponent(this.uploadId) + '/complete/',
                { method: 'POST' }
            );

            this.status = 'completed';
            return resp;
        }

        /** Cancel the upload. */
        async cancel() {
            if (this.status !== 'uploading') return;
            this._aborted = true;
            try {
                await window.apiFetch(
                    '/api/files/uploads/' + encodeURIComponent(this.uploadId) + '/cancel/',
                    { method: 'DELETE' }
                );
            } catch (_) {
                // Best-effort cancel
            }
            this.status = 'cancelled';
        }

        _reportProgress(percent) {
            if (typeof this.onProgress === 'function') {
                this.onProgress(Math.min(100, Math.max(0, percent)));
            }
        }

        /** Get the file metadata (with encrypted_file_key for a specific holder). */
        getMetadataPayload() {
            return this.encryptedMetadata;
        }
    }

    // ── Download & Decrypt ──────────────────────────────────────────────

    /**
     * Download the full encrypted file, decrypt it, and return a Blob.
     *
     * @param {number} fileId
     * @param {CryptoKey} fileKey - imported AES-GCM key
     * @param {object} metadata - result from GET /api/files/<fileId>/
     * @returns {Blob} decrypted file as a Blob
     */
    async function downloadAndDecryptFile(fileId, fileKey, metadata) {
        const clientFileId = metadata.client_file_id;
        const chunksMeta = metadata.chunks || [];

        // Download the complete ciphertext
        const resp = await fetch('/api/files/' + fileId + '/download/', {
            headers: { 'X-CSRFToken': getCookie('csrftoken') },
        });
        if (!resp.ok) {
            let detail = resp.statusText;
            try { const body = await resp.json(); detail = body.error || body.detail || detail; } catch (_) {}
            throw new Error('Download failed: ' + detail);
        }

        const ciphertextBytes = new Uint8Array(await resp.arrayBuffer());

        // Slice by chunk offsets and decrypt each
        const decryptedChunks = [];
        for (const cm of chunksMeta) {
            const slice = ciphertextBytes.slice(cm.offset_bytes, cm.offset_bytes + cm.size_bytes);
            const payload = {
                ciphertext: slice,
                nonce: base64ToBytes(cm.nonce),
                authTag: base64ToBytes(cm.auth_tag),
            };
            const plainChunk = await decryptFileChunk(payload, fileKey, clientFileId, cm.chunk_index);
            decryptedChunks.push(plainChunk);
        }

        // Concatenate
        const totalLength = decryptedChunks.reduce((sum, c) => sum + c.length, 0);
        const result = new Uint8Array(totalLength);
        let offset = 0;
        for (const chunk of decryptedChunks) {
            result.set(chunk, offset);
            offset += chunk.length;
        }

        // Detect MIME type from decrypted metadata if available
        let mimeType = 'application/octet-stream';
        try {
            const fileMeta = await decryptFileMetadata(
                metadata.encrypted_metadata,
                metadata.metadata_nonce,
                metadata.metadata_auth_tag,
                fileKey,
                clientFileId
            );
            mimeType = fileMeta.mime_type || mimeType;
        } catch (_) {
            // Metadata decryption failed — use default MIME type
        }

        return new Blob([result], { type: mimeType });
    }

    /**
     * Fetch file metadata and user's encrypted file key, decrypt the file key,
     * then download and decrypt the file.
     *
     * @param {number} fileId
     * @param {string} conversationType - 'single' | 'group'
     * @returns {{blob: Blob, metadata: object}}
     */
    async function fetchAndDecryptFile(fileId, conversationType) {
        // 1. Get file metadata
        const meta = await window.apiFetch('/api/files/' + fileId + '/');
        if (!meta.encrypted_file_key) {
            throw new Error('No file key available for this user');
        }

        // 2. Unwrap file key using existing E2EE session
        const e2eeModule = conversationType === 'group'
            ? window.iChatGroupE2EE : window.iChatPrivateE2EE;
        const localUserId = window.myUserId;

        // We need to derive the same session key used for wrapping.
        // For now, the key wrapping uses a dedicated HKDF info string.
        // The existing E2EE modules derive session keys via ECDH+HKDF.
        // We expose a helper on the E2EE modules:
        let fileKeyBytes;
        if (typeof e2eeModule.unwrapFileKey === 'function') {
            const unwrapMetadata = {
                conversation_id: meta.conversation_id || 0,
                group_id: meta.conversation_id || 0,
                sender_id: (meta.encrypted_file_key && meta.encrypted_file_key.sender_id) || meta.owner_id || 0,
                receiver_id: localUserId,
                sender_key_version: (meta.encrypted_file_key && meta.encrypted_file_key.sender_key_version) || 0,
                receiver_key_version: (meta.encrypted_file_key && meta.encrypted_file_key.receiver_key_version) || 0,
                membership_version: (meta.encrypted_file_key && meta.encrypted_file_key.membership_version) || 0
            };
            fileKeyBytes = await e2eeModule.unwrapFileKey(
                meta.encrypted_file_key,
                fileId,
                localUserId,
                (meta.encrypted_file_key && meta.encrypted_file_key.sender_id) || meta.owner_id || 0,
                unwrapMetadata
            );
        } else {
            // Fallback: use the decryptText approach with session key
            // The file_key in meta is already wrapped; try to unwrap with dedicated function
            throw new Error('File key unwrapping not available. Check E2EE module.');
        }

        // 3. Import file key
        const fileKey = await importFileKey(fileKeyBytes);

        // 4. Download and decrypt
        const blob = await downloadAndDecryptFile(fileId, fileKey, meta);

        // 5. Decrypt metadata for filename
        let decryptedMeta = {};
        try {
            decryptedMeta = await decryptFileMetadata(
                meta.encrypted_metadata, meta.metadata_nonce, meta.metadata_auth_tag,
                fileKey, meta.client_file_id
            );
        } catch (_) {}

        return { blob, metadata: decryptedMeta, fileMeta: meta };
    }

    async function fetchFileKeyBytes(fileId, conversationType) {
        const meta = await window.apiFetch('/api/files/' + fileId + '/');
        if (!meta.encrypted_file_key) {
            throw new Error('No file key available for this user');
        }

        const e2eeModule = conversationType === 'group'
            ? window.iChatGroupE2EE : window.iChatPrivateE2EE;
        if (!e2eeModule || typeof e2eeModule.unwrapFileKey !== 'function') {
            throw new Error('File key unwrapping is not available.');
        }

        const unwrapMetadata = {
            conversation_id: meta.conversation_id || 0,
            group_id: meta.conversation_id || 0,
            sender_id: (meta.encrypted_file_key && meta.encrypted_file_key.sender_id) || meta.owner_id || 0,
            receiver_id: window.myUserId,
            sender_key_version: (meta.encrypted_file_key && meta.encrypted_file_key.sender_key_version) || 0,
            receiver_key_version: (meta.encrypted_file_key && meta.encrypted_file_key.receiver_key_version) || 0,
            membership_version: (meta.encrypted_file_key && meta.encrypted_file_key.membership_version) || 0
        };
        const fileKeyBytes = await e2eeModule.unwrapFileKey(
            meta.encrypted_file_key,
            fileId,
            window.myUserId,
            (meta.encrypted_file_key && meta.encrypted_file_key.sender_id) || meta.owner_id || 0,
            unwrapMetadata
        );
        return { fileKeyBytes: fileKeyBytes, fileMeta: meta };
    }

    function ensureImageUploadModal() {
        let modal = document.getElementById('image-upload-modal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.id = 'image-upload-modal';
        modal.className = 'image-upload-modal hidden';
        modal.innerHTML =
            '<div class="image-upload-backdrop" data-image-upload-cancel></div>' +
            '<div class="image-upload-panel" role="dialog" aria-modal="true" aria-labelledby="image-upload-title">' +
                '<div class="image-upload-header">' +
                    '<button type="button" class="image-upload-icon-btn" data-image-upload-cancel title="Cancel">' +
                        '<i data-lucide="x" class="w-5 h-5"></i>' +
                    '</button>' +
                    '<div class="image-upload-title" id="image-upload-title">发送 1 张照片</div>' +
                    '<button type="button" class="image-upload-icon-btn" title="More">' +
                        '<i data-lucide="more-vertical" class="w-5 h-5"></i>' +
                    '</button>' +
                '</div>' +
                '<div class="image-upload-body">' +
                    '<img class="image-upload-preview" alt="">' +
                    '<div class="image-upload-file-meta"></div>' +
                '</div>' +
                '<div class="image-upload-footer">' +
                    '<textarea class="image-upload-caption" rows="1" maxlength="2000" placeholder="添加说明..."></textarea>' +
                    '<button type="button" class="image-upload-send" title="Send">' +
                        '<i data-lucide="send" class="w-5 h-5"></i>' +
                    '</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(modal);
        return modal;
    }

    function showImageUploadModal(file, conversationId, conversationType, messageKind) {
        return new Promise(function (resolve, reject) {
            const modal = ensureImageUploadModal();
            const preview = modal.querySelector('.image-upload-preview');
            const meta = modal.querySelector('.image-upload-file-meta');
            const captionInput = modal.querySelector('.image-upload-caption');
            const sendBtn = modal.querySelector('.image-upload-send');
            const title = modal.querySelector('.image-upload-title');
            const objectUrl = URL.createObjectURL(file);

            let closed = false;
            const cleanup = function () {
                modal.classList.add('hidden');
                URL.revokeObjectURL(objectUrl);
                modal.querySelectorAll('[data-image-upload-cancel]').forEach(function (el) {
                    el.removeEventListener('click', onCancel);
                });
                sendBtn.removeEventListener('click', onSend);
                captionInput.removeEventListener('keydown', onKeydown);
            };
            const onCancel = function () {
                if (closed) return;
                closed = true;
                cleanup();
                resolve(false);
            };
            const onSend = async function () {
                if (closed) return;
                closed = true;
                const caption = captionInput.value.trim();
                const replyToMessageId = getCurrentReplyId();
                cleanup();
                clearReplyState();
                try {
                    await startUploadFlow(file, conversationId, conversationType, messageKind, {
                        caption: caption,
                        replyToMessageId: replyToMessageId,
                    });
                    resolve(true);
                } catch (err) {
                    reject(err);
                }
            };
            const onKeydown = function (event) {
                if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                    event.preventDefault();
                    onSend();
                }
                if (event.key === 'Escape') {
                    event.preventDefault();
                    onCancel();
                }
            };

            title.textContent = '发送 1 张照片';
            preview.src = objectUrl;
            meta.textContent = file.name + (file.size ? ' - ' + formatBytes(file.size) : '');
            captionInput.value = '';
            modal.classList.remove('hidden');
            modal.querySelectorAll('[data-image-upload-cancel]').forEach(function (el) {
                el.addEventListener('click', onCancel);
            });
            sendBtn.addEventListener('click', onSend);
            captionInput.addEventListener('keydown', onKeydown);
            setTimeout(function () {
                captionInput.focus();
                if (window.lucide && window.lucide.createIcons) {
                    window.lucide.createIcons({ nodes: modal.querySelectorAll('[data-lucide]') });
                }
            }, 0);
        });
    }

    // ── File picker ─────────────────────────────────────────────────────

    async function sendSelectedFile(file, conversationId, conversationType, messageKind, options) {
        if (!conversationId) {
            window.showToast && window.showToast('Please select a conversation first.');
            return;
        }

        messageKind = messageKind || 'file';
        options = options || {};

        if (!file) return;

        const shouldPromoteImage = options.promoteImages !== false;
        const actualKind = shouldPromoteImage && file.type && file.type.indexOf('image/') === 0
            ? 'image'
            : messageKind;

        const limit = actualKind === 'image' ? 20 * 1024 * 1024
            : actualKind === 'sticker' ? 2 * 1024 * 1024
            : MAX_FILE_SIZE;
        if (file.size > limit) {
            window.showToast && window.showToast('File too large. Maximum size: ' + (limit / 1024 / 1024) + ' MB.');
            return;
        }

        try {
            if (actualKind === 'image' && options.showImageModal !== false) {
                await showImageUploadModal(file, conversationId, conversationType, actualKind);
            } else {
                const replyToMessageId = getCurrentReplyId();
                clearReplyState();
                await startUploadFlow(file, conversationId, conversationType, actualKind, {
                    replyToMessageId: replyToMessageId,
                });
            }
        } catch (err) {
            console.error('File upload failed:', err);
            window.showToast && window.showToast('Upload failed: ' + (err.message || 'Unknown error'));
        }
    }

    async function sendSelectedFiles(files, conversationId, conversationType, messageKind, options) {
        const list = Array.from(files || []).filter(Boolean);
        for (const file of list) {
            await sendSelectedFile(file, conversationId, conversationType, messageKind, options);
        }
    }

    /**
     * Show a file picker dialog and start the upload flow.
     *
     * @param {number} conversationId
     * @param {string} conversationType - 'single' | 'group'
     * @param {string} [messageKind] - 'image' | 'file' | 'sticker', defaults to 'file'
     */
    async function showFilePicker(conversationId, conversationType, messageKind) {
        if (!conversationId) {
            window.showToast && window.showToast('Please select a conversation first.');
            return;
        }

        messageKind = messageKind || 'file';

        // Create a temporary file input
        const input = document.createElement('input');
        input.type = 'file';

        if (messageKind === 'image') {
            input.accept = 'image/*';
        } else if (messageKind === 'sticker') {
            input.accept = 'image/png,image/webp';
        } else {
            input.accept = '*/*';
        }

        input.onchange = async function () {
            const file = input.files[0];
            await sendSelectedFile(file, conversationId, conversationType, messageKind);
        };

        input.click();
    }

    /**
     * Full upload → complete → send message flow.
     */
    async function startUploadFlow(file, conversationId, conversationType, messageKind, options) {
        const session = new UploadSession(file, conversationId, conversationType, messageKind, options);

        // Show progress in UI
        const progressEl = createUploadProgressElement(session.clientFileId, file.name);
        session.onProgress = function (pct) {
            updateUploadProgress(session.clientFileId, pct);
        };

        window.showToast && window.showToast('Uploading: ' + file.name + '...');

        await session.init();
        await session.uploadAll();
        await session.complete();

        window.showToast && window.showToast('Upload complete. Sending file message...');

        // Now send the file message
        await sendFileMessage(session, conversationType);

        // Remove progress element
        removeUploadProgress(session.clientFileId);

        window.showToast && window.showToast('File sent!');
    }

    /**
     * Send the file message via REST API after upload is complete.
     */
    async function sendFileMessage(session, conversationType) {
        const e2eeModule = conversationType === 'group'
            ? window.iChatGroupE2EE : window.iChatPrivateE2EE;
        const conv = window.conversationsById && window.conversationsById[session.conversationId];
        const captionText = session.caption || '';
        const cardPlaintext = captionText || ('[' + (session.messageKind || 'file') + ']');

        // Build file keys for recipients
        const fileKeys = [];

        // Wrap file key for the sender themself (multi-device)
        if (typeof e2eeModule.wrapFileKeyForSelf === 'function') {
            const senderWrapped = await e2eeModule.wrapFileKeyForSelf(
                session.fileKeyBytes, session.fileId, window.myUserId
            );
            if (senderWrapped) fileKeys.push(senderWrapped);
        }

        // Wrap file key for each recipient
        if (conversationType === 'single') {
            const conv = window.conversationsById && window.conversationsById[session.conversationId];
            const receiverId = conv ? conv.peer_id : null;
            if (receiverId && typeof e2eeModule.wrapFileKey === 'function') {
                const wrapped = await e2eeModule.wrapFileKey(
                    session.fileKeyBytes, session.fileId, receiverId
                );
                fileKeys.push(wrapped);
            }
        }
        let cardEncryption = null;
        if (conversationType === 'single') {
            const receiverId = conv ? conv.peer_id : null;
            if (!receiverId || typeof e2eeModule.encryptPrivateMessage !== 'function') {
                throw new Error('Private E2EE module or peer information is missing.');
            }
            cardEncryption = await e2eeModule.encryptPrivateMessage({
                plaintext: cardPlaintext,
                conversationId: session.conversationId,
                receiverId: receiverId,
            });
        } else {
            if (!conv) throw new Error('Group conversation information is missing.');
            if (typeof window.fetchGroupMemberIds !== 'function') {
                throw new Error('Group member loader is not available.');
            }
            if (typeof e2eeModule.encryptGroupMessage !== 'function') {
                throw new Error('Group E2EE module is not loaded.');
            }
            const memberIds = await window.fetchGroupMemberIds(session.conversationId);
            cardEncryption = window.encryptGroupMessageWithTrustRetry
                ? await window.encryptGroupMessageWithTrustRetry({
                    plaintext: cardPlaintext,
                    conv: conv,
                    groupId: session.conversationId,
                    membershipVersion: conv.membership_version || window.activeMembershipVersion || 1,
                    memberIds: memberIds,
                })
                : await e2eeModule.encryptGroupMessage({
                    plaintext: cardPlaintext,
                    groupId: session.conversationId,
                    membershipVersion: conv.membership_version || window.activeMembershipVersion || 1,
                    memberIds: memberIds,
                });

            if (typeof e2eeModule.wrapFileKey === 'function') {
                for (const holderId of memberIds) {
                    if (Number(holderId) === Number(window.myUserId)) continue;
                    const wrapped = await e2eeModule.wrapFileKey(
                        session.fileKeyBytes,
                        session.fileId,
                        holderId,
                        {
                            group_id: session.conversationId,
                            membership_version: cardEncryption.membership_version,
                            sender_id: window.myUserId,
                            receiver_id: holderId,
                            sender_key_version: cardEncryption.sender_key_version,
                            receiver_key_version: 0,
                        }
                    );
                    fileKeys.push(wrapped);
                }
            }
        }

        const clientMsgId = crypto.randomUUID();

        const body = {
            client_message_id: clientMsgId,
            conversation_id: session.conversationId,
            conversation_type: conversationType,
            message_type: session.messageKind,
            file_keys: fileKeys,
            // The ciphertext here is the "file card" — a minimal encrypted
            // description that the receiver decrypts with the session key.
            // For the initial Phase A, we send an empty card; the real
            // metadata is in the file's encrypted_metadata.
            ciphertext: cardEncryption.ciphertext || '',
            nonce: cardEncryption.nonce || '',
            auth_tag: cardEncryption.auth_tag || '',
            sender_ephemeral_public_key: cardEncryption.sender_ephemeral_public_key,
            sender_copy: cardEncryption.sender_copy,
            algorithm: cardEncryption.algorithm || 'AES-256-GCM',
            sender_key_version: cardEncryption.sender_key_version || 0,
            receiver_key_version: cardEncryption.receiver_key_version || 0,
            reply_to_message_id: session.replyToMessageId || undefined,
        };

        // For single chat, also need receiver_id
        if (conversationType === 'single') {
            body.receiver_id = conv ? conv.peer_id : 0;
        }

        // For group chat, add recipients with per-member ciphertext
        if (conversationType === 'group') {
            body.membership_version = cardEncryption.membership_version;
            body.recipients = cardEncryption.recipients || [];
        }

        return await window.apiFetch('/api/files/' + session.fileId + '/messages/', {
            method: 'POST',
            body: JSON.stringify(body),
        });
    }

    // ── Progress UI helpers ─────────────────────────────────────────────

    function createUploadProgressElement(clientFileId, fileName) {
        // Remove any existing
        removeUploadProgress(clientFileId);

        let layer = document.getElementById('file-upload-progress-layer');
        if (!layer) {
            layer = document.createElement('div');
            layer.id = 'file-upload-progress-layer';
            layer.className = 'file-upload-progress-layer';
            const history = document.getElementById('message-history-container');
            const host = history && history.parentNode ? history.parentNode : document.body;
            host.appendChild(layer);
        }

        const container = document.createElement('div');
        container.id = 'file-progress-' + clientFileId;
        container.className = 'file-upload-progress';
        container.innerHTML =
            '<div class="file-upload-progress-info">' +
                '<span class="file-upload-progress-name">' + escapeHtml(fileName) + '</span>' +
                '<span class="file-upload-progress-pct">0%</span>' +
            '</div>' +
            '<div class="file-upload-progress-track">' +
                '<div class="file-upload-progress-bar" style="width:0%"></div>' +
            '</div>';

        layer.appendChild(container);
        return container;
    }

    function updateUploadProgress(clientFileId, percent) {
        const el = document.getElementById('file-progress-' + clientFileId);
        if (!el) return;
        const bar = el.querySelector('.file-upload-progress-bar');
        const pct = el.querySelector('.file-upload-progress-pct');
        if (bar) bar.style.width = percent + '%';
        if (pct) pct.textContent = percent + '%';
    }

    function removeUploadProgress(clientFileId) {
        const el = document.getElementById('file-progress-' + clientFileId);
        if (el) {
            el.style.opacity = '0';
            el.style.transition = 'opacity 0.3s';
            setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 350);
        }
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // ── CSRF helper ─────────────────────────────────────────────────────

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // ── Native save / open (P4 T19, Electron only) ──────────────────────
    //
    // In the Chromium build a Blob reaches disk through an <a download>. Inside
    // the desktop shell that anchor is bound to the app origin, so the browser
    // download manager may place the file anywhere or drop it silently - the
    // user cannot tell whether the bytes landed. When the narrow native bridge
    // is present the decrypted bytes go through an OS save dialog instead.

    const NATIVE_WRITE_CHUNK_BYTES = 2 * 1024 * 1024; // mirrors desktop/file-io.js

    function nativeFileBridge() {
        const bridge = window.iChatDesktop;
        return bridge && bridge.files && typeof bridge.files.pickSaveTarget === 'function'
            ? bridge.files
            : null;
    }

    // Error text keyed by the codes desktop/file-io.js raises. "交付" wording
    // for the codes a user can actually act on; everything else falls back.
    const NATIVE_FILE_MESSAGES = {
        not_found: '上次的保存凭证已失效，请重新下载。',
        expired: '下载超时，请重新下载。',
        offset_mismatch: '写入位置不一致，已中止，请重新下载。',
        write_failed: '写入失败：磁盘已满或文件被占用。',
        blocked_type: '出于安全考虑，安装包和脚本不能直接打开，请在下载目录中查看。',
        open_failed: '打开失败：系统没有关联的应用。文件已保存，可在下载目录中打开。',
        incomplete: '文件没有接收完整，已中止，请重新下载。',
        too_many_grants: '同时进行的下载过多，请等待其他下载完成或稍后重试。',
        too_large: '文件超过允许的大小上限，无法保存。',
        invalid_target: '所选位置不可用，请换一个目录再试。',
        dialog_in_progress: '已有一个保存对话框打开，请先处理它。',
    };

    function describeNativeFileError(err) {
        const code = err && err.code;
        if (code && NATIVE_FILE_MESSAGES[code]) return NATIVE_FILE_MESSAGES[code];
        return '保存失败：' + ((err && (err.message || err.toString())) || '未知错误');
    }

    function rewrapNativeFileError(err) {
        const wrapped = new Error(describeNativeFileError(err));
        wrapped.cause = err;
        wrapped.nativeCode = err && err.code;
        return wrapped;
    }

    /**
     * Unwrap the `{ok, value|code}` envelope main.js resolves with.
     *
     * `ipcMain.handle` only carries `message` across the process boundary, so a
     * thrown error would lose its code and every message above would collapse
     * into "Error invoking remote method ...". Expected failures therefore come
     * back as data and are re-thrown here, inside the renderer, where the code
     * is still intact (review round 1, verified on Electron 39.8.10).
     */
    async function callNativeFileBridge(operation) {
        const envelope = await operation();
        if (envelope && envelope.ok === false) {
            const error = new Error(envelope.code);
            error.code = envelope.code;
            if (envelope.detail !== undefined) error.detail = envelope.detail;
            throw error;
        }
        if (!envelope || envelope.ok !== true) {
            throw new Error('保存通道返回了无法识别的响应。');
        }
        return envelope.value;
    }

    /**
     * Ask for a save target, then stream the blob through the native writer.
     *
     * @param {Blob} blob - decrypted bytes
     * @param {{original_name?: string, name?: string}} [metadata]
     * @returns {Promise<{status: 'canceled'|'saved', fileName?: string, token?: string, canceled?: boolean}>}
     */
    async function saveBlobToDisk(blob, metadata) {
        const bridge = nativeFileBridge();
        if (!bridge) return { status: 'canceled', canceled: true };
        // Review round 1 (MAJOR): a 0-byte file is legitimate (file-io.js seals
        // it), so it must take the native path too instead of being pushed back
        // into the invisible browser download.
        if (!blob || typeof blob.size !== 'number') {
            throw new Error('文件内容不可用，无法保存。');
        }
        const fileName = String((metadata && (metadata.original_name || metadata.name)) || '')
            .replace(/[\\/]+$/, '') || 'download';

        let target;
        try {
            target = await callNativeFileBridge(function () {
                return bridge.pickSaveTarget({ fileName: fileName, totalBytes: blob.size });
            });
        } catch (err) {
            throw rewrapNativeFileError(err);
        }
        if (!target || target.canceled) return { status: 'canceled', canceled: true };

        let offset = 0;
        try {
            while (offset < blob.size) {
                const slice = blob.slice(offset, Math.min(offset + NATIVE_WRITE_CHUNK_BYTES, blob.size));
                const chunk = new Uint8Array(await slice.arrayBuffer());
                // btoa() only accepts a binary string; 2 MiB keeps the argv-free
                // JSON payload and the base64 copy comfortably bounded.
                let binary = '';
                for (let i = 0; i < chunk.length; i += 0x8000) {
                    binary += String.fromCharCode.apply(null, chunk.subarray(i, i + 0x8000));
                }
                const result = await callNativeFileBridge(function () {
                    return bridge.writeChunk({
                        token: target.token,
                        offset: offset,
                        dataBase64: btoa(binary),
                    });
                });
                if (!result || result.written !== offset + chunk.length) {
                    const mismatch = new Error('offset_mismatch');
                    mismatch.code = 'offset_mismatch';
                    throw mismatch;
                }
                offset = result.written;
            }
            await callNativeFileBridge(function () {
                return bridge.seal({ token: target.token });
            });
        } catch (err) {
            throw rewrapNativeFileError(err);
        }
        return {
            status: 'saved',
            token: target.token,
            fileName: (target && target.fileName) || fileName,
        };
    }

    /**
     * Open a file this session saved. Resolves to a status instead of throwing
     * for the expected "the OS refused" cases so callers can show a hint.
     */
    async function openSavedFile(token) {
        const bridge = nativeFileBridge();
        if (!bridge) return { status: 'unsupported' };
        try {
            const result = await callNativeFileBridge(function () {
                return bridge.openDownloaded({ token: token });
            });
            return { status: 'opened', fileName: result && result.fileName };
        } catch (err) {
            return { status: 'failed', reason: describeNativeFileError(err), code: err && err.code };
        }
    }

    // ── Public API ──────────────────────────────────────────────────────

    window.iChatFileTransfer = {
        // Constants
        FILE_ALGORITHM: FILE_ALGORITHM,
        CHUNK_SIZE: CHUNK_SIZE,
        MAX_FILE_SIZE: MAX_FILE_SIZE,

        // Crypto primitives
        generateFileKey: generateFileKey,
        importFileKey: importFileKey,
        encryptFileChunk: encryptFileChunk,
        decryptFileChunk: decryptFileChunk,
        encryptFileMetadata: encryptFileMetadata,
        decryptFileMetadata: decryptFileMetadata,
        wrapFileKey: wrapFileKey,
        unwrapFileKey: unwrapFileKey,

        // Upload
        UploadSession: UploadSession,
        startUploadFlow: startUploadFlow,
        sendFileMessage: sendFileMessage,
        sendSelectedFile: sendSelectedFile,
        sendSelectedFiles: sendSelectedFiles,

        // Download
        downloadAndDecryptFile: downloadAndDecryptFile,
        fetchAndDecryptFile: fetchAndDecryptFile,
        fetchFileKeyBytes: fetchFileKeyBytes,

        // Native save / open (Electron only)
        nativeSaveAvailable: function () { return nativeFileBridge() !== null; },
        saveBlobToDisk: saveBlobToDisk,
        openSavedFile: openSavedFile,

        // UI
        showFilePicker: showFilePicker,
    };

    console.log('[file-transfer] Module loaded');
})();
