// iChat Pro - Client-side Encrypted Chat Engine
// Vanilla JavaScript utilizing Web Crypto API for ECDH + HKDF + AES-GCM
// Connects to real backend API, WebSocket, and E2EE modules

// Global State — populated from backend APIs
let conversations = [];          // Array of conversation objects from GET /api/conversations/
let conversationsById = {};      // ID → conversation lookup map
let activeChatId = null;
let currentLanguage = localStorage.getItem('ichat_lang') || 'en';
let isSelectingMessages = false;
let selectedMessageIds = [];
let messages = [];               // Decrypted messages for the currently active conversation
let messagePage = 1;
let hasMoreMessages = false;
let isLoadingMessages = false;
let sessionKeys = {};            // Cache: conversationId → derived CryptoKey
let myUserId = null;             // Current authenticated user PK
let wsClient = null;             // v1 /ws/chat/ client
let e2eeKeyReady = true;
let e2eeKeyError = null;
let groupMembersByConversation = {};
let fingerprintCacheByUserId = {};
let detailsPanelRequestId = 0;

function formatClockTime(date = new Date()) {
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

function normalizeTimeLabel(label) {
  if (!label || typeof label !== "string") return label || "";
  const trimmed = label.trim();
  const match = trimmed.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
  if (!match) return trimmed;

  let hour = parseInt(match[1], 10);
  const minute = match[2];
  const period = match[3].toUpperCase();
  if (period === "PM" && hour !== 12) hour += 12;
  if (period === "AM" && hour === 12) hour = 0;

  return `${String(hour).padStart(2, "0")}:${minute}`;
}

function normalizeChatData(chat) {
  if (!chat) return chat;
  chat.unread = Number.isFinite(Number(chat.unread)) ? Number(chat.unread) : 0;
  return chat;
}

// Helper: Convert ArrayBuffer to Hex String
function arrayBufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
}

// Helper: Print messages to the DOM and Developer Console
function logToCryptoConsole(message) {
  console.log(message);
  const consoleLogEl = document.getElementById("crypto-console-log");
  if (consoleLogEl) {
    const time = formatClockTime();
    consoleLogEl.textContent += `\n[${time}] ${message}`;
    consoleLogEl.scrollTop = consoleLogEl.scrollHeight;
  }
}

function setE2EEKeyError(message) {
  e2eeKeyReady = false;
  e2eeKeyError = message || 'Local encryption key is unavailable.';

  const textarea = document.getElementById("chat-input-textarea");
  if (textarea) {
    textarea.disabled = true;
    textarea.value = "";
    textarea.placeholder = currentLanguage === 'zh'
      ? "请先导入此账号的密钥备份..."
      : "Import this account's key backup first...";
  }

  const banner = document.getElementById("chat-input-security-banner");
  if (banner) {
    const span = banner.querySelector("span");
    if (span) {
      span.textContent = currentLanguage === 'zh'
        ? "本机缺少匹配的端到端加密私钥，请导入密钥备份。"
        : "Matching local E2EE private key is missing. Import your key backup.";
    }
  }

  logToCryptoConsole(`[E2EE Key Error] ${e2eeKeyError}`);
}

function clearE2EEKeyError() {
  e2eeKeyReady = true;
  e2eeKeyError = null;

  const textarea = document.getElementById("chat-input-textarea");
  if (textarea) {
    textarea.disabled = false;
    textarea.placeholder = currentLanguage === 'zh'
      ? "编写加密消息..."
      : "Write an encrypted message...";
  }

  const banner = document.getElementById("chat-input-security-banner");
  if (banner) {
    const span = banner.querySelector("span");
    if (span) {
      span.textContent = currentLanguage === 'zh'
        ? "🔒 消息已通过端到端加密保护。"
        : "🔒 Messages are secured with end-to-end encryption.";
    }
  }
}

async function recoverE2EEKeyForSending() {
  if (e2eeKeyReady) return true;
  if (!window.iChatKeyManager) return false;

  const confirmed = window.confirm(
    currentLanguage === 'zh'
      ? "本机没有可用的加密私钥。可以创建新的身份密钥继续发送新消息，但旧消息仍需要原密钥备份才能解密。是否继续？"
      : "No usable local encryption key is available. Create a new identity key so you can send new messages? Older messages still require the original key backup."
  );
  if (!confirmed) return false;

  try {
    await window.iChatKeyManager.resetIdentityKey();
    clearE2EEKeyError();
    window.showToast(currentLanguage === 'zh'
      ? "已创建新的加密身份，可继续发送新消息。"
      : "New encryption identity created. You can send new messages now.");
    await fetchConversations();
    return true;
  } catch (err) {
    setE2EEKeyError(err.message);
    window.showToast(err.message);
    return false;
  }
}

