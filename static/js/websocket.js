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
         * Subscribe to a conversation or group room
         */
        subscribeToRoom(roomId) {
            this.activeRooms.add(roomId);
            
            // Routing event to join group/channel in Django consumer
            this.sendPayload({
                action: 'join',
                room_id: roomId,
                timestamp: new Date().toISOString()
            });
            console.log(`[iChat WebSocket] Requested join channel room: ${roomId}`);
        }

        /**
         * Unsubscribe from a conversation or group room
         */
        unsubscribeFromRoom(roomId) {
            this.activeRooms.delete(roomId);
            
            this.sendPayload({
                action: 'leave',
                room_id: roomId,
                timestamp: new Date().toISOString()
            });
            console.log(`[iChat WebSocket] Requested leave channel room: ${roomId}`);
        }

        /**
         * Process and route incoming messages based on their structural event type
         */
        processReceivedPayload(data) {
            const eventType = data.type || data.action || 'message';

            switch (eventType) {
                case 'chat_message':
                case 'single_chat':
                    this.handleSingleChatMessageTemplate(data);
                    break;
                case 'group_message':
                case 'group_chat':
                    this.handleGroupChatMessageTemplate(data);
                    break;
                case 'system_message':
                    this.handleSystemMessageTemplate(data);
                    break;
                case 'key_exchange':
                    this.handleKeyExchangeTemplate(data);
                    break;
                default:
                    console.warn(`[iChat WebSocket] Unknown event type: '${eventType}'`);
                    this.options.onMessageReceived(data);
            }
        }

        /**
         * Template Handler: Received Single Chat Encrypted Payload
         */
        handleSingleChatMessageTemplate(data) {
            console.log('--- Processing Single Chat Message ---');
            console.log(`Sender ID: ${data.sender_id}`);
            console.log(`Receiver ID: ${data.receiver_id}`);
            console.log(`Ciphertext: ${data.ciphertext}`);
            console.log(`Nonce: ${data.nonce}`);
            console.log(`Auth Tag: ${data.auth_tag}`);
            console.log(`Encryption Version: ${data.receiver_public_key_version}`);

            /*
             * IMPLEMENTATION NOTE (Phase 2 Integration):
             * Here you would retrieve the local User private key and the Sender's public key,
             * derive the session key, and call:
             * 
             * const sessionKey = await iChatCryptor.deriveSessionKey(myPrivateKey, data.sender_public_key, conversationId);
             * const plaintext = await iChatCryptor.decryptMessage(data.ciphertext, data.nonce, data.auth_tag, sessionKey);
             * 
             * Then append the decrypted string to the UI.
             */

            // Trigger fallback hook to notify UI layer
            this.options.onMessageReceived({
                category: 'single',
                sender_id: data.sender_id,
                ciphertext: data.ciphertext,
                is_encrypted: true,
                raw_payload: data
            });
        }

        /**
         * Template Handler: Received Group Chat Encrypted Payload
         */
        handleGroupChatMessageTemplate(data) {
            console.log('--- Processing Group Chat Message ---');
            console.log(`Group ID: ${data.group_id}`);
            console.log(`Sender ID: ${data.sender_id}`);
            
            /*
             * Under the group E2EE design:
             * The group payload includes a "recipients" array.
             * The client filters the array for their own receiver_id, extracts the custom 
             * ciphertext/nonce/auth_tag mapped specifically to them, and decrypts it.
             */
            const myUserId = window.currentUserId || 1; // Example fallback
            const myEncryptedPackage = data.recipients.find(r => r.receiver_id === myUserId);

            if (myEncryptedPackage) {
                console.log(`Matched encrypted block for Current User (${myUserId}):`);
                console.log(`- Ciphertext: ${myEncryptedPackage.ciphertext}`);
                console.log(`- Nonce: ${myEncryptedPackage.nonce}`);
                console.log(`- Auth Tag: ${myEncryptedPackage.auth_tag}`);

                // Decrypt using recipient sessionKey...
            } else {
                console.error('[iChat WebSocket] Critical: Current user was omitted from group recipient payload.');
            }

            this.options.onMessageReceived({
                category: 'group',
                group_id: data.group_id,
                sender_id: data.sender_id,
                is_encrypted: true,
                raw_payload: data
            });
        }

        /**
         * Template Handler: System notice (e.g. member joins, encryption toggled)
         */
        handleSystemMessageTemplate(data) {
            console.log(`[iChat System Notice] ${data.content || data.text}`);
            this.options.onMessageReceived({
                category: 'system',
                text: data.content || data.text,
                raw_payload: data
            });
        }

        /**
         * Template Handler: Key Exchange (ECDH pre-key setups / identity verification)
         */
        handleKeyExchangeTemplate(data) {
            console.log('--- E2EE Public Key Exchange Event ---');
            console.log(`Key Exchange Initiator: ${data.sender_id}`);
            console.log(`Public Key Received: ${data.public_key}`);
            
            // Save key to local registry or localStorage
            if (window.iChatApp && window.iChatApp.showToast) {
                window.iChatApp.showToast(`Updated E2EE security verification for User #${data.sender_id}`, 'info');
            }
        }
    }

    // Expose classes globally
    window.iChatWebSocketClient = iChatWebSocketClient;
    window.iChatCryptor = iChatCryptor;
})();
