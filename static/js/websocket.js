/**
 * websocket.js - Django Channels WebSocket Client & E2EE Cryptography Templates
 * 
 * Includes:
 * 1. WebSocket connection management with exponential backoff auto-reconnection.
 * 2. Room / channel group routing helpers.
 * 3. iChatCryptor: Complete templates for Web Crypto API (ECDH, HKDF, AES-GCM)
 *    demonstrating client-side E2E encryption, decryption, and key derivation.
 */

(function () {
    // ==========================================
    // Part 1: iChatCryptor E2EE Boilerplate Templates
    // ==========================================
    const iChatCryptor = {
        /**
         * Helper: Convert Base64 string to ArrayBuffer
         */
        base64ToArrayBuffer(base64) {
            const binaryString = atob(base64);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            return bytes.buffer;
        },

        /**
         * Helper: Convert ArrayBuffer to Base64 string
         */
        arrayBufferToBase64(buffer) {
            let binary = '';
            const bytes = new Uint8Array(buffer);
            const len = bytes.byteLength;
            for (let i = 0; i < len; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return btoa(binary);
        },

        /**
         * Helper: Convert string to UTF-8 ArrayBuffer
         */
        stringToBuffer(str) {
            return new TextEncoder().encode(str);
        },

        /**
         * Helper: Convert ArrayBuffer to string (UTF-8)
         */
        bufferToString(buffer) {
            return new TextDecoder().decode(buffer);
        },

        /**
         * Template: Generate standard ECDH X25519 Key Pair
         * (Or P-256 depending on target browser support)
         */
        async generateKeyPair() {
            try {
                // Use ECDH over curve P-256 (or X25519 if supported in modern browsers)
                const keyPair = await window.crypto.subtle.generateKey(
                    {
                        name: 'ECDH',
                        namedCurve: 'P-256' // P-256, P-384, or P-521
                    },
                    true, // extractable
                    ['deriveKey', 'deriveBits']
                );
                
                // Export public key in SubjectPublicKeyInfo (spki) format to upload to server
                const rawPublicKey = await window.crypto.subtle.exportKey('spki', keyPair.publicKey);
                const publicKeyBase64 = this.arrayBufferToBase64(rawPublicKey);
                
                // Export private key in PKCS#8 format (keep securely locally, e.g. IndexedDB)
                const rawPrivateKey = await window.crypto.subtle.exportKey('pkcs8', keyPair.privateKey);
                const privateKeyBase64 = this.arrayBufferToBase64(rawPrivateKey);

                console.log('✓ E2EE Cryptor: Generated new ECDH P-256 Keypair');
                return {
                    publicKey: keyPair.publicKey,
                    privateKey: keyPair.privateKey,
                    publicKeyBase64,
                    privateKeyBase64
                };
            } catch (err) {
                console.error('✗ E2EE Cryptor: Key pair generation failed:', err);
                throw err;
            }
        },

        /**
         * Template: Derive Shared Secret and Session Key via ECDH + HKDF
         * @param {CryptoKey} localPrivateKey - Caller's private key object
         * @param {string} remotePublicKeyBase64 - Opponent's public key (imported from base64)
         * @param {string} conversationId - Current conversation identifier used as HKDF salt
         */
        async deriveSessionKey(localPrivateKey, remotePublicKey, conversationId) {
            try {
                let remoteCryptoKey;
                if (typeof remotePublicKey === 'string') {
                    // 1. Import remote public key from Base64 SPKI
                    const remotePublicKeyBuffer = this.base64ToArrayBuffer(remotePublicKey);
                    remoteCryptoKey = await window.crypto.subtle.importKey(
                        'spki',
                        remotePublicKeyBuffer,
                        {
                            name: 'ECDH',
                            namedCurve: 'P-256'
                        },
                        true,
                        []
                    );
                } else {
                    // 1. Import remote public key from JWK object
                    remoteCryptoKey = await window.crypto.subtle.importKey(
                        'jwk',
                        remotePublicKey,
                        {
                            name: 'ECDH',
                            namedCurve: 'P-256'
                        },
                        true,
                        []
                    );
                }

                // 2. Perform ECDH to compute sharing bits (Shared Secret)
                const sharedSecretBits = await window.crypto.subtle.deriveBits(
                    {
                        name: 'ECDH',
                        public: remoteCryptoKey
                    },
                    localPrivateKey,
                    256
                );

                // 3. Import shared secret bits as an HKDF master key
                const hkdfMasterKey = await window.crypto.subtle.importKey(
                    'raw',
                    sharedSecretBits,
                    { name: 'HKDF' },
                    false,
                    ['deriveKey']
                );

                const hkdfSalt = this.stringToBuffer(conversationId.toString());
                const hkdfInfo = this.stringToBuffer('chat-message-encryption-v1');

                // 4. Derive AES-GCM 256-bit session key using HKDF
                const sessionKey = await window.crypto.subtle.deriveKey(
                    {
                        name: 'HKDF',
                        hash: 'SHA-256',
                        salt: hkdfSalt,
                        info: hkdfInfo
                    },
                    hkdfMasterKey,
                    {
                        name: 'AES-GCM',
                        length: 256
                    },
                    true,
                    ['encrypt', 'decrypt']
                );

                console.log('✓ E2EE Cryptor: Session key successfully derived via ECDH + HKDF');
                return sessionKey;
            } catch (err) {
                console.error('✗ E2EE Cryptor: Session key derivation failed:', err);
                throw err;
            }
        },

        /**
         * Template: Encrypt plaintext using AES-GCM (256-bit key)
         * @param {string} plaintext - Original message text
         * @param {CryptoKey} sessionKey - Derived AES session key
         * @returns {object} { ciphertext, nonce, auth_tag } in Base64
         */
        async encryptMessage(plaintext, sessionKey) {
            try {
                // Generate a cryptographically strong 12-byte random initialization vector (IV/Nonce)
                const nonce = window.crypto.getRandomValues(new Uint8Array(12));
                const plaintextBuffer = this.stringToBuffer(plaintext);

                // AES-GCM encryption
                const encryptedBuffer = await window.crypto.subtle.encrypt(
                    {
                        name: 'AES-GCM',
                        iv: nonce,
                        tagLength: 128 // 128-bit authentication tag (16 bytes)
                    },
                    sessionKey,
                    plaintextBuffer
                );

                /*
                 * IMPORTANT SYSTEM INTEGRATION NOTICE:
                 * Web Crypto API appends the 16-byte authentication tag directly to the end
                 * of the encrypted ciphertext buffer. 
                 * Since our Django server expects "ciphertext" and "auth_tag" as discrete database columns,
                 * we separate them here before sending over WebSocket/HTTP.
                 */
                const fullEncryptedBytes = new Uint8Array(encryptedBuffer);
                const tagLengthBytes = 16;
                const ciphertextBytes = fullEncryptedBytes.slice(0, fullEncryptedBytes.length - tagLengthBytes);
                const authTagBytes = fullEncryptedBytes.slice(fullEncryptedBytes.length - tagLengthBytes);

                return {
                    ciphertext: this.arrayBufferToBase64(ciphertextBytes.buffer),
                    nonce: this.arrayBufferToBase64(nonce.buffer),
                    auth_tag: this.arrayBufferToBase64(authTagBytes.buffer),
                    algorithm: 'AES-GCM'
                };
            } catch (err) {
                console.error('✗ E2EE Cryptor: Encryption failed:', err);
                throw err;
            }
        },

        /**
         * Template: Decrypt payload using AES-GCM
         * @param {string} ciphertextBase64 - Base64 cipher text
         * @param {string} nonceBase64 - Base64 Initialization Vector
         * @param {string} authTagBase64 - Base64 GCM Auth Tag
         * @param {CryptoKey} sessionKey - Derived AES session key
         */
        async decryptMessage(ciphertextBase64, nonceBase64, authTagBase64, sessionKey) {
            try {
                const ciphertextBytes = new Uint8Array(this.base64ToArrayBuffer(ciphertextBase64));
                const nonceBytes = new Uint8Array(this.base64ToArrayBuffer(nonceBase64));
                const authTagBytes = new Uint8Array(this.base64ToArrayBuffer(authTagBase64));

                // Re-assemble the ciphertext and authentication tag back together for SubtleCrypto input
                const fullPayloadBytes = new Uint8Array(ciphertextBytes.length + authTagBytes.length);
                fullPayloadBytes.set(ciphertextBytes, 0);
                fullPayloadBytes.set(authTagBytes, ciphertextBytes.length);

                const decryptedBuffer = await window.crypto.subtle.decrypt(
                    {
                        name: 'AES-GCM',
                        iv: nonceBytes,
                        tagLength: 128
                    },
                    sessionKey,
                    fullPayloadBytes.buffer
                );

                return this.bufferToString(decryptedBuffer);
            } catch (err) {
                console.error('✗ E2EE Cryptor: Decryption failed (integrity check failure / wrong key):', err);
                throw new Error('该消息无法解密，可能由于密钥变更或消息损坏。');
            }
        }
    };

    // ==========================================
    // Part 2: WebSocket Connection Manager
    // ==========================================
    class iChatWebSocketClient {
        /**
         * Constructor
         * @param {string} path - WebSocket relative path, e.g. '/ws/chat/'
         * @param {object} options - Custom configuration (callbacks, reconnect options)
         */
        constructor(path, options = {}) {
            this.path = path;
            this.options = {
                autoReconnect: true,
                initialReconnectInterval: 1000,
                maxReconnectInterval: 30000,
                reconnectDecay: 1.5,
                maxReconnectAttempts: 10,
                onConnect: () => {},
                onDisconnect: () => {},
                onError: () => {},
                onMessageReceived: () => {},
                ...options
            };

            this.socket = null;
            this.reconnectAttempts = 0;
            this.forcedClose = false;
            this.reconnectTimeoutId = null;
            
            // Channel groups & subscription states
            this.activeRooms = new Set();
        }

        /**
         * Construct absolute WebSocket URL based on current window location
         */
        getAbsoluteUrl() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            // Clean path
            const cleanPath = this.path.startsWith('/') ? this.path : `/${this.path}`;
            return `${protocol}//${host}${cleanPath}`;
        }

        /**
         * Initiate WebSocket connection
         */
        connect() {
            if (this.socket && (this.socket.readyState === WebSocket.CONNECTING || this.socket.readyState === WebSocket.OPEN)) {
                return;
            }

            const url = this.getAbsoluteUrl();
            console.log(`[iChat WebSocket] Connecting to ${url}...`);
            this.forcedClose = false;

            try {
                this.socket = new WebSocket(url);
                this.setupEventHandlers();
            } catch (err) {
                this.options.onError(err);
                this.handleReconnect();
            }
        }

        /**
         * Set up standard event listeners
         */
        setupEventHandlers() {
            this.socket.onopen = (event) => {
                console.log('[iChat WebSocket] Connection established successfully.');
                this.reconnectAttempts = 0;
                
                // Re-subscribe to active room channels if reconnected
                this.activeRooms.forEach(room => {
                    this.subscribeToRoom(room);
                });

                this.options.onConnect(event);
            };

            this.socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('[iChat WebSocket] Message payload received:', data);
                    
                    // Route to specific internal message processing functions
                    this.processReceivedPayload(data);
                } catch (err) {
                    console.error('[iChat WebSocket] Failed to parse payload:', err);
                }
            };

            this.socket.onclose = (event) => {
                console.warn(`[iChat WebSocket] Connection closed. Code: ${event.code}, Reason: ${event.reason}`);
                this.options.onDisconnect(event);
                
                if (!this.forcedClose) {
                    this.handleReconnect();
                }
            };

            this.socket.onerror = (error) => {
                console.error('[iChat WebSocket] Connection error occurred:', error);
                this.options.onError(error);
            };
        }

        /**
         * Exponential backoff reconnection handler
         */
        handleReconnect() {
            if (!this.options.autoReconnect) return;
            if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
                console.error('[iChat WebSocket] Max reconnection attempts reached. Halting auto-reconnect.');
                return;
            }

            this.reconnectAttempts++;
            
            // Calculate delay: interval * (decay ^ attempt)
            const delay = Math.min(
                this.options.initialReconnectInterval * Math.pow(this.options.reconnectDecay, this.reconnectAttempts),
                this.options.maxReconnectInterval
            );

            console.log(`[iChat WebSocket] Attempting reconnect ${this.reconnectAttempts}/${this.options.maxReconnectAttempts} in ${(delay / 1000).toFixed(2)}s...`);
            
            clearTimeout(this.reconnectTimeoutId);
            this.reconnectTimeoutId = setTimeout(() => {
                this.connect();
            }, delay);
        }

        /**
         * Close WebSocket connection intentionally
         */
        disconnect() {
            this.forcedClose = true;
            clearTimeout(this.reconnectTimeoutId);
            if (this.socket) {
                this.socket.close();
            }
            console.log('[iChat WebSocket] Disconnected intentionally by client.');
        }

        /**
         * Send JSON payload over socket
         */
        sendPayload(payload) {
            if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
                console.error('[iChat WebSocket] Cannot send payload, socket is not connected.');
                return false;
            }

            try {
                this.socket.send(JSON.stringify(payload));
                return true;
            } catch (err) {
                console.error('[iChat WebSocket] Error sending payload:', err);
                return false;
            }
        }

        // ==========================================
        // Part 3: Channel Group Routing & Handlers
        // ==========================================

        /**
         * Subscribe to a conversation or group room.
         * Deprecated: v1.0 protocol uses single /ws/chat/ connection with user_{id} groups.
         * No explicit subscribe messages needed — the server handles routing automatically.
         */
        subscribeToRoom(roomId) {
            this.activeRooms.add(roomId);
            console.log(`[iChat WebSocket] Tracking room: ${roomId} (server handles routing via user group)`);
        }

        /**
         * Unsubscribe from a conversation or group room.
         * Deprecated: v1.0 protocol uses single /ws/chat/ connection with user_{id} groups.
         */
        unsubscribeFromRoom(roomId) {
            this.activeRooms.delete(roomId);
            console.log(`[iChat WebSocket] Untracking room: ${roomId}`);
        }

        /**
         * Process and route incoming messages based on protocol event type.
         * Aligned with iChat Pro v1.0 WebSocket protocol.
         */
        processReceivedPayload(data) {
            const eventType = data.event || data.type || '';

            switch (eventType) {
                // Connection lifecycle
                case 'connection.ready':
                    console.log(`[iChat WebSocket] Ready. User ID: ${data.data.user_id}`);
                    this.options.onConnect(data);
                    break;
                case 'connection.pong':
                    break; // heartbeat acknowledgment, no action needed

                // Private chat (single)
                case 'message.single.new':
                    this.handleSingleChatMessage(data.data);
                    break;
                case 'message.single.accepted':
                    this.handleSingleChatAccepted(data.data);
                    break;

                // Group chat
                case 'message.group.new':
                    this.handleGroupChatMessage(data.data);
                    break;
                case 'message.group.accepted':
                    this.handleGroupChatAccepted(data.data);
                    break;

                // Receipts
                case 'message.receipt.updated':
                    this.handleReceiptUpdated(data.data);
                    break;

                // Group membership changes
                case 'group.members.changed':
                    this.handleGroupMembersChanged(data.data);
                    break;

                // Errors
                case 'error':
                    console.error(`[iChat WebSocket] Error: ${data.data.code} - ${data.data.message}`);
                    this.handleError(data.data);
                    break;

                // Legacy compatibility for older-style payloads
                case 'chat_message':
                case 'single_chat':
                case 'group_message':
                case 'group_chat':
                case 'system_message':
                case 'key_exchange':
                    this.handleLegacyPayload(eventType, data);
                    break;

                default:
                    console.warn(`[iChat WebSocket] Unknown event: '${eventType}'`);
                    this.options.onMessageReceived(data);
            }
        }

        /**
         * Handle incoming private chat encrypted message (message.single.new).
         * The server already pushed only the current user's ciphertext.
         */
        handleSingleChatMessage(data) {
            console.log('--- Processing Private Chat Message (message.single.new) ---');
            console.log(`Message ID: ${data.message_id}`);
            console.log(`Sender ID: ${data.sender_id}`);
            console.log(`Conversation ID: ${data.conversation_id}`);
            console.log(`Ciphertext: ${data.ciphertext}`);
            console.log(`Algorithm: ${data.algorithm}`);
            console.log(`Sender Key Version: ${data.sender_key_version}`);
            console.log(`Receiver Key Version: ${data.receiver_key_version}`);

            /*
             * IMPLEMENTATION NOTE (T16 integration):
             * 1. Retrieve local private key from IndexedDB.
             * 2. Fetch sender's public key via HTTP API (key version = data.sender_key_version).
             * 3. Derive session key: iChatCryptor.deriveSessionKey(myPrivateKey, senderPublicKey, hkdfContext).
             * 4. Decrypt: iChatCryptor.decryptMessage(data.ciphertext, data.nonce, data.auth_tag, sessionKey).
             * 5. Append decrypted text to the UI.
             * 6. Send receipt: message.receipt.update { conversation_type: 'single', message_id, status: 'delivered' }.
             */

            this.options.onMessageReceived({
                category: 'single',
                message_id: data.message_id,
                conversation_id: data.conversation_id,
                sender_id: data.sender_id,
                ciphertext: data.ciphertext,
                nonce: data.nonce,
                auth_tag: data.auth_tag,
                algorithm: data.algorithm,
                sender_key_version: data.sender_key_version,
                receiver_key_version: data.receiver_key_version,
                status: data.status,
                created_at: data.created_at,
                is_encrypted: true,
                raw_payload: data,
            });
        }

        /**
         * Handle private message accepted acknowledgment (message.single.accepted).
         */
        handleSingleChatAccepted(data) {
            console.log(`[iChat] Private message accepted: client=${data.client_message_id} server=${data.message_id}`);
            this.options.onMessageReceived({
                category: 'single_accepted',
                client_message_id: data.client_message_id,
                message_id: data.message_id,
                conversation_id: data.conversation_id,
                status: data.status,
                created_at: data.created_at,
                raw_payload: data,
            });
        }

        /**
         * Handle incoming group chat per-recipient encrypted message (message.group.new).
         * The server already pushed only the current user's ciphertext.
         */
        handleGroupChatMessage(data) {
            console.log('--- Processing Group Chat Message (message.group.new) ---');
            console.log(`Message ID: ${data.message_id}`);
            console.log(`Group ID: ${data.group_id}`);
            console.log(`Sender ID: ${data.sender_id}`);
            console.log(`Membership Version: ${data.membership_version}`);
            console.log(`Ciphertext: ${data.ciphertext}`);
            console.log(`Algorithm: ${data.algorithm}`);

            /*
             * IMPLEMENTATION NOTE (T16 integration):
             * The server pushes one ciphertext per user via user_{user_id}.
             * Client uses HKDF context: group:{group_id}:{membership_version}:{sender_id}:{receiver_id}:{sender_key_version}:{receiver_key_version}
             * to derive the session key, then decrypts.
             */

            this.options.onMessageReceived({
                category: 'group',
                message_id: data.message_id,
                group_id: data.group_id,
                membership_version: data.membership_version,
                sender_id: data.sender_id,
                receiver_id: data.receiver_id,
                ciphertext: data.ciphertext,
                nonce: data.nonce,
                auth_tag: data.auth_tag,
                algorithm: data.algorithm,
                sender_key_version: data.sender_key_version,
                receiver_key_version: data.receiver_key_version,
                status: data.status,
                created_at: data.created_at,
                is_encrypted: true,
                raw_payload: data,
            });
        }

        /**
         * Handle group message accepted acknowledgment (message.group.accepted).
         */
        handleGroupChatAccepted(data) {
            console.log(`[iChat] Group message accepted: client=${data.client_message_id} server=${data.message_id}`);
            this.options.onMessageReceived({
                category: 'group_accepted',
                client_message_id: data.client_message_id,
                message_id: data.message_id,
                group_id: data.group_id,
                membership_version: data.membership_version,
                status: data.status,
                created_at: data.created_at,
                raw_payload: data,
            });
        }

        /**
         * Handle message receipt update (message.receipt.updated).
         */
        handleReceiptUpdated(data) {
            console.log(`[iChat] Receipt updated: msg=${data.message_id} user=${data.user_id} status=${data.status}`);
            this.options.onMessageReceived({
                category: 'receipt',
                conversation_type: data.conversation_type,
                message_id: data.message_id,
                user_id: data.user_id,
                status: data.status,
                raw_payload: data,
            });
        }

        /**
         * Handle group membership change notification (group.members.changed).
         */
        handleGroupMembersChanged(data) {
            console.log(`[iChat] Group members changed: group=${data.group_id} change=${data.change}`);
            this.options.onMessageReceived({
                category: 'group_members_changed',
                group_id: data.group_id,
                change: data.change,
                actor_id: data.actor_id,
                affected_user_id: data.affected_user_id,
                membership_version: data.membership_version,
                raw_payload: data,
            });
        }

        /**
         * Handle server error event.
         */
        handleError(data) {
            console.error(`[iChat WebSocket] Server error [${data.code}]: ${data.message}`);
            this.options.onMessageReceived({
                category: 'error',
                code: data.code,
                message: data.message,
                retryable: data.retryable,
                raw_payload: data,
            });
        }

        /**
         * Legacy handler fallback for older template event types.
         */
        handleLegacyPayload(eventType, data) {
            console.warn(`[iChat WebSocket] Legacy event: '${eventType}'`);
            this.options.onMessageReceived({
                category: eventType,
                raw_payload: data,
            });
        }
    }

    // Expose classes globally
    window.iChatWebSocketClient = iChatWebSocketClient;
    window.iChatCryptor = iChatCryptor;
})();