async function resetIdentityKeyFromPanel() {
  if (!window.iChatKeyManager || !window.iChatKeyManager.resetIdentityKey) {
    window.showToast(currentLanguage === 'zh'
      ? '密钥管理模块不可用'
      : 'Key manager is not available.');
    return;
  }

  const confirmed = window.confirm(
    currentLanguage === 'zh'
      ? '重置密钥会创建新的端到端加密身份。之后可以继续发送新消息，但旧消息仍需要原密钥备份才能解密。是否继续？'
      : 'Resetting creates a new E2EE identity. You can send new messages afterward, but older messages still require the original key backup. Continue?'
  );
  if (!confirmed) return;

  const btn = document.getElementById('right-panel-reset-key-btn');
  if (btn) btn.disabled = true;

  try {
    await window.iChatKeyManager.resetIdentityKey();
    clearE2EEKeyError();
    await fetchConversations();
    const conv = conversationsById[activeChatId];
    if (conv) updateDetailsPanel(conv);
    window.showToast(currentLanguage === 'zh'
      ? '已重置密钥，可继续发送新消息。'
      : 'Key reset. You can send new messages now.');
  } catch (err) {
    setE2EEKeyError(err.message);
    window.showToast(err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function decryptFailureLabel(error) {
  const code = error && error.code;
  const labels = {
    local_key_missing: {
      zh: '[无法解密：本机缺少私钥，请导入此账号的密钥备份]',
      en: '[Cannot decrypt: local private key is missing. Import this account key backup]'
    },
    local_key_changed: {
      zh: '[无法解密：这条消息使用旧密钥，请导入对应密钥备份]',
      en: '[Cannot decrypt: this message uses an older key. Import the matching key backup]'
    },
    peer_key_changed: {
      zh: '[无法解密：联系人已更换密钥，请重新验证指纹]',
      en: '[Cannot decrypt: contact key changed. Verify the new fingerprint]'
    },
    wrong_receiver: {
      zh: '[无法解密：这条密文不属于当前账号]',
      en: '[Cannot decrypt: this message belongs to another account]'
    },
    damaged_ciphertext: {
      zh: '[无法解密：密文或认证标签已损坏]',
      en: '[Cannot decrypt: ciphertext or authentication tag is damaged]'
    },
    invalid_ciphertext: {
      zh: '[无法解密：密文格式无效]',
      en: '[Cannot decrypt: encrypted payload is malformed]'
    },
    unsupported_algorithm: {
      zh: '[无法解密：不支持的加密算法]',
      en: '[Cannot decrypt: unsupported encryption algorithm]'
    },
    peer_key_missing: {
      zh: '[无法解密：联系人缺少公开密钥]',
      en: '[Cannot decrypt: contact public key is missing]'
    },
    peer_key_unavailable: {
      zh: '[无法解密：暂时无法加载联系人密钥]',
      en: '[Cannot decrypt: contact key is currently unavailable]'
    },
    invalid_peer_key: {
      zh: '[无法解密：联系人密钥记录无效]',
      en: '[Cannot decrypt: contact key record is invalid]'
    },
    peer_trust_invalid: {
      zh: '[无法解密：本地联系人信任记录损坏]',
      en: '[Cannot decrypt: saved contact trust record is damaged]'
    },
    invalid_metadata: {
      zh: '[无法解密：消息加密元数据无效]',
      en: '[Cannot decrypt: message encryption metadata is invalid]'
    }
  };
  if (labels[code]) {
    return currentLanguage === 'zh' ? labels[code].zh : labels[code].en;
  }
  return currentLanguage === 'zh'
    ? '[无法解密：未知错误，请检查密钥状态]'
    : '[Cannot decrypt: unknown error. Check key status]';
}

async function encryptPrivateMessageWithTrustRetry({ text, conv }) {
  try {
    return await window.iChatPrivateE2EE.encryptPrivateMessage({
      plaintext: text,
      conversationId: conv.id,
      receiverId: conv.peer_id
    });
  } catch (err) {
    if (err && err.code === 'peer_key_changed' && window.iChatPrivateE2EE.forgetPeerKey) {
      const confirmed = window.confirm(
        currentLanguage === 'zh'
          ? '对方的加密密钥已重置。是否信任新的密钥并重新发送？'
          : 'This contact reset their encryption key. Trust the new key and retry sending?'
      );
      if (confirmed) {
        window.iChatPrivateE2EE.forgetPeerKey(conv.peer_id);
        return window.iChatPrivateE2EE.encryptPrivateMessage({
          plaintext: text,
          conversationId: conv.id,
          receiverId: conv.peer_id
        });
      }
    }
    throw err;
  }
}

// ============================================================================
// 1. API Helpers
// ============================================================================

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

async function apiFetch(url, options = {}) {
  const csrf = getCookie('csrftoken');
  const headers = {
    'Content-Type': 'application/json',
    'X-CSRFToken': csrf,
    ...(options.headers || {}),
  };
  const resp = await fetch(url, { ...options, headers });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { const body = await resp.json(); detail = body.error || body.detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

// ============================================================================
// 2. Render Sidebar Chat List
// ============================================================================

function renderChatList() {
  const chatListContainer = document.getElementById("sidebar-chat-list");
  if (!chatListContainer) return;
  chatListContainer.innerHTML = "";
  conversations.forEach(conv => {
    appendChatItemToSidebar(conv);
  });
}

function appendChatItemToSidebar(conv) {
  const chatListContainer = document.getElementById("sidebar-chat-list");
  if (!chatListContainer) return;

  const wrapper = document.createElement("div");
  wrapper.id = `chat-item-wrapper-${conv.id}`;
  wrapper.className = "w-full";

  const lastMsgText = conv.last_message_preview || '';
  const lastMsgTime = conv.last_message_at ? formatClockTime(new Date(conv.last_message_at)) : '';
  const unreadCount = Number(conv.unread || 0);
  const safeId = encodeURIComponent(String(conv.id));
  const safeName = escapeHtml(conv.name || 'Unknown');
  const safeInitials = escapeHtml(conv.initials || '??');
  const safeLastMsg = escapeHtml(lastMsgText);
  const safeLastTime = escapeHtml(lastMsgTime);
  const safeUnread = escapeHtml(unreadCount);
  const safeAvatarColor = /^#[0-9a-fA-F]{6}$/.test(conv.avatar_color || '') ? conv.avatar_color : '#5c6bc0';

  wrapper.innerHTML = `
    <button id="chat-item-${safeId}" onclick="selectChat('${safeId}')"
      class="chat-item-btn w-full flex items-center px-4 py-3 border-b border-borderColor hover:bg-bgSearch transition-all text-left focus:outline-none relative group select-none">

      <div class="relative flex-shrink-0">
        <div class="w-12 h-12 rounded-full text-white flex items-center justify-center font-bold text-base shadow-sm" style="background-color: ${safeAvatarColor}">
          ${safeInitials}
        </div>
      </div>

      <div class="ml-3.5 flex-1 min-w-0">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-textMain truncate flex items-center space-x-1">
            <span>${safeName}</span>
            ${conv.is_secure ? '<i data-lucide="lock" class="w-3.5 h-3.5 text-brand-light dark:text-brand-dark inline-block flex-shrink-0" title="End-to-End Encrypted" data-i18n-title="e2ee_badge"></i>' : ''}
          </h3>
          <span id="chat-time-${safeId}" class="chat-item-time flex-shrink-0">${safeLastTime}</span>
        </div>

        <div class="flex items-center justify-between mt-1">
          <p id="last-msg-${safeId}" class="text-xs text-textSecondary truncate pr-4 leading-tight">
            ${safeLastMsg}
          </p>

          <span id="unread-badge-${safeId}" class="${unreadCount > 0 ? "" : "hidden"} unread-badge flex-shrink-0">
            ${safeUnread}
          </span>
        </div>
      </div>
    </button>
  `;

  chatListContainer.appendChild(wrapper);
  lucide.createIcons();
}

function updateSidebarPreview(conv, text, time) {
  if (!conv) return;
  const lastMsgEl = document.getElementById(`last-msg-${conv.id}`);
  const timeEl = document.getElementById(`chat-time-${conv.id}`);
  if (lastMsgEl) lastMsgEl.textContent = text;
  if (timeEl) timeEl.textContent = time;
}

// ============================================================================
// 3. API Data Loading
// ============================================================================

async function fetchConversations() {
  try {
    const data = await apiFetch('/api/conversations/');
    conversations = data.conversations || [];
    conversationsById = {};
    conversations.forEach(c => { conversationsById[c.id] = c; });
    renderChatList();
    // Auto-select first conversation if none active
    if (!activeChatId && conversations.length > 0) {
      const requestedConversation = new URLSearchParams(window.location.search).get("conversation");
      if (requestedConversation && conversationsById[parseInt(requestedConversation)]) {
        selectChat(requestedConversation);
      } else {
        selectChat(conversations[0].id.toString());
      }
    }
  } catch (err) {
    console.error('Failed to fetch conversations:', err);
    logToCryptoConsole(`[API] Failed to load conversations: ${err.message}`);
  }
}

async function fetchMessages(conversationId, page = 1) {
  const conv = conversationsById[parseInt(conversationId)];
  if (!conv) return;

  let url;
  if (conv.type === 'group') {
    url = `/api/groups/${conversationId}/messages/?page=${page}&per_page=30`;
  } else {
    url = `/api/conversations/${conversationId}/messages/?page=${page}&per_page=30`;
  }

  try {
    const data = await apiFetch(url);
    hasMoreMessages = data.has_next;
    messagePage = data.page;

    // Decrypt each message client-side
    const decrypted = [];
    for (const msg of data.messages) {
      try {
        let plaintext;
        if (conv.type === 'group') {
          plaintext = await window.iChatGroupE2EE.decryptGroupMessage({
            algorithm: msg.algorithm,
            ciphertext: msg.ciphertext,
            nonce: msg.nonce,
            auth_tag: msg.auth_tag,
            group_id: conv.id,
            membership_version: msg.membership_version,
            sender_id: msg.sender_id,
            receiver_id: msg.receiver_id,
            sender_key_version: msg.sender_key_version,
            receiver_key_version: msg.receiver_key_version,
          });
        } else {
          plaintext = await window.iChatPrivateE2EE.decryptPrivateMessage({
            algorithm: msg.algorithm,
            ciphertext: msg.ciphertext,
            nonce: msg.nonce,
            auth_tag: msg.auth_tag,
            conversation_id: data.conversation_id || conv.id,
            sender_id: msg.sender_id,
            receiver_id: msg.receiver_id,
            sender_key_version: msg.sender_key_version,
            receiver_key_version: msg.receiver_key_version,
          });
        }
        decrypted.push({
          id: msg.id,
          text: plaintext,
          time: formatClockTime(new Date(msg.created_at)),
          isSelf: msg.sender_id === myUserId,
          sender: msg.sender_id,
          sender_name: conv.type === 'group' ? msg.sender_name : conv.name,
          sender_initials: msg.sender_initials,
          sender_avatar_color: msg.sender_avatar_color,
          status: msg.status,
          isSystem: msg.message_type === 'system',
        });
      } catch (decryptErr) {
        console.warn(`Failed to decrypt message ${msg.id}:`, decryptErr);
        decrypted.push({
          id: msg.id,
          text: decryptFailureLabel(decryptErr),
          time: formatClockTime(new Date(msg.created_at)),
          isSelf: msg.sender_id === myUserId,
          sender: msg.sender_id,
          sender_name: conv.type === 'group' ? msg.sender_name : conv.name,
          sender_initials: msg.sender_initials,
          sender_avatar_color: msg.sender_avatar_color,
          status: msg.status,
          decryptError: true,
        });
      }
    }

    // For page 1, replace; for higher pages, prepend (older messages)
    if (page === 1) {
      messages = decrypted.reverse(); // API returns newest-first
    } else {
      // Prepend older messages
      messages = [...decrypted.reverse(), ...messages];
    }
  } catch (err) {
    console.error('Failed to fetch messages:', err);
    logToCryptoConsole(`[API] Failed to load messages: ${err.message}`);
  }
}

async function fetchGroupMemberIds(conversationId) {
  const data = await apiFetch(`/api/groups/${conversationId}/members/`);
  groupMembersByConversation[conversationId] = {};
  (data.members || []).forEach(member => {
    groupMembersByConversation[conversationId][member.user_id] = member;
  });
  return (data.members || []).map(member => member.user_id);
}

async function fetchPeerFingerprint(userId) {
  if (!userId) return null;
  if (fingerprintCacheByUserId[userId] !== undefined) {
    return fingerprintCacheByUserId[userId];
  }
  try {
    const data = await apiFetch(`/api/keys/fingerprint/${userId}/`);
    fingerprintCacheByUserId[userId] = data;
    return data;
  } catch (err) {
    fingerprintCacheByUserId[userId] = null;
    return null;
  }
}

function formatFingerprint(value) {
  if (!value) {
    return currentLanguage === 'zh' ? '联系人尚未上传公钥' : 'Contact has not uploaded a public key';
  }
  const compact = String(value).replace(/[^0-9A-Fa-f]/g, '').toUpperCase();
  if (!compact) return value;
  return compact.match(/.{1,4}/g).join(' ');
}

function getGroupMemberInfo(conversationId, userId) {
  const members = groupMembersByConversation[conversationId] || {};
  return members[userId] || null;
}

function getMessageSenderName(msg, conv) {
  if (msg.sender_name) return msg.sender_name;
  if (conv && conv.type === "single") return conv.name || conv.peer_username || "Unknown";
  const member = conv ? getGroupMemberInfo(conv.id, msg.sender) : null;
  return member ? (member.display_name || member.username) : "Unknown";
}

function renderRightPanelMembers(conv) {
  const list = document.getElementById("right-panel-members-list");
  if (!list || !conv) return;
  const members = Object.values(groupMembersByConversation[conv.id] || {});
  list.innerHTML = "";
  members.forEach(member => {
    const row = document.createElement("div");
    row.className = "flex items-center justify-between pt-3 first:pt-0";
    const safeColor = /^#[0-9a-fA-F]{6}$/.test(member.avatar_color || '') ? member.avatar_color : '#5c6bc0';
    row.innerHTML = `
      <div class="flex items-center min-w-0 space-x-2.5">
        <div class="w-8 h-8 rounded-full text-white flex items-center justify-center font-bold text-xs flex-shrink-0" style="background-color: ${safeColor}">
          ${escapeHtml(member.initials || "??")}
        </div>
        <div class="min-w-0">
          <div class="text-xs font-semibold text-textMain truncate">${escapeHtml(member.display_name || member.username || "Unknown")}</div>
          <div class="text-[10px] text-textSecondary truncate">@${escapeHtml(member.username || String(member.user_id))}</div>
        </div>
      </div>
      <span class="text-[10px] text-textSecondary flex-shrink-0">${escapeHtml(getRoleTranslation(member.role))}</span>
    `;
    list.appendChild(row);
  });
}

// ============================================================================
// 4. WebSocket Connection
// ============================================================================

function connectWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${protocol}://${window.location.host}/ws/chat/`;
  let socket = null;
  let reconnectTimer = null;

  wsClient = {
    sendPayload(payload) {
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        logToCryptoConsole('[WebSocket] Cannot send: socket is not connected.');
        return false;
      }
      socket.send(JSON.stringify(payload));
      return true;
    },
    connect() {
      socket = new WebSocket(url);
      socket.addEventListener('open', () => {
        logToCryptoConsole('[WebSocket] Connected');
      });
      socket.addEventListener('message', (event) => {
        try {
          handleIncomingMessage(JSON.parse(event.data));
        } catch (err) {
          console.error('[WebSocket] Invalid JSON payload:', err);
        }
      });
      socket.addEventListener('close', (event) => {
        logToCryptoConsole(`[WebSocket] Disconnected: ${event.reason || event.code}`);
        window.clearTimeout(reconnectTimer);
        reconnectTimer = window.setTimeout(() => wsClient.connect(), 1500);
      });
      socket.addEventListener('error', (event) => {
        console.error('[WebSocket] Error:', event);
      });
    },
  };

  wsClient.connect();
}

function handleIncomingMessage(data) {
  const event = data.event || data.type;

  if (event === 'connection.ready') {
    logToCryptoConsole(`[WebSocket] Ready for user ${data.data?.user_id || myUserId}`);
  } else if (event === 'message.single.new') {
    handlePrivateMessageReceived(data);
  } else if (event === 'message.single.accepted') {
    handleMessageAccepted(data);
  } else if (event === 'message.receipt.updated') {
    handleMessageStatusUpdate(data);
  } else if (event === 'message.group.new') {
    handleGroupMessageReceived(data);
  } else if (event === 'message.group.accepted') {
    handleMessageAccepted(data);
  } else if (event === 'group.members.changed') {
    fetchConversations();
  } else if (event === 'error') {
    logToCryptoConsole(`[WebSocket Error] ${data.data?.message || 'Unknown error'}`);
  } else {
    console.log('[WebSocket] Unknown event:', event, data);
  }
}

async function handlePrivateMessageReceived(data) {
  const payload = data.data || data;
  const convId = parseInt(payload.conversation_id);
  const conv = conversationsById[convId];
  let plaintext;
  let decryptError = null;

  try {
    if (window.iChatPrivateE2EE) {
      plaintext = await window.iChatPrivateE2EE.decryptPrivateMessage({
        algorithm: payload.algorithm,
        ciphertext: payload.ciphertext,
        nonce: payload.nonce,
        auth_tag: payload.auth_tag,
        conversation_id: payload.conversation_id,
        sender_id: payload.sender_id,
        receiver_id: payload.receiver_id,
        sender_key_version: payload.sender_key_version,
        receiver_key_version: payload.receiver_key_version,
      });
    } else {
      plaintext = '[Encrypted message — E2EE module not loaded]';
    }
  } catch (err) {
    console.error('Failed to decrypt incoming message:', err);
    plaintext = decryptFailureLabel(err);
    decryptError = err;
  }

  const newMsg = {
    id: payload.message_id,
    text: plaintext,
    time: formatClockTime(new Date(payload.created_at || Date.now())),
    isSelf: payload.sender_id === myUserId,
    sender: payload.sender_id,
    sender_name: conv ? conv.name : undefined,
    status: 'received',
    decryptError: !!decryptError,
  };

  if (messages.some(msg => msg.id === payload.message_id)) return;

  if (conv) {
    updateSidebarPreview(conv, decryptError ? 'Encrypted message' : plaintext, newMsg.time);
  } else {
    fetchConversations();
  }

  if (activeChatId === convId) {
    messages.push(newMsg);
    renderMessages();
    scrollToBottom();
    // Send delivery receipt
    if (wsClient) {
      wsClient.sendPayload({
        event: 'message.receipt.update',
        data: {
          conversation_type: 'single',
          message_id: payload.message_id,
          status: 'delivered',
        },
      });
    }
  } else {
    // Increment unread badge
    if (conv) {
      conv.unread = (conv.unread || 0) + 1;
      const badge = document.getElementById(`unread-badge-${convId}`);
      if (badge) {
        badge.textContent = conv.unread;
        badge.classList.remove('hidden');
      }
    }
  }
}

async function handleGroupMessageReceived(data) {
  const payload = data.data || data;
  const convId = payload.group_id;
  const conv = conversationsById[convId];

  try {
    let plaintext;
    if (window.iChatGroupE2EE) {
      plaintext = await window.iChatGroupE2EE.decryptGroupMessage({
        algorithm: payload.algorithm,
        ciphertext: payload.ciphertext,
        nonce: payload.nonce,
        auth_tag: payload.auth_tag,
        group_id: convId,
        membership_version: payload.membership_version,
        sender_id: payload.sender_id,
        receiver_id: payload.receiver_id,
        sender_key_version: payload.sender_key_version,
        receiver_key_version: payload.receiver_key_version,
      });
    } else {
      plaintext = '[Encrypted group message — E2EE module not loaded]';
    }

    const newMsg = {
      id: payload.message_id,
      text: plaintext,
      time: formatClockTime(new Date(payload.created_at || Date.now())),
      isSelf: payload.sender_id === myUserId,
      sender: payload.sender_id,
      sender_name: payload.sender_name || (getGroupMemberInfo(convId, payload.sender_id) || {}).display_name,
      status: 'received',
    };

    if (messages.some(msg => msg.id === payload.message_id)) return;

    if (activeChatId === convId) {
      messages.push(newMsg);
      renderMessages();
      scrollToBottom();
    } else {
      if (conv) {
        conv.unread = (conv.unread || 0) + 1;
        const badge = document.getElementById(`unread-badge-${convId}`);
        if (badge) {
          badge.textContent = conv.unread;
          badge.classList.remove('hidden');
        }
      }
    }
  } catch (err) {
    console.error('Failed to decrypt incoming group message:', err);
  }
}

function handleMessageStatusUpdate(data) {
  const payload = data.data || data;
  const msg = messages.find(m => m.id === payload.message_id);
  if (msg) {
    msg.status = payload.status;
    renderMessages();
  }
}

function handleMessageAccepted(data) {
  const payload = data.data || data;
  const tempId = payload.client_message_id;
  if (!tempId) return;
  const msg = messages.find(m => m.id === tempId);
  if (msg) {
    msg.id = payload.message_id;
    msg.status = payload.status || 'sent';
    renderMessages();
  }
}

// ============================================================================
// 5. ECDH Key Agreement on Select Chat
// ============================================================================

async function deriveActiveSessionKey(convId) {
  const conv = conversationsById[parseInt(convId)];
  if (!conv || !conv.is_secure) {
    sessionKeys[convId] = null;
    logToCryptoConsole(`[ECDH] Selected non-encrypted channel: ${conv ? conv.name : "N/A"}`);
    return;
  }

  // Check cache
  if (sessionKeys[convId]) return;

  try {
    logToCryptoConsole(`[ECDH] Computing shared secret for conversation ${convId} (${conv.name})`);

    if (conv.type === 'group') {
      // For groups, use the group E2EE module
      if (window.iChatGroupE2EE && window.iChatGroupE2EE.fetchGroupMemberKeys) {
        await window.iChatGroupE2EE.fetchGroupMemberKeys(convId);
      }
    } else {
      // For private chats, derive session key via the private E2EE module
      if (window.iChatPrivateE2EE && window.iChatPrivateE2EE.derivePrivateSessionKey) {
        const keyRecord = window.iChatKeyManager ? window.iChatKeyManager.loadCurrentRecord() : null;
        if (keyRecord && conv.peer_id) {
          const key = await window.iChatPrivateE2EE.derivePrivateSessionKey(
            keyRecord.privateKey,
            null, // peer public key will be fetched internally by the module
            { conversation_id: convId, sender_id: myUserId, receiver_id: conv.peer_id }
          );
          sessionKeys[convId] = key;
        }
      }
    }

    logToCryptoConsole(`[ECDH] Handshake completed for conversation ${convId}.`);
  } catch (err) {
    console.error('ECDH session key derivation failed:', err);
    logToCryptoConsole(`[ECDH Error] Derivation failed: ${err.message}`);
    sessionKeys[convId] = null;
  }
}

// 6. Chat Selection & Rendering
async function selectChat(chatId) {
  activeChatId = parseInt(chatId);
  const conv = conversationsById[activeChatId];
  if (!conv) return;

  // Highlight active chat
  document.querySelectorAll(".chat-item-btn").forEach(item => item.classList.remove("active"));
  const activeItem = document.getElementById(`chat-item-${chatId}`);
  if (activeItem) activeItem.classList.add("active");

  // Clear unread badge
  const badge = document.getElementById(`unread-badge-${chatId}`);
  if (badge) { badge.classList.add("hidden"); badge.textContent = "0"; }
  conv.unread = 0;

  // Close header dropdown
  const headerDropdown = document.getElementById("chat-header-more-dropdown");
  const headerMoreBtn = document.getElementById("chat-header-more-btn");
  if (headerDropdown) headerDropdown.classList.add("hidden");
  if (headerMoreBtn) headerMoreBtn.classList.remove("bg-bgSearch", "text-textMain");

  // Derive session key
  await deriveActiveSessionKey(activeChatId);

  // Populate header
  document.getElementById("chat-header-avatar").textContent = conv.initials || '??';
  document.getElementById("chat-header-avatar").className = `w-10 h-10 rounded-full text-white flex items-center justify-center font-bold text-sm shadow-sm`;
  document.getElementById("chat-header-avatar").style.backgroundColor = conv.avatar_color || '#5c6bc0';
  document.getElementById("chat-header-name").textContent = conv.name || 'Unknown';

  // Update delete/leave text
  const leaveTextEl = document.getElementById("menu-delete-chat-text");
  if (leaveTextEl) {
    const isGroup = conv.type === 'group';
    leaveTextEl.setAttribute("data-i18n", isGroup ? "menu_leave_group" : "menu_delete_chat");
    leaveTextEl.textContent = isGroup
      ? (currentLanguage === 'zh' ? "退出群聊" : "Leave Group")
      : (currentLanguage === 'zh' ? "删除聊天" : "Delete Chat");
  }
  
  // Header status
  const statusText = conv.type === 'group'
    ? (currentLanguage === 'zh' ? `${conv.member_count || 0} 位成员` : `${conv.member_count || 0} members`)
    : (currentLanguage === 'zh' ? '联系人' : 'Contact');
  if (conv.is_secure) {
    const e2eeText = currentLanguage === 'zh' ? '🔒 端到端加密' : '🔒 End-to-end encrypted';
    document.getElementById("chat-header-status").innerHTML = `${statusText} &middot; <span class='text-brand-light dark:text-brand-dark font-semibold'>${e2eeText}</span>`;
  } else {
    document.getElementById("chat-header-status").textContent = statusText;
  }

  // E2EE UI
  const securityBanner = document.getElementById("chat-input-security-banner");
  if (securityBanner) securityBanner.classList.toggle("hidden", !conv.is_secure);
  const lockBtn = document.getElementById("chat-header-lock");
  if (lockBtn) lockBtn.classList.toggle("hidden", !conv.is_secure);

  // Show chat, hide empty state
  document.getElementById("active-chat-window").classList.remove("hidden");
  const emptyState = document.getElementById("empty-state-window");
  if (emptyState) emptyState.classList.add("hidden");

  // Mobile layout
  if (window.innerWidth < 768) {
    document.getElementById("sidebar-container").classList.add("hidden");
    document.getElementById("chat-window-container").classList.remove("hidden");
    document.getElementById("chat-window-container").classList.add("w-full");
    window.location.hash = 'chat-open';
  }

  // Load messages
  messages = [];
  messagePage = 1;
  hasMoreMessages = true;
  await fetchMessages(activeChatId);
  renderMessages();
  scrollToBottom();
  updateDetailsPanel(conv);
}

async function updateDetailsPanel(conv) {
  const requestId = ++detailsPanelRequestId;
  const avatar = document.getElementById("details-avatar");
  const name = document.getElementById("details-name");
  const status = document.getElementById("details-status");
  const fp = document.getElementById("details-fingerprint");
  const fpWrapper = document.getElementById("right-panel-fingerprint-wrapper");
  const groupSection = document.getElementById("right-panel-group-section");
  const protocol = document.getElementById("right-panel-protocol");
  const resetKeyBtn = document.getElementById("right-panel-reset-key-btn");
  const verificationStatus = document.getElementById("right-panel-verification-status");

  if (avatar) {
    avatar.className = 'w-20 h-20 rounded-full text-white flex items-center justify-center font-bold text-2xl shadow-sm mb-3';
    avatar.style.backgroundColor = conv.avatar_color || '#5c6bc0';
    avatar.textContent = conv.initials || '??';
  }
  if (name) name.textContent = conv.name || '';
  if (status) status.textContent = getStatusTranslation(conv.type === 'group' ? `${conv.member_count || 0} members` : 'Contact');

  if (conv.is_secure) {
    if (fpWrapper) fpWrapper.classList.remove("hidden");
    if (protocol) protocol.textContent = "ECDH + HKDF + AES-GCM";
    if (resetKeyBtn) resetKeyBtn.classList.toggle("hidden", conv.type === "group");
    if (verificationStatus) {
      verificationStatus.className = "font-semibold text-amber-500 flex items-center space-x-1";
      verificationStatus.innerHTML = '<i data-lucide="shield-question" class="w-3.5 h-3.5 mr-0.5 inline-block text-amber-500"></i><span>' + (currentLanguage === 'zh' ? '待验证' : 'Unverified') + '</span>';
    }
    if (fp) {
      fp.textContent = currentLanguage === 'zh' ? '正在加载真实指纹...' : 'Loading real fingerprint...';
    }

    if (conv.type === "single" && conv.peer_id) {
      const fingerprint = await fetchPeerFingerprint(conv.peer_id);
      if (requestId !== detailsPanelRequestId) return;
      if (fp) {
        fp.textContent = fingerprint
          ? `v${fingerprint.key_version}: ${formatFingerprint(fingerprint.key_fingerprint)}`
          : formatFingerprint(null);
      }
      if (verificationStatus) {
        const verified = Boolean(fingerprint);
        verificationStatus.className = `font-semibold ${verified ? 'text-emerald-500' : 'text-amber-500'} flex items-center space-x-1`;
        verificationStatus.innerHTML = verified
          ? '<i data-lucide="shield-check" class="w-3.5 h-3.5 mr-0.5 inline-block text-emerald-500"></i><span>' + (currentLanguage === 'zh' ? '已加载真实指纹' : 'Real fingerprint loaded') + '</span>'
          : '<i data-lucide="shield-alert" class="w-3.5 h-3.5 mr-0.5 inline-block text-amber-500"></i><span>' + (currentLanguage === 'zh' ? '缺少公钥' : 'No public key') + '</span>';
      }
    } else if (fp) {
      fp.textContent = currentLanguage === 'zh'
        ? '群聊使用每位成员的当前公钥加密。'
        : 'Group messages are encrypted to each member public key.';
    }
    if (window.lucide) window.lucide.createIcons();
  } else {
    if (fpWrapper) fpWrapper.classList.add("hidden");
  }

  if (conv.type === 'group') {
    if (groupSection) groupSection.classList.remove("hidden");
    const mc = document.getElementById("right-panel-members-count");
    if (mc) mc.textContent = currentLanguage === 'zh' ? `群组成员 (${conv.member_count || 0})` : `Group Members (${conv.member_count || 0})`;
    try {
      await fetchGroupMemberIds(conv.id);
      if (requestId !== detailsPanelRequestId) return;
      renderRightPanelMembers(conv);
    } catch (err) {
      logToCryptoConsole(`[API] Failed to load group members: ${err.message}`);
    }
  } else {
    if (groupSection) groupSection.classList.add("hidden");
  }
}

function renderMessages() {
  const container = document.getElementById("message-history-container");
  if (!container) return;
  container.innerHTML = "";
  const conv = conversationsById[activeChatId];
  messages.forEach((msg, index) => {
    const gm = getMessageGroupMetaNew(messages, index, conv);
    container.appendChild(createMessageBubbleElementNew(msg, gm, conv));
  });
}

// 6. Mobile Layout Back Button Handler
function backToSidebar() {
  window.location.hash = '';
  document.getElementById("chat-window-container").classList.add("hidden");
  document.getElementById("sidebar-container").classList.remove("hidden");
  document.getElementById("sidebar-container").classList.add("w-full");
}

function handleMobileNavigation() {
  if (window.location.hash !== '#chat-open' && window.innerWidth < 768) {
    document.getElementById("chat-window-container").classList.add("hidden");
    document.getElementById("sidebar-container").classList.remove("hidden");
    document.getElementById("sidebar-container").classList.add("w-full");
  }
}

// Helper to derive initials and background color for user avatars based on name
function getSenderAvatarInfo(senderName, msg, conv) {
  const member = conv ? getGroupMemberInfo(conv.id, msg && msg.sender) : null;
  let initials = (msg && msg.sender_initials) || (member && member.initials) || "";
  if (!initials && senderName) {
    const parts = senderName.split(" ");
    if (parts.length > 1) {
      initials = (parts[0][0] + parts[1][0]).toUpperCase();
    } else {
      initials = senderName.substring(0, 2).toUpperCase();
    }
  }
  initials = initials || "??";
  
  // Hash sender initials to select a background color class
  const colors = ["bg-red-500", "bg-orange-500", "bg-yellow-500", "bg-green-500", "bg-teal-500", "bg-blue-500", "bg-indigo-500", "bg-purple-500", "bg-pink-500"];
  let hash = 0;
  for (let i = 0; i < initials.length; i++) {
    hash = initials.charCodeAt(i) + ((hash << 5) - hash);
  }
  const colorClass = colors[Math.abs(hash) % colors.length];
  
  const avatarColor = (msg && msg.sender_avatar_color) || (member && member.avatar_color) || "";
  const safeStyle = /^#[0-9a-fA-F]{6}$/.test(avatarColor)
    ? ` style="background-color: ${avatarColor}"`
    : "";
  return { initials, colorClass, safeStyle };
}

// Helper to look up member role in a group chat
function getGroupMemberRole(senderName) {
  return "Member";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// 8. Create Message Bubble DOM Node

// 9. Encrypt & Send Message

// Helper: Handle Unread Message Badge increment

// 11. Add Contact Modal Logic

// 12. Create Group Modal Logic

// Populate contact list inside the Create Group modal

// ============================================================================
// Message sending
// ============================================================================

async function sendMessage() {
  if (!e2eeKeyReady) {
    const recovered = await recoverE2EEKeyForSending();
    if (!recovered) {
      window.showToast(e2eeKeyError || 'Local encryption key is unavailable.');
      return;
    }
  }

  const textarea = document.getElementById("chat-input-textarea");
  if (!textarea) return;
  const text = textarea.value.trim();
  if (!text) return;

  const conv = conversationsById[activeChatId];
  if (!conv) return;

  const time = formatClockTime();
  const clientMsgId = `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  // Optimistic render
  const tempMsg = {
    id: clientMsgId,
    text: text,
    time: time,
    isSelf: true,
    status: "sending",
  };
  messages.push(tempMsg);
  renderMessages();
  scrollToBottom();
  updateSidebarPreview(conv, text, time);

  textarea.value = "";
  textarea.style.height = "auto";

  try {
    if (conv.type === "group") {
      if (!window.iChatGroupE2EE || !window.iChatGroupE2EE.encryptGroupMessage) {
        throw new Error("Group E2EE module is not loaded.");
      }
        const memberIds = await fetchGroupMemberIds(conv.id);
        const result = await window.iChatGroupE2EE.encryptGroupMessage({
          plaintext: text,
          groupId: conv.id,
          membershipVersion: conv.membership_version || 1,
          memberIds
        });
        if (!wsClient || !wsClient.sendPayload || !wsClient.sendPayload({
            event: "message.group.send",
            request_id: clientMsgId,
            data: {
              group_id: conv.id,
              membership_version: result.membership_version,
              sender_key_version: result.sender_key_version,
              message_type: "text",
              algorithm: result.algorithm,
              client_message_id: clientMsgId,
              recipients: result.recipients
            }
          })) {
            throw new Error("WebSocket is not connected.");
        }
    } else {
      if (!window.iChatPrivateE2EE || !window.iChatPrivateE2EE.encryptPrivateMessage || !conv.peer_id) {
        throw new Error("Private E2EE module or peer information is missing.");
      }
        const result = await encryptPrivateMessageWithTrustRetry({ text, conv });
        const accepted = await apiFetch(`/api/conversations/${conv.id}/messages/send/`, {
          method: "POST",
          body: JSON.stringify({
            receiver_id: conv.peer_id,
            ciphertext: result.ciphertext,
            nonce: result.nonce,
            auth_tag: result.auth_tag,
            algorithm: result.algorithm,
            sender_key_version: result.sender_key_version,
            receiver_key_version: result.receiver_key_version,
            client_message_id: clientMsgId,
            message_type: "text",
          })
        });
        handleMessageAccepted({ data: accepted });
    }
  } catch (err) {
    console.error("Send failed:", err);
    logToCryptoConsole("[Send Error] " + err.message);
    window.showToast(err.message || "Send failed.");
    const idx = messages.findIndex(m => m.id === clientMsgId);
    if (idx >= 0) messages[idx].status = "failed";
    renderMessages();
  }
}

// ============================================================================
// Add contact
// ============================================================================

async function handleAddContact(username) {
  try {
    const resp = await apiFetch("/contacts/search/?q=" + encodeURIComponent(username));
    const results = resp.results || [];
    const target = results.find(r => r.username === username || String(r.id) === username);
    if (!target) {
      window.showToast(currentLanguage === "zh" ? "未找到该用户" : "User not found");
      return;
    }

    if (target.is_contact) {
      const data = await apiFetch("/api/conversations/create/", {
        method: "POST",
        body: JSON.stringify({ peer_id: target.user_id || target.id })
      });
      logToCryptoConsole("[Contact] Conversation ready: " + data.conversation_id);
      await fetchConversations();
      if (data.conversation_id) {
        selectChat(data.conversation_id.toString());
      }
      window.showToast(currentLanguage === "zh" ? "会话已创建" : "Conversation ready");
      return;
    }

    if (target.has_pending_out) {
      window.showToast(currentLanguage === "zh" ? "好友请求已发送" : "Friend request already sent");
      return;
    }

    if (target.has_pending_in) {
      window.location.href = "/contacts/";
      return;
    }

    const formData = new URLSearchParams();
    formData.set("username", target.username);
    formData.set("user_id", String(target.id));
    const requestResp = await fetch("/contacts/request/send/", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": getCookie("csrftoken")
      },
      body: formData.toString()
    });
    if (!requestResp.ok) {
      throw new Error("Friend request failed (" + requestResp.status + ")");
    }
    window.showToast(currentLanguage === "zh" ? "好友请求已发送" : "Friend request sent");
  } catch (err) {
    console.error("Add contact failed:", err);
    logToCryptoConsole("[Contact Error] " + err.message);
    window.showToast(currentLanguage === "zh" ? "添加联系人失败" : "Could not add contact");
  }
}

// ============================================================================
// Create group
// ============================================================================

async function handleCreateGroup(groupName) {
  try {
    const checkedBoxes = document.querySelectorAll(".group-member-checkbox:checked");
    const memberIds = Array.from(checkedBoxes).map(cb => parseInt(cb.value));
    const data = await apiFetch("/api/groups/", {
      method: "POST",
      body: JSON.stringify({ name: groupName })
    });
    logToCryptoConsole("[Group] Created: " + data.id);
    for (const uid of memberIds) {
      try {
        await apiFetch("/api/groups/" + data.id + "/invite/", {
          method: "POST",
          body: JSON.stringify({ user_id: uid })
        });
      } catch (e) { console.warn("Failed to invite", uid, e); }
    }
    await fetchConversations();
    if (data.id) selectChat(data.id.toString());
    window.showToast(currentLanguage === "zh" ? "群组已创建" : "Group created");
  } catch (err) {
    console.error("Create group failed:", err);
    logToCryptoConsole("[Group Error] " + err.message);
  }
}

// ============================================================================
// Populate group member selection list
// ============================================================================

function populateGroupMembersList() {
  const listEl = document.getElementById("group-members-list");
  if (!listEl) return;
  listEl.innerHTML = "";
  conversations.forEach(conv => {
    if (conv.type === "single" && conv.peer_id) {
      const item = document.createElement("div");
      const peerId = Number(conv.peer_id);
      const safePeerId = Number.isFinite(peerId) ? String(peerId) : "";
      const safeAvatarColor = /^#[0-9a-fA-F]{6}$/.test(conv.avatar_color || '') ? conv.avatar_color : '#5c6bc0';
      item.className = "flex items-center space-x-3 py-1.5 px-2 hover:bg-bgSearch/40 rounded cursor-pointer";
      item.innerHTML = '<input type="checkbox" class="group-member-checkbox rounded border-borderColor text-brand-light focus:ring-brand-light w-4 h-4" value="' + safePeerId + '" id="member-chk-' + safePeerId + '">'
        + '<div class="w-8 h-8 rounded-full text-white flex items-center justify-center font-bold text-xs" style="background-color: ' + safeAvatarColor + '">'
        + escapeHtml(conv.initials || "??") + '</div>'
        + '<label class="text-sm font-medium text-textMain cursor-pointer flex-1" for="member-chk-' + safePeerId + '">'
        + escapeHtml(conv.name || conv.peer_username || "Unknown") + '</label>';
      listEl.appendChild(item);
    }
  });
}

// ============================================================================
// Fingerprint modal
// ============================================================================

async function showFingerprintModal() {
  if (!activeChatId) return;
  const conv = conversationsById[activeChatId];
  if (!conv || !conv.is_secure) return;
  document.getElementById("fp-modal-name").textContent = conv.name || "Unknown";
  const keyEl = document.getElementById("fp-modal-key");
  if (keyEl) {
    keyEl.textContent = currentLanguage === 'zh' ? '正在加载真实指纹...' : 'Loading real fingerprint...';
  }
  const modal = document.getElementById("fingerprint-modal");
  if (modal) { modal.classList.remove("hidden"); modal.classList.add("flex"); }
  if (conv.type === "single" && conv.peer_id) {
    const fingerprint = await fetchPeerFingerprint(conv.peer_id);
    if (activeChatId !== conv.id || !keyEl) return;
    keyEl.textContent = fingerprint
      ? `v${fingerprint.key_version}: ${formatFingerprint(fingerprint.key_fingerprint)}`
      : formatFingerprint(null);
  } else if (keyEl) {
    keyEl.textContent = currentLanguage === 'zh'
      ? '群聊没有单一联系人指纹，请分别验证成员公钥。'
      : 'Group chats do not have one peer fingerprint; verify member keys individually.';
  }
}

// ============================================================================
// Unread badge helper
// ============================================================================

function triggerUnreadCount(chatId) {
  const badge = document.getElementById("unread-badge-" + chatId);
  if (badge) {
    const conv = conversationsById[chatId];
    if (conv) {
      conv.unread = Number(conv.unread || 0) + 1;
      badge.textContent = conv.unread;
    } else {
      badge.textContent = parseInt(badge.textContent || "0", 10) + 1;
    }
    badge.classList.remove("hidden");
  }
}

// ============================================================================
// Infinite scroll
// ============================================================================

function setupInfiniteScroll() {
  const container = document.getElementById("message-history-container");
  if (!container) return;
  container.addEventListener("scroll", () => {
    if (container.scrollTop < 100 && hasMoreMessages && !isLoadingMessages && activeChatId) {
      isLoadingMessages = true;
      const prevScrollHeight = container.scrollHeight;
      fetchMessages(activeChatId, messagePage + 1).then(() => {
        requestAnimationFrame(() => {
          container.scrollTop = container.scrollHeight - prevScrollHeight;
        });
        isLoadingMessages = false;
      });
    }
  });
}

// ============================================================================
// Message group meta & bubble rendering
// ============================================================================

function getMessageGroupMetaNew(msgs, index, conv) {
  const msg = msgs[index];
  if (!msg || msg.isSystem) return { isConsecutive: false, isFirstInGroup: true, isLastInGroup: true };
  const prev = msgs[index - 1];
  const next = msgs[index + 1];
  const key = msg.isSelf ? "self" : (msg.sender || "peer");
  const prevKey = prev && !prev.isSystem ? (prev.isSelf ? "self" : (prev.sender || "peer")) : null;
  const nextKey = next && !next.isSystem ? (next.isSelf ? "self" : (next.sender || "peer")) : null;
  return {
    isConsecutive: Boolean(prevKey && prevKey === key),
    isFirstInGroup: !prevKey || prevKey !== key,
    isLastInGroup: !nextKey || nextKey !== key,
  };
}

function createMessageBubbleElementNew(msg, groupMeta, conv) {
  if (typeof groupMeta === "boolean") {
    groupMeta = { isConsecutive: groupMeta, isFirstInGroup: !groupMeta, isLastInGroup: true };
  } else if (!groupMeta) {
    groupMeta = { isConsecutive: false, isFirstInGroup: true, isLastInGroup: true };
  }
  var _a = groupMeta, isConsecutive = _a.isConsecutive, isFirstInGroup = _a.isFirstInGroup, isLastInGroup = _a.isLastInGroup;
  var div = document.createElement("div");
  div.className = "message-row " + (isConsecutive ? "message-row-grouped " : "") + (isFirstInGroup ? "message-row-group-first " : "") + (isLastInGroup ? "message-row-group-last" : "");

  if (msg.isSystem || msg.decryptError) {
    div.className += " message-row-system";
    var text = msg.decryptError ? msg.text : getSystemMessageTranslation(msg.text);
    div.innerHTML = '<div class="system-capsule"><span>' + escapeHtml(text) + '</span></div>';
    setTimeout(function() { if (div.querySelector("[data-lucide]")) lucide.createIcons(); }, 0);
    return div;
  }

  if (!msg.isSystem) {
    div.onclick = function(e) {
      if (isSelectingMessages) { e.stopPropagation(); toggleMessageSelection(msg.id); }
    };
  }

  var checkboxHtml = '<div class="message-select-checkbox select-none ' + (isSelectingMessages ? "" : "hidden") + '" id="msg-select-check-' + msg.id + '"><i data-lucide="' + (selectedMessageIds.includes(msg.id) ? "check-circle-2" : "circle") + '" class="w-5 h-5 text-textSecondary"></i></div>';

  var isGroup = conv && conv.type === "group";
  var senderName = msg.isSelf ? "You" : getMessageSenderName(msg, conv);
  var messageText = escapeHtml(msg.text);
  var messageTime = escapeHtml(msg.time || "");

  if (msg.isSelf) {
    div.className += " message-row-self";
    if (isSelectingMessages) div.className += " message-row-selecting";
    var statusIcon = "check";
    if (msg.status === "delivered" || msg.status === "sent") statusIcon = "check-check";
    if (msg.status === "read") statusIcon = "check-check";
    var statusClass = msg.status === "read" ? "text-brand-light dark:text-brand-dark" : "";
    div.innerHTML = checkboxHtml
      + '<div class="message-bubble-custom bubble-self" data-message-id="' + msg.id + '">'
      + '<p class="message-text-content">' + messageText + '</p>'
      + '<div class="message-meta-line">'
      + '<span>' + messageTime + '</span>'
      + '<i data-lucide="' + statusIcon + '" class="w-3.5 h-3.5 ' + statusClass + '"></i>'
      + '</div></div>';
  } else {
    div.className += " message-row-peer";
    if (isSelectingMessages) div.className += " message-row-selecting";
    var avatarHtml = "";
    if (isLastInGroup) {
      var avatarInfo = getSenderAvatarInfo(senderName, msg, conv);
      avatarHtml = '<div class="message-avatar ' + avatarInfo.colorClass + '" title="' + escapeHtml(senderName) + '"' + avatarInfo.safeStyle + '>' + escapeHtml(avatarInfo.initials) + '</div>';
    } else {
      avatarHtml = '<div class="message-avatar-spacer" aria-hidden="true"></div>';
    }
    var senderNameHtml = "";
    if (isFirstInGroup) {
      senderNameHtml = '<div class="message-sender-line"><span class="message-sender-name">' + escapeHtml(senderName) + '</span></div>';
    }
    div.innerHTML = checkboxHtml + avatarHtml
      + '<div class="message-bubble-custom bubble-peer" data-message-id="' + msg.id + '">'
      + senderNameHtml
      + '<p class="message-text-content">' + messageText + '</p>'
      + '<div class="message-meta-line"><span>' + messageTime + '</span></div>'
      + '</div>';
  }
  setTimeout(function() { if (div.querySelector("[data-lucide]")) lucide.createIcons(); }, 0);
  return div;
}

// 13. UI Setup & Listeners
function setupEventListeners() {
  // Search filtering
  const searchInput = document.getElementById("sidebar-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      filterChatList(e.target.value.trim());
    });
  }

  // Enter to send message
  const chatInput = document.getElementById("chat-input-textarea");
  if (chatInput) {
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
  }

  // Left Hamburger Drawer toggles
  const drawerBtn = document.getElementById("drawer-btn");
  const drawerOverlay = document.getElementById("drawer-menu-overlay");
  if (drawerOverlay) {
    drawerOverlay.addEventListener("click", toggleDrawer);
  }

  // Profile/Settings toggles
  const menuSettings = document.getElementById("menu-settings-btn");
  const menuProfile = document.getElementById("menu-profile-btn");
  const settingsBack = document.getElementById("settings-back-btn");
  if (menuSettings) {
    menuSettings.addEventListener("click", () => {
      navigateSidebar('settings-home');
      toggleDrawer();
    });
  }
  if (menuProfile) {
    menuProfile.addEventListener("click", () => {
      navigateSidebar('settings-home');
      toggleDrawer();
    });
  }
  if (settingsBack) {
    settingsBack.addEventListener("click", hideSettingsPanel);
  }

  // Theme switch checkbox
  const themeSwitch = document.getElementById("theme-toggle-switch");
  if (themeSwitch) {
    themeSwitch.addEventListener("change", () => {
      window.toggleTheme();
    });
  }

  // Right Profile Details Panel Toggles
  const rightDetailsPanel = document.getElementById("right-panel");
  const chatHeaderDetails = document.getElementById("chat-header-details");
  const chatHeaderLock = document.getElementById("chat-header-lock");
  const closeDetailsBtn = document.getElementById("close-details-btn");

  window.toggleRightPanel = function() {
    if (rightDetailsPanel) {
      rightDetailsPanel.classList.toggle("collapsed");
      lucide.createIcons();
    }
  };

  if (chatHeaderDetails) {
    chatHeaderDetails.addEventListener("click", window.toggleRightPanel);
  }
  if (chatHeaderLock) {
    chatHeaderLock.addEventListener("click", (e) => {
      e.stopPropagation();
      window.toggleRightPanel();
    });
  }
  if (closeDetailsBtn) {
    closeDetailsBtn.addEventListener("click", () => {
      if (rightDetailsPanel) {
        rightDetailsPanel.classList.add("collapsed");
      }
    });
  }

  // Close dropdowns on outside click
  document.addEventListener("click", (e) => {
    // 1. More menu
    const moreDropdown = document.getElementById("chat-header-more-dropdown");
    const moreBtn = document.getElementById("chat-header-more-btn");
    if (moreDropdown && !moreDropdown.classList.contains("hidden")) {
      if (moreBtn && !moreBtn.contains(e.target) && !moreDropdown.contains(e.target)) {
        moreDropdown.classList.add("hidden");
        moreBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
    }
    // 2. Main menu
    const mainDropdown = document.getElementById("main-menu-dropdown");
    const mainBtn = document.getElementById("drawer-btn");
    if (mainDropdown && !mainDropdown.classList.contains("hidden")) {
      if (mainBtn && !mainBtn.contains(e.target) && !mainDropdown.contains(e.target)) {
        mainDropdown.classList.add("hidden");
        mainBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
    }
  });

  // Handle ESC key press to close dropdowns and modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const moreDropdown = document.getElementById("chat-header-more-dropdown");
      const moreBtn = document.getElementById("chat-header-more-btn");
      if (moreDropdown && !moreDropdown.classList.contains("hidden")) {
        moreDropdown.classList.add("hidden");
        if (moreBtn) moreBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
      const mainDropdown = document.getElementById("main-menu-dropdown");
      const mainBtn = document.getElementById("drawer-btn");
      if (mainDropdown && !mainDropdown.classList.contains("hidden")) {
        mainDropdown.classList.add("hidden");
        if (mainBtn) mainBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
      closeReportModal();
      closeDeleteConfirmModal();
      closeLogoutConfirmModal();
      if (typeof closeClearAllModal === 'function') closeClearAllModal();
      closeVisibilityPicker();
      closeAutoDeletePicker();
      closeBlockedUsersList();
      closePrivacyConfirmModal();
    }
  });

  // Close main dropdown when scrolling sidebar list
  const sidebarScrollContainer = document.querySelector('#sidebar-chat-view .overflow-y-auto');
  if (sidebarScrollContainer) {
    sidebarScrollContainer.addEventListener("scroll", () => {
      const mainDropdown = document.getElementById("main-menu-dropdown");
      const mainBtn = document.getElementById("drawer-btn");
      if (mainDropdown && !mainDropdown.classList.contains("hidden")) {
        mainDropdown.classList.add("hidden");
        if (mainBtn) mainBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
    });
  }

  // Close header dropdown when scrolling message window
  const msgHistoryContainer = document.getElementById("message-history-container");
  if (msgHistoryContainer) {
    msgHistoryContainer.addEventListener("scroll", () => {
      const moreDropdown = document.getElementById("chat-header-more-dropdown");
      const moreBtn = document.getElementById("chat-header-more-btn");
      if (moreDropdown && !moreDropdown.classList.contains("hidden")) {
        moreDropdown.classList.add("hidden");
        if (moreBtn) moreBtn.classList.remove("bg-bgSearch", "text-textMain");
      }
    });
  }

  // Clear Crypto Console Log
  const clearConsoleBtn = document.getElementById("clear-crypto-console-btn");
  if (clearConsoleBtn) {
    clearConsoleBtn.addEventListener("click", () => {
      const logEl = document.getElementById("crypto-console-log");
      if (logEl) {
        logEl.textContent = "[Crypto Console Cleared]";
      }
    });
  }

  // Group Modal Listeners
  const groupModal = document.getElementById("group-modal");
  const menuNewGroupBtn = document.getElementById("menu-new-group-btn");
  const closeGroupModalBtn = document.getElementById("close-group-modal-btn");
  const submitGroupModalBtn = document.getElementById("submit-group-modal-btn");
  const groupNameInput = document.getElementById("group-name");

  if (menuNewGroupBtn) {
    menuNewGroupBtn.addEventListener("click", () => {
      toggleDrawer();
      populateGroupMembersList();
      if (groupModal) {
        groupModal.classList.remove("hidden");
        groupModal.classList.add("flex");
      }
    });
  }
  if (closeGroupModalBtn) {
    closeGroupModalBtn.addEventListener("click", () => {
      if (groupModal) {
        groupModal.classList.add("hidden");
        groupModal.classList.remove("flex");
      }
    });
  }
  if (submitGroupModalBtn) {
    submitGroupModalBtn.addEventListener("click", () => {
      const groupName = groupNameInput.value.trim();
      if (!groupName) return alert("Please enter group name.");
      handleCreateGroup(groupName);
      groupNameInput.value = "";
      if (groupModal) {
        groupModal.classList.add("hidden");
        groupModal.classList.remove("flex");
      }
    });
  }

  // Contacts Modal Listeners
  const contactsModal = document.getElementById("contacts-modal");
  const menuContactsBtn = document.getElementById("menu-contacts-btn");
  const closeContactsModalBtn = document.getElementById("close-contacts-modal-btn");
  const submitContactsModalBtn = document.getElementById("submit-contacts-modal-btn");
  const contactUsernameInput = document.getElementById("contact-username");

  if (menuContactsBtn) {
    menuContactsBtn.addEventListener("click", () => {
      toggleDrawer();
      if (contactsModal) {
        contactsModal.classList.remove("hidden");
        contactsModal.classList.add("flex");
      }
    });
  }
  if (closeContactsModalBtn) {
    closeContactsModalBtn.addEventListener("click", () => {
      if (contactsModal) {
        contactsModal.classList.add("hidden");
        contactsModal.classList.remove("flex");
      }
    });
  }
  if (submitContactsModalBtn) {
    submitContactsModalBtn.addEventListener("click", async () => {
      const username = contactUsernameInput.value.trim();
      if (!username) return alert("Please enter a username.");
      await handleAddContact(username);
      contactUsernameInput.value = "";
      if (contactsModal) {
        contactsModal.classList.add("hidden");
        contactsModal.classList.remove("flex");
      }
    });
  }

  // Window resize to restore desktop sidebar layout
  window.addEventListener("resize", () => {
    if (window.innerWidth >= 768) {
      document.getElementById("sidebar-container").classList.remove("hidden", "w-full");
      document.getElementById("chat-window-container").classList.remove("w-full");
      if (!activeChatId) {
        document.getElementById("chat-window-container").classList.add("hidden");
      } else {
        document.getElementById("chat-window-container").classList.remove("hidden");
      }
    } else {
      handleMobileNavigation();
    }
  });

  window.addEventListener("hashchange", handleMobileNavigation);
}

// 14. Additional Interface Utilities
function scrollToBottom() {
  const container = document.getElementById("message-history-container");
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

function filterChatList(query) {
  const cleaned = query.toLowerCase();
  conversations.forEach(chat => {
    const el = document.getElementById(`chat-item-wrapper-${chat.id}`);
    if (!el) return;
    if (chat.name.toLowerCase().includes(cleaned)) {
      el.classList.remove("hidden");
    } else {
      el.classList.add("hidden");
    }
  });
}

function toggleDrawer() {
  // Close main menu dropdown popover
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) {
    mainDropdown.classList.add("hidden");
    if (mainBtn) mainBtn.classList.remove("bg-bgSearch", "text-textMain");
  }

  // Fallback drawer overlay if exists
  const overlay = document.getElementById("drawer-menu-overlay");
  const content = document.getElementById("drawer-menu-content");
  if (overlay && content) {
    overlay.classList.add("hidden");
    content.classList.add("-translate-x-full");
  }
}

// Phase 2 sidebar navigation — supports chat/settings/contacts/search and settings subpages.
let lastSidebarView = 'chat';

function navigateSidebar(viewName) {
  lastSidebarView = viewName;
  var views = [
    'chat',
    'settings-home',
    'settings',
    'settings-profile',
    'contacts',
    'search',
    'notifications',
    'data-storage',
    'privacy-security',
    'chat-folders',
    'sessions-shortcuts'
  ];
  views.forEach(function(name) {
    var el = name === 'chat'
      ? document.getElementById('sidebar-chat-view')
      : document.getElementById('sidebar-view-' + name);
    if (el) el.classList.toggle('hidden', name !== viewName);
  });
  // On mobile, back to sidebar when navigating settings/contacts
  if (window.innerWidth < 768 && viewName !== 'chat') {
    document.getElementById('sidebar-container').classList.remove('hidden');
    document.getElementById('chat-window-container').classList.add('hidden');
    window.location.hash = '';
  }
  // Refresh data-storage stats when navigating to that view
  if (viewName === 'data-storage' && typeof renderStorageUsage === 'function') {
    setTimeout(function() { renderStorageUsage(); }, 150);
  }
  // Refresh privacy settings when navigating to that view
  if (viewName === 'privacy-security' && typeof loadPrivacySettings === 'function') {
    setTimeout(function() { loadPrivacySettings(); }, 150);
  }
  // Re-render lucide icons after view switch
  if (window.lucide) setTimeout(function() { lucide.createIcons(); }, 50);
}

// Backward-compatible wrappers
function showSettingsPanel() {
  navigateSidebar('settings');
}

function hideSettingsPanel() {
  navigateSidebar('chat');
}

function setupSidebarResizer() {
  const sidebar = document.getElementById("sidebar-container");
  const handle = document.getElementById("sidebar-resize-handle");
  if (!sidebar || !handle) return;

  let startX = 0;
  let startWidth = 0;
  let latestWidth = 0;
  let animationFrame = null;

  const clampWidth = (width) => {
    const maxByViewport = Math.max(440, window.innerWidth - 440);
    return Math.min(Math.max(width, 280), Math.min(440, maxByViewport));
  };

  const applyWidth = (width) => {
    const nextWidth = clampWidth(width);
    document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
    latestWidth = nextWidth;
    return nextWidth;
  };

  const savedWidth = Number(localStorage.getItem("ichat-sidebar-width"));
  if (Number.isFinite(savedWidth) && savedWidth >= 280) applyWidth(savedWidth);

  const onPointerMove = (event) => {
    latestWidth = startWidth + event.clientX - startX;
    if (animationFrame) return;
    animationFrame = requestAnimationFrame(() => {
      applyWidth(latestWidth);
      animationFrame = null;
    });
  };

  const stopResize = (event) => {
    if (animationFrame) {
      cancelAnimationFrame(animationFrame);
      animationFrame = null;
    }
    const nextWidth = applyWidth(latestWidth || sidebar.getBoundingClientRect().width);
    localStorage.setItem("ichat-sidebar-width", String(nextWidth));
    if (event?.pointerId && handle.hasPointerCapture?.(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
    document.body.classList.remove("sidebar-resizing");
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", stopResize);
    window.removeEventListener("pointercancel", stopResize);
  };

  handle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth < 768) return;
    event.preventDefault();
    startX = event.clientX;
    startWidth = sidebar.getBoundingClientRect().width;
    latestWidth = startWidth;
    handle.setPointerCapture?.(event.pointerId);
    document.body.classList.add("sidebar-resizing");
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
  });

  window.addEventListener("resize", () => {
    const currentWidth = sidebar.getBoundingClientRect().width;
    const nextWidth = applyWidth(currentWidth);
    localStorage.setItem("ichat-sidebar-width", String(nextWidth));
  });
}

function closeFingerprintModal() {
  const modal = document.getElementById("fingerprint-modal");
  if (modal) {
    modal.classList.remove("flex");
    modal.classList.add("hidden");
  }
}

// QR Code modal (P2 T03)
function showQRCodeModal() {
  const modal = document.getElementById("qr-code-modal");
  if (modal) { modal.classList.remove("hidden"); modal.classList.add("flex"); }
}
function closeQRCodeModal() {
  const modal = document.getElementById("qr-code-modal");
  if (modal) { modal.classList.remove("flex"); modal.classList.add("hidden"); }
}
function copyQRCode() {
  const fb = document.getElementById("qr-copy-feedback");
  const btn = document.querySelector("[data-qr-username]");
  const username = btn ? btn.getAttribute("data-qr-username") : "";
  const inviteUrl = window.location.origin + "/contacts/add/" + (username ? "?ref=" + encodeURIComponent(username) : "");
  navigator.clipboard.writeText(inviteUrl).then(function() {
    if (fb) { fb.classList.remove("hidden"); setTimeout(function() { fb.classList.add("hidden"); }, 2000); }
  }).catch(function() {
    window.showToast("Failed to copy QR code link");
  });
}
window.showQRCodeModal = showQRCodeModal;
window.closeQRCodeModal = closeQRCodeModal;
window.copyQRCode = copyQRCode;

function adjustTextareaHeight(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = textarea.scrollHeight + "px";
}

function toggleEmojiDropdown() {
  const picker = document.getElementById("emoji-picker");
  if (picker) {
    picker.classList.toggle("hidden");
  }
}

// Ensure toggleTheme is also exposed for direct HTML calls
window.toggleTheme = function() {
  const htmlEl = document.documentElement;
  const isDark = htmlEl.getAttribute("data-theme") === "dark";
  const nextTheme = isDark ? "light" : "dark";

  htmlEl.setAttribute("data-theme", nextTheme);
  localStorage.setItem("ichat-theme", nextTheme);

  if (nextTheme === "dark") {
    document.body.classList.add("dark");
  } else {
    document.body.classList.remove("dark");
  }

  const switchEl = document.getElementById("theme-toggle-switch");
  if (switchEl) {
    switchEl.checked = (nextTheme === "dark");
  }

  // Dispatch custom theme event
  const event = new CustomEvent("themeChanged", { detail: { theme: nextTheme } });
  window.dispatchEvent(event);
};

function insertEmoji(emoji) {
  const textarea = document.getElementById("chat-input-textarea");
  if (textarea) {
    textarea.value += emoji;
    adjustTextareaHeight(textarea);
    textarea.focus();
  }
  toggleEmojiDropdown();
}

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", async () => {
  window.addEventListener('ichat:key-missing', () => {
    window.showToast('Local private key is missing. Import your key backup to decrypt messages.');
  });

  var keyScript = document.getElementById('ichat-key-manager-script');
  myUserId = keyScript ? parseInt(keyScript.dataset.currentUserId) : null;

  setupEventListeners();
  setupSidebarResizer();

  try {
    if (window.iChatKeyManager) {
      await window.iChatKeyManager.initialize();
    }
  } catch (err) {
    console.error('Key init failed:', err);
    setE2EEKeyError(err.message);
  }

  await fetchConversations();

  connectWebSocket();

  setupInfiniteScroll();

  applyLanguage();
});

// Translation Dictionary
const translations = {
  en: {
    account_details: "Account Details",
    active_sessions: "Active Sessions (3)",
    active_sessions_desc: "Manage all devices logged into your account",
    attach_document: "Attach Document",
    back_to_sidebar: "Back to Chats",
    blocked_contacts: "Blocked Contacts",
    blocked_contacts_desc: "No users currently blocked",
    chat_info_title: "Chat Info",
    close_panel: "Close Panel",
    cryptographic_fingerprint: "Cryptographic Fingerprint",
    dark_theme_mode: "Dark Theme Mode",
    e2ee_banner: "🔒 Messages are secured with end-to-end encryption.",
    email_address: "Email Address",
    empty_desc: "Choose a contact from the sidebar list or search for someone new to initiate an end-to-end encrypted session.",
    empty_item1: "Messages are encrypted locally with ECDH P-256 key agreement.",
    empty_item2: "No plain text is ever stored on the server directory.",
    empty_item3: "Verify encryption status by checking active fingerprints.",
    empty_title: "No Chat Selected",
    encryption_details: "Encryption Details",
    fp_match_btn: "Fingerprints Match",
    group_members_title: "Group Members",
    insert_emoji: "Insert Emoji",
    lang_display: "English",
    language_mode: "Language / 语言",
    main_menu: "Main Menu",
    manage_keys: "Manage Cryptographic Keys",
    manage_keys_desc: "View and verify Elliptic Curve key pairs",
    menu_contacts: "Contacts",
    menu_help: "iChat Pro Help & FAQ",
    menu_logout: "Sign Out",
    menu_new_group: "New Group",
    menu_profile: "My Profile",
    menu_saved_messages: "Saved Messages",
    menu_settings: "Settings",
    menu_theme: "Toggle Theme",
    more_operations: "More Operations",
    off: "Off",
    online: "Online",
    phone_number: "Phone Number",
    privacy_security: "Privacy & Security",
    protocol: "Protocol",
    search_chat: "Search Chat",
    search_placeholder: "Search chats or messages...",
    self_destruct_timer: "Self-Destruct Timer",
    settings: "Settings",
    system_preferences: "System Preferences",
    timer_1h: "1 Hour",
    username: "Username",
    verification: "Verification",
    verified: "Verified",
    verify_fingerprint_btn: "Verify Fingerprint",
    verify_fp_desc: "E2EE Encrypted. Click to verify fingerprint.",
    verify_fp_title: "Verify Security Fingerprint",
    reset_key_btn: "Reset Key",
    view_info: "Chat Info",
    write_placeholder: "Write an encrypted message...",
    menu_boost_group: "Boost Group",
    menu_mute_group: "Mute...",
    menu_select_messages: "Select messages",
    menu_report: "Report",
    menu_leave_group: "Leave Group",
    menu_delete_chat: "Delete Chat",
    menu_add_account: "Add Account",
    menu_more: "More",
    menu_about: "About iChat Pro",
    menu_updates: "Check Updates"
  },
  zh: {
    account_details: "账号详情",
    active_sessions: "活跃会话 (3)",
    active_sessions_desc: "管理所有已登录此账号的设备",
    attach_document: "附加文件",
    back_to_sidebar: "返回聊天列表",
    blocked_contacts: "已屏蔽联系人",
    blocked_contacts_desc: "目前没有被屏蔽的用户",
    chat_info_title: "聊天信息",
    close_panel: "关闭面板",
    cryptographic_fingerprint: "加密指纹",
    dark_theme_mode: "暗黑主题模式",
    e2ee_banner: "🔒 消息已通过端到端加密保护。",
    email_address: "电子邮箱地址",
    empty_desc: "从侧边栏列表中选择一个联系人，或搜索新联系人以启动端到端加密会话。",
    empty_item1: "消息使用 ECDH P-256 密钥协商在本地进行加密。",
    empty_item2: "服务器目录中绝不存储任何明文消息。",
    empty_item3: "通过检查当前的安全指纹来验证加密状态。",
    empty_title: "未选择聊天",
    encryption_details: "加密详情",
    fp_match_btn: "指纹匹配",
    group_members_title: "群组成员",
    insert_emoji: "插入表情符号",
    lang_display: "简体中文",
    language_mode: "语言 / Language",
    main_menu: "主菜单",
    manage_keys: "管理加密密钥",
    manage_keys_desc: "查看并验证椭圆曲线密钥对",
    menu_contacts: "联系人",
    menu_help: "iChat Pro 帮助与常见问题",
    menu_logout: "退出登录",
    menu_new_group: "新建群组",
    menu_profile: "个人资料",
    menu_saved_messages: "收藏夹",
    menu_settings: "设置",
    menu_theme: "切换主题",
    more_operations: "更多操作",
    off: "关闭",
    online: "在线",
    phone_number: "手机号码",
    privacy_security: "隐私与安全",
    protocol: "加密协议",
    search_chat: "搜索聊天记录",
    search_placeholder: "搜索聊天或消息...",
    self_destruct_timer: "阅后即焚定时器",
    settings: "设置",
    system_preferences: "系统首选项",
    timer_1h: "1 小时",
    username: "用户名",
    verification: "验证状态",
    verified: "已验证",
    verify_fingerprint_btn: "验证指纹",
    verify_fp_desc: "端到端加密。点击以验证安全指纹。",
    verify_fp_title: "验证安全指纹",
    reset_key_btn: "重置密钥",
    view_info: "查看信息",
    write_placeholder: "编写加密消息...",
    menu_boost_group: "助力群组",
    menu_mute_group: "静音免打扰",
    menu_select_messages: "选择消息",
    menu_report: "举报",
    menu_leave_group: "退出群聊",
    menu_delete_chat: "删除聊天",
    menu_add_account: "添加账号",
    menu_more: "更多",
    menu_about: "关于 iChat Pro",
    menu_updates: "检查更新"
  }
};

const literalTextTranslations = {
  zh: {
    "Notifications and Sounds": "通知与声音",
    "Data and Storage": "数据和存储",
    "Privacy and Security": "隐私和安全",
    "Chat Folders": "聊天文件夹",
    "Customize folder appearance": "自定义文件夹显示",
    "5 chats": "5 个聊天",
    "Stickers and Emoji": "贴纸与表情",
    "Speakers and Camera": "扬声器和摄像头",
    "Devices": "设备",
    "3 active": "3 个活跃",
    "Language / 语言": "语言",
    "Keyboard Shortcuts": "快捷键",
    "Manage Cryptographic Keys": "管理加密密钥",
    "Checking...": "检查中...",
    "Key Fingerprint (SHA-256)": "密钥指纹 (SHA-256)",
    "Generate Keys": "生成密钥",
    "Upload to Server": "上传到服务器",
    "Export Backup": "导出备份",
    "Import Backup": "导入备份",
    "Security Status": "安全状态",
    "E2EE key setup and contact verification": "端到端加密密钥和联系人验证状态",
    "Local Keys": "本地密钥",
    "Server Synced": "服务器同步",
    "Refresh Status": "刷新状态",
    "Storage Usage": "存储用量",
    "Images": "图片",
    "Video files": "视频文件",
    "Stickers and emojis": "贴纸和表情",
    "Other": "其他",
    "Cached video stream chunks": "缓存的视频流片段",
    "Calculating…": "计算中…",
    "Auto-Download": "自动下载",
    "Reset Auto-Download Settings": "重置自动下载设置",
    "On Mobile Data": "使用移动数据时",
    "On Wi-Fi": "使用 Wi-Fi 时",
    "On Roaming": "漫游时",
    "Photos": "照片",
    "Files": "文件",
    "Files / Documents": "文件 / 文档",
    "All on": "全部开启",
    "All off": "全部关闭",
    "Maximum File Size for Auto-Download": "自动下载文件大小限制",
    "Cache Management": "缓存管理",
    "Cache retention period": "缓存保留时间",
    "1 week": "1 周",
    "1 month": "1 个月",
    "3 months": "3 个月",
    "Forever": "永久",
    "Maximum cache size": "最大缓存大小",
    "Clear Images": "清除图片",
    "Clear Video files": "清除视频文件",
    "Clear Stickers & Emojis": "清除贴纸和表情",
    "Clear Other Cached Data": "清除其他缓存数据",
    "Clear Cached Video Stream Chunks": "清除缓存的视频流片段",
    "Clear All Cache": "清除所有缓存",
    "Clear Local Cache": "清理本地缓存",
    "Clear All Cache Settings": "清除所有缓存设置",
    "Privacy": "隐私",
    "Last Seen & Online": "最后在线与在线状态",
    "Everybody": "所有人",
    "Profile Photo": "头像",
    "Phone Number": "电话号码",
    "My Contacts": "我的联系人",
    "Security": "安全",
    "Two-Step Verification": "两步验证",
    "Off": "关闭",
    "Active Sessions": "活跃会话",
    "3 devices": "3 台设备",
    "Blocked Users": "已屏蔽用户",
    "Data": "数据",
    "Delete Synced Contacts": "删除已同步联系人",
    "Delete Account": "删除账号",
    "Create folders for different groups of chats to easily access them.": "为不同类型的聊天创建文件夹，方便快速访问。",
    "Create New Folder": "创建新文件夹",
    "Team Chats": "团队聊天",
    "Demo": "演示",
    "Stickers & Emoji": "贴纸与表情",
    "Sticker Sets": "贴纸包",
    "0 installed": "已安装 0 个",
    "Suggest Emoji": "表情建议",
    "Replace text like :) with emoji": "将 :) 等文本替换为表情",
    "Custom Emoji": "自定义表情",
    "Devices currently logged into your account.": "当前登录此账号的设备。",
    "Windows / Chrome": "Windows / Chrome",
    "This browser / Active now / IP: not exposed": "此浏览器 / 当前活跃 / IP：前端不展示",
    "Session management API not connected": "会话管理接口未接入",
    "Only the current browser can be shown right now": "当前只能显示本浏览器",
    "Terminate": "终止",
    "Terminate All Other Sessions": "终止其它所有会话",
    "Language": "语言",
    "Search chats": "搜索聊天",
    "New chat": "新建聊天",
    "Toggle mute": "切换静音",
    "Send message": "发送消息",
    "New line": "换行",
    "Settings": "设置",
    "Edit Profile": "编辑资料",
    "Search": "搜索",
    "Data and Storage": "数据和存储",
    "Privacy and Security": "隐私和安全",
    "Devices and Shortcuts": "设备与快捷键"
  }
};

const literalTextTranslationsReverse = {
  en: Object.fromEntries(
    Object.entries(literalTextTranslations.zh).map(([en, zh]) => [zh, en])
  )
};

function applyLiteralTextTranslations() {
  const map = currentLanguage === 'zh'
    ? literalTextTranslations.zh
    : literalTextTranslationsReverse.en;
  const root = document.getElementById('sidebar-container');
  if (!root || !map) return;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || parent.closest('script, style, textarea, input')) {
        return NodeFilter.FILTER_REJECT;
      }
      const text = node.nodeValue.trim();
      return text && map[text] ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });

  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const original = node.nodeValue;
    const leading = original.match(/^\s*/)[0];
    const trailing = original.match(/\s*$/)[0];
    const translated = map[original.trim()];
    node.nodeValue = `${leading}${translated}${trailing}`;
  });
}

function applyLanguage() {
  const langDisplay = document.getElementById("lang-display-val");
  if (langDisplay) {
    langDisplay.textContent = currentLanguage === 'zh' ? '简体中文' : 'English';
  }

  // Translate all text content using data-i18n
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const key = el.getAttribute("data-i18n");
    if (translations[currentLanguage] && translations[currentLanguage][key]) {
      const icon = el.querySelector("i, svg");
      if (icon) {
        const iconClone = icon.cloneNode(true);
        el.innerHTML = "";
        el.appendChild(iconClone);
        el.appendChild(document.createTextNode(" " + translations[currentLanguage][key]));
      } else {
        el.textContent = translations[currentLanguage][key];
      }
    }
  });

  // Translate placeholder attributes using data-i18n-placeholder
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (translations[currentLanguage] && translations[currentLanguage][key]) {
      el.setAttribute("placeholder", translations[currentLanguage][key]);
    }
  });

  // Translate title attributes using data-i18n-title
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    const key = el.getAttribute("data-i18n-title");
    if (translations[currentLanguage] && translations[currentLanguage][key]) {
      el.setAttribute("title", translations[currentLanguage][key]);
    }
  });

  applyLiteralTextTranslations();

  // Re-render sidebar previews and selected chat UI
  if (activeChatId && conversationsById[activeChatId]) {
    const conv = conversationsById[activeChatId];
    selectChat(activeChatId.toString());
    updateDetailsPanel(conv);
  }

  // Also update self-destruct slider labels if they exist
  const destructSlider = document.querySelector('input[type="range"][oninput*="updateSelfDestructLabel"]');
  if (destructSlider) {
    if (typeof updateSelfDestructLabel === "function") {
      updateSelfDestructLabel(destructSlider.value);
    }
  }
}

window.toggleLanguage = function() {
  currentLanguage = currentLanguage === 'en' ? 'zh' : 'en';
  localStorage.setItem('ichat_lang', currentLanguage);
  applyLanguage();
};

function getStatusTranslation(status) {
  if (!status) return "";
  const statusStr = String(status).toLowerCase();
  if (currentLanguage === 'zh') {
    if (statusStr === 'online') return '在线';
    if (statusStr === 'offline') return '离线';
    const match = statusStr.match(/^(\d+)\s+members$/);
    if (match) {
      return `${match[1]} 位成员`;
    }
    return status;
  }
  return status;
}

function getRoleTranslation(role) {
  if (!role) return "";
  const roleStr = String(role).toLowerCase();
  if (currentLanguage === 'zh') {
    if (roleStr === 'creator') return '所有者';
    if (roleStr === 'admin') return '管理员';
    if (roleStr === 'member') return '普通成员';
    return role;
  }
  return role;
}

function getSystemMessageTranslation(text) {
  if (!text) return "";
  const trimmed = text.trim();
  if (currentLanguage === 'zh') {
    if (trimmed === 'Today') return '今天';
    if (trimmed === 'Yesterday') return '昨天';
    if (trimmed === 'Monday') return '星期一';
    if (trimmed === 'Tuesday') return '星期二';
    if (trimmed === 'Wednesday') return '星期三';
    if (trimmed === 'Thursday') return '星期四';
    if (trimmed === 'Friday') return '星期五';
    if (trimmed === 'Saturday') return '星期六';
    if (trimmed === 'Sunday') return '星期日';

    if (trimmed.includes("Channel secured with ECDH + HKDF")) {
      return "🔒 通道已通过 ECDH + HKDF 加密。零知识保护已启用。";
    }

    let match = trimmed.match(/^(.+?)\s+created group\s+\"(.+?)\"$/);
    if (match) {
      const creator = match[1] === "You" ? "你" : match[1];
      return `${creator} 创建了群组 "${match[2]}"`;
    }

    match = trimmed.match(/^(.+?)\s+added\s+(.+?)\s+to the group$/);
    if (match) {
      const adder = match[1] === "You" ? "你" : match[1];
      const addee = match[2] === "You" ? "你" : match[2];
      return `${adder} 将 ${addee} 添加到群组`;
    }

    match = trimmed.match(/^(.+?)\s+removed\s+(.+?)\s+from the group$/);
    if (match) {
      const remover = match[1] === "You" ? "你" : match[1];
      const removee = match[2] === "You" ? "你" : match[2];
      return `${remover} 将 ${removee} 移出了群组`;
    }

    const timeMatch = trimmed.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (timeMatch) {
      const period = timeMatch[3].toUpperCase() === 'AM' ? '上午' : '下午';
      return `${period} ${timeMatch[1]}:${timeMatch[2]}`;
    }
  }
  return text;
}

window.toggleMoreMenu = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  const btn = document.getElementById("chat-header-more-btn");
  if (!dropdown) return;
  
  const isHidden = dropdown.classList.contains("hidden");
  if (isHidden) {
    dropdown.classList.remove("hidden");
    btn.classList.add("active");
    if (window.lucide) {
      window.lucide.createIcons();
    }
  } else {
    dropdown.classList.add("hidden");
    btn.classList.remove("active");
  }
};

window.toggleMainMenu = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("main-menu-dropdown");
  const btn = document.getElementById("drawer-btn");
  if (!dropdown) return;
  
  const isHidden = dropdown.classList.contains("hidden");
  if (isHidden) {
    dropdown.classList.remove("hidden");
    btn.classList.add("active");
    if (window.lucide) {
      window.lucide.createIcons();
    }
  } else {
    dropdown.classList.add("hidden");
    btn.classList.remove("active");
    // Also close submenu if open
    const submenu = document.getElementById("main-menu-more-submenu");
    if (submenu) submenu.classList.add("hidden");
  }
};

window.toggleMoreSubmenu = function(e) {
  if (e) e.stopPropagation();
  const submenu = document.getElementById("main-menu-more-submenu");
  if (!submenu) return;
  submenu.classList.toggle("hidden");
  if (window.lucide) {
    window.lucide.createIcons();
  }
};

window.showToast = function(message) {
  const container = document.getElementById("toast-container");
  if (!container) return;
  
  const toast = document.createElement("div");
  toast.className = "px-4 py-2.5 bg-black/80 dark:bg-zinc-800/90 text-white text-xs font-semibold rounded-full shadow-lg backdrop-blur-md border border-white/10 animate-fadeIn pointer-events-auto transition-all duration-300 transform translate-y-0 opacity-100 flex items-center space-x-2";
  toast.innerHTML = `<i data-lucide="info" class="w-4 h-4 text-brand-light dark:text-brand-dark"></i><span>${escapeHtml(message)}</span>`;
  
  container.appendChild(toast);
  if (window.lucide) {
    window.lucide.createIcons();
  }
  
  setTimeout(() => {
    toast.classList.add("opacity-0", "translate-y-[-10px]");
    setTimeout(() => {
      toast.remove();
    }, 300);
  }, 2500);
};

window.triggerBoostGroupAction = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  const msg = currentLanguage === 'zh' ? "助力群组功能暂未开放" : "Boost Group feature is not yet available";
  window.showToast(msg);
};

window.triggerMuteAction = async function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  const chat = conversationsById[activeChatId];
  if (!chat) return;
  
  const nextMuted = !chat.isMuted;
  try {
    const response = await fetch(`/api/conversations/${activeChatId}/mute/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify({ mute: nextMuted })
    });
    if (!response.ok) {
      throw new Error('mute_endpoint_unavailable');
    }
    chat.isMuted = nextMuted;
  } catch (err) {
    window.showToast(currentLanguage === 'zh'
      ? '静音接口尚未接入，未保存更改'
      : 'Mute API is not available yet. No change was saved.');
    return;
  }
  
  // Update UI mute text and icons
  const muteTextEl = document.getElementById("menu-mute-group-text");
  const muteIconEl = document.getElementById("menu-mute-group-icon");
  if (muteTextEl) {
    if (chat.isMuted) {
      muteTextEl.setAttribute("data-i18n", "menu_unmute_group");
      muteTextEl.textContent = currentLanguage === 'zh' ? "取消静音" : "Unmute";
      if (muteIconEl) {
        muteIconEl.setAttribute("data-lucide", "bell");
      }
    } else {
      muteTextEl.setAttribute("data-i18n", "menu_mute_group");
      muteTextEl.textContent = currentLanguage === 'zh' ? "静音免打扰" : "Mute...";
      if (muteIconEl) {
        muteIconEl.setAttribute("data-lucide", "bell-off");
      }
    }
    if (window.lucide) window.lucide.createIcons();
  }
  
  const toastMsg = chat.isMuted 
    ? (currentLanguage === 'zh' ? "已开启群聊免打扰" : "Mute notifications enabled")
    : (currentLanguage === 'zh' ? "已取消群聊免打扰" : "Mute notifications disabled");
  window.showToast(toastMsg);
};

window.triggerSelectMessagesAction = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  isSelectingMessages = true;
  selectedMessageIds = [];
  
  // Toggle selection headers
  const headerNormal = document.getElementById("chat-header-normal");
  const headerSelect = document.getElementById("chat-header-select-mode");
  if (headerNormal && headerSelect) {
    headerNormal.classList.add("hidden");
    headerSelect.classList.remove("hidden");
    headerSelect.classList.add("flex");
  }
  
  // Toggle input footer
  const inputNormal = document.getElementById("chat-input-normal-wrapper");
  const inputSelect = document.getElementById("chat-input-select-mode-banner");
  if (inputNormal && inputSelect) {
    inputNormal.classList.add("hidden");
    inputSelect.classList.remove("hidden");
    inputSelect.classList.add("flex");
  }
  
  // Refresh bubbles to render checkbox
  renderMessages();
  window.updateSelectModeCount();
};

window.exitSelectMode = function() {
  isSelectingMessages = false;
  selectedMessageIds = [];
  
  const headerNormal = document.getElementById("chat-header-normal");
  const headerSelect = document.getElementById("chat-header-select-mode");
  if (headerNormal && headerSelect) {
    headerNormal.classList.remove("hidden");
    headerSelect.classList.add("hidden");
    headerSelect.classList.remove("flex");
  }
  
  const inputNormal = document.getElementById("chat-input-normal-wrapper");
  const inputSelect = document.getElementById("chat-input-select-mode-banner");
  if (inputNormal && inputSelect) {
    inputNormal.classList.remove("hidden");
    inputSelect.classList.add("hidden");
    inputSelect.classList.remove("flex");
  }
  
  renderMessages();
};

window.updateSelectModeCount = function() {
  const countEl = document.getElementById("select-mode-count");
  if (countEl) {
    const text = currentLanguage === 'zh' 
      ? `已选择 ${selectedMessageIds.length} 条消息`
      : `Selected ${selectedMessageIds.length} messages`;
    countEl.textContent = text;
  }
};

window.toggleMessageSelection = function(msgId) {
  const index = selectedMessageIds.indexOf(msgId);
  const checkboxEl = document.getElementById(`msg-select-check-${msgId}`);
  
  if (index === -1) {
    selectedMessageIds.push(msgId);
    if (checkboxEl) {
      checkboxEl.innerHTML = `<i data-lucide="check-circle-2" class="w-5 h-5 text-brand-light dark:text-brand-dark"></i>`;
    }
  } else {
    selectedMessageIds.splice(index, 1);
    if (checkboxEl) {
      checkboxEl.innerHTML = `<i data-lucide="circle" class="w-5 h-5 text-textSecondary"></i>`;
    }
  }
  
  if (window.lucide) {
    window.lucide.createIcons();
  }
  window.updateSelectModeCount();
};

window.triggerReportAction = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  const modal = document.getElementById("report-modal");
  if (modal) {
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  }
};

window.closeReportModal = function() {
  const modal = document.getElementById("report-modal");
  if (modal) {
    modal.classList.remove("flex");
    modal.classList.add("hidden");
  }
};

window.submitReport = async function() {
  const selectedReason = document.querySelector('input[name="report-reason"]:checked');
  const reason = selectedReason ? selectedReason.value : "other";
  
  try {
    const response = await fetch(`/api/reports/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify({
        conversation_id: activeChatId,
        reason: reason
      })
    });
    if (!response.ok) {
      throw new Error('report_endpoint_unavailable');
    }
  } catch (err) {
    window.showToast(currentLanguage === 'zh'
      ? '举报接口尚未接入，未提交'
      : 'Report API is not available yet. Nothing was submitted.');
    return;
  }
  
  window.closeReportModal();
  const toastMsg = currentLanguage === 'zh' ? "举报已提交" : "Report has been submitted";
  window.showToast(toastMsg);
};

window.triggerDeleteChatAction = function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  const modal = document.getElementById("delete-conversation-modal");
  if (modal) {
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  }
};

window.closeDeleteConfirmModal = function() {
  const modal = document.getElementById("delete-conversation-modal");
  if (modal) {
    modal.classList.remove("flex");
    modal.classList.add("hidden");
  }
};

window.confirmDeleteChat = async function() {
  if (!activeChatId) return;
  
  const chatIdToDelete = activeChatId;
  
  try {
    const response = await fetch(`/api/conversations/${chatIdToDelete}/hide/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      }
    });
    if (!response.ok) {
      throw new Error('hide_endpoint_unavailable');
    }
  } catch (err) {
    window.closeDeleteConfirmModal();
    window.showToast(currentLanguage === 'zh'
      ? '删除/隐藏会话接口尚未接入，未移除会话'
      : 'Delete/hide conversation API is not available yet. No chat was removed.');
    return;
  }
  
  // Local deletion
  conversations = conversations.filter(c => c.id !== chatIdToDelete); conversationsById = {}; conversations.forEach(c => { conversationsById[c.id] = c; });
    
  window.closeDeleteConfirmModal();
  
  // Reset active chat ID
  activeChatId = null;
  
  // Reload sidebar
  renderChatList();
  
  // Reset UI
  const emptyState = document.getElementById("empty-state-window");
  const activeChatWindow = document.getElementById("active-chat-window");
  if (emptyState) emptyState.classList.remove("hidden");
  if (activeChatWindow) activeChatWindow.classList.add("hidden");
  
  // Close details panel
  const rightDetailsPanel = document.getElementById("right-panel");
  if (rightDetailsPanel) rightDetailsPanel.classList.add("collapsed");
  
  const toastMsg = currentLanguage === 'zh' ? "会话已删除" : "Conversation deleted";
  window.showToast(toastMsg);
};

window.showLogoutConfirmModal = function(e) {
  if (e) e.stopPropagation();
  // Close main dropdown if open
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) mainDropdown.classList.add("hidden");
  if (mainBtn) mainBtn.classList.remove("active");
  
  const modal = document.getElementById("logout-confirm-modal");
  if (modal) {
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  }
};

window.closeLogoutConfirmModal = function() {
  const modal = document.getElementById("logout-confirm-modal");
  if (modal) {
    modal.classList.remove("flex");
    modal.classList.add("hidden");
  }
};

window.triggerMyProfileFromInfo = function(e) {
  if (e) e.stopPropagation();
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) mainDropdown.classList.add("hidden");
  if (mainBtn) mainBtn.classList.remove("active");
  
  showSettingsPanel();
};

window.showAboutInfo = function(e) {
  if (e) e.stopPropagation();
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) mainDropdown.classList.add("hidden");
  if (mainBtn) mainBtn.classList.remove("active");
  
  const msg = currentLanguage === 'zh' 
    ? "关于 iChat Pro：端到端安全加密聊天客户端 v1.0.0" 
    : "About iChat Pro: Secure E2EE Chat Client v1.0.0";
  window.showToast(msg);
};

window.showHelpFeedback = function(e) {
  if (e) e.stopPropagation();
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) mainDropdown.classList.add("hidden");
  if (mainBtn) mainBtn.classList.remove("active");
  
  const msg = currentLanguage === 'zh' 
    ? "关于 iChat Pro 帮助：当前暂无可用的在线文档" 
    : "Help guide is not yet available";
  window.showToast(msg);
};

window.checkForUpdates = function(e) {
  if (e) e.stopPropagation();
  const mainDropdown = document.getElementById("main-menu-dropdown");
  const mainBtn = document.getElementById("drawer-btn");
  if (mainDropdown) mainDropdown.classList.add("hidden");
  if (mainBtn) mainBtn.classList.remove("active");
  
  const msg = currentLanguage === 'zh' ? "当前已是最新版本" : "Already the latest version";
  window.showToast(msg);
};

// ============================================================================
// P2 T06: Privacy & Security Settings
// ============================================================================

var _privacySettingsCache = null;

var _visibilityLabelMap = {
  'everyone': 'Everyone',
  'contacts': 'My Contacts',
  'nobody': 'Nobody'
};

var _visibilityLabelMapZh = {
  'everyone': '所有人',
  'contacts': '我的联系人',
  'nobody': '无人'
};

var _autoDeleteLabelMap = {
  0: 'Off',
  1: '1 Day',
  7: '7 Days',
  30: '30 Days'
};

var _autoDeleteLabelMapZh = {
  0: '关闭',
  1: '1 天',
  7: '7 天',
  30: '30 天'
};

function _privacyVisLabel(value) {
  var map = currentLanguage === 'zh' ? _visibilityLabelMapZh : _visibilityLabelMap;
  return map[value] || value;
}

function _privacyBoolLabel(value) {
  if (currentLanguage === 'zh') {
    return value ? '开启' : '关闭';
  }
  return value ? 'On' : 'Off';
}

async function loadPrivacySettings() {
  try {
    var data = await apiFetch('/api/privacy/settings/');
    _privacySettingsCache = data.settings;

    // Update visibility labels
    ['last_seen_visibility', 'profile_photo_visibility', 'phone_number_visibility',
     'bio_visibility', 'forward_link_visibility', 'who_can_send_messages',
     'who_can_voice_video_call'].forEach(function(key) {
      var el = document.getElementById('privacy-label-' + key);
      if (el && _privacySettingsCache[key] !== undefined) {
        el.textContent = _privacyVisLabel(_privacySettingsCache[key]);
      }
    });

    // Update boolean toggles
    ['two_step_verification_enabled', 'passcode_lock_enabled',
     'sensitive_content_filter'].forEach(function(key) {
      var el = document.getElementById('privacy-label-' + key);
      if (el && _privacySettingsCache[key] !== undefined) {
        el.textContent = _privacyBoolLabel(_privacySettingsCache[key]);
      }
    });

    // Update login email input
    var emailInput = document.getElementById('privacy-input-login_email');
    if (emailInput && _privacySettingsCache.login_email !== undefined) {
      emailInput.value = _privacySettingsCache.login_email || '';
    }

    // Update auto-delete
    var autoDeleteEl = document.getElementById('privacy-label-auto_delete_messages_days');
    if (autoDeleteEl && _privacySettingsCache.auto_delete_messages_days !== undefined) {
      var days = _privacySettingsCache.auto_delete_messages_days;
      var labelMap = currentLanguage === 'zh' ? _autoDeleteLabelMapZh : _autoDeleteLabelMap;
      autoDeleteEl.textContent = labelMap[days] || (days + ' days');
    }

    // Load blocked users count
    loadBlockedUsersCount();
  } catch (err) {
    console.error('Failed to load privacy settings:', err);
    window.showToast(currentLanguage === 'zh'
      ? '加载隐私设置失败'
      : 'Failed to load privacy settings');
  }
}

var _P2_T28_PLACEHOLDER_FIELDS = [
  'passcode_lock_enabled',
  'two_step_verification_enabled',
  'login_email',
];

async function savePrivacySetting(key, value) {
  // T28 placeholder fields — not yet implemented server-side
  if (_P2_T28_PLACEHOLDER_FIELDS.indexOf(key) >= 0) {
    window.showToast(currentLanguage === 'zh'
      ? '此功能尚未开放 (P2 T28)'
      : 'This feature is not yet available (P2 T28)');
    return;
  }
  var payload = {};
  payload[key] = value;
  return savePrivacySettings(payload);
}

async function savePrivacySettings(settings) {
  try {
    var data = await apiFetch('/api/privacy/settings/', {
      method: 'POST',
      body: JSON.stringify(settings)
    });
    _privacySettingsCache = data.settings;
    // Reload UI to reflect changes
    loadPrivacySettings();
    var lang = currentLanguage;
    window.showToast(lang === 'zh' ? '隐私设置已保存' : 'Privacy settings saved');
  } catch (err) {
    console.error('Failed to save privacy settings:', err);
    window.showToast(currentLanguage === 'zh'
      ? '保存隐私设置失败'
      : 'Failed to save privacy settings');
  }
}

// ── Visibility Picker (bottom sheet) ──

var _visibilityPickerKey = null;
var _visibilityPickerIsPermission = false;

function showVisibilityPicker(rowEl, key, isPermission) {
  _visibilityPickerKey = key;
  _visibilityPickerIsPermission = !!isPermission;

  var picker = document.getElementById('privacy-visibility-picker');
  if (!picker) return;
  picker.classList.remove('hidden');

  var title = document.getElementById('privacy-picker-title');
  var optionsEl = document.getElementById('privacy-picker-options');
  if (!title || !optionsEl) return;

  // Set title based on the row's text
  var rowTitle = rowEl.querySelector('.text-sm') || rowEl.querySelector('.font-medium');
  if (title && rowTitle) {
    title.textContent = rowTitle.textContent.trim();
  }

  // Determine which options to show
  var options;
  if (_visibilityPickerIsPermission) {
    options = [
      { value: 'everyone', label: _visibilityLabelMap['everyone'], labelZh: _visibilityLabelMapZh['everyone'] },
      { value: 'contacts', label: _visibilityLabelMap['contacts'], labelZh: _visibilityLabelMapZh['contacts'] },
    ];
  } else {
    options = [
      { value: 'everyone', label: _visibilityLabelMap['everyone'], labelZh: _visibilityLabelMapZh['everyone'] },
      { value: 'contacts', label: _visibilityLabelMap['contacts'], labelZh: _visibilityLabelMapZh['contacts'] },
      { value: 'nobody', label: _visibilityLabelMap['nobody'], labelZh: _visibilityLabelMapZh['nobody'] },
    ];
  }

  var currentValue = _privacySettingsCache ? _privacySettingsCache[key] : null;

  var html = '';
  options.forEach(function(opt) {
    var isSelected = currentValue === opt.value;
    var label = currentLanguage === 'zh' ? opt.labelZh : opt.label;
    html += '<button onclick="selectVisibilityOption(\'' + opt.value + '\')" class="w-full py-3 px-4 text-left text-sm text-textMain hover:bg-bgSearch rounded-custom-md transition-colors flex items-center justify-between">';
    html += '<span>' + label + '</span>';
    html += '<i data-lucide="check" class="w-4 h-4 text-brand-light' + (isSelected ? '' : ' hidden') + '"></i>';
    html += '</button>';
  });

  optionsEl.innerHTML = html;
  if (window.lucide) setTimeout(function() { lucide.createIcons(); }, 50);
}

function selectVisibilityOption(value) {
  if (_visibilityPickerKey) {
    savePrivacySetting(_visibilityPickerKey, value);
  }
  closeVisibilityPicker();
}

function closeVisibilityPicker() {
  var picker = document.getElementById('privacy-visibility-picker');
  if (picker) picker.classList.add('hidden');
  _visibilityPickerKey = null;
}

// ── Auto-Delete Picker ──

function showAutoDeletePicker(rowEl) {
  var picker = document.getElementById('privacy-autodelete-picker');
  if (!picker) return;
  picker.classList.remove('hidden');

  // Highlight current value
  var currentDays = _privacySettingsCache ? _privacySettingsCache.auto_delete_messages_days : 0;
  [0, 1, 7, 30].forEach(function(d) {
    var check = document.getElementById('autodelete-check-' + d);
    if (check) {
      if (d === currentDays) {
        check.classList.remove('hidden');
      } else {
        check.classList.add('hidden');
      }
    }
  });
  if (window.lucide) setTimeout(function() { lucide.createIcons(); }, 50);
}

function closeAutoDeletePicker() {
  var picker = document.getElementById('privacy-autodelete-picker');
  if (picker) picker.classList.add('hidden');
}

// ── Boolean toggle ──

async function togglePrivacySwitch(key) {
  if (!_privacySettingsCache) return;
  var currentValue = _privacySettingsCache[key];
  var newValue = !currentValue;
  await savePrivacySetting(key, newValue);
}

// ── Blocked Users ──

async function loadBlockedUsersCount() {
  try {
    var data = await apiFetch('/api/privacy/blocked/');
    var count = data.blocked_users ? data.blocked_users.length : 0;
    var el = document.getElementById('privacy-blocked-count');
    if (el) el.textContent = String(count);
    _blockedUsersCache = data.blocked_users || [];
  } catch (err) {
    console.error('Failed to load blocked users:', err);
  }
}

var _blockedUsersCache = [];

async function openBlockedUsersList() {
  var modal = document.getElementById('privacy-blocked-modal');
  if (!modal) return;

  try {
    var data = await apiFetch('/api/privacy/blocked/');
    _blockedUsersCache = data.blocked_users || [];

    var listEl = document.getElementById('privacy-blocked-list');
    if (!listEl) return;

    if (_blockedUsersCache.length === 0) {
      var emptyMsg = currentLanguage === 'zh' ? '没有被屏蔽的用户' : 'No blocked users';
      listEl.innerHTML = '<p class="text-sm text-textSecondary text-center py-8">' + emptyMsg + '</p>';
    } else {
      var html = '<div class="space-y-2">';
      _blockedUsersCache.forEach(function(user) {
        var displayName = user.nickname || user.username;
        html += '<div class="flex items-center justify-between py-2 px-2 hover:bg-bgSearch/50 rounded-custom-md">';
        html += '<div class="flex items-center space-x-3">';
        html += '<div class="w-9 h-9 rounded-full bg-brand-light dark:bg-brand-dark text-white flex items-center justify-center font-bold text-sm">' + (displayName[0] || '?').toUpperCase() + '</div>';
        html += '<div>';
        html += '<div class="text-sm font-medium text-textMain">' + escapeHtml(displayName) + '</div>';
        html += '<div class="text-[10px] text-textSecondary">@' + escapeHtml(user.username) + '</div>';
        html += '</div></div>';
        html += '<button onclick="unblockUser(' + user.id + ')" class="px-3 py-1.5 text-xs font-semibold text-red-500 hover:bg-red-500/10 rounded-custom-md transition-colors">';
        html += (currentLanguage === 'zh' ? '解除屏蔽' : 'Unblock');
        html += '</button></div>';
      });
      html += '</div>';
      listEl.innerHTML = html;
    }

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (window.lucide) setTimeout(function() { lucide.createIcons(); }, 50);

    // Update count
    var countEl = document.getElementById('privacy-blocked-count');
    if (countEl) countEl.textContent = String(_blockedUsersCache.length);
  } catch (err) {
    console.error('Failed to load blocked users:', err);
    window.showToast(currentLanguage === 'zh'
      ? '加载已屏蔽用户失败'
      : 'Failed to load blocked users');
  }
}

function closeBlockedUsersList() {
  var modal = document.getElementById('privacy-blocked-modal');
  if (modal) {
    modal.classList.remove('flex');
    modal.classList.add('hidden');
  }
}

async function unblockUser(userId) {
  try {
    await apiFetch('/api/privacy/unblock/', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId })
    });
    window.showToast(currentLanguage === 'zh'
      ? '已解除屏蔽'
      : 'User unblocked');
    // Refresh the list
    openBlockedUsersList();
    loadBlockedUsersCount();
  } catch (err) {
    console.error('Failed to unblock user:', err);
    window.showToast(currentLanguage === 'zh'
      ? '解除屏蔽失败'
      : 'Failed to unblock user');
  }
}

// ── Delete Synced Contacts ──

function deleteSyncedContacts() {
  var title = currentLanguage === 'zh' ? '删除同步联系人' : 'Delete Synced Contacts';
  var desc = currentLanguage === 'zh'
    ? '确定要删除所有同步的联系人吗？此操作不可撤销。'
    : 'Are you sure you want to delete all synced contacts? This cannot be undone.';
  showPrivacyConfirmModal(title, desc, async function() {
    try {
      var data = await apiFetch('/api/privacy/delete-contacts/', { method: 'POST' });
      window.showToast(currentLanguage === 'zh'
        ? '已删除 ' + data.deleted_count + ' 个联系人'
        : 'Deleted ' + data.deleted_count + ' contacts');
      closePrivacyConfirmModal();
    } catch (err) {
      console.error('Failed to delete contacts:', err);
      window.showToast(currentLanguage === 'zh'
        ? '删除联系人失败'
        : 'Failed to delete contacts');
    }
  });
}

// ── Delete Account ──

function deleteAccount() {
  var title = currentLanguage === 'zh' ? '删除账号' : 'Delete Account';
  var desc = currentLanguage === 'zh'
    ? '确定要永久删除您的账号吗？所有数据将被清除，此操作不可撤销。'
    : 'Are you sure you want to permanently delete your account? All data will be lost. This cannot be undone.';
  showPrivacyConfirmModal(title, desc, async function() {
    try {
      await apiFetch('/api/privacy/delete-account/', { method: 'POST' });
      window.location.href = '/accounts/login/';
    } catch (err) {
      console.error('Failed to delete account:', err);
      window.showToast(currentLanguage === 'zh'
        ? '删除账号失败'
        : 'Failed to delete account');
    }
  });
}

// ── Generic Confirm Modal ──

var _privacyConfirmCallback = null;

function showPrivacyConfirmModal(title, desc, callback) {
  var modal = document.getElementById('privacy-confirm-modal');
  if (!modal) return;
  document.getElementById('privacy-confirm-title').textContent = title;
  document.getElementById('privacy-confirm-desc').textContent = desc;
  _privacyConfirmCallback = callback;
  var btn = document.getElementById('privacy-confirm-btn');
  if (btn) {
    btn.onclick = function() {
      if (_privacyConfirmCallback) _privacyConfirmCallback();
    };
  }
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closePrivacyConfirmModal() {
  var modal = document.getElementById('privacy-confirm-modal');
  if (modal) {
    modal.classList.remove('flex');
    modal.classList.add('hidden');
  }
  _privacyConfirmCallback = null;
}

// Helper function to extract cookies (e.g. csrftoken for Django)

