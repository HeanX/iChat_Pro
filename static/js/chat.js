// iChat Pro - Client-side Encrypted Chat Engine
// Vanilla JavaScript utilizing Web Crypto API for ECDH + HKDF + AES-GCM
// Connects to real backend API, WebSocket, and E2EE modules

// Global State — populated from backend APIs
let conversations = [];          // Array of conversation objects from GET /api/conversations/
let conversationsById = {};      // ID → conversation lookup map
let activeChatId = null;
let activeSpecialChatId = null;
let activeAiAssistantId = 'ai-assistant';
let currentLanguage = localStorage.getItem('ichat_lang') || (function() {
  try { return JSON.parse(localStorage.getItem('ichat_sessions') || '{}').uiLang; } catch(e) { return null; }
})() || 'en';
// 4-language inline helper: _t4(en, zh, zhTW, ja)
function _t4(en, zh, zhTW, ja) { return {en, zh, 'zh-TW': zhTW, ja}[currentLanguage] || en; }
let isSelectingMessages = false;
let selectedMessageIds = [];
let messages = [];               // Decrypted messages for the currently active conversation
let messagePage = 1;
let hasMoreMessages = false;
let isLoadingMessages = false;
let currentUserProfile = { username: "", initials: "", avatarUrl: "", avatarColor: "" };
let sessionKeys = {};            // Cache: conversationId → derived CryptoKey
let myUserId = null;             // Current authenticated user PK
let wsClient = null;             // v1 /ws/chat/ client
let e2eeKeyReady = true;
let e2eeKeyError = null;
let groupMembersByConversation = {};
let fingerprintCacheByUserId = {};
let contactKeyStatusCacheByUserId = {};
let keyTrustListCache = null;
let detailsPanelRequestId = 0;
let currentSearchTab = 'chats';     // Active search type tab
let searchDebounceTimer = null;    // Debounce timer for search input
let chatSearchResults = [];
let chatSearchIndex = -1;
let replyToMessage = null;          // { id, sender_name, text_preview } for reply quoting
let typingUsers = {};              // Map: conversationId → { userId, timeoutId }
let connectionStatus = 'connecting'; // 'connected' | 'connecting' | 'disconnected'
const AUTO_IMAGE_PREVIEW_LIMIT_BYTES = 10 * 1024 * 1024;
const filePreviewCache = new Map();
let activeImageViewer = null;

// Expose state to window for cross-module access (T12/T13/T14 modules)
// — readable via getters that always return the current value
// — writable where new modules need to update state (clear/delete/reset)
Object.defineProperty(window, 'conversations', {
  get() { return conversations; },
  set(v) { conversations = v; },
  enumerable: true, configurable: true
});
Object.defineProperty(window, 'conversationsById', {
  get() { return conversationsById; },
  set(v) { conversationsById = v; },
  enumerable: true, configurable: true
});
Object.defineProperty(window, 'activeChatId', {
  get() { return activeChatId; },
  set(v) { activeChatId = v; },
  enumerable: true, configurable: true
});
Object.defineProperty(window, 'messages', {
  get() { return messages; },
  set(v) { messages = v; },
  enumerable: true, configurable: true
});
Object.defineProperty(window, 'myUserId', { get() { return myUserId; }, enumerable: true, configurable: true });
Object.defineProperty(window, 'currentLanguage', { get() { return currentLanguage; }, enumerable: true, configurable: true });
Object.defineProperty(window, 'isSelectingMessages', { get() { return isSelectingMessages; }, enumerable: true, configurable: true });
Object.defineProperty(window, 'selectedMessageIds', { get() { return selectedMessageIds; }, enumerable: true, configurable: true });
Object.defineProperty(window, 'wsClient', { get() { return wsClient; }, enumerable: true, configurable: true });
Object.defineProperty(window, 'replyToMessage', {
  get() { return replyToMessage; },
  set(v) { replyToMessage = v; },
  enumerable: true, configurable: true
});
Object.defineProperty(window, 'typingUsers', { get() { return typingUsers; }, enumerable: true, configurable: true });

function formatClockTime(date = new Date()) {
  let timeFormat = '24h';
  try {
    const saved = JSON.parse(localStorage.getItem('ichat_general') || '{}');
    if (saved.time_format) {
      timeFormat = saved.time_format;
    }
  } catch (e) {}

  let hours = date.getHours();
  const minutes = String(date.getMinutes()).padStart(2, '0');

  if (timeFormat === '12h') {
    const isPM = hours >= 12;
    hours = hours % 12;
    hours = hours ? hours : 12; // the hour '0' should be '12'
    const hoursStr = String(hours).padStart(2, '0');
    if (typeof currentLanguage !== 'undefined' && currentLanguage === 'zh') {
      const zhAmpm = isPM ? '下午' : '上午';
      return `${zhAmpm} ${hoursStr}:${minutes}`;
    } else {
      const ampm = isPM ? 'PM' : 'AM';
      return `${hoursStr}:${minutes} ${ampm}`;
    }
  } else {
    const hoursStr = String(hours).padStart(2, '0');
    return `${hoursStr}:${minutes}`;
  }
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

const CHAT_DRAFTS_STORAGE_KEY = "ichat_drafts";

function readChatDrafts() {
  try {
    const raw = localStorage.getItem(CHAT_DRAFTS_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function writeChatDrafts(drafts) {
  try {
    localStorage.setItem(CHAT_DRAFTS_STORAGE_KEY, JSON.stringify(drafts || {}));
  } catch (_) {}
}

function getConversationDraft(conversationId) {
  const drafts = readChatDrafts();
  return drafts[String(conversationId)] || "";
}

function setConversationDraft(conversationId, text) {
  if (!conversationId) return;
  const drafts = readChatDrafts();
  const key = String(conversationId);
  const value = String(text || "");
  if (value.trim()) {
    drafts[key] = value;
  } else {
    delete drafts[key];
  }
  writeChatDrafts(drafts);
}

function clearConversationDraft(conversationId) {
  if (!conversationId) return;
  const drafts = readChatDrafts();
  delete drafts[String(conversationId)];
  writeChatDrafts(drafts);
}

function formatDraftPreview(text) {
  return String(text || "").replace(/\s*\r?\n\s*/g, " ").trim();
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

function isPositiveIntegerValue(value) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0;
}

function canDecryptMessagePayload(payload, conversationType) {
  if (!payload || payload.message_type === 'system') return true;
  if (!payload.algorithm || !payload.ciphertext || !payload.nonce || !payload.auth_tag) {
    return false;
  }
  const required = [
    payload.sender_id,
    payload.receiver_id,
    payload.sender_key_version,
    payload.receiver_key_version,
  ];
  if (conversationType === 'group') {
    required.push(payload.membership_version);
  }
  return required.every(isPositiveIntegerValue);
}

function getMessageTypeLabel(messageType) {
  const labels = {
    image: { zh: "\u56fe\u7247", en: "Image" },
    sticker: { zh: "\u8868\u60c5", en: "Sticker" },
    file: { zh: "\u6587\u4ef6", en: "File" },
    text: { zh: "\u6d88\u606f", en: "Message" }
  };
  const label = labels[messageType] || labels.text;
  return currentLanguage === "zh" ? label.zh : label.en;
}

function getMessageTypePlaceholder(messageType) {
  return "[" + getMessageTypeLabel(messageType) + "]";
}

function isDecryptFailureText(text) {
  const value = String(text || "").trim();
  return /^\[(Cannot decrypt|\u65e0\u6cd5\u89e3\u5bc6)[\s:：]/i.test(value);
}

function getMessageReplyPreviewText(msg) {
  if (!msg) return "";
  const text = getSearchableMessageText(msg).replace(/\s+/g, " ").trim();
  const messageType = msg.message_type || (msg.isFile || msg.file || msg.file_id ? "file" : "text");
  const placeholderTexts = ["[image]", "[file]", "[sticker]", "[Image]", "[File]", "[Sticker]"];
  if (text && !isDecryptFailureText(text) && placeholderTexts.indexOf(text) === -1) {
    return text;
  }
  if (msg.isFile || msg.file || msg.file_id || ["image", "file", "sticker"].indexOf(messageType) !== -1) {
    return getMessageTypePlaceholder(messageType);
  }
  return text && !isDecryptFailureText(text) ? text : getMessageTypeLabel("text");
}
window.getMessageReplyPreviewText = getMessageReplyPreviewText;

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
  getAiAssistantSessions().forEach(function(session) {
    appendChatItemToSidebar(getAiConversationListItem(session.id));
  });
  conversations.forEach(conv => {
    appendChatItemToSidebar(conv);
  });
}

function renderDraftPreview(text) {
  const markdownPayload = getMarkdownPayload(text);
  const draftText = markdownPayload === null
    ? escapeHtml(formatDraftPreview(text))
    : renderInlineMarkdown(formatDraftPreview(markdownPayload));
  return `<span class="telegram-chat-draft-label">草稿:</span> ${draftText}`;
}

function renderConversationListPreview(conv) {
  const draft = getConversationDraft(conv.id);
  if (draft.trim()) {
    return renderDraftPreview(draft);
  }
  return renderSidebarPreview(conv, conv.last_message_preview || "", conv.last_message_sender_id);
}

function refreshConversationDraftPreview(conversationId) {
  const conv = conversationsById[conversationId];
  if (!conv) return;
  const lastMsgEl = document.getElementById(`last-msg-${conversationId}`);
  if (lastMsgEl) {
    lastMsgEl.innerHTML = renderConversationListPreview(conv);
  }
}

function saveActiveConversationDraftFromInput() {
  if (!activeChatId) return;
  const textarea = document.getElementById("chat-input-textarea");
  if (!textarea || textarea.disabled) return;
  setConversationDraft(activeChatId, textarea.value);
  refreshConversationDraftPreview(activeChatId);
}

function restoreDraftForActiveConversation() {
  if (!activeChatId) return;
  const textarea = document.getElementById("chat-input-textarea");
  if (!textarea || textarea.disabled) return;
  textarea.value = getConversationDraft(activeChatId);
  adjustTextareaHeight(textarea);
  updateMarkdownPreview();
}

function appendChatItemToSidebar(conv) {
  const chatListContainer = document.getElementById("sidebar-chat-list");
  if (!chatListContainer) return;

  const wrapper = document.createElement("div");
  wrapper.id = `chat-item-wrapper-${conv.id}`;
  wrapper.className = "w-full";

  const lastMsgTime = conv.last_message_at ? formatClockTime(new Date(conv.last_message_at)) : '';
  const unreadCount = Number(conv.unread || 0);
  const safeId = encodeURIComponent(String(conv.id));
  const safeName = escapeHtml(conv.name || 'Unknown');
  const safeInitials = escapeHtml(conv.initials || '??');
  const safeLastMsg = renderConversationListPreview(conv);
  const safeLastTime = escapeHtml(lastMsgTime);
  const safeUnread = escapeHtml(unreadCount);
  const safeAvatarColor = /^#[0-9a-fA-F]{6}$/.test(conv.avatar_color || '') ? conv.avatar_color : '#5c6bc0';
  var avatarInner = conv.avatar_url
    ? `<img src="${escapeHtml(conv.avatar_url)}" class="${conv.avatar_fit === 'contain' ? 'ai-model-logo-img' : 'w-full h-full object-cover rounded-full'}">`
    : safeInitials;
  var avatarBgStyle = conv.avatar_url
    ? (conv.avatar_fit === 'contain' ? 'background-color: #ffffff' : 'background-color: transparent')
    : `background-color: ${safeAvatarColor}`;
  var avatarExtraClass = conv.avatar_fit === 'contain' ? ' ai-model-avatar flex items-center justify-center' : '';

  wrapper.innerHTML = `
    <button id="chat-item-${safeId}" onclick="${conv.is_ai_assistant ? `openAiAssistant('${safeId}')` : `selectChat('${safeId}')`}"
      class="chat-item-btn telegram-chat-item w-full text-left focus:outline-none relative group select-none ${conv.is_ai_assistant && activeSpecialChatId === conv.id ? 'active' : ''}">

      <div class="telegram-chat-avatar-wrap">
        <div class="telegram-chat-avatar${avatarExtraClass}" style="${avatarBgStyle}; overflow: hidden;">
          ${avatarInner}
        </div>
      </div>

      <div class="telegram-chat-content">
        <div class="telegram-chat-topline">
          <h3 class="telegram-chat-title">
            <span>${safeName}</span>
            ${conv.is_ai_assistant ? `<span class="user-role-badge badge-agent">${currentLanguage === 'zh' ? '智能助理' : 'Assistant'}</span>` : ''}
            ${conv.peer_user_type === 'agent' ? `<span class="user-role-badge badge-agent">${currentLanguage === 'zh' ? '智能代理' : 'Agent'}</span>` : ''}
            ${conv.peer_user_type === 'bot' ? `<span class="user-role-badge badge-bot">${currentLanguage === 'zh' ? '机器人' : 'Bot'}</span>` : ''}
            ${conv.is_secure ? '<i data-lucide="lock" class="w-3.5 h-3.5 text-brand-light dark:text-brand-dark inline-block flex-shrink-0" title="End-to-End Encrypted" data-i18n-title="e2ee_badge"></i>' : ''}
          </h3>
          <span id="chat-time-${safeId}" class="chat-item-time telegram-chat-time">${safeLastTime}</span>
        </div>

        <div class="telegram-chat-bottomline">
          <p id="last-msg-${safeId}" class="telegram-chat-preview">
            ${safeLastMsg}
          </p>

          <span id="unread-badge-${safeId}" class="${unreadCount > 0 ? "" : "hidden"} unread-badge telegram-chat-unread">
            ${safeUnread}
          </span>
        </div>
      </div>
    </button>
  `;

  chatListContainer.appendChild(wrapper);
  lucide.createIcons();

  // Wire right-click context menu on this conversation item
  const btn = wrapper.querySelector('.chat-item-btn');
  if (btn) {
    btn.addEventListener('contextmenu', function(e) {
      e.preventDefault();
      e.stopPropagation();
      if (conv.is_ai_assistant) {
        showAiAssistantConversationMenu(e, conv);
        return;
      }
      if (typeof ConversationActions !== 'undefined' && ConversationActions.showMenu) {
        ConversationActions.showMenu(e, conv);
      }
    });
  }

  // Add avatar class for online indicator
  const avatarDiv = wrapper.querySelector('.w-12.h-12.rounded-full');
  if (avatarDiv) {
    avatarDiv.classList.add('avatar');
    // Check if peer is online (for private chats)
    if (conv.type === 'single' && conv.is_online) {
      avatarDiv.classList.add('online');
    }
  }

  // Add status icons (pin, mute) after insertion
  if (typeof ConversationActions !== 'undefined' && ConversationActions.updateStatusIcons) {
    ConversationActions.updateStatusIcons(conv);
  }
}

function formatSidebarPreviewText(conv, text, senderId) {
  const previewText = text || '';
  const numericSenderId = senderId == null ? null : Number(senderId);
  if (!previewText || !numericSenderId || numericSenderId === Number(myUserId)) {
    return previewText;
  }
  return `${numericSenderId}：${previewText}`;
}

function renderSidebarPreviewMarkdown(text) {
  const singleLine = String(text || '').replace(/\s*\r?\n\s*/g, ' ').trim();
  const markdownPayload = getMarkdownPayload(singleLine);
  if (markdownPayload !== null) {
    return renderInlineMarkdown(markdownPayload);
  }
  return escapeHtml(singleLine);
}

function formatSidebarPreviewText(conv, text, senderId) {
  const previewText = text || '';
  const numericSenderId = senderId == null ? null : Number(senderId);
  if (!previewText || !numericSenderId || numericSenderId === Number(myUserId)) {
    return previewText;
  }
  return `${numericSenderId}: ${previewText}`;
}

function renderSidebarPreview(conv, text, senderId) {
  const previewText = text || '';
  const numericSenderId = senderId == null ? null : Number(senderId);
  const previewHtml = renderSidebarPreviewMarkdown(previewText);
  if (!previewText || !numericSenderId || numericSenderId === Number(myUserId)) {
    return previewHtml;
  }

  let senderLabel = null;
  if (conv) {
    if (conv.type === 'single') {
      senderLabel = conv.name || null;
    } else {
      const member = getGroupMemberInfo(conv.id, numericSenderId);
      senderLabel = member ? (member.display_name || member.username) : null;
    }
  }
  const prefix = senderLabel || String(numericSenderId);
  return escapeHtml(`${prefix}: `) + previewHtml;
}

function updateSidebarPreview(conv, text, time, senderId) {
  if (!conv) return;
  conv.last_message_preview = text || "";
  conv.last_message_sender_id = senderId;
  const lastMsgEl = document.getElementById(`last-msg-${conv.id}`);
  const timeEl = document.getElementById(`chat-time-${conv.id}`);
  if (lastMsgEl) lastMsgEl.innerHTML = renderConversationListPreview(conv);
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
    // Decrypt last message preview for each conversation client-side
    for (const conv of conversations) {
      if (conv.last_message_data) {
        try {
          let plaintext = '';
          const msg = conv.last_message_data;
          const isFileMsg = msg.file_id || (msg.file && msg.file.file_id);
          if (msg.message_type === 'system') {
            plaintext = msg.ciphertext;
          } else if (isFileMsg && !canDecryptMessagePayload(msg, conv.type)) {
            plaintext = '[' + (msg.message_type || 'file') + ']';
          } else if (!canDecryptMessagePayload(msg, conv.type)) {
            plaintext = 'Encrypted message';
          } else if (conv.type === 'group') {
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
              conversation_id: conv.id,
              sender_id: msg.sender_id,
              receiver_id: msg.receiver_id,
              sender_key_version: msg.sender_key_version,
              receiver_key_version: msg.receiver_key_version,
            });
          }
          conv.last_message_preview = plaintext;
          conv.last_message_sender_id = msg.sender_id;
        } catch (decryptErr) {
          console.warn(`Failed to decrypt last message preview for conversation ${conv.id}:`, decryptErr);
          conv.last_message_preview = decryptFailureLabel(decryptErr);
        }
      }
    }

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
        const isFileMsg = msg.file_id || (msg.file && msg.file.file_id);
        if (msg.message_type === 'system') {
          plaintext = msg.ciphertext || '';
        } else if (isFileMsg && !canDecryptMessagePayload(msg, conv.type)) {
          plaintext = '[' + (msg.message_type || 'file') + ']';
        } else if (!canDecryptMessagePayload(msg, conv.type)) {
          plaintext = decryptFailureLabel({ code: 'invalid_metadata' });
        } else if (conv.type === 'group') {
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
          created_at: msg.created_at,
          time: formatClockTime(new Date(msg.created_at)),
          isSelf: msg.sender_id === myUserId,
          sender: msg.sender_id,
          sender_name: conv.type === 'group' ? msg.sender_name : conv.name,
          sender_initials: msg.sender_initials,
          sender_avatar_color: msg.sender_avatar_color,
          sender_avatar_url: msg.sender_avatar_url,
          status: msg.status,
          isSystem: msg.message_type === 'system',
          isFile: isFileMsg,
          file: msg.file || null,
          message_type: msg.message_type || 'text',
          file_id: msg.file_id || (msg.file ? msg.file.file_id : null),
          reply_to_message_id: msg.reply_to_message_id,
        });
      } catch (decryptErr) {
        console.warn(`Failed to decrypt message ${msg.id}:`, decryptErr);
        const isFileMsg = msg.file_id || (msg.file && msg.file.file_id);
        decrypted.push({
          id: msg.id,
          text: isFileMsg ? ('[' + (msg.message_type || 'file') + ']') : decryptFailureLabel(decryptErr),
          created_at: msg.created_at,
          time: formatClockTime(new Date(msg.created_at)),
          isSelf: msg.sender_id === myUserId,
          sender: msg.sender_id,
          sender_name: conv.type === 'group' ? msg.sender_name : conv.name,
          sender_initials: msg.sender_initials,
          sender_avatar_color: msg.sender_avatar_color,
          sender_avatar_url: msg.sender_avatar_url,
          status: msg.status,
          decryptError: !isFileMsg,
          isFile: isFileMsg,
          file: msg.file || null,
          message_type: msg.message_type || 'text',
          file_id: msg.file_id || (msg.file ? msg.file.file_id : null),
          reply_to_message_id: msg.reply_to_message_id,
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
window.fetchGroupMemberIds = fetchGroupMemberIds;

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

async function fetchContactKeyStatus(userId, { force = false } = {}) {
  if (!userId) return null;
  if (!force && contactKeyStatusCacheByUserId[userId] !== undefined) {
    return contactKeyStatusCacheByUserId[userId];
  }
  try {
    const data = await apiFetch(`/api/keys/contacts/${userId}/fingerprints/`);
    contactKeyStatusCacheByUserId[userId] = data;
    return data;
  } catch (err) {
    contactKeyStatusCacheByUserId[userId] = null;
    return null;
  }
}

function getActiveKeyStatus(contactStatus) {
  if (!contactStatus || !contactStatus.active_key) return null;
  const active = contactStatus.active_key;
  const keys = Array.isArray(contactStatus.keys) ? contactStatus.keys : [];
  const matched = keys.find(key => key.key_fingerprint === active.key_fingerprint);
  return {
    ...active,
    trust_status: matched ? matched.trust_status : 'untrusted'
  };
}

function contactKeyHasChanged(contactStatus) {
  if (!contactStatus || !contactStatus.active_key || !Array.isArray(contactStatus.keys)) return false;
  return contactStatus.keys.some(key =>
    key.trust_status &&
    key.trust_status !== 'untrusted' &&
    key.key_fingerprint !== contactStatus.active_key.key_fingerprint
  );
}

function trustStatusMeta(status, keyChanged = false) {
  if (keyChanged) {
    return {
      className: 'font-semibold text-red-500 flex items-center space-x-1',
      icon: 'shield-alert',
      iconClass: 'text-red-500',
      label: currentLanguage === 'zh' ? '密钥已变更' : 'Key changed'
    };
  }
  if (status === 'missing') {
    return {
      className: 'font-semibold text-red-500 flex items-center space-x-1',
      icon: 'shield-alert',
      iconClass: 'text-red-500',
      label: currentLanguage === 'zh' ? '缺少公钥' : 'No public key'
    };
  }
  if (status === 'trusted' || status === 'verified') {
    return {
      className: 'font-semibold text-emerald-500 flex items-center space-x-1',
      icon: 'shield-check',
      iconClass: 'text-emerald-500',
      label: currentLanguage === 'zh' ? '已验证' : 'Verified'
    };
  }
  return {
    className: 'font-semibold text-amber-500 flex items-center space-x-1',
    icon: 'shield-question',
    iconClass: 'text-amber-500',
    label: currentLanguage === 'zh' ? '未验证' : 'Unverified'
  };
}

function setVerificationStatus(el, status, keyChanged = false) {
  if (!el) return;
  const meta = trustStatusMeta(status, keyChanged);
  el.className = `chat-details-verification ${meta.iconClass || ""}`;
  el.innerHTML = `<i data-lucide="${meta.icon}"></i><span>${escapeHtml(meta.label)}</span>`;
}

async function setContactKeyTrust(userId, trustStatus = 'verified') {
  const data = await apiFetch(`/api/keys/contacts/${userId}/trust/`, {
    method: 'POST',
    body: JSON.stringify({ trust_status: trustStatus })
  });
  delete contactKeyStatusCacheByUserId[userId];
  keyTrustListCache = null;
  fingerprintCacheByUserId[userId] = null;
  const refreshed = await fetchContactKeyStatus(userId, { force: true });
  const active = getActiveKeyStatus(refreshed);
  if (active && window.iChatPrivateE2EE && window.iChatPrivateE2EE.trustPeerKey) {
    window.iChatPrivateE2EE.trustPeerKey(active);
  }
  return { data, refreshed };
}

async function clearContactKeyTrust(userId) {
  const data = await apiFetch(`/api/keys/contacts/${userId}/trust/`, { method: 'DELETE' });
  delete contactKeyStatusCacheByUserId[userId];
  keyTrustListCache = null;
  if (window.iChatPrivateE2EE && window.iChatPrivateE2EE.forgetPeerKey) {
    window.iChatPrivateE2EE.forgetPeerKey(userId);
  }
  return data;
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

function getConversationKind(conv) {
  if (!conv) return "user";
  if (conv.type === "group") return "group";
  if (conv.peer_user_type === "agent") return "agent";
  if (conv.peer_user_type === "bot") return "bot";
  if (conv.peer_user_type === "user") return "user";
  const source = [
    conv.kind,
    conv.category,
    conv.peer_type,
    conv.name,
    conv.username,
    conv.peer_username,
    conv.display_name
  ].filter(Boolean).join(" ").toLowerCase();
  if (/\b(ai|agent|gpt|assistant)\b/.test(source) || source.includes("智能体")) return "agent";
  if (source.includes("bot") || source.includes("机器人")) return "bot";
  return "user";
}

function getConversationUsername(conv) {
  return conv.peer_username || conv.username || conv.handle || "";
}

function getConversationBio(conv, kind) {
  if (conv.peer_bio) return conv.peer_bio;
  if (conv.description || conv.bio || conv.about) return conv.description || conv.bio || conv.about;
  if (kind === "group") return "群聊公告、频道入口和成员动态会显示在这里。";
  if (kind === "bot") return "机器人会根据指令自动响应消息。发送 /help 可以查看可用命令。";
  if (kind === "agent") return "AI Agent 可以协助总结、检索和处理聊天任务。";
  return "这个联系人还没有填写个人简介。";
}

function getConversationLink(conv, kind) {
  if (conv.invite_link || conv.link || conv.url) return conv.invite_link || conv.link || conv.url;
  return "";
}

function getConversationDetailTitle(kind) {
  if (kind === "group") return "群组信息";
  if (kind === "bot") return "机器人信息";
  if (kind === "agent") return "AI Agent 信息";
  return "聊天信息";
}

function getConversationStatusText(conv, kind) {
  if (kind === "group") {
    const count = Number(conv.member_count || 0);
    const online = Number(conv.online_count || 0);
    return online > 0 ? `${count} 位成员，${online} 人在线` : `${count} 位成员`;
  }
  if (kind === "bot") return conv.subscriber_count ? `${conv.subscriber_count} users` : "机器人";
  if (kind === "agent") return "AI Agent";
  return conv.is_online ? "在线" : "联系人";
}

function setDetailsText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value || "--";
}

function setDetailsHidden(id, hidden) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("hidden", !!hidden);
}

function renderRightPanelMembers(conv) {
  const list = document.getElementById("right-panel-members-list");
  if (!list || !conv) return;
  const members = Object.values(groupMembersByConversation[conv.id] || {});
  list.innerHTML = "";
  members.forEach(member => {
    const row = document.createElement("div");
    row.className = "chat-details-member-row";
    const safeColor = /^#[0-9a-fA-F]{6}$/.test(member.avatar_color || '') ? member.avatar_color : '#5c6bc0';
    const avatarInner = member.avatar_url
      ? `<img src="${escapeHtml(member.avatar_url)}" class="w-full h-full object-cover rounded-full">`
      : escapeHtml(member.initials || "??");
    const avatarBgStyle = member.avatar_url
      ? 'background-color: transparent; overflow: hidden;'
      : `background-color: ${safeColor}`;
    row.innerHTML = `
      <div class="chat-details-member-main">
        <div class="chat-details-member-avatar" style="${avatarBgStyle}">
          ${avatarInner}
        </div>
        <div class="chat-details-member-copy">
          <div class="chat-details-member-name">${escapeHtml(member.display_name || member.username || "Unknown")}</div>
          <div class="chat-details-member-status">${escapeHtml(member.status || member.last_seen || (String(member.username || "").toLowerCase().includes("bot") ? "机器人" : "最近曾上线"))}</div>
        </div>
      </div>
      <span class="chat-details-member-role">${escapeHtml(getRoleTranslation(member.role))}</span>
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
        updateConnectionBadge('connected');
      });
      socket.addEventListener('message', (event) => {
        try {
          handleIncomingMessage(JSON.parse(event.data));
        } catch (err) {
          console.error('[WebSocket] Invalid JSON payload:', err);
        }
      });
      socket.addEventListener('close', (event) => {
        updateConnectionBadge('disconnected');
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
    updateConnectionBadge('connected');
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
  } else if (event === 'typing' || event === 'typing.indicator') {
    handleTypingEvent(data);
  } else if (event === 'presence.updated') {
    handlePresenceEvent(data);
  } else if (event === 'profile.updated') {
    handleProfileUpdatedEvent(data);
  } else if (event === 'message.recalled') {
    handleMessageRecalled(data);
  } else if (event === 'message.deleted') {
    handleMessageDeleted(data);
  } else if (event === 'file.upload.completed') {
    const payload = data.data || {};
    console.log('[file-transfer] Upload completed:', payload.file_id);
    // If there's a progress bar, hide it
    if (window.iChatFileTransfer && window.iChatFileTransfer._onUploadCompleted) {
      window.iChatFileTransfer._onUploadCompleted(payload);
    }
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
  const isFileMsg = payload.file_id || (payload.file && payload.file.file_id);
  const fileData = payload.file || null;
  let plaintext;
  let decryptError = null;

  try {
    if (isFileMsg && !canDecryptMessagePayload(payload, 'single')) {
      plaintext = '[' + (payload.message_type || 'file') + ']';
    } else if (window.iChatPrivateE2EE) {
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
    if (isFileMsg) {
      plaintext = '[' + (payload.message_type || 'file') + ']';
    } else {
      plaintext = decryptFailureLabel(err);
      decryptError = err;
    }
  }

  const newMsg = {
    id: payload.message_id,
    text: plaintext,
    created_at: payload.created_at || new Date().toISOString(),
    time: formatClockTime(new Date(payload.created_at || Date.now())),
    isSelf: payload.sender_id === myUserId,
    sender: payload.sender_id,
    sender_name: conv ? conv.name : undefined,
    sender_avatar_url: payload.sender_avatar_url || '',
    status: 'received',
    decryptError: !!decryptError,
    reply_to_message_id: payload.reply_to_message_id,
    isFile: isFileMsg,
    file: fileData,
    message_type: payload.message_type || 'text',
    file_id: payload.file_id || (fileData ? fileData.file_id : null),
  };

  if (messages.some(msg => msg.id === payload.message_id)) return;

  if (conv) {
    updateSidebarPreview(
      conv,
      decryptError ? 'Encrypted message' : (isFileMsg ? ('[' + (payload.message_type || 'file') + ']') : plaintext),
      newMsg.time,
      payload.sender_id
    );
  } else {
    fetchConversations();
  }

  if (activeChatId === convId) {
    messages.push(newMsg);
    appendMessageElement(newMsg);
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
  const isFileMsg = payload.file_id || (payload.file && payload.file.file_id);
  const fileData = payload.file || null;

  try {
    let plaintext;
    if (isFileMsg && !canDecryptMessagePayload(payload, 'group')) {
      plaintext = '[' + (payload.message_type || 'file') + ']';
    } else if (window.iChatGroupE2EE) {
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
      created_at: payload.created_at || new Date().toISOString(),
      time: formatClockTime(new Date(payload.created_at || Date.now())),
      isSelf: payload.sender_id === myUserId,
      sender: payload.sender_id,
      sender_name: payload.sender_name || (getGroupMemberInfo(convId, payload.sender_id) || {}).display_name,
      sender_avatar_url: payload.sender_avatar_url || '',
      status: 'received',
      reply_to_message_id: payload.reply_to_message_id,
      isFile: isFileMsg,
      file: fileData,
      message_type: payload.message_type || 'text',
      file_id: payload.file_id || (fileData ? fileData.file_id : null),
    };

    if (messages.some(msg => msg.id === payload.message_id)) return;

    if (activeChatId === convId) {
      messages.push(newMsg);
      appendMessageElement(newMsg);
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
    if (conv) {
      updateSidebarPreview(conv, isFileMsg ? ('[' + (payload.message_type || 'file') + ']') : plaintext, newMsg.time, payload.sender_id);
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
    patchMessageStatusInPlace(msg);
  }
}

function handleMessageRecalled(data) {
  const payload = data.data || data;
  const msg = messages.find(m => m.id === payload.message_id);
  if (msg) {
    msg.isRecalled = true;
    msg.isSystem = true;
    msg.text = payload.sender_id === myUserId
      ? (currentLanguage === 'zh' ? '你撤回了一条消息' : 'You recalled a message')
      : (currentLanguage === 'zh' ? '消息已被撤回' : 'message recalled');
    patchMessageRowInPlace(msg);
  }
}

function handleMessageDeleted(data) {
  const payload = data.data || data;
  // Per-user deletion notification — remove from local view if active
  const msg = messages.find(m => m.id === payload.message_id);
  if (msg) {
    msg.isDeleted = true;
    msg.isSystem = true;
    msg.text = currentLanguage === 'zh' ? '消息已删除' : 'message deleted';
    patchMessageRowInPlace(msg);
  }
}

function handleMessageAccepted(data) {
  const payload = data.data || data;
  const tempId = payload.client_message_id;
  if (!tempId) return;
  const msg = messages.find(m => m.id === tempId);
  if (msg) {
    var oldId = msg.id;
    msg.id = payload.message_id;
    msg.status = payload.status || 'sent';

    // Update data-message-id on the bubble before patching status
    var bubble = document.querySelector('.message-bubble-custom[data-message-id="' + oldId + '"]');
    if (bubble) {
      bubble.setAttribute('data-message-id', msg.id);
      // Also update the selection checkbox id if present
      var checkbox = document.getElementById('msg-select-check-' + oldId);
      if (checkbox) checkbox.id = 'msg-select-check-' + msg.id;
    }

    // Sync selectedMessageIds in case the user entered select mode while sending
    var selIdx = selectedMessageIds.indexOf(oldId);
    if (selIdx >= 0) selectedMessageIds[selIdx] = msg.id;
    patchMessageStatusInPlace(msg);
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
      // Private chat keys are derived per message because the HKDF context
      // includes sender/receiver key versions.  A conversation-level preflight
      // cannot build a valid context until an encrypted payload exists.
      sessionKeys[convId] = true;
    }

    logToCryptoConsole(`[ECDH] Handshake completed for conversation ${convId}.`);
  } catch (err) {
    console.error('ECDH session key derivation failed:', err);
    logToCryptoConsole(`[ECDH Error] Derivation failed: ${err.message}`);
    sessionKeys[convId] = null;
  }
}

// ============================================================================
// T14 Helper: send read receipts for undelivered messages in active chat
// ============================================================================
function sendReadReceipts(conv) {
  if (!wsClient || !wsClient.sendPayload) return;
  messages.forEach(function(msg) {
    if (msg.isSelf) return; // don't send receipts for own messages
    if (msg.status === 'delivered' || msg.status === 'sent' || msg.status === 'received') {
      wsClient.sendPayload({
        event: 'message.receipt.update',
        data: {
          conversation_type: conv.type === 'group' ? 'group' : 'single',
          message_id: msg.id,
          status: 'read'
        }
      });
    }
  });
}

// ============================================================================
// T14 Helper: update chat header with online/last-seen presence
// ============================================================================
function updateHeaderPresence(conv) {
  var statusEl = document.getElementById('chat-header-status');
  if (!statusEl) return;

  // Check typing indicator first
  if (typingUsers[conv.id]) {
    var typingUserIds = typingUsers[conv.id];
    if (Object.keys(typingUserIds).length > 0) {
      var typingHtml = '<span class="text-brand-light dark:text-brand-dark font-medium">' +
        _t4('Typing', '正在输入', '正在輸入', '入力中') +
        '</span><span class="typing-indicator-dots">' +
        '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></span>';
      statusEl.innerHTML = typingHtml;
      return;
    }
  }

  if (conv.type === 'group') {
    statusEl.textContent = _t4(
      (conv.member_count || 0) + ' members',
      (conv.member_count || 0) + ' 位成员',
      (conv.member_count || 0) + ' 位成員',
      (conv.member_count || 0) + ' メンバー'
    );
    return;
  }

  // Private chat: check presence
  if (conv.is_online) {
    statusEl.innerHTML = '<span class="inline-block w-2 h-2 rounded-full bg-green-500 mr-1.5 align-middle"></span>' +
      _t4('Online', '在线', '線上', 'オンライン');
  } else if (conv.last_seen) {
    var lastSeen = new Date(conv.last_seen);
    var now = new Date();
    var diffMs = now - lastSeen;
    var diffMin = Math.floor(diffMs / 60000);
    var diffHours = Math.floor(diffMs / 3600000);
    var diffDays = Math.floor(diffMs / 86400000);

    var timeStr = formatClockTime(lastSeen);
    var text;
    if (diffMin < 1) {
      text = _t4('Last seen just now', '刚刚在线', '剛剛上線', 'たった今オンライン');
    } else if (diffMin < 60) {
      text = _t4('Last seen ', '最后上线 ', '最後上線 ', '最終オンライン ') + diffMin + _t4(' min ago', ' 分钟前', ' 分鐘前', ' 分前');
    } else if (diffHours < 6) {
      text = _t4('Last seen ', '最后上线 ', '最後上線 ', '最終オンライン ') + diffHours + _t4(' hours ago', ' 小时前', ' 小時前', ' 時間前');
    } else if (diffDays === 0) {
      text = _t4('Last seen today at ', '最后上线今天 ', '最後上線今天 ', '本日最終オンライン ') + timeStr;
    } else if (diffDays === 1) {
      text = _t4('Last seen yesterday at ', '最后上线昨天 ', '最後上線昨天 ', '昨日最終オンライン ') + timeStr;
    } else {
      var dateStr = lastSeen.toLocaleDateString([], { month: 'short', day: 'numeric' });
      text = _t4('Last seen ', '最后上线 ', '最後上線 ', '最終オンライン ') + dateStr;
    }
    statusEl.textContent = text;
  } else {
    statusEl.textContent = _t4('Contact', '联系人', '聯絡人', '連絡先');
  }
}

// ============================================================================
// T14 Helper: update WebSocket connection status badge
// ============================================================================
function updateConnectionBadge(status) {
  connectionStatus = status;
  var badge = document.getElementById('connection-status-badge');

  if (!badge) {
    // Create the badge dynamically
    badge = document.createElement('div');
    badge.id = 'connection-status-badge';
    badge.className = 'connection-badge';
    document.body.appendChild(badge);
  }

  var icon, text, className;
  if (status === 'connected') {
    icon = 'wifi';
    text = _t4('Connected', '已连接', '已連線', '接続済み');
    className = 'connection-badge connected visible';
    // Auto-hide connected badge after 3s
    setTimeout(function() {
      if (connectionStatus === 'connected') {
        badge.classList.remove('visible');
      }
    }, 3000);
  } else if (status === 'connecting') {
    icon = 'wifi';
    text = _t4('Reconnecting...', '重连中...', '重新連線中...', '再接続中...');
    className = 'connection-badge reconnecting visible';
  } else {
    icon = 'wifi-off';
    text = _t4('Disconnected', '已断开', '已中斷連線', '切断されました');
    className = 'connection-badge disconnected visible';
  }

  badge.className = className;
  badge.innerHTML = '<i data-lucide="' + icon + '" class="w-3.5 h-3.5"></i><span>' + text + '</span>';
  if (window.lucide) window.lucide.createIcons({ nodes: badge.querySelectorAll('[data-lucide]') });
}

// ============================================================================
// T14 Helper: handle typing indicator from WebSocket events
// ============================================================================
function handleTypingEvent(data) {
  var payload = data.data || data;
  var convId = payload.conversation_id;
  var userId = payload.user_id;
  var action = payload.action; // 'typing' or 'stop'

  if (!typingUsers[convId]) typingUsers[convId] = {};

  if (action === 'typing') {
    // Set typing
    typingUsers[convId][userId] = true;
    // Auto-clear after 4 seconds
    if (typingUsers[convId]['_timeout_' + userId]) {
      clearTimeout(typingUsers[convId]['_timeout_' + userId]);
    }
    typingUsers[convId]['_timeout_' + userId] = setTimeout(function() {
      delete typingUsers[convId][userId];
      delete typingUsers[convId]['_timeout_' + userId];
      if (Object.keys(typingUsers[convId]).filter(function(k) { return k.indexOf('_timeout_') !== 0; }).length === 0) {
        // Update header to show normal status
        if (activeChatId === convId) {
          var conv = conversationsById[convId];
          if (conv) updateHeaderPresence(conv);
        }
      }
    }, 4000);
  } else if (action === 'stop') {
    delete typingUsers[convId][userId];
    if (typingUsers[convId]['_timeout_' + userId]) {
      clearTimeout(typingUsers[convId]['_timeout_' + userId]);
      delete typingUsers[convId]['_timeout_' + userId];
    }
  }

  // Update header if this is the active conversation
  if (activeChatId === convId) {
    var conv = conversationsById[convId];
    if (conv) updateHeaderPresence(conv);
  }
}

// ============================================================================
// T14 Helper: handle presence update from WebSocket
// ============================================================================
function handlePresenceEvent(data) {
  var payload = data.data || data;
  var userId = payload.user_id;

  // Update all conversations involving this user
  for (var convId in conversationsById) {
    var conv = conversationsById[convId];
    if (conv.type === 'single' && conv.peer_id === userId) {
      conv.is_online = payload.is_online;
      conv.last_seen = payload.last_seen;
      conv.status_text = payload.status;

      // Update online dot in sidebar
      var item = document.getElementById('chat-item-' + conv.id);
      var avatar = item ? item.querySelector('.avatar') : null;
      if (avatar) {
        if (payload.is_online) {
          avatar.classList.add('online');
        } else {
          avatar.classList.remove('online');
        }
      }

      // Update header if this is the active chat
      if (activeChatId === conv.id) {
        updateHeaderPresence(conv);
      }
    }
  }
}

// T14/T15 Helper: handle profile update from WebSocket
function handleProfileUpdatedEvent(data) {
  var payload = data.data || data;
  var userId = parseInt(payload.user_id);
  var username = payload.username;
  var displayName = payload.display_name;
  var avatarUrl = payload.avatar_url;

  if (!userId) return;

  // 1. Update conversations cache and sidebar chat item list
  for (var convId in conversationsById) {
    var conv = conversationsById[convId];
    if (conv.type === 'single' && parseInt(conv.peer_id) === userId) {
      conv.avatar_url = avatarUrl;
      if (displayName) {
        conv.name = displayName;
      }
      
      // Update sidebar chat item avatar & name
      var chatItem = document.getElementById('chat-item-' + conv.id);
      if (chatItem) {
        var avatarDiv = chatItem.querySelector('.telegram-chat-avatar');
        if (avatarDiv) {
          if (avatarUrl) {
            avatarDiv.innerHTML = `<img src="${escapeHtml(avatarUrl)}" class="w-full h-full object-cover rounded-full">`;
            avatarDiv.style.backgroundColor = 'transparent';
            avatarDiv.style.overflow = 'hidden';
          } else {
            var initials = (displayName || username || '?')[0].toUpperCase();
            avatarDiv.innerHTML = escapeHtml(initials);
            var safeColor = /^#[0-9a-fA-F]{6}$/.test(conv.avatar_color || '') ? conv.avatar_color : '#5c6bc0';
            avatarDiv.style.backgroundColor = safeColor;
          }
        }
        var nameSpan = chatItem.querySelector('.telegram-chat-name');
        if (nameSpan && displayName) {
          nameSpan.textContent = displayName;
        }
      }

      // If this is the active chat, update Header and Details Panel
      if (activeChatId === conv.id) {
        var headerAvatar = document.getElementById("chat-header-avatar");
        if (headerAvatar) {
          if (avatarUrl) {
            headerAvatar.innerHTML = `<img src="${escapeHtml(avatarUrl)}" class="w-full h-full object-cover rounded-full">`;
            headerAvatar.style.backgroundColor = 'transparent';
          } else {
            headerAvatar.textContent = (displayName || username || '?')[0].toUpperCase();
            headerAvatar.style.backgroundColor = conv.avatar_color || '#5c6bc0';
          }
        }
        var headerName = document.getElementById("chat-header-name");
        if (headerName && displayName) {
          headerName.textContent = displayName;
        }

        var detailsAvatar = document.getElementById("right-panel-avatar");
        if (detailsAvatar) {
          if (avatarUrl) {
            detailsAvatar.innerHTML = `<img src="${escapeHtml(avatarUrl)}" class="w-full h-full object-cover rounded-full">`;
            detailsAvatar.style.backgroundColor = 'transparent';
          } else {
            detailsAvatar.textContent = (displayName || username || '?')[0].toUpperCase();
            detailsAvatar.style.backgroundColor = conv.avatar_color || '#5c6bc0';
          }
        }
        var detailsName = document.getElementById("right-panel-name");
        if (detailsName && displayName) {
          detailsName.textContent = displayName;
        }
      }
    }
  }

  // 2. Update group member cache and render if active
  for (var gid in groupMembersByConversation) {
    var members = groupMembersByConversation[gid];
    if (members && members[userId]) {
      var member = members[userId];
      member.avatar_url = avatarUrl;
      if (displayName) {
        member.display_name = displayName;
      }
      // If the current active chat is this group, re-render the right panel member list
      if (activeChatId && activeChatId.toString() === gid.toString()) {
        const conv = conversationsById[activeChatId];
        if (conv) {
          renderRightPanelMembers(conv);
        }
      }
    }
  }

  // 3. Update any contact list view elements (sidebar contact page, contacts page)
  var contactAvatars = document.querySelectorAll('.avatar-clickable[data-user-id="' + userId + '"]');
  contactAvatars.forEach(function(el) {
    if (avatarUrl) {
      el.innerHTML = `<img src="${escapeHtml(avatarUrl)}" class="w-full h-full object-cover">`;
    } else {
      var initials = (displayName || username || '?')[0].toUpperCase();
      el.textContent = initials;
    }
  });
}

// 6. Chat Selection & Rendering
async function selectChat(chatId) {
  saveActiveConversationDraftFromInput();
  closeChatSearch();
  activeSpecialChatId = null;
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
  var headerAvatar = document.getElementById("chat-header-avatar");
  if (conv.avatar_url) {
    headerAvatar.innerHTML = `<img src="${escapeHtml(conv.avatar_url)}" class="w-full h-full object-cover rounded-full">`;
    headerAvatar.style.backgroundColor = 'transparent';
  } else {
    headerAvatar.textContent = conv.initials || '??';
    headerAvatar.style.backgroundColor = conv.avatar_color || '#5c6bc0';
  }
  headerAvatar.className = `w-10 h-10 rounded-full text-white flex items-center justify-center font-bold text-sm shadow-sm overflow-hidden`;
  const headerNameEl = document.getElementById("chat-header-name");
  headerNameEl.innerHTML = `<span>${escapeHtml(conv.name || 'Unknown')}</span>`;
  if (conv.peer_user_type === 'agent') {
    headerNameEl.innerHTML += `<span class="user-role-badge badge-agent">${_t4('Agent', '智能代理', '智能代理', 'エージェント')}</span>`;
  } else if (conv.peer_user_type === 'bot') {
    headerNameEl.innerHTML += `<span class="user-role-badge badge-bot">${_t4('Bot', '机器人', '機器人', 'ボット')}</span>`;
  }

  // Update delete/leave text
  const leaveTextEl = document.getElementById("menu-delete-chat-text");
  if (leaveTextEl) {
    const isGroup = conv.type === 'group';
    leaveTextEl.setAttribute("data-i18n", isGroup ? "menu_leave_group" : "menu_delete_chat");
    leaveTextEl.textContent = isGroup
      ? (currentLanguage === 'zh' ? "退出群聊" : "Leave Group")
      : (currentLanguage === 'zh' ? "删除聊天" : "Delete Chat");
  }
  const blockContactBtn = document.getElementById("menu-block-contact-btn");
  if (blockContactBtn) {
    blockContactBtn.classList.toggle("hidden", conv.type !== "single" || !conv.peer_id);
  }
  const blockContactText = document.getElementById("menu-block-contact-text");
  if (blockContactText) {
    blockContactText.textContent = currentLanguage === "zh" ? "拉黑" : "Block";
  }
  
  // Header status
  const statusText = conv.type === 'group'
    ? _t4(`${conv.member_count || 0} members`, `${conv.member_count || 0} 位成员`, `${conv.member_count || 0} 位成員`, `${conv.member_count || 0} メンバー`)
    : _t4('Contact', '联系人', '聯絡人', '連絡先');
  if (conv.is_secure) {
    const e2eeText = _t4('🔒 End-to-end encrypted', '🔒 端到端加密', '🔒 端對端加密', '🔒 エンドツーエンド暗号化');
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
  const aiWindow = document.getElementById("ai-assistant-window");
  if (aiWindow) aiWindow.classList.add("hidden");

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
  restoreDraftForActiveConversation();
  await updateDetailsPanelRich(conv);

  // Send read receipts for any undelivered messages
  sendReadReceipts(conv);

  // Mark conversation as read via REST API
  fetch('/api/conversations/' + activeChatId + '/read/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken') || ''
    }
  }).catch(function() {});

  // Update header with presence data
  updateHeaderPresence(conv);
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
    if (conv.avatar_url) {
      avatar.innerHTML = `<img src="${escapeHtml(conv.avatar_url)}" class="w-full h-full object-cover rounded-full">`;
      avatar.style.backgroundColor = 'transparent';
    } else {
      avatar.textContent = conv.initials || '??';
      avatar.style.backgroundColor = conv.avatar_color || '#5c6bc0';
    }
    avatar.className = 'w-20 h-20 rounded-full text-white flex items-center justify-center font-bold text-2xl shadow-sm mb-3 overflow-hidden';
  }
  if (name) name.textContent = conv.name || '';
  if (status) status.textContent = getStatusTranslation(conv.type === 'group' ? `${conv.member_count || 0} members` : 'Contact');

  if (conv.is_secure) {
    if (fpWrapper) fpWrapper.classList.remove("hidden");
    if (protocol) protocol.textContent = "ECDH + HKDF + AES-GCM";
    if (resetKeyBtn) resetKeyBtn.classList.toggle("hidden", conv.type === "group");
    if (verificationStatus) {
      verificationStatus.className = "font-semibold text-amber-500 flex items-center space-x-1";
      verificationStatus.innerHTML = '<i data-lucide="shield-question" class="w-3.5 h-3.5 mr-0.5 inline-block text-amber-500"></i><span>' + _t4('Unverified', '待验证', '待驗證', '未検証') + '</span>';
    }
    if (fp) {
      fp.textContent = _t4('Loading real fingerprint...', '正在加载真实指纹...', '正在載入真實指紋...', '実際の指紋を読み込み中...');
    }

    if (conv.type === "single" && conv.peer_id) {
      const contactStatus = await fetchContactKeyStatus(conv.peer_id);
      if (requestId !== detailsPanelRequestId) return;
      const activeKey = getActiveKeyStatus(contactStatus);
      const keyChanged = contactKeyHasChanged(contactStatus);
      if (fp) {
        fp.textContent = activeKey
          ? `v${activeKey.key_version}: ${formatFingerprint(activeKey.key_fingerprint)}`
          : formatFingerprint(null);
      }
      setVerificationStatus(verificationStatus, activeKey ? activeKey.trust_status : 'missing', keyChanged);
    } else if (fp) {
      fp.textContent = _t4(
        'Group messages are encrypted to each member public key.',
        '群聊使用每位成员的当前公钥加密。',
        '群組訊息使用每位成員的當前公鑰加密。',
        'グループメッセージは各メンバーの公開鍵で暗号化されます。'
      );
    }
    if (window.lucide) window.lucide.createIcons();
  } else {
    if (fpWrapper) fpWrapper.classList.add("hidden");
  }

  if (conv.type === 'group') {
    if (groupSection) groupSection.classList.remove("hidden");
    const mc = document.getElementById("right-panel-members-count");
    if (mc) mc.textContent = _t4(`Group Members (${conv.member_count || 0})`, `群组成员 (${conv.member_count || 0})`, `群組成員 (${conv.member_count || 0})`, `グループメンバー (${conv.member_count || 0})`);
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

async function updateDetailsPanelRich(conv) {
  const requestId = ++detailsPanelRequestId;
  const kind = getConversationKind(conv);
  const avatar = document.getElementById("details-avatar");
  const name = document.getElementById("details-name");
  const status = document.getElementById("details-status");
  const fp = document.getElementById("details-fingerprint");
  const fpWrapper = document.getElementById("right-panel-fingerprint-wrapper");
  const groupSection = document.getElementById("right-panel-group-section");
  const protocol = document.getElementById("right-panel-protocol");
  const resetKeyBtn = document.getElementById("right-panel-reset-key-btn");
  const verificationStatus = document.getElementById("right-panel-verification-status");

  setDetailsText("right-panel-title", getConversationDetailTitle(kind));

  // Restore any AI details panel modifications
  setDetailsHidden("right-panel-username-row", false);
  setDetailsHidden("right-panel-bio-row", false);
  setDetailsHidden("right-panel-notify-row", false);
  setDetailsHidden("right-panel-action-media", false);
  setDetailsHidden("right-panel-action-files", false);
  setDetailsHidden("right-panel-qr-btn", kind === "group");
  setDetailsHidden("right-panel-ai-settings-card", true);

  const verifyFpBtn = document.querySelector("#right-panel-fingerprint-wrapper .chat-details-verify-btn");
  if (verifyFpBtn) verifyFpBtn.classList.remove("hidden");

  const encTitle = document.querySelector("#right-panel-encryption-card .chat-details-section-title");
  if (encTitle) encTitle.textContent = currentLanguage === 'zh' ? '加密详情' : 'Encryption Details';

  const fpLabel = document.querySelector("#right-panel-fingerprint-wrapper span");
  if (fpLabel) fpLabel.textContent = currentLanguage === 'zh' ? '加密指纹' : 'Cryptographic Fingerprint';

  if (avatar) {
    if (conv.avatar_url) {
      avatar.innerHTML = `<img src="${escapeHtml(conv.avatar_url)}" class="w-full h-full object-cover rounded-full">`;
      avatar.style.backgroundColor = "transparent";
    } else {
      avatar.innerHTML = escapeHtml(conv.initials || "??");
      avatar.style.backgroundColor = conv.avatar_color || "#5c6bc0";
    }
    avatar.className = "chat-details-avatar";
  }

  if (name) {
    name.innerHTML = `<span>${escapeHtml(conv.name || 'Unknown')}</span>`;
    if (conv.peer_user_type === 'agent') {
      name.innerHTML += `<span class="user-role-badge badge-agent">${currentLanguage === 'zh' ? '智能代理' : 'Agent'}</span>`;
    } else if (conv.peer_user_type === 'bot') {
      name.innerHTML += `<span class="user-role-badge badge-bot">${currentLanguage === 'zh' ? '机器人' : 'Bot'}</span>`;
    }
  }
  if (status) status.textContent = getConversationStatusText(conv, kind);

  const username = getConversationUsername(conv);
  setDetailsText(
    "right-panel-username",
    username ? `@${username.replace(/^@/, "")}` : (kind === "group" ? `group-${conv.id}` : `user-${conv.peer_id || conv.id}`)
  );
  setDetailsText("right-panel-username-label", kind === "group" ? "群组标识" : "用户名");
  setDetailsText("right-panel-bio", getConversationBio(conv, kind));
  setDetailsText(
    "right-panel-bio-label",
    kind === "group" ? "群组简介" : (kind === "bot" ? "机器人简介" : (kind === "agent" ? "Agent 简介" : "个人简介"))
  );

  setDetailsHidden("right-panel-email-row", !conv.peer_email);
  setDetailsText("right-panel-email", conv.peer_email || "");

  setDetailsHidden("right-panel-phone-row", !conv.peer_phone_number);
  setDetailsText("right-panel-phone", conv.peer_phone_number || "");

  setDetailsHidden("right-panel-location-row", !conv.peer_location);
  setDetailsText("right-panel-location", conv.peer_location || "");

  const link = getConversationLink(conv, kind);
  setDetailsHidden("right-panel-link-row", !link);
  setDetailsText("right-panel-link", link);

  const notifyToggle = document.getElementById("right-panel-notify-toggle");
  if (notifyToggle) {
    const isMuted = conv.muted_until && new Date(conv.muted_until) > new Date();
    notifyToggle.classList.toggle("is-on", !isMuted);
  }

  if (protocol) protocol.textContent = conv.is_secure ? "ECDH + HKDF + AES-GCM" : "未启用";
  if (resetKeyBtn) resetKeyBtn.classList.toggle("hidden", kind === "group");

  if (conv.is_secure) {
    if (fpWrapper) fpWrapper.classList.remove("hidden");
    if (verificationStatus) setVerificationStatus(verificationStatus, "untrusted");
    if (fp) fp.textContent = "正在加载真实指纹...";

    if (conv.type === "single" && conv.peer_id) {
      const contactStatus = await fetchContactKeyStatus(conv.peer_id);
      if (requestId !== detailsPanelRequestId) return;
      const activeKey = getActiveKeyStatus(contactStatus);
      const keyChanged = contactKeyHasChanged(contactStatus);
      if (fp) {
        fp.textContent = activeKey
          ? `v${activeKey.key_version}: ${formatFingerprint(activeKey.key_fingerprint)}`
          : formatFingerprint(null);
      }
      setVerificationStatus(verificationStatus, activeKey ? activeKey.trust_status : "missing", keyChanged);
    } else if (fp) {
      fp.textContent = "群聊消息会使用每位成员的当前公钥分别加密。";
      setVerificationStatus(verificationStatus, "verified");
    }
  } else if (fpWrapper) {
    fpWrapper.classList.add("hidden");
  }

  if (kind === "group") {
    if (groupSection) groupSection.classList.remove("hidden");
    setDetailsText("right-panel-members-count", `成员 (${conv.member_count || 0})`);
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

  if (window.lucide) window.lucide.createIcons();
}

function renderMessages() {
  const container = document.getElementById("message-history-container");
  if (!container) return;
  container.innerHTML = "";
  const conv = conversationsById[activeChatId];
  messages.forEach((msg, index) => {
    if (msg.created_at) {
      msg.time = formatClockTime(new Date(msg.created_at));
    }
    const gm = getMessageGroupMetaNew(messages, index, conv);
    container.appendChild(createMessageBubbleElementNew(msg, gm, conv));
  });
  if (isChatSearchOpen()) {
    runChatSearch(false);
  }
}

/**
 * Incrementally append a single new message to the message history container.
 *
 * Unlike renderMessages() this does NOT clear the container — it only creates
 * one new DOM node and appends it.  When the new message is consecutive with
 * the previous one (same sender), the previous row is patched in-place so its
 * avatar/group styling is updated without touching its content (avoiding image
 * reloads for file/image messages).
 */
function appendMessageElement(newMsg) {
  const container = document.getElementById("message-history-container");
  if (!container) return;

  if (newMsg.created_at) {
    newMsg.time = formatClockTime(new Date(newMsg.created_at));
  }

  const conv = conversationsById[activeChatId];
  const idx = messages.indexOf(newMsg);
  if (idx < 0) return;

  // Recompute group-meta for every message so getMessageGroupMetaNew sees the
  // correct neighbours.  We only need the meta for the new message and the
  // previous one, but the helper reads index ± 1.
  const gm = getMessageGroupMetaNew(messages, idx, conv);

  // If the new message is consecutive with the previous one, patch the
  // previous row in-place so it no longer looks like "last in group".
  if (gm.isConsecutive) {
    const prevRow = container.lastElementChild;
    if (prevRow) {
      prevRow.classList.remove("message-row-group-last");
      // Replace the avatar with a spacer on peer messages
      const prevMsg = messages[idx - 1];
      if (prevMsg && !prevMsg.isSelf && !prevMsg.isSystem) {
        const prevAvatar = prevRow.querySelector(".message-avatar");
        if (prevAvatar) {
          const spacer = document.createElement("div");
          spacer.className = "message-avatar-spacer";
          spacer.setAttribute("aria-hidden", "true");
          prevAvatar.replaceWith(spacer);
        }
      }
    }
  }

  const row = createMessageBubbleElementNew(newMsg, gm, conv);
  container.appendChild(row);

  // Trigger image preview for file/image messages
  const fileId = newMsg.file_id || (newMsg.file && newMsg.file.file_id);
  if (fileId) {
    applyImagePreviewToBubble(row, fileId, conv ? conv.type : null);
  }

  // Initialise Lucide icons inside the new row
  if (window.lucide && window.lucide.createIcons) {
    lucide.createIcons({ nodes: row.querySelectorAll("[data-lucide]") });
  }

  if (isChatSearchOpen()) {
    runChatSearch(false);
  }
}

/**
 * Patch the status icon of a single self-sent message in-place.
 *
 * Avoids a full renderMessages() cycle so existing images / file previews
 * in other bubbles are not reloaded.
 */
function patchMessageStatusInPlace(msg) {
  var bubble = document.querySelector('.message-bubble-custom[data-message-id="' + msg.id + '"]');
  if (!bubble) return;

  // File/image bubbles use .file-image-meta; text & regular file bubbles use .message-meta-line
  var metaLine = bubble.querySelector('.message-meta-line, .file-image-meta');
  if (!metaLine) return;

  // Remove any existing status icon (may be a Lucide-rendered SVG)
  var oldIcon = metaLine.querySelector('.msg-status-icon, .msg-status-sending, .msg-status-read, .msg-status-failed');
  if (oldIcon) oldIcon.remove();

  // Parse the shared HTML helper to get a new icon element
  var tmp = document.createElement('div');
  tmp.innerHTML = renderStatusIconHtml(msg);
  var newIcon = tmp.firstElementChild;
  if (!newIcon) return;

  metaLine.appendChild(newIcon);

  if (window.lucide && window.lucide.createIcons) {
    lucide.createIcons({ nodes: [newIcon] });
  }
}

/**
 * Replace a single message row in-place (used for recall / delete).
 *
 * Still isolated to one row — it will not cause other images to reload.
 */
/**
 * Replace a single message row in-place AND re-render its immediate neighbours.
 *
 * Recall / delete turn a message into a system row which changes the sender-key
 * chain, so the previous and next rows may need their group-meta recalculated
 * (avatar visibility, "last-in-group" / "first-in-group" classes, etc.).
 */
function patchMessageRowInPlace(msg) {
  var container = document.getElementById("message-history-container");
  if (!container) return;

  var conv = conversationsById[activeChatId];
  var idx = messages.indexOf(msg);
  if (idx < 0) return;

  var allRows = container.querySelectorAll(':scope > .message-row');
  var start = Math.max(0, idx - 1);
  var end = Math.min(messages.length - 1, idx + 1);

  for (var i = start; i <= end; i++) {
    var neighbor = messages[i];
    var oldRow = allRows[i];
    if (!oldRow) continue;

    var gm = getMessageGroupMetaNew(messages, i, conv);
    var newRow = createMessageBubbleElementNew(neighbor, gm, conv);
    oldRow.replaceWith(newRow);

    var fileId = neighbor.file_id || (neighbor.file && neighbor.file.file_id);
    if (fileId) {
      applyImagePreviewToBubble(newRow, fileId, conv ? conv.type : null);
    }
  }

  // Initialise any new Lucide icons in the replaced rows
  if (window.lucide && window.lucide.createIcons) {
    lucide.createIcons({
      nodes: container.querySelectorAll(
        '.message-row:nth-child(n+' + (start + 1) + '):nth-child(-n+' + (end + 1) + ') [data-lucide]'
      ),
    });
  }
}

function getChatSearchEls() {
  return {
    overlay: document.getElementById("chat-search-overlay"),
    input: document.getElementById("chat-search-input"),
    results: document.getElementById("chat-search-results"),
    prev: document.getElementById("chat-search-prev"),
    next: document.getElementById("chat-search-next"),
    close: document.getElementById("chat-search-close")
  };
}

function isChatSearchOpen() {
  const overlay = document.getElementById("chat-search-overlay");
  return !!overlay && !overlay.classList.contains("hidden");
}

function openChatSearch() {
  const els = getChatSearchEls();
  if (!els.overlay || !els.input) return;
  const header = document.getElementById("chat-header-normal");
  if (header) header.classList.add("chat-search-active");
  els.overlay.classList.remove("hidden");
  els.input.focus();
  els.input.select();
  runChatSearch(false);
  if (window.lucide) window.lucide.createIcons();
}

function closeChatSearch() {
  const els = getChatSearchEls();
  const header = document.getElementById("chat-header-normal");
  if (header) header.classList.remove("chat-search-active");
  clearChatSearchHighlight();
  chatSearchResults = [];
  chatSearchIndex = -1;
  if (els.input) els.input.value = "";
  if (els.results) {
    els.results.innerHTML = "";
    els.results.classList.add("hidden");
  }
  if (els.prev) els.prev.classList.add("hidden");
  if (els.next) els.next.classList.add("hidden");
  if (els.overlay) els.overlay.classList.add("hidden");
}

function getSearchableMessageText(msg) {
  const markdownPayload = getMarkdownPayload(msg && msg.text);
  return String(markdownPayload === null ? (msg && msg.text) : markdownPayload || "");
}

function formatSearchResultDate(msg) {
  if (!msg) return "";
  const raw = msg.created_at || msg.timestamp || msg.time;
  if (!raw) return "";
  const date = raw instanceof Date ? raw : new Date(raw);
  if (Number.isNaN(date.getTime())) return String(msg.time || raw || "");
  const now = new Date();
  if (date.getFullYear() !== now.getFullYear()) {
    return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function getSearchResultAvatarHtml(msg, conv) {
  if (msg && msg.isSelf) {
    return `<div class="chat-search-result-avatar self">${escapeHtml((window.currentUserInitials || "我").slice(0, 2))}</div>`;
  }
  const senderName = getMessageSenderName(msg || {}, conv);
  const avatarInfo = getSenderAvatarInfo(senderName, msg || {}, conv);
  if (avatarInfo.avatarUrl) {
    return `<div class="chat-search-result-avatar image"><img src="${escapeHtml(avatarInfo.avatarUrl)}" alt=""></div>`;
  }
  return `<div class="chat-search-result-avatar ${escapeHtml(avatarInfo.colorClass || "")}"${avatarInfo.safeStyle || ""}>${escapeHtml(avatarInfo.initials || "?")}</div>`;
}

function getSearchResultAvatarHtmlRich(msg, conv) {
  if (msg && msg.isSelf) {
    if (currentUserProfile.avatarUrl) {
      return `<div class="chat-search-result-avatar image"><img src="${escapeHtml(currentUserProfile.avatarUrl)}" alt=""></div>`;
    }
    const initials = currentUserProfile.initials || (currentUserProfile.username || "我").slice(0, 2);
    const color = /^#[0-9a-fA-F]{6}$/.test(currentUserProfile.avatarColor) ? currentUserProfile.avatarColor : "#3390ec";
    return `<div class="chat-search-result-avatar self" style="background-color: ${color}">${escapeHtml(initials)}</div>`;
  }
  const senderName = getMessageSenderName(msg || {}, conv);
  const avatarInfo = getSenderAvatarInfo(senderName, msg || {}, conv);
  if (avatarInfo.avatarUrl) {
    return `<div class="chat-search-result-avatar image"><img src="${escapeHtml(avatarInfo.avatarUrl)}" alt=""></div>`;
  }
  return `<div class="chat-search-result-avatar ${escapeHtml(avatarInfo.colorClass || "")}"${avatarInfo.safeStyle || ""}>${escapeHtml(avatarInfo.initials || "?")}</div>`;
}

function renderSearchResultSnippet(msg) {
  const html = renderMessageContent(msg && msg.text);
  return html.replace(/<p>/g, '<span>').replace(/<\/p>/g, '</span>');
}

function getSearchSnippet(text, query) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "";
  const lower = normalized.toLowerCase();
  const needle = String(query || "").toLowerCase();
  const hit = needle ? lower.indexOf(needle) : -1;
  const start = hit > 18 ? hit - 18 : 0;
  const end = Math.min(normalized.length, (hit >= 0 ? hit + needle.length + 48 : 72));
  return (start > 0 ? "..." : "") + normalized.slice(start, end) + (end < normalized.length ? "..." : "");
}

function renderChatSearchResults(query) {
  const els = getChatSearchEls();
  if (!els.results || !els.prev || !els.next) return;
  const hasQuery = String(query || "").trim().length > 0;
  els.prev.classList.toggle("hidden", !hasQuery || chatSearchResults.length < 2);
  els.next.classList.toggle("hidden", !hasQuery || chatSearchResults.length < 2);

  if (!hasQuery) {
    els.results.innerHTML = "";
    els.results.classList.add("hidden");
    return;
  }

  els.results.classList.remove("hidden");
  if (!chatSearchResults.length) {
    els.results.innerHTML = `<div class="chat-search-empty">${_t4("No matching messages", "没有找到匹配消息", "沒有找到符合的訊息", "一致するメッセージはありません")}</div>`;
    return;
  }

  const conv = conversationsById[activeChatId];
  els.results.innerHTML = chatSearchResults.map(function(msg, index) {
    const senderName = msg.isSelf
      ? (currentLanguage === "zh" ? "我" : "You")
      : getMessageSenderName(msg, conv);
    const snippet = renderSearchResultSnippet(msg);
    return `<button type="button" class="chat-search-result-item${index === chatSearchIndex ? " is-active" : ""}" data-search-index="${index}">
      ${getSearchResultAvatarHtmlRich(msg, conv)}
      <span class="chat-search-result-main">
        <span class="chat-search-result-title">${escapeHtml(senderName)}</span>
        <span class="chat-search-result-snippet">${snippet}</span>
      </span>
      <span class="chat-search-result-date">${escapeHtml(formatSearchResultDate(msg))}</span>
    </button>`;
  }).join("");
}

function clearChatSearchHighlight() {
  document.querySelectorAll(".message-search-hit").forEach(function(el) {
    el.classList.remove("message-search-hit");
  });
}

function activateChatSearchResult(index) {
  if (!chatSearchResults.length) return;
  if (index < 0) index = chatSearchResults.length - 1;
  if (index >= chatSearchResults.length) index = 0;
  chatSearchIndex = index;
  clearChatSearchHighlight();

  const msg = chatSearchResults[chatSearchIndex];
  const bubbles = Array.from(document.querySelectorAll(".message-bubble-custom[data-message-id]"));
  const bubble = bubbles.find(function(el) {
    return String(el.dataset.messageId) === String(msg.id);
  });
  const row = bubble ? bubble.closest(".message-row") : null;
  if (row) row.classList.add("message-search-hit");
  if (bubble) {
    bubble.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const els = getChatSearchEls();
  if (els.results) {
    els.results.querySelectorAll(".chat-search-result-item").forEach(function(item, itemIndex) {
      item.classList.toggle("is-active", itemIndex === chatSearchIndex);
    });
  }
}

function runChatSearch(activateFirst) {
  const els = getChatSearchEls();
  if (!els.input) return;
  const query = els.input.value.trim();
  clearChatSearchHighlight();
  chatSearchIndex = -1;

  if (!query) {
    chatSearchResults = [];
    renderChatSearchResults(query);
    return;
  }

  const lowerQuery = query.toLowerCase();
  chatSearchResults = messages.filter(function(msg) {
    if (!msg || msg.isSystem || msg.decryptError) return false;
    return getSearchableMessageText(msg).toLowerCase().includes(lowerQuery);
  });

  renderChatSearchResults(query);
  if (chatSearchResults.length && activateFirst) {
    activateChatSearchResult(0);
  }
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

  let avatarUrl = "";
  if (msg && msg.sender_avatar_url) {
    avatarUrl = msg.sender_avatar_url;
  } else if (member && member.avatar_url) {
    avatarUrl = member.avatar_url;
  } else if (conv && conv.type === 'single' && msg && msg.sender !== myUserId) {
    avatarUrl = conv.avatar_url;
  }

  return { initials, colorClass, safeStyle, avatarUrl };
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

function sanitizeMarkdownUrl(url) {
  var value = String(url || "").trim();
  return /^(https?:\/\/|mailto:)/i.test(value) ? value : "";
}

function getMarkdownPayload(text) {
  var value = String(text || "");
  var match = value.match(/^\/md(?:[ \t]+|\r?\n|$)/i);
  if (!match) return null;
  return value.slice(match[0].length);
}

function ensureMarkdownPrefix(textarea) {
  if (!textarea) return 0;
  if (getMarkdownPayload(textarea.value) !== null) return 0;
  textarea.value = "/md " + textarea.value;
  return 4;
}

function renderPlainMessageText(text) {
  var lines = String(text || "").split(/\r?\n/).map(function(line) {
    return escapeHtml(line);
  });
  return "<p>" + lines.join("<br>") + "</p>";
}

function renderInlineMarkdown(text) {
  var html = escapeHtml(text);
  var codeTokens = [];

  html = html.replace(/`([^`\n]+)`/g, function(_, code) {
    var token = "\u0000CODE" + codeTokens.length + "\u0000";
    codeTokens.push('<code>' + code + '</code>');
    return token;
  });

  html = html.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/gi, function(_, label, url) {
    var safeUrl = sanitizeMarkdownUrl(url);
    if (!safeUrl) return label;
    return '<a href="' + escapeHtml(safeUrl) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>';
  });

  html = html
    .replace(/\|\|(.+?)\|\|/gs, '<span class="message-spoiler" tabindex="0">$1</span>')
    .replace(/\*\*(.+?)\*\*/gs, '<strong>$1</strong>')
    .replace(/\+\+(.+?)\+\+/gs, '<u>$1</u>')
    .replace(/~~(.+?)~~/gs, '<s>$1</s>')
    .replace(/(^|[^\*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>');

  codeTokens.forEach(function(codeHtml, index) {
    html = html.replace("\u0000CODE" + index + "\u0000", codeHtml);
  });
  return html;
}

function renderCodeBlockMarkdown(code, language) {
  var safeLanguage = String(language || "").trim().replace(/[^a-z0-9_+.#-]/gi, "");
  var languageAttr = safeLanguage ? ' data-language="' + escapeHtml(safeLanguage) + '"' : "";
  return '<pre class="message-code-block"><code' + languageAttr + '>' + escapeHtml(code).replace(/\n$/, "") + '</code></pre>';
}

function splitMarkdownTableRow(line) {
  var value = String(line || "").trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|")) value = value.slice(0, -1);
  return value.split("|").map(function(cell) {
    return cell.trim();
  });
}

function isMarkdownTableSeparator(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(String(line || ""));
}

function renderMarkdownTable(headerLine, separatorLine, bodyLines) {
  var headers = splitMarkdownTableRow(headerLine);
  var alignments = splitMarkdownTableRow(separatorLine).map(function(cell) {
    if (/^:-+:$/.test(cell)) return "center";
    if (/-+:$/.test(cell)) return "right";
    return "";
  });
  var rows = bodyLines.map(splitMarkdownTableRow);
  var html = '<div class="message-table-wrap"><table class="message-markdown-table"><thead><tr>';
  headers.forEach(function(header, index) {
    var alignAttr = alignments[index] ? ' style="text-align: ' + alignments[index] + '"' : "";
    html += '<th' + alignAttr + '>' + renderInlineMarkdown(header) + '</th>';
  });
  html += '</tr></thead><tbody>';
  rows.forEach(function(row) {
    html += '<tr>';
    headers.forEach(function(_, index) {
      var alignAttr = alignments[index] ? ' style="text-align: ' + alignments[index] + '"' : "";
      html += '<td' + alignAttr + '>' + renderInlineMarkdown(row[index] || "") + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  return html;
}

function renderMarkdownList(items, ordered) {
  var tag = ordered ? "ol" : "ul";
  return '<' + tag + ' class="message-markdown-list">' + items.map(function(item) {
    return '<li>' + renderInlineMarkdown(item) + '</li>';
  }).join("") + '</' + tag + '>';
}

function renderToolBlockMarkdown(kind, lines) {
  var isResponse = kind === "response";
  var title = isResponse
    ? (currentLanguage === "zh" ? "工具结果" : "Tool result")
    : (currentLanguage === "zh" ? "工具调用" : "Tool call");
  var language = isResponse ? "json" : "xml";
  return '<details class="message-tool-block"><summary>' + escapeHtml(title) + '</summary>'
    + renderCodeBlockMarkdown(lines.join("\n"), language)
    + '</details>';
}

function renderMessageMarkdown(text) {
  var lines = String(text || "").split(/\r?\n/);
  var html = "";
  var paragraph = [];
  var codeFence = null;
  var codeLines = [];
  var toolBlock = null;
  var toolLines = [];

  function flushParagraph() {
    if (!paragraph.length) return;
    html += '<p>' + paragraph.map(renderInlineMarkdown).join('<br>') + '</p>';
    paragraph = [];
  }

  function flushCodeBlock() {
    if (codeFence === null) return;
    html += renderCodeBlockMarkdown(codeLines.join("\n"), codeFence);
    codeFence = null;
    codeLines = [];
  }

  function flushToolBlock() {
    if (toolBlock === null) return;
    html += renderToolBlockMarkdown(toolBlock, toolLines);
    toolBlock = null;
    toolLines = [];
  }

  for (var i = 0; i < lines.length; i += 1) {
    var line = lines[i];
    var fenceMatch = line.match(/^\s*```([a-z0-9_+.#-]*)\s*$/i);
    if (fenceMatch) {
      if (codeFence !== null) {
        flushCodeBlock();
      } else {
        flushParagraph();
        codeFence = fenceMatch[1] || "";
        codeLines = [];
      }
      continue;
    }
    if (codeFence !== null) {
      codeLines.push(line);
      continue;
    }

    if (toolBlock !== null) {
      var closeTag = toolBlock === "response" ? "</function_response>" : "</function_calls>";
      if (line.trim() === closeTag) {
        flushToolBlock();
      } else {
        toolLines.push(line);
      }
      continue;
    }

    if (line.trim() === "<function_calls>" || line.trim() === "<function_response>") {
      flushParagraph();
      toolBlock = line.trim() === "<function_response>" ? "response" : "call";
      toolLines = [];
      continue;
    }

    if (line.indexOf("|") !== -1 && i + 1 < lines.length && isMarkdownTableSeparator(lines[i + 1])) {
      flushParagraph();
      var bodyLines = [];
      i += 2;
      while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim() !== "") {
        bodyLines.push(lines[i]);
        i += 1;
      }
      i -= 1;
      html += renderMarkdownTable(line, lines[i - bodyLines.length], bodyLines);
      continue;
    }

    var headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      html += '<h' + headingMatch[1].length + '>' + renderInlineMarkdown(headingMatch[2]) + '</h' + headingMatch[1].length + '>';
      continue;
    }

    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
      flushParagraph();
      html += '<hr class="message-markdown-rule">';
      continue;
    }

    var listMatch = line.match(/^\s*([-*+])\s+(.+)$/);
    var orderedMatch = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (listMatch || orderedMatch) {
      flushParagraph();
      var ordered = Boolean(orderedMatch);
      var items = [];
      while (i < lines.length) {
        var current = lines[i];
        var currentMatch = ordered
          ? current.match(/^\s*\d+[.)]\s+(.+)$/)
          : current.match(/^\s*[-*+]\s+(.+)$/);
        if (!currentMatch) break;
        items.push(currentMatch[1]);
        i += 1;
      }
      i -= 1;
      html += renderMarkdownList(items, ordered);
      continue;
    }
    if (/^\s*&gt;/.test(escapeHtml(line))) {
      flushParagraph();
      html += '<blockquote>' + renderInlineMarkdown(line.replace(/^\s*>\s?/, "")) + '</blockquote>';
      continue;
    }
    if (line.trim() === "") {
      flushParagraph();
      continue;
    }
    paragraph.push(line);
  }
  flushCodeBlock();
  flushToolBlock();
  flushParagraph();
  return html || "<p></p>";
}

function renderMessageContent(text) {
  var markdownPayload = getMarkdownPayload(text);
  if (markdownPayload === null) {
    return renderPlainMessageText(text);
  }
  return renderMessageMarkdown(markdownPayload);
}

function findLoadedMessageById(messageId) {
  if (messageId === null || messageId === undefined || messageId === "") return null;
  return messages.find(function(candidate) {
    return String(candidate.id) === String(messageId);
  }) || null;
}

function getReplySenderName(replyMsg, conv) {
  if (!replyMsg) return "";
  if (replyMsg.isSelf) {
    return currentUserProfile.username || (currentLanguage === "zh" ? "我" : "You");
  }
  return getMessageSenderName(replyMsg, conv);
}

function getReplyPreviewHtml(replyMsg) {
  if (!replyMsg) return "";
  const text = getMessageReplyPreviewText(replyMsg);
  const preview = text.length > 120 ? text.slice(0, 120) + "..." : text;
  return renderInlineMarkdown(preview || (currentLanguage === "zh" ? "消息" : "Message"));
}

function renderInlineReplyQuote(msg, conv) {
  const replyId = msg && (msg.reply_to_message_id || msg.reply_to);
  const replyMsg = findLoadedMessageById(replyId);
  if (!replyMsg) return "";
  return '<button type="button" class="message-reply-quote" data-reply-message-id="' + escapeHtml(replyMsg.id) + '">'
    + '<span class="message-reply-sender">' + escapeHtml(getReplySenderName(replyMsg, conv)) + '</span>'
    + '<span class="message-reply-preview">' + getReplyPreviewHtml(replyMsg) + '</span>'
    + '</button>';
}

function getChatTextarea() {
  return document.getElementById("chat-input-textarea");
}

function textHasMarkdownSyntax(text) {
  return /(\*\*[^*]+\*\*|\*[^*\n]+\*|_[^_\n]+_|\+\+[^+]+\+\+|~~[^~]+~~|`[^`]+`|\|\|[^|]+\|\||^\s*>|\[[^\]]+\]\((https?:\/\/|mailto:))/m.test(String(text || ""));
}

function updateMarkdownPreview() {
  var textarea = getChatTextarea();
  var preview = document.getElementById("chat-markdown-preview");
  if (!textarea || !preview) return;

  var value = textarea.value || "";
  var markdownPayload = getMarkdownPayload(value);
  if (markdownPayload === null || !markdownPayload.trim()) {
    preview.classList.add("hidden");
    preview.innerHTML = "";
    return;
  }

  preview.innerHTML = renderMessageMarkdown(markdownPayload);
  preview.classList.remove("hidden");
}

function wrapTextareaSelection(format) {
  var textarea = getChatTextarea();
  if (!textarea || textarea.disabled) return;

  var originalStart = textarea.selectionStart || 0;
  var originalEnd = textarea.selectionEnd || 0;
  var prefixOffset = ensureMarkdownPrefix(textarea);
  var start = originalStart + prefixOffset;
  var end = originalEnd + prefixOffset;
  if (prefixOffset) {
    textarea.setSelectionRange(start, end);
  }
  var selected = textarea.value.slice(start, end);
  var hasSelection = end > start;
  var fallback = selected || (format === "link" ? "link text" : "text");
  var before = "";
  var after = "";
  var replacement = fallback;

  if (format === "bold") {
    before = "**"; after = "**";
  } else if (format === "italic") {
    before = "*"; after = "*";
  } else if (format === "underline") {
    before = "++"; after = "++";
  } else if (format === "strike") {
    before = "~~"; after = "~~";
  } else if (format === "code") {
    before = "`"; after = "`";
    replacement = fallback.replace(/\n/g, " ");
  } else if (format === "spoiler") {
    before = "||"; after = "||";
  } else if (format === "quote") {
    replacement = fallback.split(/\r?\n/).map(function(line) {
      return "> " + line;
    }).join("\n");
  } else if (format === "link") {
    var currentUrl = /^https?:\/\//i.test(selected) ? selected : "https://";
    var url = window.prompt(currentLanguage === "zh" ? "输入链接地址" : "Enter link URL", currentUrl);
    if (!url) return;
    url = sanitizeMarkdownUrl(url);
    if (!url) {
      window.showToast(currentLanguage === "zh" ? "链接必须以 http、https 或 mailto 开头" : "Links must start with http, https, or mailto.");
      return;
    }
    replacement = "[" + fallback + "](" + url + ")";
  }

  if (format !== "quote" && format !== "link") {
    replacement = before + replacement + after;
  }

  textarea.setRangeText(replacement, start, end, "end");
  var selectionStart = format === "link" || format === "quote" ? start : start + before.length;
  var selectionEnd = format === "link" || format === "quote"
    ? start + replacement.length
    : selectionStart + fallback.length;
  textarea.focus();
  if (hasSelection && format !== "link" && format !== "quote") {
    textarea.setSelectionRange(selectionStart, selectionEnd);
  }
  adjustTextareaHeight(textarea);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  updateMarkdownPreview();
  updateFormatToolbarVisibility();
}

function updateFormatToolbarVisibility() {
  var toolbar = document.getElementById("chat-format-toolbar");
  var textarea = getChatTextarea();
  if (!toolbar || !textarea) return;
  var hasSelection = document.activeElement === textarea && textarea.selectionEnd > textarea.selectionStart;
  toolbar.classList.toggle("hidden", !hasSelection);
}

function setupFormatToolbar() {
  var toolbar = document.getElementById("chat-format-toolbar");
  var textarea = getChatTextarea();
  if (!toolbar || !textarea || toolbar.dataset.bound === "true") return;
  toolbar.dataset.bound = "true";

  toolbar.addEventListener("mousedown", function(event) {
    event.preventDefault();
  });
  toolbar.addEventListener("click", function(event) {
    var button = event.target.closest("[data-format]");
    if (!button) return;
    wrapTextareaSelection(button.dataset.format);
  });
  ["select", "keyup", "mouseup", "focus", "input"].forEach(function(eventName) {
    textarea.addEventListener(eventName, updateFormatToolbarVisibility);
  });
  textarea.addEventListener("input", updateMarkdownPreview);
  textarea.addEventListener("blur", function() {
    setTimeout(updateFormatToolbarVisibility, 120);
  });
  updateMarkdownPreview();
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

  // Capture reply info before clearing
  const replyInfo = replyToMessage;
  if (replyToMessage) {
    // Clear reply state
    replyToMessage = null;
    if (typeof MessageActions !== 'undefined' && MessageActions.cancelReply) {
      MessageActions.cancelReply();
    } else {
      var banner = document.getElementById('reply-quote-banner');
      if (banner) banner.style.display = 'none';
    }
  }

  // Optimistic render
  const tempMsg = {
    id: clientMsgId,
    text: text,
    created_at: new Date().toISOString(),
    time: time,
    isSelf: true,
    status: "sending",
    reply_to: replyInfo ? replyInfo.id : null,
  };
  messages.push(tempMsg);
  appendMessageElement(tempMsg);
  scrollToBottom();
  clearConversationDraft(conv.id);
  updateSidebarPreview(conv, text, time);

  textarea.value = "";
  adjustTextareaHeight(textarea);
  updateMarkdownPreview();

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
              recipients: result.recipients,
              reply_to_message_id: tempMsg.reply_to || undefined
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
            reply_to_message_id: tempMsg.reply_to || undefined,
          })
        });
        handleMessageAccepted({ data: accepted });
    }
  } catch (err) {
    console.error("Send failed:", err);
    logToCryptoConsole("[Send Error] " + err.message);
    window.showToast(err.message || "Send failed.");
    const idx = messages.findIndex(m => m.id === clientMsgId);
    if (idx >= 0) { messages[idx].status = "failed"; patchMessageStatusInPlace(messages[idx]); }
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
// Create group (Sidebar Redesign)
// ============================================================================

let selectedGroupAvatarBase64 = "";
let selectedMemberIds = new Set();

function resetGroupCreationFlow() {
  selectedGroupAvatarBase64 = "";
  selectedMemberIds.clear();
  
  const searchInput = document.getElementById("group-add-search-input");
  if (searchInput) searchInput.value = "";
  
  const checkboxes = document.querySelectorAll(".group-member-checkbox-custom");
  checkboxes.forEach(cb => {
    cb.checked = false;
    const row = cb.closest(".group-add-member-item");
    if (row) row.classList.remove("bg-bgSearch/40");
  });
  
  const listCard = document.getElementById("group-add-members-list-card");
  if (listCard) listCard.classList.remove("hidden");
  const emptyState = document.getElementById("group-add-empty-state");
  if (emptyState) emptyState.classList.add("hidden");
  
  const nameInput = document.getElementById("group-create-name-input");
  if (nameInput) nameInput.value = "";
  
  const avatarPreview = document.getElementById("group-avatar-preview");
  if (avatarPreview) {
    avatarPreview.src = "";
    avatarPreview.classList.add("hidden");
  }
  
  const avatarPlaceholder = document.getElementById("group-avatar-placeholder");
  if (avatarPlaceholder) avatarPlaceholder.classList.remove("hidden");
  
  const fileInput = document.getElementById("group-avatar-file-input");
  if (fileInput) fileInput.value = "";
  
  filterGroupAddMembers("");
  updateGroupMemberCount();
}

function filterGroupAddMembers(query) {
  const trimmed = (query || "").toLowerCase().trim();
  const items = document.querySelectorAll(".group-add-member-item");
  let visibleCount = 0;
  
  items.forEach(item => {
    const username = (item.getAttribute("data-username") || "").toLowerCase();
    const nickname = (item.getAttribute("data-nickname") || "").toLowerCase();
    if (!trimmed || username.includes(trimmed) || nickname.includes(trimmed)) {
      item.classList.remove("hidden");
      visibleCount++;
    } else {
      item.classList.add("hidden");
    }
  });
  
  const listCard = document.getElementById("group-add-members-list-card");
  const emptyState = document.getElementById("group-add-empty-state");
  
  if (visibleCount === 0 && trimmed.length >= 2) {
    fetch('/contacts/search/?q=' + encodeURIComponent(trimmed))
      .then(r => r.json())
      .then(data => {
        if (data.results && data.results.length > 0) {
          if (emptyState) emptyState.classList.add("hidden");
          if (listCard) listCard.classList.remove("hidden");
          
          data.results.forEach(user => {
            let existing = document.querySelector(`.group-add-member-item[data-user-id="${user.id}"]`);
            if (!existing) {
              const row = document.createElement("div");
              row.className = "settings-template-row group-add-member-item cursor-pointer";
              row.setAttribute("data-user-id", user.id);
              row.setAttribute("data-username", user.username);
              row.setAttribute("data-nickname", user.nickname || "");
              
              const isChecked = selectedMemberIds.has(String(user.id)) ? "checked" : "";
              const initials = user.username.slice(0, 1).toUpperCase();
              
              row.innerHTML = `
                <div class="flex items-center justify-center" onclick="event.stopPropagation();">
                  <input type="checkbox" class="group-member-checkbox-custom w-5 h-5 rounded border-borderColor text-brand-light focus:ring-brand-light bg-transparent" value="${user.id}" ${isChecked} onclick="updateGroupMemberCount()">
                </div>
                <div class="w-10 h-10 rounded-full text-white flex items-center justify-center font-bold text-sm bg-brand-light flex-shrink-0 overflow-hidden">
                  <span>${initials}</span>
                </div>
                <div class="settings-template-row-main">
                  <span class="settings-template-row-title">${user.nickname || user.username}</span>
                  <span class="settings-template-row-subtitle">${user.user_type === 'agent' ? '智能代理' : (user.user_type === 'bot' ? '机器人' : (user.username.toLowerCase().endsWith('bot') ? '机器人' : '未添加联系人'))}</span>
                </div>
              `;
              row.onclick = () => toggleGroupMemberSelection(row, user.id);
              if (listCard) listCard.appendChild(row);
            }
          });
        } else {
          if (listCard && listCard.children.length === 0) listCard.classList.add("hidden");
          if (emptyState) emptyState.classList.remove("hidden");
        }
      });
  } else {
    if (visibleCount === 0) {
      if (listCard) listCard.classList.add("hidden");
      if (emptyState) emptyState.classList.remove("hidden");
    } else {
      if (listCard) listCard.classList.remove("hidden");
      if (emptyState) emptyState.classList.add("hidden");
    }
  }
}

function toggleGroupMemberSelection(rowEl, userId) {
  const cb = rowEl.querySelector(".group-member-checkbox-custom");
  if (cb) {
    cb.checked = !cb.checked;
    if (cb.checked) {
      rowEl.classList.add("bg-bgSearch/40");
      selectedMemberIds.add(String(userId));
    } else {
      rowEl.classList.remove("bg-bgSearch/40");
      selectedMemberIds.delete(String(userId));
    }
    updateGroupMemberCount();
  }
}

function updateGroupMemberCount() {
  const checkboxes = document.querySelectorAll(".group-member-checkbox-custom");
  checkboxes.forEach(cb => {
    const row = cb.closest(".group-add-member-item");
    if (cb.checked) {
      selectedMemberIds.add(String(cb.value));
      if (row) row.classList.add("bg-bgSearch/40");
    } else {
      selectedMemberIds.delete(String(cb.value));
      if (row) row.classList.remove("bg-bgSearch/40");
    }
  });

  const nextBtn = document.getElementById("group-add-next-btn");
  if (nextBtn) {
    if (selectedMemberIds.size > 0) {
      nextBtn.classList.remove("opacity-50", "pointer-events-none");
    } else {
      nextBtn.classList.add("opacity-50", "pointer-events-none");
    }
  }
}

function goToGroupCreateFinalStep() {
  if (selectedMemberIds.size === 0) {
    window.showToast("请选择至少一位成员");
    return;
  }
  
  navigateSidebar('group-create-final');
  
  const countEl = document.getElementById("group-create-members-count");
  if (countEl) countEl.textContent = `${selectedMemberIds.size} 人`;
  
  const listEl = document.getElementById("group-create-selected-list");
  if (listEl) {
    listEl.innerHTML = "";
    selectedMemberIds.forEach(uid => {
      const row = document.querySelector(`.group-add-member-item[data-user-id="${uid}"]`);
      if (row) {
        const title = row.querySelector(".settings-template-row-title").textContent.trim();
        const avatarHtml = row.querySelector(".w-10").innerHTML;
        
        const item = document.createElement("div");
        item.className = "settings-template-row py-2";
        item.innerHTML = `
          <div class="w-8 h-8 rounded-full text-white flex items-center justify-center font-bold text-xs flex-shrink-0 overflow-hidden bg-brand-light">
            ${avatarHtml}
          </div>
          <div class="settings-template-row-main">
            <span class="settings-template-row-title text-sm">${title}</span>
          </div>
          <button class="p-1 rounded-full text-textSecondary hover:text-red-500 transition-colors" onclick="removeSelectedMemberStep2('${uid}')">
            <i data-lucide="x" class="w-4 h-4"></i>
          </button>
        `;
        listEl.appendChild(item);
      }
    });
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }
}

function removeSelectedMemberStep2(uid) {
  selectedMemberIds.delete(String(uid));
  
  const cb = document.querySelector(`.group-member-checkbox-custom[value="${uid}"]`);
  if (cb) cb.checked = false;
  
  const row = document.querySelector(`.group-add-member-item[data-user-id="${uid}"]`);
  if (row) row.classList.remove("bg-bgSearch/40");
  
  goToGroupCreateFinalStep();
  if (selectedMemberIds.size === 0) {
    navigateSidebar('group-add-members');
  }
}

function previewGroupAvatar(input) {
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = function(e) {
      selectedGroupAvatarBase64 = e.target.result;
      const preview = document.getElementById("group-avatar-preview");
      if (preview) {
        preview.src = e.target.result;
        preview.classList.remove("hidden");
      }
      const placeholder = document.getElementById("group-avatar-placeholder");
      if (placeholder) placeholder.classList.add("hidden");
    };
    reader.readAsDataURL(input.files[0]);
  }
}

async function submitCreateGroupForm() {
  const nameInput = document.getElementById("group-create-name-input");
  const name = nameInput ? nameInput.value.trim() : "";
  if (!name) {
    window.showToast("请输入群聊名称");
    return;
  }
  
  const submitBtn = document.getElementById("group-create-submit-btn");
  if (submitBtn) submitBtn.disabled = true;
  
  try {
    const data = await apiFetch("/api/groups/", {
      method: "POST",
      body: JSON.stringify({
        name: name,
        avatar: selectedGroupAvatarBase64 || ""
      })
    });
    
    for (const uid of selectedMemberIds) {
      try {
        await apiFetch("/api/groups/" + data.id + "/invite/", {
          method: "POST",
          body: JSON.stringify({ user_id: parseInt(uid) })
        });
      } catch (e) {
        console.warn("Failed to invite", uid, e);
      }
    }
    
    await fetchConversations();
    if (data.id) selectChat(data.id.toString());
    
    navigateSidebar('chat');
    window.showToast("群组已创建");
    resetGroupCreationFlow();
  } catch (err) {
    console.error("Create group failed:", err);
    window.showToast("创建群组失败，请重试");
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// ============================================================================
// Fingerprint modal
// ============================================================================

async function showFingerprintModal() {
  if (!activeChatId) return;
  const conv = conversationsById[activeChatId];
  if (!conv || !conv.is_secure) return;
  const keyEl = document.getElementById("fp-modal-key");
  const statusEl = document.getElementById("fp-modal-status");
  const actionBtn = document.getElementById("fp-modal-trust-btn");
  const untrustBtn = document.getElementById("fp-modal-untrust-btn");
  const warningEl = document.getElementById("fp-modal-warning");
  if (keyEl) {
    keyEl.textContent = currentLanguage === 'zh' ? '正在加载真实指纹...' : 'Loading real fingerprint...';
  }
  if (statusEl) {
    statusEl.className = 'security-status-chip security-status-loading';
    statusEl.textContent = currentLanguage === 'zh' ? '正在加载信任状态' : 'Loading trust status';
  }
  if (warningEl) warningEl.classList.add("hidden");
  if (actionBtn) {
    actionBtn.classList.add("hidden");
    actionBtn.disabled = true;
  }
  if (untrustBtn) {
    untrustBtn.classList.add("hidden");
    untrustBtn.disabled = true;
  }
  const modal = document.getElementById("fingerprint-modal");
  if (modal) { modal.classList.remove("hidden"); modal.classList.add("flex"); }
  if (conv.type === "single" && conv.peer_id) {
    const contactStatus = await fetchContactKeyStatus(conv.peer_id);
    if (String(activeChatId) !== String(conv.id) || !keyEl) return;
    const activeKey = getActiveKeyStatus(contactStatus);
    const keyChanged = contactKeyHasChanged(contactStatus);
    keyEl.textContent = activeKey
      ? `v${activeKey.key_version}: ${formatFingerprint(activeKey.key_fingerprint)}`
      : formatFingerprint(null);
    renderFingerprintModalState(conv, activeKey, keyChanged);
  } else if (keyEl) {
    keyEl.textContent = currentLanguage === 'zh'
      ? '群聊没有单一联系人指纹，请分别验证成员公钥。'
      : 'Group chats do not have one peer fingerprint; verify member keys individually.';
    if (statusEl) {
      statusEl.className = 'security-status-chip security-status-unverified';
      statusEl.textContent = currentLanguage === 'zh' ? '请在成员列表逐个验证' : 'Verify members individually';
    }
  }
}

function renderFingerprintModalState(conv, activeKey, keyChanged) {
  const statusEl = document.getElementById("fp-modal-status");
  const actionBtn = document.getElementById("fp-modal-trust-btn");
  const untrustBtn = document.getElementById("fp-modal-untrust-btn");
  const warningEl = document.getElementById("fp-modal-warning");
  const explainEl = document.getElementById("fp-modal-explain-container");

  if (warningEl) {
    warningEl.classList.toggle("hidden", !keyChanged);
    warningEl.textContent = currentLanguage === 'zh'
      ? '高风险警告：这个联系人当前公钥与此前已验证的指纹不同。请通过其他渠道确认后再重新验证。'
      : 'High risk: this contact active key differs from a previously verified fingerprint. Confirm out-of-band before re-verifying.';
  }

  if (explainEl) {
    explainEl.innerHTML = currentLanguage === 'zh'
      ? '请与 <strong id="fp-modal-name">' + escapeHtml(conv.name || 'this contact') + '</strong> 通过其他渠道核对下方真实指纹：'
      : 'Verify with <strong id="fp-modal-name">' + escapeHtml(conv.name || 'this contact') + '</strong> that the real fingerprint below matches:';
  }

  if (!activeKey) {
    if (statusEl) {
      statusEl.className = 'security-status-chip security-status-missing';
      statusEl.textContent = currentLanguage === 'zh' ? '缺少公钥' : 'No public key';
    }
    if (actionBtn) actionBtn.classList.add("hidden");
    if (untrustBtn) untrustBtn.classList.add("hidden");
    return;
  }

  const trusted = activeKey.trust_status === 'trusted' || activeKey.trust_status === 'verified';
  if (statusEl) {
    statusEl.className = 'security-status-chip ' + (keyChanged ? 'security-status-danger' : trusted ? 'security-status-verified' : 'security-status-unverified');
    statusEl.textContent = keyChanged
      ? (currentLanguage === 'zh' ? '密钥已变更' : 'Key changed')
      : trusted
        ? (currentLanguage === 'zh' ? '已验证' : 'Verified')
        : (currentLanguage === 'zh' ? '未验证' : 'Unverified');
  }

  if (actionBtn) {
    actionBtn.classList.remove("hidden");
    actionBtn.disabled = false;
    actionBtn.textContent = trusted && !keyChanged
      ? (currentLanguage === 'zh' ? '重新验证当前指纹' : 'Re-verify Current Fingerprint')
      : (currentLanguage === 'zh' ? '标记为已验证' : 'Mark as Verified');
  }
  if (untrustBtn) {
    untrustBtn.classList.toggle("hidden", !trusted);
    untrustBtn.disabled = !trusted;
  }
}

async function trustActiveFingerprintFromModal() {
  const conv = conversationsById[activeChatId];
  if (!conv || conv.type !== "single" || !conv.peer_id) return;
  const actionBtn = document.getElementById("fp-modal-trust-btn");
  if (actionBtn) actionBtn.disabled = true;
  try {
    const result = await setContactKeyTrust(conv.peer_id, 'verified');
    const activeKey = getActiveKeyStatus(result.refreshed);
    renderFingerprintModalState(conv, activeKey, false);
    updateDetailsPanel(conv);
    window.showToast(currentLanguage === 'zh' ? '联系人密钥已标记为已验证' : 'Contact key marked as verified.');
  } catch (err) {
    window.showToast(err.message || (currentLanguage === 'zh' ? '验证失败' : 'Verification failed'));
  } finally {
    if (actionBtn) actionBtn.disabled = false;
  }
}

async function untrustActiveFingerprintFromModal() {
  const conv = conversationsById[activeChatId];
  if (!conv || conv.type !== "single" || !conv.peer_id) return;
  const untrustBtn = document.getElementById("fp-modal-untrust-btn");
  if (untrustBtn) untrustBtn.disabled = true;
  try {
    await clearContactKeyTrust(conv.peer_id);
    const refreshed = await fetchContactKeyStatus(conv.peer_id, { force: true });
    renderFingerprintModalState(conv, getActiveKeyStatus(refreshed), contactKeyHasChanged(refreshed));
    updateDetailsPanel(conv);
    window.showToast(currentLanguage === 'zh' ? '已撤销当前密钥信任' : 'Trust removed for the current key.');
  } catch (err) {
    window.showToast(err.message || (currentLanguage === 'zh' ? '撤销失败' : 'Could not remove trust'));
  } finally {
    if (untrustBtn) untrustBtn.disabled = false;
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

function applyImagePreviewToBubble(div, fileId, convType) {
  var frame = div.querySelector('.file-image-frame');
  if (!frame || !fileId) return;
  var msgId = div.querySelector('.message-bubble-custom') && div.querySelector('.message-bubble-custom').getAttribute('data-message-id');
  var msg = findLoadedMessageById(msgId);

  function renderLoaded(item) {
    var payload = typeof item === 'string' ? { url: item } : item;
    frame.classList.remove('is-loading', 'is-error');
    frame.classList.add('is-loaded');
    frame.innerHTML = '<img class="file-image-preview-img" src="' + escapeHtml(payload.url) + '" alt="">';
    frame.onclick = function (event) {
      event.preventDefault();
      event.stopPropagation();
      openImagePreviewViewer({
        fileId: fileId,
        url: payload.url,
        blob: payload.blob || null,
        metadata: payload.metadata || {},
        message: msg || null,
      });
    };
  }

  function renderError(message) {
    frame.classList.remove('is-loading');
    frame.classList.add('is-error');
    frame.innerHTML =
      '<button type="button" class="file-image-retry">' +
        '<i data-lucide="download" class="w-4 h-4"></i>' +
        '<span>' + escapeHtml(message || 'Download') + '</span>' +
      '</button>';
    var retry = frame.querySelector('.file-image-retry');
    if (retry) {
      retry.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        filePreviewCache.delete(String(fileId));
        applyImagePreviewToBubble(div, fileId, convType);
      });
    }
    if (window.lucide && window.lucide.createIcons) lucide.createIcons({ nodes: frame.querySelectorAll('[data-lucide]') });
  }

  var cacheKey = String(fileId);
  var cached = filePreviewCache.get(cacheKey);
  if (cached && cached.status === 'loaded') {
    renderLoaded(cached);
    return;
  }
  if (cached && cached.status === 'pending') {
    cached.promise.then(function (result) {
      renderLoaded(result);
    }).catch(function () {
      renderError('Retry');
    });
    return;
  }

  frame.classList.add('is-loading');
  frame.innerHTML =
    '<div class="file-image-loading">' +
      '<i data-lucide="loader-2" class="w-6 h-6 animate-spin"></i>' +
    '</div>';
  if (window.lucide && window.lucide.createIcons) lucide.createIcons({ nodes: frame.querySelectorAll('[data-lucide]') });

  if (!window.iChatFileTransfer || !window.iChatFileTransfer.fetchAndDecryptFile) {
    renderError('Download');
    return;
  }

  var promise = window.iChatFileTransfer.fetchAndDecryptFile(parseInt(fileId), convType).then(function (result) {
    if (!result || !result.blob || result.blob.type.indexOf('image/') !== 0) {
      throw new Error('not_image');
    }
    var url = URL.createObjectURL(result.blob);
    var payload = { status: 'loaded', url: url, blob: result.blob, metadata: result.metadata || {} };
    filePreviewCache.set(cacheKey, payload);
    return payload;
  });

  filePreviewCache.set(cacheKey, { status: 'pending', promise: promise });
  promise.then(function (result) {
    renderLoaded(result);
  }).catch(function (err) {
    console.error('Image preview failed:', err);
    filePreviewCache.delete(cacheKey);
    renderError('Retry');
  });
}

function ensureImagePreviewViewer() {
  var viewer = document.getElementById('image-preview-viewer');
  if (viewer) return viewer;

  viewer = document.createElement('div');
  viewer.id = 'image-preview-viewer';
  viewer.className = 'image-preview-viewer hidden';
  viewer.innerHTML =
    '<div class="image-preview-scrim" data-image-viewer-close></div>' +
    '<div class="image-preview-toolbar" role="toolbar" aria-label="Image actions">' +
      '<button type="button" class="image-preview-action" data-image-viewer-share title="Share"><i data-lucide="forward" class="w-5 h-5"></i></button>' +
      '<button type="button" class="image-preview-action" data-image-viewer-download title="Download"><i data-lucide="download" class="w-5 h-5"></i></button>' +
      '<button type="button" class="image-preview-action" data-image-viewer-rotate title="Rotate"><i data-lucide="rotate-ccw" class="w-5 h-5"></i></button>' +
      '<button type="button" class="image-preview-action" data-image-viewer-zoom title="Zoom"><i data-lucide="zoom-in" class="w-5 h-5"></i></button>' +
      '<button type="button" class="image-preview-action" data-image-viewer-close title="Close"><i data-lucide="x" class="w-5 h-5"></i></button>' +
    '</div>' +
    '<div class="image-preview-stage">' +
      '<img class="image-preview-full" alt="">' +
    '</div>' +
    '<div class="image-preview-zoom-panel hidden">' +
      '<i data-lucide="zoom-out" class="w-5 h-5"></i>' +
      '<input type="range" min="1" max="3" step="0.01" value="1" class="image-preview-zoom-range" data-image-viewer-zoom-range>' +
      '<i data-lucide="zoom-in" class="w-5 h-5"></i>' +
    '</div>';
  document.body.appendChild(viewer);

  viewer.addEventListener('click', function (event) {
    if (event.target.closest('[data-image-viewer-close]')) {
      closeImagePreviewViewer();
      return;
    }
    if (event.target.closest('[data-image-viewer-download]')) {
      downloadActiveImageViewer();
      return;
    }
    if (event.target.closest('[data-image-viewer-rotate]')) {
      rotateActiveImageViewer();
      return;
    }
    if (event.target.closest('[data-image-viewer-zoom]')) {
      toggleImageViewerZoomPanel();
      return;
    }
    if (event.target.closest('[data-image-viewer-share]')) {
      shareActiveImageViewer();
    }
  });

  var zoomRange = viewer.querySelector('[data-image-viewer-zoom-range]');
  if (zoomRange) {
    zoomRange.addEventListener('input', function () {
      setActiveImageViewerScale(parseFloat(zoomRange.value) || 1);
    });
  }

  var previewStage = viewer.querySelector('.image-preview-stage');
  if (previewStage) {
    previewStage.addEventListener('wheel', handleImageViewerWheel, { passive: false });
  }

  var previewImage = viewer.querySelector('.image-preview-full');
  if (previewImage) {
    previewImage.setAttribute('draggable', 'false');
    previewImage.addEventListener('pointerdown', startImageViewerPan);
    previewImage.addEventListener('dragstart', function (event) {
      event.preventDefault();
    });
  }

  document.addEventListener('keydown', function (event) {
    if (!activeImageViewer) return;
    if (event.key === 'Escape') closeImagePreviewViewer();
    if (event.key === '+' || event.key === '=') bumpActiveImageViewerScale(0.2);
    if (event.key === '-' || event.key === '_') bumpActiveImageViewerScale(-0.2);
    if (event.key === 'r' || event.key === 'R') rotateActiveImageViewer();
  });

  return viewer;
}

function updateImageViewerTransform() {
  if (!activeImageViewer) return;
  var viewer = document.getElementById('image-preview-viewer');
  var image = viewer && viewer.querySelector('.image-preview-full');
  if (!image) return;
  var offsetX = Number(activeImageViewer.offsetX) || 0;
  var offsetY = Number(activeImageViewer.offsetY) || 0;
  image.style.transform = 'translate(' + offsetX + 'px, ' + offsetY + 'px) rotate(' + activeImageViewer.rotation + 'deg) scale(' + activeImageViewer.scale + ')';
  viewer.classList.toggle('is-zoomed', activeImageViewer.scale > 1);
  viewer.classList.toggle('is-panning', Boolean(activeImageViewer.isPanning));
  var zoomRange = viewer.querySelector('[data-image-viewer-zoom-range]');
  if (zoomRange && document.activeElement !== zoomRange) {
    zoomRange.value = String(activeImageViewer.scale);
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function startImageViewerPan(event) {
  if (!activeImageViewer || event.button > 0) return;
  var image = event.currentTarget;
  event.preventDefault();
  activeImageViewer.isPanning = true;
  activeImageViewer.panStartX = event.clientX;
  activeImageViewer.panStartY = event.clientY;
  activeImageViewer.panOriginX = Number(activeImageViewer.offsetX) || 0;
  activeImageViewer.panOriginY = Number(activeImageViewer.offsetY) || 0;
  if (image.setPointerCapture) {
    image.setPointerCapture(event.pointerId);
  }
  image.addEventListener('pointermove', moveImageViewerPan);
  image.addEventListener('pointerup', stopImageViewerPan);
  image.addEventListener('pointercancel', stopImageViewerPan);
  updateImageViewerTransform();
}

function moveImageViewerPan(event) {
  if (!activeImageViewer || !activeImageViewer.isPanning) return;
  event.preventDefault();
  activeImageViewer.offsetX = activeImageViewer.panOriginX + event.clientX - activeImageViewer.panStartX;
  activeImageViewer.offsetY = activeImageViewer.panOriginY + event.clientY - activeImageViewer.panStartY;
  updateImageViewerTransform();
}

function stopImageViewerPan(event) {
  var image = event.currentTarget;
  if (activeImageViewer) {
    activeImageViewer.isPanning = false;
    activeImageViewer.panStartX = 0;
    activeImageViewer.panStartY = 0;
    activeImageViewer.panOriginX = 0;
    activeImageViewer.panOriginY = 0;
  }
  if (image && image.hasPointerCapture && image.hasPointerCapture(event.pointerId)) {
    image.releasePointerCapture(event.pointerId);
  }
  if (image) {
    image.removeEventListener('pointermove', moveImageViewerPan);
    image.removeEventListener('pointerup', stopImageViewerPan);
    image.removeEventListener('pointercancel', stopImageViewerPan);
  }
  updateImageViewerTransform();
}

function handleImageViewerWheel(event) {
  if (!activeImageViewer) return;
  if (event.target.closest('.image-preview-toolbar, .image-preview-zoom-panel')) return;
  event.preventDefault();
  var factor = Math.pow(1.0015, -event.deltaY);
  setActiveImageViewerScale(activeImageViewer.scale * factor);
}

function imageViewerFilename() {
  if (!activeImageViewer) return 'image';
  var name = activeImageViewer.metadata && activeImageViewer.metadata.original_name;
  return name || ('image-' + activeImageViewer.fileId + '.png');
}

function openImagePreviewViewer(payload) {
  var viewer = ensureImagePreviewViewer();
  var image = viewer.querySelector('.image-preview-full');
  var zoomPanel = viewer.querySelector('.image-preview-zoom-panel');
  var zoomRange = viewer.querySelector('[data-image-viewer-zoom-range]');
  activeImageViewer = {
    fileId: payload.fileId,
    url: payload.url,
    blob: payload.blob,
    metadata: payload.metadata || {},
    message: payload.message || null,
    rotation: 0,
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    isPanning: false,
  };
  image.src = payload.url;
  if (zoomPanel) zoomPanel.classList.add('hidden');
  if (zoomRange) zoomRange.value = '1';
  viewer.classList.remove('hidden');
  document.body.classList.add('image-preview-open');
  updateImageViewerTransform();
  if (window.lucide && window.lucide.createIcons) {
    window.lucide.createIcons({ nodes: viewer.querySelectorAll('[data-lucide]') });
  }
}

function closeImagePreviewViewer() {
  var viewer = document.getElementById('image-preview-viewer');
  if (viewer) {
    viewer.classList.add('hidden');
    viewer.classList.remove('is-panning');
  }
  document.body.classList.remove('image-preview-open');
  activeImageViewer = null;
}

function downloadActiveImageViewer() {
  if (!activeImageViewer) return;
  var a = document.createElement('a');
  a.href = activeImageViewer.url;
  a.download = imageViewerFilename();
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function rotateActiveImageViewer() {
  if (!activeImageViewer) return;
  activeImageViewer.rotation = (activeImageViewer.rotation - 90) % 360;
  updateImageViewerTransform();
}

function toggleImageViewerZoomPanel() {
  if (!activeImageViewer) return;
  var viewer = document.getElementById('image-preview-viewer');
  var panel = viewer && viewer.querySelector('.image-preview-zoom-panel');
  var range = viewer && viewer.querySelector('[data-image-viewer-zoom-range]');
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden') && range) {
    range.value = String(activeImageViewer.scale);
    range.focus();
  }
}

function setActiveImageViewerScale(scale) {
  if (!activeImageViewer) return;
  activeImageViewer.scale = Math.min(3, Math.max(1, Number(scale) || 1));
  if (activeImageViewer.scale === 1) {
    activeImageViewer.offsetX = 0;
    activeImageViewer.offsetY = 0;
  }
  updateImageViewerTransform();
}

function bumpActiveImageViewerScale(delta) {
  if (!activeImageViewer) return;
  setActiveImageViewerScale(activeImageViewer.scale + delta);
}

async function shareActiveImageViewer() {
  if (!activeImageViewer) return;
  try {
    if (activeImageViewer.blob && navigator.share) {
      var file = new File([activeImageViewer.blob], imageViewerFilename(), {
        type: activeImageViewer.blob.type || 'image/png',
      });
      if (!navigator.canShare || navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: imageViewerFilename() });
        return;
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(imageViewerFilename());
      window.showToast && window.showToast('Image name copied. Download to share this file.');
      return;
    }
    window.showToast && window.showToast('Sharing is not supported in this browser.');
  } catch (err) {
    if (err && err.name === 'AbortError') return;
    console.error('Image share failed:', err);
    window.showToast && window.showToast('Share failed.');
  }
}

/**
 * Return the HTML for a message status icon (<i data-lucide="…">).
 * Used by both text and file bubble renderers so the DOM is consistent,
 * and by patchMessageStatusInPlace() for in-place updates.
 */
function renderStatusIconHtml(msg) {
  var iconName = 'check';
  var iconClass = 'msg-status-icon';
  var tooltip = '';
  var extraClass = '';

  if (msg.status === 'sending') {
    iconName = 'clock';
    iconClass += ' msg-status-sending';
    tooltip = currentLanguage === 'zh' ? '发送中...' : 'Sending...';
  } else if (msg.status === 'sent') {
    iconName = 'check';
    tooltip = currentLanguage === 'zh' ? '已发送' : 'Sent';
  } else if (msg.status === 'delivered') {
    iconName = 'check-check';
    tooltip = currentLanguage === 'zh' ? '已送达' : 'Delivered';
  } else if (msg.status === 'read') {
    iconName = 'check-check';
    iconClass += ' msg-status-read';
    extraClass = 'text-brand-light dark:text-brand-dark';
    tooltip = currentLanguage === 'zh' ? '已读' : 'Read';
  } else if (msg.status === 'failed' || msg.client_status === 'failed') {
    iconName = 'alert-circle';
    iconClass += ' msg-status-failed';
    tooltip = currentLanguage === 'zh' ? '发送失败' : 'Failed to send';
  }

  return '<i data-lucide="' + iconName + '" class="w-3.5 h-3.5 ' + iconClass + (extraClass ? ' ' + extraClass : '') + '" title="' + tooltip + '"></i>';
}

function renderFileMessageBubble(div, msg, conv, groupMeta) {
  var _a = groupMeta || {}, isConsecutive = _a.isConsecutive, isFirstInGroup = _a.isFirstInGroup, isLastInGroup = _a.isLastInGroup;
  var messageType = msg.message_type || 'file';
  var fileData = msg.file || {};
  var fileId = msg.file_id || fileData.file_id;
  var kindLabel = messageType === 'image' ? 'Image' : messageType === 'sticker' ? 'Sticker' : 'File';

  var bubbleClass = msg.isSelf ? 'bubble-self' : 'bubble-peer';
  var messageTime = escapeHtml(msg.time || '');

  var avatarHtml = '';
  var senderName = msg.isSelf ? 'You' : getMessageSenderName(msg, conv);
  var senderNameHtml = '';
  if (!msg.isSelf && isFirstInGroup) {
    senderNameHtml = '<div class="message-sender-line"><span class="message-sender-name">' + escapeHtml(senderName) + '</span></div>';
  }
  if (!msg.isSelf) {
    if (isLastInGroup) {
      var avatarInfo = getSenderAvatarInfo(senderName, msg, conv);
      if (avatarInfo.avatarUrl) {
        avatarHtml = '<div class="message-avatar" title="' + escapeHtml(senderName) + '" style="background: transparent; overflow: hidden;"><img src="' + escapeHtml(avatarInfo.avatarUrl) + '" class="w-full h-full object-cover rounded-full"></div>';
      } else {
        avatarHtml = '<div class="message-avatar ' + avatarInfo.colorClass + '" title="' + escapeHtml(senderName) + '"' + avatarInfo.safeStyle + '>' + escapeHtml(avatarInfo.initials) + '</div>';
      }
    } else {
      avatarHtml = '<div class="message-avatar-spacer" aria-hidden="true"></div>';
    }
  }

  var replyQuoteHtml = typeof renderInlineReplyQuote === 'function' ? renderInlineReplyQuote(msg, conv) : '';
  var checkboxHtml = '<div class="message-select-checkbox select-none ' + (isSelectingMessages ? '' : 'hidden') + '" id="msg-select-check-' + msg.id + '"><i data-lucide="' + (selectedMessageIds.includes(msg.id) ? 'check-circle-2' : 'circle') + '" class="w-5 h-5 text-textSecondary"></i></div>';

  var iconName = messageType === 'image' ? 'image' : messageType === 'sticker' ? 'sticker' : 'file-text';
  var fileSizeText = fileData.total_size_bytes ? formatFileSize(fileData.total_size_bytes) : '';
  var isAutoPreviewImage = messageType === 'image'
    && fileId
    && fileData.total_size_bytes
    && Number(fileData.total_size_bytes) <= AUTO_IMAGE_PREVIEW_LIMIT_BYTES;
  var captionText = String(msg.text || '').trim();
  var placeholderTexts = ['[image]', '[file]', '[sticker]', '[Image]', '[File]', '[Sticker]'];
  var captionHtml = captionText && placeholderTexts.indexOf(captionText) === -1 && captionText.indexOf('[无法解密') !== 0
    ? '<div class="file-bubble-caption">' + renderMessageContent(captionText) + '</div>'
    : '';

  if (isAutoPreviewImage) {
    div.innerHTML = checkboxHtml + avatarHtml +
      '<div class="message-bubble-custom file-bubble file-image-bubble ' + bubbleClass + '" data-message-id="' + msg.id + '" data-file-id="' + (fileId || '') + '">' +
      senderNameHtml +
      replyQuoteHtml +
      '<div class="file-image-frame is-loading" data-file-id="' + (fileId || '') + '">' +
        '<div class="file-image-loading"><i data-lucide="loader-2" class="w-6 h-6 animate-spin"></i></div>' +
      '</div>' +
      captionHtml +
      '<div class="file-image-meta">' +
        '<span>' + escapeHtml(fileSizeText) + '</span>' +
        '<span>' + messageTime + '</span>' +
        (msg.isSelf ? renderStatusIconHtml(msg) : '') +
      '</div>' +
      '</div>';
    setTimeout(function () {
      applyImagePreviewToBubble(div, fileId, conv ? conv.type : 'single');
      if (div.querySelector('[data-lucide]')) lucide.createIcons();
    }, 0);
  } else {
    div.innerHTML = checkboxHtml + avatarHtml +
      '<div class="message-bubble-custom file-bubble ' + bubbleClass + '" data-message-id="' + msg.id + '" data-file-id="' + (fileId || '') + '">' +
      senderNameHtml +
      replyQuoteHtml +
      '<div class="file-bubble-content">' +
        '<div class="file-bubble-icon"><i data-lucide="' + iconName + '" class="w-8 h-8"></i></div>' +
        '<div class="file-bubble-info">' +
          '<div class="file-bubble-kind">' + escapeHtml(kindLabel) + '</div>' +
          '<div class="file-bubble-size">' + escapeHtml(fileSizeText) + '</div>' +
          '<button class="file-download-btn" data-file-id="' + (fileId || '') + '">' +
            '<i data-lucide="download" class="w-4 h-4"></i> Download' +
          '</button>' +
        '</div>' +
      '</div>' +
      '<div class="file-bubble-preview hidden"></div>' +
      captionHtml +
      '<div class="message-meta-line"><span>' + messageTime + '</span>' + (msg.isSelf ? renderStatusIconHtml(msg) : '') + '</div>' +
      '</div>';
  }

  // Attach download click handler
  var downloadBtn = div.querySelector('.file-download-btn');
  if (downloadBtn && fileId) {
    downloadBtn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var btn = this;
      var fid = btn.getAttribute('data-file-id');
      var convType = conv ? conv.type : 'single';
      if (!window.iChatFileTransfer || !window.iChatFileTransfer.fetchAndDecryptFile) {
        window.showToast && window.showToast('File transfer module not loaded.');
        return;
      }
      btn.classList.add('downloading');
      btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i> Downloading...';
      lucide.createIcons();
      window.iChatFileTransfer.fetchAndDecryptFile(parseInt(fid), convType).then(function (result) {
        btn.classList.remove('downloading');
        btn.classList.add('downloaded');
        btn.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i> Done';
        lucide.createIcons();
        if (result && result.blob) {
          var url = URL.createObjectURL(result.blob);
          var previewEl = div.querySelector('.file-bubble-preview');
          if (previewEl) {
            // Show image preview
            if (result.blob.type.indexOf('image/') === 0) {
              var img = document.createElement('img');
              img.src = url;
              previewEl.appendChild(img);
              previewEl.classList.remove('hidden');
            }
          }
          // Trigger download for non-image files
          if (result.blob.type.indexOf('image/') !== 0) {
            var a = document.createElement('a');
            a.href = url;
            a.download = (result.metadata && result.metadata.original_name) || 'file';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          }
        }
      }).catch(function (err) {
        btn.classList.remove('downloading');
        btn.innerHTML = '<i data-lucide="alert-circle" class="w-4 h-4"></i> Failed';
        lucide.createIcons();
        console.error('File download failed:', err);
        window.showToast && window.showToast('Download failed: ' + (err.message || 'Unknown error'));
      });
    });
  }

  div.onclick = function(e) {
    if (isSelectingMessages) { e.stopPropagation(); toggleMessageSelection(msg.id); }
  };
  div.oncontextmenu = function(e) {
    e.preventDefault();
    e.stopPropagation();
    if (typeof MessageActions !== 'undefined' && MessageActions.showMenu) {
      MessageActions.showMenu(e, msg, conv || conversationsById[activeChatId]);
    }
  };

  setTimeout(function() { if (div.querySelector('[data-lucide]')) lucide.createIcons(); }, 0);
  return div;
}

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
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

  // ── File / image / sticker message ───────────────────────────────
  if (msg.isFile || msg.file || msg.message_type === 'image' || msg.message_type === 'file' || msg.message_type === 'sticker') {
    div.className += msg.isSelf ? " message-row-self" : " message-row-peer";
    if (isSelectingMessages) div.className += " message-row-selecting";
    return renderFileMessageBubble(div, msg, conv, groupMeta);
  }

  if (!msg.isSystem) {
    div.onclick = function(e) {
      if (isSelectingMessages) { e.stopPropagation(); toggleMessageSelection(msg.id); }
    };
    div.oncontextmenu = function(e) {
      e.preventDefault();
      e.stopPropagation();
      if (typeof MessageActions !== 'undefined' && MessageActions.showMenu) {
        MessageActions.showMenu(e, msg, conv || conversationsById[activeChatId]);
      }
    };
  }

  var checkboxHtml = '<div class="message-select-checkbox select-none ' + (isSelectingMessages ? "" : "hidden") + '" id="msg-select-check-' + msg.id + '"><i data-lucide="' + (selectedMessageIds.includes(msg.id) ? "check-circle-2" : "circle") + '" class="w-5 h-5 text-textSecondary"></i></div>';

  var isGroup = conv && conv.type === "group";
  var senderName = msg.isSelf ? "You" : getMessageSenderName(msg, conv);
  var replyQuoteHtml = renderInlineReplyQuote(msg, conv);
  var messageText = renderMessageContent(msg.text);
  var messageTime = escapeHtml(msg.time || "");

  if (msg.isSelf) {
    div.className += " message-row-self";
    if (isSelectingMessages) div.className += " message-row-selecting";
    var statusIconHtml = renderStatusIconHtml(msg);
    div.innerHTML = checkboxHtml
      + '<div class="message-bubble-custom bubble-self" data-message-id="' + msg.id + '">'
      + replyQuoteHtml
      + '<div class="message-text-content">' + messageText + '</div>'
      + '<div class="message-meta-line">'
      + '<span>' + messageTime + '</span>'
      + statusIconHtml
      + '</div></div>';
  } else {
    div.className += " message-row-peer";
    if (isSelectingMessages) div.className += " message-row-selecting";
    var avatarHtml = "";
    if (isLastInGroup) {
      var avatarInfo = getSenderAvatarInfo(senderName, msg, conv);
      if (avatarInfo.avatarUrl) {
        avatarHtml = '<div class="message-avatar" title="' + escapeHtml(senderName) + '" style="background: transparent; overflow: hidden;"><img src="' + escapeHtml(avatarInfo.avatarUrl) + '" class="w-full h-full object-cover rounded-full"></div>';
      } else {
        avatarHtml = '<div class="message-avatar ' + avatarInfo.colorClass + '" title="' + escapeHtml(senderName) + '"' + avatarInfo.safeStyle + '>' + escapeHtml(avatarInfo.initials) + '</div>';
      }
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
      + replyQuoteHtml
      + '<div class="message-text-content">' + messageText + '</div>'
      + '<div class="message-meta-line"><span>' + messageTime + '</span></div>'
      + '</div>';
  }
  div.querySelectorAll(".message-reply-quote").forEach(function(replyQuote) {
    replyQuote.addEventListener("click", function(event) {
      event.preventDefault();
      event.stopPropagation();
      const targetId = replyQuote.dataset.replyMessageId;
      const targetBubble = Array.from(document.querySelectorAll(".message-bubble-custom[data-message-id]")).find(function(el) {
        return String(el.dataset.messageId) === String(targetId);
      });
      if (targetBubble) {
        targetBubble.scrollIntoView({ behavior: "smooth", block: "center" });
        targetBubble.classList.add("message-reply-jump-highlight");
        setTimeout(function() {
          targetBubble.classList.remove("message-reply-jump-highlight");
        }, 1600);
      }
    });
  });
  div.querySelectorAll(".message-spoiler").forEach(function(spoiler) {
    spoiler.addEventListener("click", function(event) {
      event.stopPropagation();
      spoiler.classList.add("is-revealed");
    });
    spoiler.addEventListener("keydown", function(event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        spoiler.classList.add("is-revealed");
      }
    });
  });
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

  const chatHeaderSearchBtn = document.getElementById("chat-header-search-btn");
  const chatSearchInput = document.getElementById("chat-search-input");
  const chatSearchClose = document.getElementById("chat-search-close");
  const chatSearchPrev = document.getElementById("chat-search-prev");
  const chatSearchNext = document.getElementById("chat-search-next");
  const chatSearchResultsEl = document.getElementById("chat-search-results");
  if (chatHeaderSearchBtn) {
    chatHeaderSearchBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openChatSearch();
    });
  }
  if (chatSearchInput) {
    chatSearchInput.addEventListener("input", () => runChatSearch(false));
    chatSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeChatSearch();
      } else if (e.key === "Enter") {
        e.preventDefault();
        activateChatSearchResult(chatSearchIndex < 0 ? 0 : chatSearchIndex + (e.shiftKey ? -1 : 1));
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        activateChatSearchResult(chatSearchIndex < 0 ? 0 : chatSearchIndex + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activateChatSearchResult(chatSearchIndex < 0 ? chatSearchResults.length - 1 : chatSearchIndex - 1);
      }
    });
  }
  if (chatSearchClose) {
    chatSearchClose.addEventListener("click", (e) => {
      e.preventDefault();
      closeChatSearch();
    });
  }
  if (chatSearchPrev) {
    chatSearchPrev.addEventListener("click", (e) => {
      e.preventDefault();
      activateChatSearchResult(chatSearchIndex < 0 ? chatSearchResults.length - 1 : chatSearchIndex - 1);
    });
  }
  if (chatSearchNext) {
    chatSearchNext.addEventListener("click", (e) => {
      e.preventDefault();
      activateChatSearchResult(chatSearchIndex < 0 ? 0 : chatSearchIndex + 1);
    });
  }
  if (chatSearchResultsEl) {
    chatSearchResultsEl.addEventListener("click", (e) => {
      const item = e.target.closest(".chat-search-result-item");
      if (!item) return;
      activateChatSearchResult(Number(item.dataset.searchIndex || 0));
    });
  }

  // Enter to send message
  const chatInput = document.getElementById("chat-input-textarea");
  if (chatInput) {
    setupFormatToolbar();
    chatInput.addEventListener("input", () => {
      if (!activeChatId) return;
      setConversationDraft(activeChatId, chatInput.value);
      refreshConversationDraftPreview(activeChatId);
    });
    chatInput.addEventListener("keydown", (e) => {
      var key = e.key.toLowerCase();
      var handledFormat = null;
      if (e.ctrlKey && !e.shiftKey && key === "b") handledFormat = "bold";
      else if (e.ctrlKey && !e.shiftKey && key === "i") handledFormat = "italic";
      else if (e.ctrlKey && !e.shiftKey && key === "u") handledFormat = "underline";
      else if (e.ctrlKey && e.shiftKey && key === "x") handledFormat = "strike";
      else if (e.ctrlKey && e.shiftKey && key === "m") handledFormat = "code";
      else if (e.ctrlKey && e.shiftKey && key === "h") handledFormat = "spoiler";
      else if (e.ctrlKey && e.shiftKey && (key === "9" || e.key === "(")) handledFormat = "quote";
      else if (e.ctrlKey && !e.shiftKey && key === "k") handledFormat = "link";

      if (handledFormat) {
        e.preventDefault();
        wrapTextareaSelection(handledFormat);
        return;
      }

      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    // T14: Send typing.start / typing.stop via WebSocket
    let typingTimer = null;
    let typingSent = false;
    chatInput.addEventListener("input", () => {
      if (!activeChatId || !wsClient || !wsClient.sendPayload) return;
      if (!typingSent) {
        wsClient.sendPayload({
          event: "typing.start",
          data: { conversation_id: activeChatId }
        });
        typingSent = true;
      }
      clearTimeout(typingTimer);
      typingTimer = setTimeout(() => {
        if (wsClient && wsClient.sendPayload) {
          wsClient.sendPayload({
            event: "typing.stop",
            data: { conversation_id: activeChatId }
          });
        }
        typingSent = false;
      }, 2500);
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
    // AI More menu
    const aiMoreDropdown = document.getElementById("ai-header-more-dropdown");
    const aiMoreBtn = document.getElementById("ai-header-more-btn");
    if (aiMoreDropdown && !aiMoreDropdown.classList.contains("hidden")) {
      if (aiMoreBtn && !aiMoreBtn.contains(e.target) && !aiMoreDropdown.contains(e.target)) {
        aiMoreDropdown.classList.add("hidden");
      }
    }
    // 2. Main menu
    const mainDropdown = document.getElementById("main-menu-dropdown");
    const mainBtn = document.getElementById("drawer-btn");
    const mainSubmenu = document.getElementById("main-menu-more-submenu");
    if (mainDropdown && !mainDropdown.classList.contains("hidden")) {
      if (
        mainBtn
        && !mainBtn.contains(e.target)
        && !mainDropdown.contains(e.target)
        && !(mainSubmenu && mainSubmenu.contains(e.target))
      ) {
        mainDropdown.classList.add("hidden");
        mainBtn.classList.remove("bg-bgSearch", "text-textMain");
        const menuMoreBtn = document.getElementById("menu-more-btn");
        if (mainSubmenu) {
          mainSubmenu.classList.add("hidden");
          mainSubmenu.style.left = "";
          mainSubmenu.style.top = "";
        }
        if (menuMoreBtn) menuMoreBtn.classList.remove("is-open");
      }
    }
    // 3. Settings home more menu
    const settingsMoreDropdown = document.getElementById("settings-home-more-dropdown");
    const settingsMoreBtn = document.getElementById("settings-home-more-btn");
    if (settingsMoreDropdown && !settingsMoreDropdown.classList.contains("hidden")) {
      if (settingsMoreBtn && !settingsMoreBtn.contains(e.target) && !settingsMoreDropdown.contains(e.target)) {
        settingsMoreDropdown.classList.add("hidden");
        settingsMoreBtn.classList.remove("active");
      }
    }
    // 4. Contacts FAB menu (P2 T09)
    const fabMenu = document.getElementById("contacts-fab-menu");
    const fabBtn = document.getElementById("contacts-fab-btn");
    if (fabMenu && !fabMenu.classList.contains("hidden")) {
      if (fabBtn && !fabBtn.contains(e.target) && !fabMenu.contains(e.target)) {
        closeContactsFab();
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
      const settingsMoreDropdown = document.getElementById("settings-home-more-dropdown");
      const settingsMoreBtn = document.getElementById("settings-home-more-btn");
      if (settingsMoreDropdown && !settingsMoreDropdown.classList.contains("hidden")) {
        settingsMoreDropdown.classList.add("hidden");
        if (settingsMoreBtn) settingsMoreBtn.classList.remove("active");
      }
      closeReportModal();
      closeDeleteConfirmModal();
      closeLogoutConfirmModal();
      if (typeof closeClearAllModal === 'function') closeClearAllModal();
      closeVisibilityPicker();
      closeAutoDeletePicker();
      closeBlockedUsersList();
      closePrivacyConfirmModal();
      closeContactsFab();
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

  // Group Modal / Sidebar Listeners
  const newGroupButtons = document.querySelectorAll("#menu-new-group-btn, [data-action='new-group']");
  newGroupButtons.forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      // Close drawer if open
      const overlay = document.getElementById("drawer-menu-overlay");
      const content = document.getElementById("drawer-menu-content");
      if (overlay && content) {
        overlay.classList.add("hidden");
        content.classList.add("-translate-x-full");
      }
      // Close hamburger menu dropdown if open
      const dropdown = document.getElementById("main-menu-dropdown");
      if (dropdown) dropdown.classList.add("hidden");
      
      resetGroupCreationFlow();
      navigateSidebar('group-add-members');
    });
  });

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

  // P2 T10: Search sidebar input with debounce
  var searchSidebarInput = document.getElementById('search-sidebar-input');
  if (searchSidebarInput) {
    searchSidebarInput.addEventListener('input', function() {
      clearTimeout(searchDebounceTimer);
      var clearBtn = document.getElementById('search-sidebar-clear');
      if (clearBtn) clearBtn.classList.toggle('hidden', !this.value.trim());
      searchDebounceTimer = setTimeout(performSearch, 300);
    });
    searchSidebarInput.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        clearSearchSidebar();
      }
    });
  }

  // AI Assistant Search & Action Hooks
  const aiSearchInput = document.getElementById("ai-search-input");
  const aiSearchClose = document.getElementById("ai-search-close");
  const aiSearchPrev = document.getElementById("ai-search-prev");
  const aiSearchNext = document.getElementById("ai-search-next");
  const aiSearchResultsEl = document.getElementById("ai-search-results");
  if (aiSearchInput) {
    aiSearchInput.addEventListener("input", () => runAiSearch(false));
    aiSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeAiSearch();
      } else if (e.key === "Enter") {
        e.preventDefault();
        activateAiSearchResult(aiSearchIndex < 0 ? 0 : aiSearchIndex + (e.shiftKey ? -1 : 1));
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        activateAiSearchResult(aiSearchIndex < 0 ? 0 : aiSearchIndex + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activateAiSearchResult(aiSearchIndex < 0 ? aiSearchResults.length - 1 : aiSearchIndex - 1);
      }
    });
  }
  if (aiSearchClose) {
    aiSearchClose.addEventListener("click", (e) => {
      e.preventDefault();
      closeAiSearch();
    });
  }
  if (aiSearchPrev) {
    aiSearchPrev.addEventListener("click", (e) => {
      e.preventDefault();
      activateAiSearchResult(aiSearchIndex < 0 ? aiSearchResults.length - 1 : aiSearchIndex - 1);
    });
  }
  if (aiSearchNext) {
    aiSearchNext.addEventListener("click", (e) => {
      e.preventDefault();
      activateAiSearchResult(aiSearchIndex < 0 ? 0 : aiSearchIndex + 1);
    });
  }
  if (aiSearchResultsEl) {
    aiSearchResultsEl.addEventListener("click", (e) => {
      const item = e.target.closest(".chat-search-result-item");
      if (!item) return;
      activateAiSearchResult(Number(item.dataset.searchIndex || 0));
    });
  }

  // Right Details Panel Search Button Hook
  const rightPanelSearchBtn = document.getElementById("right-panel-action-search");
  if (rightPanelSearchBtn) {
    rightPanelSearchBtn.addEventListener("click", () => {
      const aiWindow = document.getElementById("ai-assistant-window");
      if (aiWindow && !aiWindow.classList.contains("hidden")) {
        openAiSearch();
      } else {
        openChatSearch();
      }
    });
  }
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
    'general-settings',
    'chat-folders',
    'stickers-emoji',
    'language',
    'sessions-shortcuts',
    'accounts-switcher',
    'settings-birthday',
    'group-add-members',
    'group-create-final'
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
  if (viewName === 'privacy-security' && typeof refreshPrivacySecurity === 'function') {
    setTimeout(function() { refreshPrivacySecurity(); }, 150);
  }
  if (viewName === 'general-settings' && typeof refreshGeneralSettings === 'function') {
    setTimeout(function() { refreshGeneralSettings(); }, 150);
  }
  if (viewName === 'chat-folders' && typeof refreshChatFoldersSettings === 'function') {
    setTimeout(function() { refreshChatFoldersSettings(); }, 150);
  }
  // Refresh account switcher list when navigating to it (P2 T11)
  if (viewName === 'accounts-switcher') {
    setTimeout(function() { renderAccountsSwitcher(); }, 50);
  }
  // Re-render lucide icons after view switch
  if (window.lucide) setTimeout(function() { lucide.createIcons(); }, 50);
}

// Backward-compatible wrappers
function showSettingsPanel() {
  navigateSidebar('settings-home');
}

function hideSettingsPanel() {
  navigateSidebar('chat');
}

// ============================================================================
// P2 T11: Multi-Account Add & Switch
// ============================================================================

function getSavedAccounts() {
  try {
    return JSON.parse(localStorage.getItem('ichat_accounts') || '[]');
  } catch (e) {
    return [];
  }
}

function saveAccountToLocalList(user) {
  if (!user || !user.username) return;
  var accounts = getSavedAccounts();
  // Remove existing entry for this username (dedup)
  accounts = accounts.filter(function(a) { return a.username !== user.username; });
  // Add to front
  accounts.unshift({
    username: user.username,
    initials: user.initials || user.username[0].toUpperCase(),
    email: user.email || '',
    addedAt: Date.now()
  });
  // Keep max 10 accounts
  if (accounts.length > 10) accounts = accounts.slice(0, 10);
  try {
    localStorage.setItem('ichat_accounts', JSON.stringify(accounts));
  } catch (e) {
    // localStorage full or unavailable
  }
}

function removeSavedAccount(username) {
  var accounts = getSavedAccounts().filter(function(a) { return a.username !== username; });
  try {
    localStorage.setItem('ichat_accounts', JSON.stringify(accounts));
  } catch (e) {}
}

function autoSaveCurrentAccount() {
  var keyScript = document.getElementById('ichat-key-manager-script');
  if (!keyScript) return;
  var username = keyScript.dataset.username;
  if (!username) return;
  var initials = username[0].toUpperCase();
  var email = keyScript.dataset.email || '';
  saveAccountToLocalList({ username: username, initials: initials, email: email });

  // If coming from 'add account' flow, clear the URL param
  var params = new URLSearchParams(window.location.search);
  if (params.get('add_account') === '1') {
    params.delete('add_account');
    var newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    if (window.history && window.history.replaceState) {
      window.history.replaceState({}, '', newUrl);
    }
  }
}

function renderAccountsSwitcher() {
  var list = document.getElementById('accounts-switcher-list');
  if (!list) return;
  var accounts = getSavedAccounts();
  var keyScript = document.getElementById('ichat-key-manager-script');
  var currentUsername = keyScript ? keyScript.dataset.username : '';

  list.innerHTML = '';

  if (!accounts.length) {
    list.innerHTML = '<div class="text-xs text-textSecondary text-center py-8">' +
      (currentLanguage === 'zh' ? '暂无已保存的账号。点击下方按钮添加新账号。' : 'No saved accounts. Tap below to add one.') +
      '</div>';
    return;
  }

  accounts.forEach(function(acc) {
    var isCurrent = acc.username === currentUsername;
    var card = document.createElement('div');
    card.className = 'account-card' + (isCurrent ? ' account-card-current' : '');
    card.innerHTML =
      '<div class="account-card-avatar">' + escapeHtml(acc.initials) + '</div>' +
      '<div class="flex-1 min-w-0">' +
        '<div class="text-sm font-semibold text-textMain truncate">' + escapeHtml(acc.username) +
          (isCurrent ? ' <span class="text-[10px] text-brand-light dark:text-brand-dark font-medium ml-1">' + (currentLanguage === 'zh' ? '当前' : 'Current') + '</span>' : '') +
        '</div>' +
        '<div class="text-[11px] text-textSecondary truncate">' + (acc.email ? escapeHtml(acc.email) : '') + '</div>' +
      '</div>';

    if (!isCurrent) {
      var btn = document.createElement('button');
      btn.className = 'text-xs font-semibold text-brand-light dark:text-brand-dark hover:underline flex-shrink-0 ml-2';
      btn.textContent = currentLanguage === 'zh' ? '切换' : 'Switch';
      btn.onclick = function(e) {
        e.stopPropagation();
        switchToAccount(acc.username);
      };
      card.appendChild(btn);
    }

    // Long-press or right-click to remove
    card.addEventListener('contextmenu', function(e) {
      e.preventDefault();
      if (isCurrent) {
        window.showToast(currentLanguage === 'zh' ? '不能移除当前账号' : 'Cannot remove current account');
        return;
      }
      var confirmMsg = currentLanguage === 'zh'
        ? '确定从列表中移除账号 "' + acc.username + '" ？'
        : 'Remove "' + acc.username + '" from the account list?';
      if (window.confirm(confirmMsg)) {
        removeSavedAccount(acc.username);
        renderAccountsSwitcher();
      }
    });

    list.appendChild(card);
  });

  if (window.lucide) window.lucide.createIcons();
}

function switchToAccount(username) {
  var msg = currentLanguage === 'zh'
    ? '切换到账号 "' + username + '" ？当前会话将退出。'
    : 'Switch to "' + username + '"? You will be logged out of the current account.';
  if (!window.confirm(msg)) return;
  // T11: logout then redirect to login to switch accounts
  window.location.href = '/logout/?next=' + encodeURIComponent('/login/?next=/chat/');
}

function addNewAccount() {
  // T11: logout first so login_view won't redirect authenticated users away
  window.location.href = '/logout/?next=' + encodeURIComponent('/login/?next=/chat/?add_account=1');
}

// ============================================================================
// P2 T09: Contacts FAB (Floating Action Button) & header search
// ============================================================================

function toggleContactsFab(event) {
  event.stopPropagation();
  const btn = document.getElementById('contacts-fab-btn');
  const menu = document.getElementById('contacts-fab-menu');
  if (!btn || !menu) return;
  const isOpen = !menu.classList.contains('hidden');
  if (isOpen) {
    menu.classList.add('hidden');
    btn.classList.remove('expanded');
  } else {
    menu.classList.remove('hidden');
    btn.classList.add('expanded');
    if (window.lucide) lucide.createIcons();
  }
}

function fabNewPrivateChat(event) {
  event.stopPropagation();
  closeContactsFab();
  // Open the existing contacts modal for adding a contact to start a private chat
  const contactsModal = document.getElementById('contacts-modal');
  if (contactsModal) {
    contactsModal.classList.remove('hidden');
    contactsModal.classList.add('flex');
  }
}

function fabNewGroup(event) {
  event.stopPropagation();
  closeContactsFab();
  resetGroupCreationFlow();
  navigateSidebar('group-add-members');
}

function fabNewChannel(event) {
  event.stopPropagation();
  closeContactsFab();
  window.showToast(_t4('Channels are not yet available', '频道功能暂未开放', '頻道功能暫未開放', 'チャンネル機能はまだ利用できません'));
}

function closeContactsFab() {
  const btn = document.getElementById('contacts-fab-btn');
  const menu = document.getElementById('contacts-fab-menu');
  if (menu) menu.classList.add('hidden');
  if (btn) btn.classList.remove('expanded');
}

function filterContactsInSidebar(query) {
  const trimmed = (query || '').toLowerCase().trim();
  const sections = document.querySelectorAll('#sidebar-contacts-content > .divide-y > div');
  const contactItems = document.querySelectorAll('#sidebar-contacts-content [data-contact-search]');

  if (!trimmed) {
    // Show all sections, all items
    if (sections.length) sections.forEach(function(s) { s.style.display = ''; });
    contactItems.forEach(function(el) { el.style.display = ''; });
    return;
  }

  contactItems.forEach(function(el) {
    const searchText = (el.getAttribute('data-contact-search') || '').toLowerCase();
    el.style.display = searchText.includes(trimmed) ? '' : 'none';
  });

  // Hide section headers when searching (simplifies view)
  if (sections.length) {
    sections.forEach(function(s) {
      // Keep visible if it contains visible contact items
      const visibleItems = s.querySelectorAll('[data-contact-search]');
      let hasVisible = false;
      visibleItems.forEach(function(item) {
        if (item.style.display !== 'none') hasVisible = true;
      });
      s.style.display = hasVisible ? '' : 'none';
    });
  }
}

// ============================================================================
// P2 T10: Search Sidebar
// ============================================================================

function switchSearchTab(tab) {
  currentSearchTab = tab;
  // Update active tab styling
  document.querySelectorAll('.search-type-tab').forEach(function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
  });
  // Show/hide scope bar (only for chats)
  const scopeBar = document.getElementById('search-scope-bar');
  if (scopeBar) {
    scopeBar.style.display = (tab === 'chats') ? '' : 'none';
  }
  // Show placeholder for non-chats tabs
  if (tab !== 'chats') {
    showSearchPlaceholder(tab);
  } else {
    // Re-run search if input has text
    const input = document.getElementById('search-sidebar-input');
    if (input && input.value.trim()) {
      performSearch();
    } else {
      showSearchEmpty();
    }
  }
}

function onSearchScopeChange() {
  const input = document.getElementById('search-sidebar-input');
  if (input && input.value.trim()) {
    performSearch();
  }
}

function clearSearchSidebar() {
  const input = document.getElementById('search-sidebar-input');
  const clearBtn = document.getElementById('search-sidebar-clear');
  if (input) input.value = '';
  if (clearBtn) clearBtn.classList.add('hidden');
  showSearchEmpty();
}

function performSearch() {
  var input = document.getElementById('search-sidebar-input');
  var clearBtn = document.getElementById('search-sidebar-clear');
  var query = input ? input.value.trim() : '';

  // Toggle clear button
  if (clearBtn) {
    clearBtn.classList.toggle('hidden', !query);
  }

  if (!query) {
    showSearchEmpty();
    return;
  }

  if (currentSearchTab !== 'chats') {
    showSearchPlaceholder(currentSearchTab);
    return;
  }

  // Show loading
  var emptyState = document.getElementById('search-empty-state');
  var resultsContent = document.getElementById('search-results-content');
  var loading = document.getElementById('search-loading');
  if (emptyState) emptyState.classList.add('hidden');
  if (resultsContent) resultsContent.classList.add('hidden');
  if (loading) loading.classList.remove('hidden');

  var scope = document.getElementById('search-scope-select');
  var scopeValue = scope ? scope.value : 'all';

  var url = '/api/search/?q=' + encodeURIComponent(query) + '&scope=' + encodeURIComponent(scopeValue);

  // Race-condition guard: only render if the input hasn't changed since request
  var sentQuery = query;
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (loading) loading.classList.add('hidden');
      var currentInput = document.getElementById('search-sidebar-input');
      var currentQuery = currentInput ? currentInput.value.trim() : '';
      if (currentQuery !== sentQuery) return; // stale response, discard
      renderSearchResults(data, currentQuery);
    })
    .catch(function(err) {
      if (loading) loading.classList.add('hidden');
      console.error('Search failed:', err);
      showSearchEmpty();
      window.showToast(_t4('Search failed. Please retry.', '搜索失败，请重试', '搜尋失敗，請重試', '検索に失敗しました。再試行してください。'));
    });
}

function renderSearchResults(data, query) {
  var emptyState = document.getElementById('search-empty-state');
  var resultsContent = document.getElementById('search-results-content');
  if (!resultsContent) return;

  resultsContent.innerHTML = '';
  var hasContent = false;

  // Group: Contacts
  var contacts = (data.results && data.results.contacts) ? data.results.contacts : [];
  if (contacts.length > 0) {
    hasContent = true;
    resultsContent.appendChild(createResultGroupLabel('Contacts'));
    contacts.forEach(function(c) {
      resultsContent.appendChild(createSearchResultItem({
        initials: (c.username || '?')[0].toUpperCase(),
        name: c.nickname || c.username || 'Unknown',
        subtitle: (function() {
          let subText = '@' + (c.username || '');
          if (c.user_type === 'agent') {
            subText = (currentLanguage === 'zh' ? '智能代理' : 'Agent') + ' · ' + subText;
          } else if (c.user_type === 'bot') {
            subText = (currentLanguage === 'zh' ? '机器人' : 'Bot') + ' · ' + subText;
          }
          if (c.is_contact) {
            subText = _t4('Already a contact', '已是联系人', '已是聯絡人', '既に連絡先です') + ' · ' + subText;
          }
          return subText;
        })(),
        color: '#5c6bc0',
        avatar_url: c.avatar_url,
        onclick: function() {
          if (c.is_contact) {
            // Navigate to chat with this contact
            apiFetch('/api/conversations/create/', {
              method: 'POST',
              body: JSON.stringify({ peer_id: c.id })
            }).then(function(resp) {
              navigateSidebar('chat');
              fetchConversations().then(function() {
                if (resp.conversation_id) selectChat(resp.conversation_id.toString());
              });
            }).catch(function(err) {
              window.showToast(err.message);
            });
          } else {
            // Open add-contact flow — fill the Add Contact form in contacts view
            clearSearchSidebar();
            navigateSidebar('contacts');
            setTimeout(function() {
              var addInput = document.getElementById('contact-search-input');
              if (addInput) { addInput.value = c.username; addInput.focus(); }
            }, 150);
          }
        }
      }));
    });
  }

  // Group: Groups
  var groups = (data.results && data.results.groups) ? data.results.groups : [];
  if (groups.length > 0) {
    hasContent = true;
    resultsContent.appendChild(createResultGroupLabel('Groups'));
    groups.forEach(function(g) {
      resultsContent.appendChild(createSearchResultItem({
        initials: (g.name || 'G')[0].toUpperCase(),
        name: g.name || 'Unnamed Group',
        subtitle: (g.is_member ? '' : _t4('Not joined · ', '未加入 · ', '未加入 · ', '未参加 · ')) + (g.member_count || 0) + ' members',
        color: '#6f42c1',
        onclick: function() {
          if (g.is_member) {
            navigateSidebar('chat');
            // Match group by Conversation ID (g.id === Conversation.id)
            fetchConversations().then(function() {
              var conv = conversationsById[g.id];
              if (!conv) {
                // Fallback: id-based scan
                conv = conversations.find(function(c) { return c.type === 'group' && c.id === g.id; });
              }
              if (conv) selectChat(conv.id.toString());
            });
          } else {
            window.showToast(_t4('You are not a member of this group', '你尚未加入该群组', '你尚未加入該群組', 'このグループのメンバーではありません'));
          }
        }
      }));
    });
  }

  // Group: Conversations
  var conversations_ = (data.results && data.results.conversations) ? data.results.conversations : [];
  if (conversations_.length > 0) {
    hasContent = true;
    resultsContent.appendChild(createResultGroupLabel('Conversations'));
    conversations_.forEach(function(conv) {
      resultsContent.appendChild(createSearchResultItem({
        initials: (conv.peer_display_name || conv.peer_username || '?')[0].toUpperCase(),
        name: conv.peer_display_name || conv.peer_username || 'Unknown',
        subtitle: '@' + (conv.peer_username || ''),
        color: '#3390ec',
        avatar_url: conv.avatar_url,
        onclick: function() {
          navigateSidebar('chat');
          if (conv.conversation_id) {
            selectChat(conv.conversation_id.toString());
          }
        }
      }));
    });
  }

  if (!hasContent) {
    if (emptyState) emptyState.classList.remove('hidden');
    resultsContent.classList.add('hidden');
    var emptyP = emptyState ? emptyState.querySelector('p') : null;
    if (emptyP) emptyP.textContent = _t4(
      'No results found for "' + query + '"',
      '未找到与 "' + query + '" 相关的结果',
      '未找到與 "' + query + '" 相關的結果',
      '"' + query + '" の検索結果はありません'
    );
  } else {
    if (emptyState) emptyState.classList.add('hidden');
    resultsContent.classList.remove('hidden');
  }
}

function createResultGroupLabel(text) {
  var div = document.createElement('div');
  div.className = 'search-result-group-label';
  div.textContent = text;
  return div;
}

function createSearchResultItem(opts) {
  var btn = document.createElement('button');
  btn.className = 'search-result-item';
  btn.onclick = opts.onclick;
  var safeColor = /^#[0-9a-fA-F]{6}$/.test(opts.color || '') ? opts.color : '#5c6bc0';
  var avatarInner = opts.avatar_url
    ? '<img src="' + escapeHtml(opts.avatar_url) + '" class="w-full h-full object-cover rounded-full">'
    : escapeHtml(opts.initials || '?');
  var avatarBgStyle = opts.avatar_url
    ? 'background-color: transparent; overflow: hidden;'
    : 'background-color:' + safeColor;
  btn.innerHTML = '<div class="result-avatar" style="' + avatarBgStyle + '">'
    + avatarInner
    + '</div>'
    + '<div class="result-info">'
    + '<div class="result-name">' + escapeHtml(opts.name || '') + '</div>'
    + '<div class="result-subtitle">' + escapeHtml(opts.subtitle || '') + '</div>'
    + '</div>';
  return btn;
}

function showSearchEmpty() {
  var emptyState = document.getElementById('search-empty-state');
  var resultsContent = document.getElementById('search-results-content');
  var loading = document.getElementById('search-loading');
  if (emptyState) {
    emptyState.classList.remove('hidden');
    var p = emptyState.querySelector('p');
    if (p) p.textContent = currentLanguage === 'zh'
      ? '搜索聊天、联系人和消息'
      : 'Search for chats, contacts, and messages';
  }
  if (resultsContent) resultsContent.classList.add('hidden');
  if (loading) loading.classList.add('hidden');
}

function showSearchPlaceholder(tab) {
  var emptyState = document.getElementById('search-empty-state');
  var resultsContent = document.getElementById('search-results-content');
  var loading = document.getElementById('search-loading');
  if (resultsContent) resultsContent.classList.add('hidden');
  if (loading) loading.classList.add('hidden');
  if (emptyState) {
    emptyState.classList.remove('hidden');
    var labels = {
      channels: _t4('Channel search is not yet available', '频道搜索暂未开放', '頻道搜尋暫未開放', 'チャンネル検索はまだ利用できません'),
      apps: _t4('Apps search is not yet available', 'Apps 搜索暂未开放', 'Apps 搜尋暫未開放', 'アプリ検索はまだ利用できません'),
      posts: _t4('Posts search is not yet available', 'Posts 搜索暂未开放', 'Posts 搜尋暫未開放', '投稿検索はまだ利用できません')
    };
    var p = emptyState.querySelector('p');
    if (p) p.textContent = labels[tab] || labels.channels;
  }
}

function _kmgr() {
  return window.iChatKeyManager;
}

function setTextById(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setStatusGlyph(el, ok, missingLabel = '-') {
  if (!el) return;
  el.textContent = ok ? 'OK' : missingLabel;
  el.classList.remove('text-green-500', 'text-red-400', 'text-gray-400', 'text-amber-500');
  el.classList.add(ok ? 'text-green-500' : missingLabel === '!' ? 'text-amber-500' : 'text-red-400');
}

function toggleKeyManagement() {
  const panel = document.getElementById('key-mgmt-panel');
  const chevron = document.getElementById('key-mgmt-chevron');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !isHidden);
  if (chevron) chevron.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
  if (isHidden) refreshKeyUI();
}

async function refreshKeyUI() {
  const kmgr = _kmgr();
  const record = kmgr ? kmgr.loadCurrentRecord() : null;
  const fpEl = document.getElementById('key-fingerprint-display');
  const statusDot = document.querySelector('#key-status-badge span.w-2');
  const statusText = document.getElementById('key-status-text');
  const subtitle = document.getElementById('key-mgmt-subtitle');
  const genBtn = document.getElementById('btn-key-generate');
  const uploadBtn = document.getElementById('btn-key-upload');

  if (fpEl) fpEl.textContent = record && record.key_fingerprint ? formatFingerprint(record.key_fingerprint) : '-';
  if (record && record.key_fingerprint) {
    if (subtitle) subtitle.textContent = 'ECDH P-256 keys stored locally';
    if (statusDot) {
      statusDot.classList.remove('bg-gray-400', 'bg-red-400');
      statusDot.classList.add('bg-green-500');
    }
    if (statusText) statusText.textContent = record.key_version ? `Keys synced (v${record.key_version})` : 'Keys ready';
    if (genBtn) {
      genBtn.disabled = false;
      genBtn.textContent = 'Re-initialize Keys';
    }
    if (uploadBtn) uploadBtn.disabled = false;
  } else {
    if (subtitle) subtitle.textContent = 'No key pair found. Initialize one to enable E2EE.';
    if (statusDot) {
      statusDot.classList.remove('bg-green-500', 'bg-red-400');
      statusDot.classList.add('bg-gray-400');
    }
    if (statusText) statusText.textContent = 'No local keys';
    if (genBtn) {
      genBtn.disabled = false;
      genBtn.textContent = 'Initialize Keys';
    }
    if (uploadBtn) uploadBtn.disabled = true;
  }

  try {
    const data = await apiFetch('/api/keys/fingerprints/');
    const activeKey = (data.keys || []).find(key => key.is_active) || (data.keys || [])[0];
    if (activeKey) {
      setTextById('key-version-display', `v${activeKey.key_version}`);
      if (fpEl) fpEl.textContent = formatFingerprint(activeKey.key_fingerprint);
    } else {
      setTextById('key-version-display', '-');
    }
    setTextById('key-trusted-by-display', String(data.trusted_by_count || 0));
  } catch (err) {
    setTextById('key-version-display', '-');
    setTextById('key-trusted-by-display', '-');
  }
}

function _showKeyMsg(text, isError = false) {
  const el = document.getElementById('key-mgmt-message');
  if (!el) return;
  el.textContent = text;
  el.className = 'text-xs p-2.5 rounded-custom-md border';
  el.classList.add(
    isError ? 'bg-red-50' : 'bg-green-50',
    isError ? 'border-red-200' : 'border-green-200',
    isError ? 'text-red-700' : 'text-green-700',
    isError ? 'dark:bg-red-950/20' : 'dark:bg-green-950/20',
    isError ? 'dark:border-red-900/50' : 'dark:border-green-900/50',
    isError ? 'dark:text-red-400' : 'dark:text-green-400'
  );
  setTimeout(() => el.classList.add('hidden'), 5000);
}

async function keyMgrGenerate() {
  const btn = document.getElementById('btn-key-generate');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Initializing...';
  }
  try {
    const kmgr = _kmgr();
    if (!kmgr) throw new Error('Key manager is not available.');
    const record = await kmgr.initialize();
    _showKeyMsg(`ECDH P-256 keys initialized and synced. Version: ${record.key_version}`);
    await refreshKeyUI();
    await refreshSecurityStatus();
  } catch (err) {
    _showKeyMsg(`Initialization failed: ${err.message}`, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Initialize Keys';
    }
  }
}

async function keyMgrUpload() {
  const btn = document.getElementById('btn-key-upload');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Uploading...';
  }
  try {
    const kmgr = _kmgr();
    if (!kmgr) throw new Error('Key manager is not available.');
    await kmgr.uploadCurrentRecord();
    _showKeyMsg('Public key re-synced to server.');
    await refreshKeyUI();
  } catch (err) {
    _showKeyMsg(`Upload failed: ${err.message}`, true);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Upload to Server';
    }
  }
}

function keyMgrExport() {
  try {
    const kmgr = _kmgr();
    if (!kmgr || !kmgr.loadCurrentRecord()) {
      _showKeyMsg('No keys to export. Initialize them first.', true);
      return;
    }
    kmgr.exportBackup();
    _showKeyMsg('Backup downloaded. Keep it safe.');
  } catch (err) {
    _showKeyMsg(`Export failed: ${err.message}`, true);
  }
}

async function keyMgrImport(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const kmgr = _kmgr();
    if (!kmgr) throw new Error('Key manager is not available.');
    const record = await kmgr.importBackup(file);
    _showKeyMsg(`Keys restored. Fingerprint: ${record.key_fingerprint.slice(0, 16)}...`);
    await refreshKeyUI();
    await refreshSecurityStatus();
  } catch (err) {
    _showKeyMsg(`Import failed: ${err.message}`, true);
  } finally {
    event.target.value = '';
  }
}

function toggleSecurityStatus() {
  const panel = document.getElementById('security-status-panel');
  const chevron = document.getElementById('security-status-chevron');
  if (!panel) return;
  const isHidden = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !isHidden);
  if (chevron) chevron.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
  if (isHidden) refreshSecurityStatus();
}

async function fetchKeyTrustList({ force = false } = {}) {
  if (!force && keyTrustListCache) return keyTrustListCache;
  const data = await apiFetch('/api/keys/trust/');
  keyTrustListCache = data.trusts || [];
  return keyTrustListCache;
}

function renderTrustList(trusts) {
  const list = document.getElementById('security-trust-list');
  if (!list) return;
  list.innerHTML = '';
  if (!trusts.length) {
    list.innerHTML = '<div class="text-[10px] text-textSecondary text-center py-2">No verified contact keys yet.</div>';
    return;
  }
  trusts.forEach(trust => {
    const row = document.createElement('div');
    const changed = Boolean(trust.key_changed);
    row.className = 'security-trust-row';
    row.innerHTML =
      '<div class="min-w-0">' +
        '<div class="text-xs font-semibold text-textMain truncate">' + escapeHtml(trust.contact_username || 'Unknown') + '</div>' +
        '<div class="text-[10px] text-textSecondary font-mono truncate">v' + escapeHtml(trust.key_version) + ' ' + escapeHtml(formatFingerprint(trust.key_fingerprint)) + '</div>' +
      '</div>' +
      '<span class="' + (changed ? 'security-status-chip security-status-danger' : 'security-status-chip security-status-verified') + '">' +
        (changed ? (currentLanguage === 'zh' ? '已变更' : 'Changed') : escapeHtml(trust.trust_status || 'trusted')) +
      '</span>';
    list.appendChild(row);
  });
}

async function refreshSecurityStatus() {
  const keysEl = document.getElementById('sec-keys-status');
  const serverEl = document.getElementById('sec-server-status');
  const trustEl = document.getElementById('sec-trust-status');
  const kmgr = _kmgr();
  const record = kmgr ? kmgr.loadCurrentRecord() : null;
  setStatusGlyph(keysEl, Boolean(record && record.key_fingerprint));

  try {
    const data = await apiFetch('/api/keys/fingerprints/');
    const activeKey = (data.keys || []).find(key => key.is_active);
    setStatusGlyph(serverEl, Boolean(activeKey));
    if (activeKey) {
      setTextById('key-version-display', `v${activeKey.key_version}`);
      setTextById('key-trusted-by-display', String(data.trusted_by_count || 0));
    }
  } catch (err) {
    setStatusGlyph(serverEl, false);
  }

  try {
    const trusts = await fetchKeyTrustList({ force: true });
    const changedCount = trusts.filter(trust => trust.key_changed).length;
    if (trustEl) {
      trustEl.textContent = changedCount > 0 ? '!' : String(trusts.length);
      trustEl.classList.remove('text-green-500', 'text-red-400', 'text-gray-400', 'text-amber-500');
      trustEl.classList.add(changedCount > 0 ? 'text-amber-500' : trusts.length > 0 ? 'text-green-500' : 'text-gray-400');
    }
    renderTrustList(trusts);
  } catch (err) {
    if (trustEl) {
      trustEl.textContent = '-';
      trustEl.classList.remove('text-green-500', 'text-red-400', 'text-amber-500');
      trustEl.classList.add('text-gray-400');
    }
  }
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
  const minHeight = 24;
  const maxHeight = 160;
  textarea.style.height = "auto";
  textarea.style.height = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight) + "px";
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? "auto" : "hidden";
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

  // ── File transfer: paperclip / attach button ─────────────────────
  const attachBtn = document.querySelector('.telegram-composer-action[title="Attach Document"]');
  if (attachBtn) {
    attachBtn.addEventListener('click', function (e) {
      e.preventDefault();
      if (!activeChatId) {
        window.showToast && window.showToast('Please select a conversation first.');
        return;
      }
      if (!e2eeKeyReady) {
        window.showToast && window.showToast('Encryption key not ready. Please import or create your key first.');
        return;
      }
      if (window.iChatFileTransfer && window.iChatFileTransfer.showFilePicker) {
        const conv = conversationsById[activeChatId];
        const convType = conv ? conv.type : 'single';
        window.iChatFileTransfer.showFilePicker(activeChatId, convType, 'file');
      }
    });
  }
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
  if (keyScript) {
    currentUserProfile = {
      username: keyScript.dataset.username || "",
      initials: keyScript.dataset.initials || "",
      avatarUrl: keyScript.dataset.avatarUrl || "",
      avatarColor: keyScript.dataset.avatarColor || ""
    };
  }

  // T11: Auto-save current account to multi-account list
  autoSaveCurrentAccount();

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
    general_settings: "General Settings",
    logout_confirm_title: "Sign Out",
    logout_confirm_desc: "Are you sure you want to sign out?",
    logout_confirm_btn: "Confirm",
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
    menu_block_contact: "Block",
    menu_mute_group: "Mute...",
    menu_select_messages: "Select messages",
    menu_report: "Report",
    menu_leave_group: "Leave Group",
    menu_delete_chat: "Delete Chat",
    menu_add_account: "Add Account",
    menu_more: "More",
    menu_about: "About iChat Pro",
    menu_updates: "Check Updates",
    // T12: Conversation actions
    convPin: "Pin",
    convUnpin: "Unpin",
    convPinned: "Pinned",
    convUnpinned: "Unpinned",
    convMute: "Mute",
    convUnmute: "Unmute",
    convMuted: "Muted",
    convUnmuted: "Unmuted",
    convMute1h: "Mute for 1 hour",
    convMute8h: "Mute for 8 hours",
    convMute24h: "Mute for 24 hours",
    convMuteForever: "Mute forever",
    convArchive: "Archive",
    convUnarchive: "Unarchive",
    convArchived: "Archived",
    convUnarchived: "Unarchived",
    convClear: "Clear History",
    convDelete: "Delete Chat",
    convMarkRead: "Mark as Read",
    convMarkUnread: "Mark as Unread",
    convMarkedRead: "Marked as read",
    convMarkedUnread: "Marked as unread",
    convCleared: "History cleared",
    convDeleted: "Conversation deleted",
    convClearConfirm: "Clear all messages in this chat?",
    convDeleteConfirm: "Delete this conversation?",
    convActionFailed: "Action failed",
    // T13: Message actions
    msgCopy: "Copy",
    msgCopied: "Copied",
    msgCopyFailed: "Copy failed",
    msgNothingToCopy: "Nothing to copy",
    msgReply: "Reply",
    msgForward: "Forward",
    msgForwardTitle: "Forward to...",
    msgForwarded: "Forwarded",
    msgForwardFailed: "Forward failed",
    msgNoForwardTargets: "No conversations to forward to",
    msgSelect: "Select",
    msgDelete: "Delete",
    msgDeleted: "message deleted",
    msgDeletedToast: "Message deleted",
    msgRecall: "Recall",
    msgRecalled: "message recalled",
    msgYouRecalled: "You recalled a message",
    msgRecalledToast: "Message recalled",
    msgRecallFailed: "Recall failed",
    msgResend: "Resend",
    msgCancelReply: "Cancel reply",
    msgActionFailed: "Action failed",
    // T14: Status & presence
    statusSending: "Sending...",
    statusSent: "Sent",
    statusDelivered: "Delivered",
    statusRead: "Read",
    statusFailed: "Failed to send",
    connectionConnected: "Connected",
    connectionReconnecting: "Reconnecting...",
    connectionDisconnected: "Disconnected",
    lastSeenJustNow: "Last seen just now",
    lastSeenMinAgo: "Last seen %d min ago",
    lastSeenHoursAgo: "Last seen %d hours ago",
    lastSeenToday: "Last seen today at",
    lastSeenYesterday: "Last seen yesterday at",
    lastSeenDate: "Last seen",
    typingIndicator: "Typing",
    // Settings categories
    notifications_sounds: "Notifications and Sounds",
    data_and_storage: "Data and Storage",
    privacy_and_security: "Privacy and Security",
    chat_folders: "Chat Folders",
    stickers_and_emoji: "Stickers and Emoji",
    speakers_and_camera: "Speakers and Camera",
    devices: "Devices",
    keyboard_shortcuts: "Keyboard Shortcuts",
    manage_crypto_keys: "Manage Cryptographic Keys",
    edit_profile: "Edit Profile",
    language: "Language",
    birthday: "Birthday",
    // Notification settings
    web_notifications: "Web Notifications",
    display_notifications: "Show Notifications",
    show_offline_notifications: "Show Offline Notifications",
    all_accounts: "All Accounts",
    enable_private_chats: "Enable Private Chats",
    sound_effects: "Sound Effects",
    notification_tone: "Notification Tone",
    message_sent_sound_effect: "Message Sent",
    private_chat_notifications: "Private Chat Notifications",
    message_preview: "Message Preview",
    group_notifications: "Group Notifications",
    channel_notifications: "Channel Notifications",
    other_notifications: "Other",
    contact_joined_telegram: "Contact joined Telegram",
    // Data & Storage
    auto_download_media: "Auto-Download Media",
    reset_auto_download_settings: "Reset Auto-Download Settings",
    estimated_storage_quota: "Estimated Storage Quota",
    cached_files: "Cached Files",
    cached_video_stream_chunks: "Cached Video Stream Chunks",
    clear_cache_older_than: "Clear Cache Older Than",
    cache_size_limit: "Maximum Cache Size",
    clear_all_cache: "Clear All Cache",
    // Privacy
    blocked_users: "Blocked Users",
    auto_delete_messages: "Auto-Delete Messages",
    passcode_lock: "Passcode Lock",
    two_step_verification: "Two-Step Verification",
    login_email: "Login Email",
    passkey: "Passkey",
    privacy: "Privacy",
    who_can_see_my_phone_number: "Who can see my phone number?",
    who_can_see_my_last_seen: "Who can see my last seen?",
    who_can_see_my_profile_photo: "Who can see my profile photo?",
    who_can_see_my_bio: "Who can see my bio?",
    who_can_call_me: "Who can call me?",
    who_can_forward_link: "Who can link to my account when forwarding?",
    who_can_invite_me: "Who can invite me?",
    who_can_send_messages: "Who can send me messages?",
    who_can_see_my_birthday: "Who can see my birthday?",
    who_can_send_me_gifts: "Who can send me gifts?",
    who_can_see_my_saved_music: "Who can see my saved music?",
    sensitive_content: "Sensitive Content",
    disable_filtering: "Disable Filtering",
    payments: "Payments",
    clear_payment_shipping_info: "Clear Payment and Shipping Info",
    delete_cloud_drafts: "Delete All Cloud Drafts",
    // General settings
    message_font_size: "Message Font Size",
    chat_wallpaper: "Chat Wallpaper",
    power_saving_mode: "Power Saving Mode",
    theme_color: "Theme Color",
    time_format: "Time Format",
    light_theme: "Light",
    dark_theme_night: "Dark / Night",
    system_default: "System Default",
    hour_12: "12-Hour",
    hour_24: "24-Hour",
    enabled: "Enabled",
    disabled: "Disabled",
    // Stickers & Emoji
    quick_reactions: "Quick Reactions",
    suggest_emoji: "Suggest Emoji",
    loop_animated_stickers: "Loop Animated Stickers",
    emoji: "Emoji",
    suggested_emojis: "Suggested Emojis",
    large_emoji: "Large Emoji",
    sticker_packs_order: "Sticker Packs Order",
    dynamic_sticker_order: "Dynamic Sticker Pack Order",
    sticker_packs: "Sticker Packs",
    // Folders
    folders: "Folders",
    create_folder: "Create Folder",
    folders_view: "Folders View",
    folders_sidebar: "Left Sidebar",
    folders_above_chats: "Folders Above Chats",
    no_folders: "No Folders",
    // Sessions & Shortcuts
    terminate: "Terminate",
    terminate_all_other_sessions: "Terminate All Other Sessions",
    current_session: "Current",
    no_active_sessions: "No Active Sessions",
    // Profile edit
    first_name: "First Name",
    last_name: "Last Name",
    bio: "Bio (optional)",
    username_optional: "Username (optional)",
    save_changes: "Save Changes",
    add_birthday: "Add Birthday",
    change_avatar: "Change Avatar",
    // Birthday
    never_allow: "Never Allow",
    always_allow: "Always Allow",
    add_users: "Add Users",
    exceptions: "Exceptions",
    // Misc
    search_contacts: "Search contacts...",
    new_private_chat: "New Private Chat",
    new_channel: "New Channel",
    soon: "Soon",
    add_another_account: "Add Another Account",
    loading_accounts: "Loading accounts...",
    groups: "Groups",
    all_chats: "All Chats",
    private_chats: "Private Chats",
    group_chats: "Group Chats",
    channels_label: "Channels",
    search_for_chats: "Search for chats, contacts, and messages",
    // Translate section
    translate_messages: "Translate Messages",
    show_translate_button: "Show Translate Button",
    translate_all_chats: "Translate All Chats",
    do_not_translate: "Do Not Translate",
    ichat_premium_hint: "Subscribe to iChat Premium to translate entire chats."
  },
  zh: {
    general_settings: "通用设置",
    logout_confirm_title: "退出登录",
    logout_confirm_desc: "确定要退出登录吗？",
    logout_confirm_btn: "确认",
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
    menu_block_contact: "拉黑",
    menu_mute_group: "静音免打扰",
    menu_select_messages: "选择消息",
    menu_report: "举报",
    menu_leave_group: "退出群聊",
    menu_delete_chat: "删除聊天",
    menu_add_account: "添加账号",
    menu_more: "更多",
    menu_about: "关于 iChat Pro",
    menu_updates: "检查更新",
    // T12: Conversation actions
    convPin: "置顶",
    convUnpin: "取消置顶",
    convPinned: "已置顶",
    convUnpinned: "已取消置顶",
    convMute: "静音",
    convUnmute: "取消静音",
    convMuted: "已静音",
    convUnmuted: "已取消静音",
    convMute1h: "静音1小时",
    convMute8h: "静音8小时",
    convMute24h: "静音24小时",
    convMuteForever: "永久静音",
    convArchive: "归档",
    convUnarchive: "取消归档",
    convArchived: "已归档",
    convUnarchived: "已取消归档",
    convClear: "清空聊天记录",
    convDelete: "删除会话",
    convMarkRead: "标为已读",
    convMarkUnread: "标为未读",
    convMarkedRead: "已标为已读",
    convMarkedUnread: "已标为未读",
    convCleared: "聊天记录已清空",
    convDeleted: "会话已删除",
    convClearConfirm: "确定清空该会话的所有消息？",
    convDeleteConfirm: "确定删除该会话？",
    convActionFailed: "操作失败",
    // T13: Message actions
    msgCopy: "复制",
    msgCopied: "已复制",
    msgCopyFailed: "复制失败",
    msgNothingToCopy: "无可复制内容",
    msgReply: "回复",
    msgForward: "转发",
    msgForwardTitle: "转发到...",
    msgForwarded: "已转发",
    msgForwardFailed: "转发失败",
    msgNoForwardTargets: "没有可转发的会话",
    msgSelect: "选择",
    msgDelete: "删除",
    msgDeleted: "消息已删除",
    msgDeletedToast: "消息已删除",
    msgRecall: "撤回",
    msgRecalled: "消息已撤回",
    msgYouRecalled: "你撤回了一条消息",
    msgRecalledToast: "消息已撤回",
    msgRecallFailed: "撤回失败",
    msgResend: "重发",
    msgCancelReply: "取消回复",
    msgActionFailed: "操作失败",
    // T14: Status & presence
    statusSending: "发送中...",
    statusSent: "已发送",
    statusDelivered: "已送达",
    statusRead: "已读",
    statusFailed: "发送失败",
    connectionConnected: "已连接",
    connectionReconnecting: "重连中...",
    connectionDisconnected: "已断开",
    lastSeenJustNow: "刚刚在线",
    lastSeenMinAgo: "最后上线 %d 分钟前",
    lastSeenHoursAgo: "最后上线 %d 小时前",
    lastSeenToday: "最后上线今天",
    lastSeenYesterday: "最后上线昨天",
    lastSeenDate: "最后上线",
    typingIndicator: "正在输入",
    // Settings categories
    notifications_sounds: "通知与声音",
    data_and_storage: "数据和存储",
    privacy_and_security: "隐私和安全",
    chat_folders: "聊天文件夹",
    stickers_and_emoji: "贴纸与表情",
    speakers_and_camera: "扬声器和摄像头",
    devices: "设备",
    keyboard_shortcuts: "快捷键",
    manage_crypto_keys: "管理加密密钥",
    edit_profile: "编辑资料",
    language: "语言",
    birthday: "生日",
    // Notification settings
    web_notifications: "网页通知",
    display_notifications: "显示通知",
    show_offline_notifications: "显示线下通知",
    all_accounts: "全部账号",
    enable_private_chats: "启用私聊",
    sound_effects: "声音特效",
    notification_tone: "通知音",
    message_sent_sound_effect: "消息已发送",
    private_chat_notifications: "私聊通知",
    message_preview: "消息预览",
    group_notifications: "群组通知",
    channel_notifications: "频道通知",
    other_notifications: "其它",
    contact_joined_telegram: "联系人已加入 Telegram",
    // Data & Storage
    auto_download_media: "自动下载媒体文件",
    reset_auto_download_settings: "重置自动下载设置",
    estimated_storage_quota: "预估存储空间",
    cached_files: "缓存文件",
    cached_video_stream_chunks: "缓存的视频流片段",
    clear_cache_older_than: "清除早于以下时间的缓存",
    cache_size_limit: "最大缓存大小",
    clear_all_cache: "清除所有缓存",
    // Privacy
    blocked_users: "已拉黑用户",
    auto_delete_messages: "自动删除消息",
    passcode_lock: "密码锁",
    two_step_verification: "两步验证",
    login_email: "登录邮箱",
    passkey: "通行密钥",
    privacy: "隐私",
    who_can_see_my_phone_number: "谁可以看见我的手机号码？",
    who_can_see_my_last_seen: "谁可以看到我最后上线的时间？",
    who_can_see_my_profile_photo: "谁能看见我的头像？",
    who_can_see_my_bio: "谁可以看到我的个人简介？",
    who_can_call_me: "谁可以给我打电话？",
    who_can_forward_link: "转发我的消息时，谁可以链接至我的账号？",
    who_can_invite_me: "谁可以邀请我？",
    who_can_send_messages: "谁可以给我发消息？",
    who_can_see_my_birthday: "谁可以看到我的生日？",
    who_can_send_me_gifts: "谁可以给我送礼物？",
    who_can_see_my_saved_music: "谁可以看到我的已收藏音乐？",
    sensitive_content: "敏感内容",
    disable_filtering: "停用过滤",
    payments: "付款",
    clear_payment_shipping_info: "清除付款和配送信息",
    delete_cloud_drafts: "删除所有的云草稿",
    // General settings
    message_font_size: "消息字号",
    chat_wallpaper: "聊天壁纸",
    power_saving_mode: "省电模式",
    theme_color: "主题颜色",
    time_format: "时间格式",
    light_theme: "日光白",
    dark_theme_night: "夜间",
    system_default: "系统默认",
    hour_12: "12小时制",
    hour_24: "24小时制",
    enabled: "已启用",
    disabled: "已停用",
    // Stickers & Emoji
    quick_reactions: "快速回应",
    suggest_emoji: "根据 Emoji 联想表情",
    loop_animated_stickers: "循环播放动态贴纸",
    emoji: "Emoji",
    suggested_emojis: "推荐的表情",
    large_emoji: "大号表情",
    sticker_packs_order: "贴纸包动态顺序",
    dynamic_sticker_order: "贴纸包动态顺序",
    sticker_packs: "表情",
    // Folders
    folders: "文件夹",
    create_folder: "创建文件夹",
    folders_view: "文件夹视图",
    folders_sidebar: "左侧文件夹",
    folders_above_chats: "聊天上方显示文件夹",
    no_folders: "暂无文件夹",
    // Sessions & Shortcuts
    terminate: "终止",
    terminate_all_other_sessions: "终止其他所有会话",
    current_session: "当前",
    no_active_sessions: "未找到活跃会话",
    // Profile edit
    first_name: "名字",
    last_name: "姓氏",
    bio: "个人简介（可选）",
    username_optional: "用户名（可选）",
    save_changes: "保存修改",
    add_birthday: "添加生日",
    change_avatar: "更换头像",
    // Birthday
    never_allow: "永不允许",
    always_allow: "总是允许",
    add_users: "添加用户",
    exceptions: "例外",
    // Misc
    search_contacts: "搜索联系人...",
    new_private_chat: "新建私聊",
    new_channel: "新建频道",
    soon: "即将推出",
    add_another_account: "添加其他账号",
    loading_accounts: "正在加载账号...",
    groups: "群组",
    all_chats: "全部聊天",
    private_chats: "私聊",
    group_chats: "群聊",
    channels_label: "频道",
    search_for_chats: "搜索聊天、联系人和消息",
    // Translate section
    translate_messages: "翻译消息",
    show_translate_button: "显示“翻译”按钮",
    translate_all_chats: "翻译全部聊天记录",
    do_not_translate: "无需翻译",
    ichat_premium_hint: "订阅 iChat 高级版 以翻译所有聊天。"
  },
  'zh-TW': {
    general_settings: "一般設定",
    logout_confirm_title: "登出",
    logout_confirm_desc: "確定要登出嗎？",
    logout_confirm_btn: "確認",
    account_details: "帳號詳情",
    active_sessions: "活躍工作階段 (3)",
    active_sessions_desc: "管理所有已登入此帳號的裝置",
    attach_document: "附加檔案",
    back_to_sidebar: "返回聊天列表",
    blocked_contacts: "已封鎖聯絡人",
    blocked_contacts_desc: "目前沒有被封鎖的使用者",
    chat_info_title: "聊天資訊",
    close_panel: "關閉面板",
    cryptographic_fingerprint: "加密指紋",
    dark_theme_mode: "深色主題模式",
    e2ee_banner: "🔒 訊息已透過端對端加密保護。",
    email_address: "電子郵件地址",
    empty_desc: "從側邊欄列表中選擇一個聯絡人，或搜尋新聯絡人以啟動端對端加密工作階段。",
    empty_item1: "訊息使用 ECDH P-256 金鑰協商在本地進行加密。",
    empty_item2: "伺服器目錄中絕不儲存任何明文訊息。",
    empty_item3: "透過檢查目前的安全指紋來驗證加密狀態。",
    empty_title: "未選擇聊天",
    encryption_details: "加密詳情",
    fp_match_btn: "指紋相符",
    group_members_title: "群組成員",
    insert_emoji: "插入表情符號",
    lang_display: "繁體中文",
    language_mode: "語言 / Language",
    main_menu: "主選單",
    manage_keys: "管理加密金鑰",
    manage_keys_desc: "檢視並驗證橢圓曲線金鑰對",
    menu_contacts: "聯絡人",
    menu_help: "iChat Pro 說明與常見問題",
    menu_logout: "登出",
    menu_new_group: "新增群組",
    menu_profile: "個人檔案",
    menu_saved_messages: "收藏",
    menu_settings: "設定",
    menu_theme: "切換主題",
    more_operations: "更多操作",
    off: "關閉",
    online: "線上",
    phone_number: "手機號碼",
    privacy_security: "隱私與安全",
    protocol: "加密協定",
    search_chat: "搜尋聊天記錄",
    search_placeholder: "搜尋聊天或訊息...",
    self_destruct_timer: "閱後即焚計時器",
    settings: "設定",
    system_preferences: "系統偏好設定",
    timer_1h: "1 小時",
    username: "使用者名稱",
    verification: "驗證狀態",
    verified: "已驗證",
    verify_fingerprint_btn: "驗證指紋",
    verify_fp_desc: "端對端加密。點選以驗證安全指紋。",
    verify_fp_title: "驗證安全指紋",
    reset_key_btn: "重設金鑰",
    view_info: "檢視資訊",
    write_placeholder: "撰寫加密訊息...",
    menu_boost_group: "強化群組",
    menu_block_contact: "封鎖",
    menu_mute_group: "靜音免打擾",
    menu_select_messages: "選取訊息",
    menu_report: "檢舉",
    menu_leave_group: "離開群組",
    menu_delete_chat: "刪除聊天",
    menu_add_account: "新增帳號",
    menu_more: "更多",
    menu_about: "關於 iChat Pro",
    menu_updates: "檢查更新",
    // T12: Conversation actions
    convPin: "置頂",
    convUnpin: "取消置頂",
    convPinned: "已置頂",
    convUnpinned: "已取消置頂",
    convMute: "靜音",
    convUnmute: "取消靜音",
    convMuted: "已靜音",
    convUnmuted: "已取消靜音",
    convMute1h: "靜音 1 小時",
    convMute8h: "靜音 8 小時",
    convMute24h: "靜音 24 小時",
    convMuteForever: "永久靜音",
    convArchive: "封存",
    convUnarchive: "取消封存",
    convArchived: "已封存",
    convUnarchived: "已取消封存",
    convClear: "清除聊天記錄",
    convDelete: "刪除會話",
    convMarkRead: "標示為已讀",
    convMarkUnread: "標示為未讀",
    convMarkedRead: "已標示為已讀",
    convMarkedUnread: "已標示為未讀",
    convCleared: "聊天記錄已清除",
    convDeleted: "會話已刪除",
    convClearConfirm: "確定清除此會話的所有訊息？",
    convDeleteConfirm: "確定刪除此會話？",
    convActionFailed: "操作失敗",
    // T13: Message actions
    msgCopy: "複製",
    msgCopied: "已複製",
    msgCopyFailed: "複製失敗",
    msgNothingToCopy: "無可複製內容",
    msgReply: "回覆",
    msgForward: "轉寄",
    msgForwardTitle: "轉寄到...",
    msgForwarded: "已轉寄",
    msgForwardFailed: "轉寄失敗",
    msgNoForwardTargets: "沒有可轉寄的會話",
    msgSelect: "選取",
    msgDelete: "刪除",
    msgDeleted: "訊息已刪除",
    msgDeletedToast: "訊息已刪除",
    msgRecall: "收回",
    msgRecalled: "訊息已收回",
    msgYouRecalled: "你收回了一條訊息",
    msgRecalledToast: "訊息已收回",
    msgRecallFailed: "收回失敗",
    msgResend: "重發",
    msgCancelReply: "取消回覆",
    msgActionFailed: "操作失敗",
    // T14: Status & presence
    statusSending: "傳送中...",
    statusSent: "已傳送",
    statusDelivered: "已送達",
    statusRead: "已讀",
    statusFailed: "傳送失敗",
    connectionConnected: "已連線",
    connectionReconnecting: "重新連線中...",
    connectionDisconnected: "已中斷連線",
    lastSeenJustNow: "剛剛上線",
    lastSeenMinAgo: "最後上線 %d 分鐘前",
    lastSeenHoursAgo: "最後上線 %d 小時前",
    lastSeenToday: "最後上線今天",
    lastSeenYesterday: "最後上線昨天",
    lastSeenDate: "最後上線",
    typingIndicator: "正在輸入",
    // Settings categories
    notifications_sounds: "通知與音效",
    data_and_storage: "資料與儲存",
    privacy_and_security: "隱私與安全",
    chat_folders: "聊天資料夾",
    stickers_and_emoji: "貼圖與表情",
    speakers_and_camera: "喇叭與相機",
    devices: "裝置",
    keyboard_shortcuts: "鍵盤快速鍵",
    manage_crypto_keys: "管理加密金鑰",
    edit_profile: "編輯個人檔案",
    language: "語言",
    birthday: "生日",
    // Notification settings
    web_notifications: "網頁通知",
    display_notifications: "顯示通知",
    show_offline_notifications: "顯示離線通知",
    all_accounts: "全部帳號",
    enable_private_chats: "啟用私聊",
    sound_effects: "音效",
    notification_tone: "通知音",
    message_sent_sound_effect: "訊息已傳送",
    private_chat_notifications: "私聊通知",
    message_preview: "訊息預覽",
    group_notifications: "群組通知",
    channel_notifications: "頻道通知",
    other_notifications: "其他",
    contact_joined_telegram: "聯絡人已加入 Telegram",
    // Data & Storage
    auto_download_media: "自動下載媒體檔案",
    reset_auto_download_settings: "重設自動下載設定",
    estimated_storage_quota: "預估儲存空間",
    cached_files: "快取檔案",
    cached_video_stream_chunks: "快取的視訊串流片段",
    clear_cache_older_than: "清除早於以下時間的快取",
    cache_size_limit: "最大快取大小",
    clear_all_cache: "清除所有快取",
    // Privacy
    blocked_users: "已封鎖使用者",
    auto_delete_messages: "自動刪除訊息",
    passcode_lock: "密碼鎖定",
    two_step_verification: "雙步驟驗證",
    login_email: "登入電子郵件",
    passkey: "通行金鑰",
    privacy: "隱私",
    who_can_see_my_phone_number: "誰可以看到我的電話號碼？",
    who_can_see_my_last_seen: "誰可以看到我最後上線的時間？",
    who_can_see_my_profile_photo: "誰可以看到我的大頭貼？",
    who_can_see_my_bio: "誰可以看到我的個人簡介？",
    who_can_call_me: "誰可以打電話給我？",
    who_can_forward_link: "轉寄我的訊息時，誰可以連結至我的帳號？",
    who_can_invite_me: "誰可以邀請我？",
    who_can_send_messages: "誰可以傳訊息給我？",
    who_can_see_my_birthday: "誰可以看到我的生日？",
    who_can_send_me_gifts: "誰可以送禮物給我？",
    who_can_see_my_saved_music: "誰可以看到我收藏的音樂？",
    sensitive_content: "敏感內容",
    disable_filtering: "停用過濾",
    payments: "付款",
    clear_payment_shipping_info: "清除付款與配送資訊",
    delete_cloud_drafts: "刪除所有雲端草稿",
    // General settings
    message_font_size: "訊息字型大小",
    chat_wallpaper: "聊天背景",
    power_saving_mode: "省電模式",
    theme_color: "主題色彩",
    time_format: "時間格式",
    light_theme: "日間模式",
    dark_theme_night: "夜間模式",
    system_default: "跟隨系統",
    hour_12: "12 小時制",
    hour_24: "24 小時制",
    enabled: "已啟用",
    disabled: "已停用",
    // Stickers & Emoji
    quick_reactions: "快速回應",
    suggest_emoji: "根據 Emoji 聯想表情",
    loop_animated_stickers: "循環播放動態貼圖",
    emoji: "Emoji",
    suggested_emojis: "推薦的表情",
    large_emoji: "大型表情",
    sticker_packs_order: "貼圖包順序",
    dynamic_sticker_order: "動態貼圖包順序",
    sticker_packs: "貼圖",
    // Folders
    folders: "資料夾",
    create_folder: "建立資料夾",
    folders_view: "資料夾檢視",
    folders_sidebar: "左側資料夾",
    folders_above_chats: "聊天上方顯示資料夾",
    no_folders: "尚無資料夾",
    // Sessions & Shortcuts
    terminate: "終止",
    terminate_all_other_sessions: "終止其他所有工作階段",
    current_session: "目前",
    no_active_sessions: "找不到活躍工作階段",
    // Profile edit
    first_name: "名字",
    last_name: "姓氏",
    bio: "個人簡介（選填）",
    username_optional: "使用者名稱（選填）",
    save_changes: "儲存變更",
    add_birthday: "新增生日",
    change_avatar: "更換大頭貼",
    // Birthday
    never_allow: "永不允許",
    always_allow: "總是允許",
    add_users: "新增使用者",
    exceptions: "例外",
    // Misc
    search_contacts: "搜尋聯絡人...",
    new_private_chat: "新增私聊",
    new_channel: "新增頻道",
    soon: "即將推出",
    add_another_account: "新增其他帳號",
    loading_accounts: "正在載入帳號...",
    groups: "群組",
    all_chats: "所有聊天",
    private_chats: "私聊",
    group_chats: "群組聊天",
    channels_label: "頻道",
    search_for_chats: "搜尋聊天、聯絡人和訊息",
    // Translate section
    translate_messages: "翻譯訊息",
    show_translate_button: "顯示「翻譯」按鈕",
    translate_all_chats: "翻譯所有聊天記錄",
    do_not_translate: "無需翻譯",
    ichat_premium_hint: "訂閱 iChat 進階版 以翻譯所有聊天。"
  },
  ja: {
    general_settings: "一般設定",
    logout_confirm_title: "ログアウト",
    logout_confirm_desc: "ログアウトしてもよろしいですか？",
    logout_confirm_btn: "確認",
    account_details: "アカウント詳細",
    active_sessions: "アクティブセッション (3)",
    active_sessions_desc: "アカウントにログインしているすべてのデバイスを管理",
    attach_document: "ファイルを添付",
    back_to_sidebar: "チャット一覧に戻る",
    blocked_contacts: "ブロックした連絡先",
    blocked_contacts_desc: "現在ブロックしているユーザーはいません",
    chat_info_title: "チャット情報",
    close_panel: "パネルを閉じる",
    cryptographic_fingerprint: "暗号指紋",
    dark_theme_mode: "ダークテーマモード",
    e2ee_banner: "🔒 メッセージはエンドツーエンド暗号化で保護されています。",
    email_address: "メールアドレス",
    empty_desc: "サイドバーリストから連絡先を選択するか、新しい相手を検索してエンドツーエンド暗号化セッションを開始します。",
    empty_item1: "メッセージは ECDH P-256 鍵共有でローカルに暗号化されます。",
    empty_item2: "平文がサーバーディレクトリに保存されることはありません。",
    empty_item3: "アクティブな指紋を確認して暗号化状態を検証します。",
    empty_title: "チャットが選択されていません",
    encryption_details: "暗号化の詳細",
    fp_match_btn: "指紋が一致",
    group_members_title: "グループメンバー",
    insert_emoji: "絵文字を挿入",
    lang_display: "日本語",
    language_mode: "言語 / Language",
    main_menu: "メインメニュー",
    manage_keys: "暗号鍵の管理",
    manage_keys_desc: "楕円曲線鍵ペアの表示と検証",
    menu_contacts: "連絡先",
    menu_help: "iChat Pro ヘルプ & FAQ",
    menu_logout: "ログアウト",
    menu_new_group: "新規グループ",
    menu_profile: "マイプロフィール",
    menu_saved_messages: "保存済みメッセージ",
    menu_settings: "設定",
    menu_theme: "テーマ切り替え",
    more_operations: "その他の操作",
    off: "オフ",
    online: "オンライン",
    phone_number: "電話番号",
    privacy_security: "プライバシーとセキュリティ",
    protocol: "プロトコル",
    search_chat: "チャットを検索",
    search_placeholder: "チャットまたはメッセージを検索...",
    self_destruct_timer: "自動消滅タイマー",
    settings: "設定",
    system_preferences: "システム設定",
    timer_1h: "1 時間",
    username: "ユーザー名",
    verification: "検証",
    verified: "検証済み",
    verify_fingerprint_btn: "指紋を検証",
    verify_fp_desc: "E2EE 暗号化。クリックして指紋を検証。",
    verify_fp_title: "セキュリティ指紋の検証",
    reset_key_btn: "鍵をリセット",
    view_info: "情報を見る",
    write_placeholder: "暗号化メッセージを入力...",
    menu_boost_group: "グループをブースト",
    menu_block_contact: "ブロック",
    menu_mute_group: "ミュート...",
    menu_select_messages: "メッセージを選択",
    menu_report: "報告",
    menu_leave_group: "グループを退出",
    menu_delete_chat: "チャットを削除",
    menu_add_account: "アカウントを追加",
    menu_more: "もっと見る",
    menu_about: "iChat Pro について",
    menu_updates: "アップデートを確認",
    // T12: Conversation actions
    convPin: "ピン留め",
    convUnpin: "ピン留め解除",
    convPinned: "ピン留め済み",
    convUnpinned: "ピン留め解除済み",
    convMute: "ミュート",
    convUnmute: "ミュート解除",
    convMuted: "ミュート済み",
    convUnmuted: "ミュート解除済み",
    convMute1h: "1 時間ミュート",
    convMute8h: "8 時間ミュート",
    convMute24h: "24 時間ミュート",
    convMuteForever: "永久にミュート",
    convArchive: "アーカイブ",
    convUnarchive: "アーカイブ解除",
    convArchived: "アーカイブ済み",
    convUnarchived: "アーカイブ解除済み",
    convClear: "履歴を消去",
    convDelete: "会話を削除",
    convMarkRead: "既読にする",
    convMarkUnread: "未読にする",
    convMarkedRead: "既読にしました",
    convMarkedUnread: "未読にしました",
    convCleared: "履歴を消去しました",
    convDeleted: "会話を削除しました",
    convClearConfirm: "このチャットのすべてのメッセージを消去しますか？",
    convDeleteConfirm: "この会話を削除しますか？",
    convActionFailed: "操作に失敗しました",
    // T13: Message actions
    msgCopy: "コピー",
    msgCopied: "コピーしました",
    msgCopyFailed: "コピーに失敗しました",
    msgNothingToCopy: "コピーする内容がありません",
    msgReply: "返信",
    msgForward: "転送",
    msgForwardTitle: "転送先...",
    msgForwarded: "転送しました",
    msgForwardFailed: "転送に失敗しました",
    msgNoForwardTargets: "転送先の会話がありません",
    msgSelect: "選択",
    msgDelete: "削除",
    msgDeleted: "メッセージが削除されました",
    msgDeletedToast: "メッセージを削除しました",
    msgRecall: "取り消し",
    msgRecalled: "メッセージが取り消されました",
    msgYouRecalled: "メッセージを取り消しました",
    msgRecalledToast: "メッセージを取り消しました",
    msgRecallFailed: "取り消しに失敗しました",
    msgResend: "再送",
    msgCancelReply: "返信をキャンセル",
    msgActionFailed: "操作に失敗しました",
    // T14: Status & presence
    statusSending: "送信中...",
    statusSent: "送信済み",
    statusDelivered: "配信済み",
    statusRead: "既読",
    statusFailed: "送信失敗",
    connectionConnected: "接続済み",
    connectionReconnecting: "再接続中...",
    connectionDisconnected: "切断されました",
    lastSeenJustNow: "たった今オンライン",
    lastSeenMinAgo: "最終オンライン %d 分前",
    lastSeenHoursAgo: "最終オンライン %d 時間前",
    lastSeenToday: "本日最終オンライン",
    lastSeenYesterday: "昨日最終オンライン",
    lastSeenDate: "最終オンライン",
    typingIndicator: "入力中",
    // Settings categories
    notifications_sounds: "通知とサウンド",
    data_and_storage: "データとストレージ",
    privacy_and_security: "プライバシーとセキュリティ",
    chat_folders: "チャットフォルダー",
    stickers_and_emoji: "スタンプと絵文字",
    speakers_and_camera: "スピーカーとカメラ",
    devices: "デバイス",
    keyboard_shortcuts: "キーボードショートカット",
    manage_crypto_keys: "暗号鍵の管理",
    edit_profile: "プロフィール編集",
    language: "言語",
    birthday: "誕生日",
    // Notification settings
    web_notifications: "ウェブ通知",
    display_notifications: "通知を表示",
    show_offline_notifications: "オフライン通知を表示",
    all_accounts: "すべてのアカウント",
    enable_private_chats: "プライベートチャットを有効化",
    sound_effects: "サウンドエフェクト",
    notification_tone: "通知音",
    message_sent_sound_effect: "メッセージ送信音",
    private_chat_notifications: "プライベートチャット通知",
    message_preview: "メッセージプレビュー",
    group_notifications: "グループ通知",
    channel_notifications: "チャンネル通知",
    other_notifications: "その他",
    contact_joined_telegram: "連絡先が Telegram に参加しました",
    // Data & Storage
    auto_download_media: "メディアの自動ダウンロード",
    reset_auto_download_settings: "自動ダウンロード設定をリセット",
    estimated_storage_quota: "推定ストレージ使用量",
    cached_files: "キャッシュファイル",
    cached_video_stream_chunks: "キャッシュされた動画ストリーム",
    clear_cache_older_than: "指定期間より古いキャッシュを削除",
    cache_size_limit: "最大キャッシュサイズ",
    clear_all_cache: "すべてのキャッシュを削除",
    // Privacy
    blocked_users: "ブロックしたユーザー",
    auto_delete_messages: "メッセージの自動削除",
    passcode_lock: "パスコードロック",
    two_step_verification: "2 段階認証",
    login_email: "ログインメール",
    passkey: "パスキー",
    privacy: "プライバシー",
    who_can_see_my_phone_number: "電話番号を表示できるユーザー",
    who_can_see_my_last_seen: "最終オンラインを表示できるユーザー",
    who_can_see_my_profile_photo: "プロフィール写真を表示できるユーザー",
    who_can_see_my_bio: "自己紹介を表示できるユーザー",
    who_can_call_me: "通話を許可するユーザー",
    who_can_forward_link: "転送時にアカウントにリンクできるユーザー",
    who_can_invite_me: "招待を許可するユーザー",
    who_can_send_messages: "メッセージ送信を許可するユーザー",
    who_can_see_my_birthday: "誕生日を表示できるユーザー",
    who_can_send_me_gifts: "ギフトを送信できるユーザー",
    who_can_see_my_saved_music: "保存した音楽を表示できるユーザー",
    sensitive_content: "センシティブなコンテンツ",
    disable_filtering: "フィルタリングを無効化",
    payments: "支払い",
    clear_payment_shipping_info: "支払い・配送情報を消去",
    delete_cloud_drafts: "すべてのクラウド下書きを削除",
    // General settings
    message_font_size: "メッセージのフォントサイズ",
    chat_wallpaper: "チャット壁紙",
    power_saving_mode: "省電力モード",
    theme_color: "テーマカラー",
    time_format: "時刻形式",
    light_theme: "ライト",
    dark_theme_night: "ダーク",
    system_default: "システムデフォルト",
    hour_12: "12 時間表示",
    hour_24: "24 時間表示",
    enabled: "有効",
    disabled: "無効",
    // Stickers & Emoji
    quick_reactions: "クイックリアクション",
    suggest_emoji: "絵文字を提案",
    loop_animated_stickers: "アニメーションスタンプをループ再生",
    emoji: "絵文字",
    suggested_emojis: "おすすめ絵文字",
    large_emoji: "大きな絵文字",
    sticker_packs_order: "スタンプパックの順序",
    dynamic_sticker_order: "スタンプパックの動的順序",
    sticker_packs: "スタンプ",
    // Folders
    folders: "フォルダー",
    create_folder: "フォルダーを作成",
    folders_view: "フォルダー表示",
    folders_sidebar: "左サイドバー",
    folders_above_chats: "チャット上部に表示",
    no_folders: "フォルダーがありません",
    // Sessions & Shortcuts
    terminate: "終了",
    terminate_all_other_sessions: "他のすべてのセッションを終了",
    current_session: "現在",
    no_active_sessions: "アクティブなセッションはありません",
    // Profile edit
    first_name: "名",
    last_name: "姓",
    bio: "自己紹介（任意）",
    username_optional: "ユーザー名（任意）",
    save_changes: "変更を保存",
    add_birthday: "誕生日を追加",
    change_avatar: "アバターを変更",
    // Birthday
    never_allow: "許可しない",
    always_allow: "常に許可",
    add_users: "ユーザーを追加",
    exceptions: "例外",
    // Misc
    search_contacts: "連絡先を検索...",
    new_private_chat: "新しいプライベートチャット",
    new_channel: "新しいチャンネル",
    soon: "近日公開",
    add_another_account: "別のアカウントを追加",
    loading_accounts: "アカウントを読み込み中...",
    groups: "グループ",
    all_chats: "すべてのチャット",
    private_chats: "プライベートチャット",
    group_chats: "グループチャット",
    channels_label: "チャンネル",
    search_for_chats: "チャット、連絡先、メッセージを検索",
    // Translate section
    translate_messages: "メッセージを翻訳",
    show_translate_button: "「翻訳」ボタンを表示",
    translate_all_chats: "すべてのチャットを翻訳",
    do_not_translate: "翻訳しない",
    ichat_premium_hint: "iChat プレミアム に登録して全チャットを翻訳。"
  }
};

// Expose translations globally so T12/T13 modules can use t()
window.translations = translations;

// Literal text translations: canonical_key → { en, zh, zh-TW, ja }
// Used by TreeWalker to translate hardcoded text in templates
const literalTextTranslations = {
  notifications_sounds: { en: "Notifications and Sounds", zh: "通知与声音", 'zh-TW': "通知與音效", ja: "通知とサウンド" },
  data_and_storage: { en: "Data and Storage", zh: "数据和存储", 'zh-TW': "資料與儲存", ja: "データとストレージ" },
  privacy_and_security: { en: "Privacy and Security", zh: "隐私和安全", 'zh-TW': "隱私與安全", ja: "プライバシーとセキュリティ" },
  chat_folders: { en: "Chat Folders", zh: "聊天文件夹", 'zh-TW': "聊天資料夾", ja: "チャットフォルダー" },
  customize_folder_appearance: { en: "Customize folder appearance", zh: "自定义文件夹显示", 'zh-TW': "自訂資料夾顯示", ja: "フォルダーの外観をカスタマイズ" },
  n_chats: { en: "5 chats", zh: "5 个聊天", 'zh-TW': "5 個聊天", ja: "5 チャット" },
  stickers_and_emoji: { en: "Stickers and Emoji", zh: "贴纸与表情", 'zh-TW': "貼圖與表情", ja: "スタンプと絵文字" },
  speakers_and_camera: { en: "Speakers and Camera", zh: "扬声器和摄像头", 'zh-TW': "喇叭與相機", ja: "スピーカーとカメラ" },
  devices: { en: "Devices", zh: "设备", 'zh-TW': "裝置", ja: "デバイス" },
  n_active: { en: "3 active", zh: "3 个活跃", 'zh-TW': "3 個活躍", ja: "3 アクティブ" },
  language_slash: { en: "Language / 语言", zh: "语言", 'zh-TW': "語言", ja: "言語" },
  keyboard_shortcuts: { en: "Keyboard Shortcuts", zh: "快捷键", 'zh-TW': "鍵盤快速鍵", ja: "キーボードショートカット" },
  manage_crypto_keys: { en: "Manage Cryptographic Keys", zh: "管理加密密钥", 'zh-TW': "管理加密金鑰", ja: "暗号鍵の管理" },
  checking: { en: "Checking...", zh: "检查中...", 'zh-TW': "檢查中...", ja: "確認中..." },
  key_fingerprint: { en: "Key Fingerprint (SHA-256)", zh: "密钥指纹 (SHA-256)", 'zh-TW': "金鑰指紋 (SHA-256)", ja: "鍵指紋 (SHA-256)" },
  generate_keys: { en: "Generate Keys", zh: "生成密钥", 'zh-TW': "產生金鑰", ja: "鍵を生成" },
  upload_to_server: { en: "Upload to Server", zh: "上传到服务器", 'zh-TW': "上傳到伺服器", ja: "サーバーにアップロード" },
  export_backup: { en: "Export Backup", zh: "导出备份", 'zh-TW': "匯出備份", ja: "バックアップをエクスポート" },
  import_backup: { en: "Import Backup", zh: "导入备份", 'zh-TW': "匯入備份", ja: "バックアップをインポート" },
  security_status: { en: "Security Status", zh: "安全状态", 'zh-TW': "安全狀態", ja: "セキュリティ状態" },
  e2ee_key_setup: { en: "E2EE key setup and contact verification", zh: "端到端加密密钥和联系人验证状态", 'zh-TW': "端對端加密金鑰和聯絡人驗證狀態", ja: "E2EE 鍵設定と連絡先検証" },
  local_keys: { en: "Local Keys", zh: "本地密钥", 'zh-TW': "本機金鑰", ja: "ローカル鍵" },
  server_synced: { en: "Server Synced", zh: "服务器同步", 'zh-TW': "伺服器同步", ja: "サーバー同期済み" },
  refresh_status: { en: "Refresh Status", zh: "刷新状态", 'zh-TW': "重新整理狀態", ja: "状態を更新" },
  storage_usage: { en: "Storage Usage", zh: "存储用量", 'zh-TW': "儲存用量", ja: "ストレージ使用量" },
  images: { en: "Images", zh: "图片", 'zh-TW': "圖片", ja: "画像" },
  video_files: { en: "Video files", zh: "视频文件", 'zh-TW': "影片檔案", ja: "動画ファイル" },
  stickers_and_emojis: { en: "Stickers and emojis", zh: "贴纸和表情", 'zh-TW': "貼圖和表情", ja: "スタンプと絵文字" },
  other: { en: "Other", zh: "其他", 'zh-TW': "其他", ja: "その他" },
  cached_video_stream_chunks: { en: "Cached video stream chunks", zh: "缓存的视频流片段", 'zh-TW': "快取的視訊串流片段", ja: "キャッシュされた動画ストリーム" },
  calculating: { en: "Calculating…", zh: "计算中…", 'zh-TW': "計算中…", ja: "計算中…" },
  auto_download: { en: "Auto-Download", zh: "自动下载", 'zh-TW': "自動下載", ja: "自動ダウンロード" },
  reset_auto_download_settings: { en: "Reset Auto-Download Settings", zh: "重置自动下载设置", 'zh-TW': "重設自動下載設定", ja: "自動ダウンロード設定をリセット" },
  on_mobile_data: { en: "On Mobile Data", zh: "使用移动数据时", 'zh-TW': "使用行動數據時", ja: "モバイルデータ使用時" },
  on_wifi: { en: "On Wi-Fi", zh: "使用 Wi-Fi 时", 'zh-TW': "使用 Wi-Fi 時", ja: "Wi-Fi 使用時" },
  on_roaming: { en: "On Roaming", zh: "漫游时", 'zh-TW': "漫遊時", ja: "ローミング時" },
  photos: { en: "Photos", zh: "照片", 'zh-TW': "照片", ja: "写真" },
  files: { en: "Files", zh: "文件", 'zh-TW': "檔案", ja: "ファイル" },
  files_documents: { en: "Files / Documents", zh: "文件 / 文档", 'zh-TW': "檔案 / 文件", ja: "ファイル / ドキュメント" },
  all_on: { en: "All on", zh: "全部开启", 'zh-TW': "全部開啟", ja: "すべてオン" },
  all_off: { en: "All off", zh: "全部关闭", 'zh-TW': "全部關閉", ja: "すべてオフ" },
  max_file_size_auto_download: { en: "Maximum File Size for Auto-Download", zh: "自动下载文件大小限制", 'zh-TW': "自動下載檔案大小限制", ja: "自動ダウンロードの最大ファイルサイズ" },
  cache_management: { en: "Cache Management", zh: "缓存管理", 'zh-TW': "快取管理", ja: "キャッシュ管理" },
  cache_retention_period: { en: "Cache retention period", zh: "缓存保留时间", 'zh-TW': "快取保留時間", ja: "キャッシュ保持期間" },
  one_week: { en: "1 week", zh: "1 周", 'zh-TW': "1 週", ja: "1 週間" },
  one_month: { en: "1 month", zh: "1 个月", 'zh-TW': "1 個月", ja: "1 ヶ月" },
  three_months: { en: "3 months", zh: "3 个月", 'zh-TW': "3 個月", ja: "3 ヶ月" },
  forever: { en: "Forever", zh: "永久", 'zh-TW': "永久", ja: "永久的" },
  max_cache_size: { en: "Maximum cache size", zh: "最大缓存大小", 'zh-TW': "最大快取大小", ja: "最大キャッシュサイズ" },
  clear_images: { en: "Clear Images", zh: "清除图片", 'zh-TW': "清除圖片", ja: "画像を消去" },
  clear_video_files: { en: "Clear Video files", zh: "清除视频文件", 'zh-TW': "清除影片檔案", ja: "動画ファイルを消去" },
  clear_stickers_emojis: { en: "Clear Stickers & Emojis", zh: "清除贴纸和表情", 'zh-TW': "清除貼圖和表情", ja: "スタンプと絵文字を消去" },
  clear_other_cached: { en: "Clear Other Cached Data", zh: "清除其他缓存数据", 'zh-TW': "清除其他快取資料", ja: "その他のキャッシュデータを消去" },
  clear_cached_video: { en: "Clear Cached Video Stream Chunks", zh: "清除缓存的视频流片段", 'zh-TW': "清除快取的視訊串流片段", ja: "キャッシュされた動画ストリームを消去" },
  clear_all_cache: { en: "Clear All Cache", zh: "清除所有缓存", 'zh-TW': "清除所有快取", ja: "すべてのキャッシュを消去" },
  clear_local_cache: { en: "Clear Local Cache", zh: "清理本地缓存", 'zh-TW': "清理本機快取", ja: "ローカルキャッシュを消去" },
  clear_all_cache_settings: { en: "Clear All Cache Settings", zh: "清除所有缓存设置", 'zh-TW': "清除所有快取設定", ja: "すべてのキャッシュ設定を消去" },
  privacy_label: { en: "Privacy", zh: "隐私", 'zh-TW': "隱私", ja: "プライバシー" },
  last_seen_online: { en: "Last Seen & Online", zh: "最后在线与在线状态", 'zh-TW': "最後上線與線上狀態", ja: "最終オンラインとオンライン状態" },
  everybody: { en: "Everybody", zh: "所有人", 'zh-TW': "所有人", ja: "全員" },
  profile_photo: { en: "Profile Photo", zh: "头像", 'zh-TW': "大頭貼", ja: "プロフィール写真" },
  phone_number_label: { en: "Phone Number", zh: "电话号码", 'zh-TW': "電話號碼", ja: "電話番号" },
  my_contacts: { en: "My Contacts", zh: "我的联系人", 'zh-TW': "我的聯絡人", ja: "自分の連絡先" },
  security: { en: "Security", zh: "安全", 'zh-TW': "安全", ja: "セキュリティ" },
  two_step_verification: { en: "Two-Step Verification", zh: "两步验证", 'zh-TW': "雙步驟驗證", ja: "2 段階認証" },
  off_label: { en: "Off", zh: "关闭", 'zh-TW': "關閉", ja: "オフ" },
  active_sessions_label: { en: "Active Sessions", zh: "活跃会话", 'zh-TW': "活躍工作階段", ja: "アクティブセッション" },
  n_devices: { en: "3 devices", zh: "3 台设备", 'zh-TW': "3 台裝置", ja: "3 台のデバイス" },
  blocked_users_label: { en: "Blocked Users", zh: "已屏蔽用户", 'zh-TW': "已封鎖使用者", ja: "ブロックしたユーザー" },
  data_label: { en: "Data", zh: "数据", 'zh-TW': "資料", ja: "データ" },
  delete_synced_contacts: { en: "Delete Synced Contacts", zh: "删除已同步联系人", 'zh-TW': "刪除已同步聯絡人", ja: "同期した連絡先を削除" },
  delete_account: { en: "Delete Account", zh: "删除账号", 'zh-TW': "刪除帳號", ja: "アカウントを削除" },
  folders_description: { en: "Create folders for different groups of chats to easily access them.", zh: "为不同类型的聊天创建文件夹，方便快速访问。", 'zh-TW': "為不同類型的聊天建立資料夾，方便快速存取。", ja: "異なるグループのチャット用にフォルダーを作成して簡単にアクセス。" },
  create_new_folder: { en: "Create New Folder", zh: "创建新文件夹", 'zh-TW': "建立新資料夾", ja: "新しいフォルダーを作成" },
  team_chats: { en: "Team Chats", zh: "团队聊天", 'zh-TW': "團隊聊天", ja: "チームチャット" },
  demo: { en: "Demo", zh: "演示", 'zh-TW': "示範", ja: "デモ" },
  stickers_emoji_label: { en: "Stickers & Emoji", zh: "贴纸与表情", 'zh-TW': "貼圖與表情", ja: "スタンプと絵文字" },
  sticker_sets: { en: "Sticker Sets", zh: "贴纸包", 'zh-TW': "貼圖包", ja: "スタンプセット" },
  n_installed: { en: "0 installed", zh: "已安装 0 个", 'zh-TW': "已安裝 0 個", ja: "0 インストール済み" },
  suggest_emoji_label: { en: "Suggest Emoji", zh: "表情建议", 'zh-TW': "表情建議", ja: "絵文字を提案" },
  replace_text_emoji: { en: "Replace text like :) with emoji", zh: "将 :) 等文本替换为表情", 'zh-TW': "將 :) 等文字替換為表情", ja: ":) などのテキストを絵文字に置換" },
  custom_emoji: { en: "Custom Emoji", zh: "自定义表情", 'zh-TW': "自訂表情", ja: "カスタム絵文字" },
  devices_logged_in: { en: "Devices currently logged into your account.", zh: "当前登录此账号的设备。", 'zh-TW': "目前已登入此帳號的裝置。", ja: "現在このアカウントにログインしているデバイス。" },
  windows_chrome: { en: "Windows / Chrome", zh: "Windows / Chrome", 'zh-TW': "Windows / Chrome", ja: "Windows / Chrome" },
  this_browser_active: { en: "This browser / Active now / IP: not exposed", zh: "此浏览器 / 当前活跃 / IP：前端不展示", 'zh-TW': "此瀏覽器 / 目前活躍 / IP：前端不顯示", ja: "このブラウザ / 現在アクティブ / IP：非表示" },
  session_api_not_connected: { en: "Session management API not connected", zh: "会话管理接口未接入", 'zh-TW': "工作階段管理介面未接入", ja: "セッション管理 API 未接続" },
  only_current_browser: { en: "Only the current browser can be shown right now", zh: "当前只能显示本浏览器", 'zh-TW': "目前只能顯示本瀏覽器", ja: "現在は現在のブラウザのみ表示可能" },
  terminate_label: { en: "Terminate", zh: "终止", 'zh-TW': "終止", ja: "終了" },
  terminate_all_other: { en: "Terminate All Other Sessions", zh: "终止其它所有会话", 'zh-TW': "終止其他所有工作階段", ja: "他のすべてのセッションを終了" },
  language_label: { en: "Language", zh: "语言", 'zh-TW': "語言", ja: "言語" },
  search_chats: { en: "Search chats", zh: "搜索聊天", 'zh-TW': "搜尋聊天", ja: "チャットを検索" },
  new_chat: { en: "New chat", zh: "新建聊天", 'zh-TW': "新增聊天", ja: "新しいチャット" },
  toggle_mute: { en: "Toggle mute", zh: "切换静音", 'zh-TW': "切換靜音", ja: "ミュート切り替え" },
  send_message: { en: "Send message", zh: "发送消息", 'zh-TW': "傳送訊息", ja: "メッセージを送信" },
  new_line: { en: "New line", zh: "换行", 'zh-TW': "換行", ja: "改行" },
  settings_label: { en: "Settings", zh: "设置", 'zh-TW': "設定", ja: "設定" },
  edit_profile_label: { en: "Edit Profile", zh: "编辑资料", 'zh-TW': "編輯個人檔案", ja: "プロフィールを編集" },
  search_label: { en: "Search", zh: "搜索", 'zh-TW': "搜尋", ja: "検索" },
  devices_and_shortcuts: { en: "Devices and Shortcuts", zh: "设备与快捷键", 'zh-TW': "裝置與快速鍵", ja: "デバイスとショートカット" },
  // Additional settings page literal text
  chats: { en: "Chats", zh: "聊天", 'zh-TW': "聊天", ja: "チャット" },
  channels: { en: "Channels", zh: "频道", 'zh-TW': "頻道", ja: "チャンネル" },
  posts: { en: "Posts", zh: "动态", 'zh-TW': "動態", ja: "投稿" },
  apps_label: { en: "Apps", zh: "应用", 'zh-TW': "應用", ja: "アプリ" },
  private_chats: { en: "Private Chats", zh: "私聊", 'zh-TW': "私聊", ja: "プライベートチャット" },
  group_chats: { en: "Group Chats", zh: "群聊", 'zh-TW': "群組聊天", ja: "グループチャット" },
  all_chats: { en: "All Chats", zh: "全部聊天", 'zh-TW': "所有聊天", ja: "すべてのチャット" },
  search_chats_contacts: { en: "Search for chats, contacts, and messages", zh: "搜索聊天、联系人和消息", 'zh-TW': "搜尋聊天、聯絡人和訊息", ja: "チャット、連絡先、メッセージを検索" },
  new_private_chat: { en: "New Private Chat", zh: "新建私聊", 'zh-TW': "新增私聊", ja: "新しいプライベートチャット" },
  new_group: { en: "New Group", zh: "新建群组", 'zh-TW': "新增群組", ja: "新しいグループ" },
  new_channel: { en: "New Channel", zh: "新建频道", 'zh-TW': "新增頻道", ja: "新しいチャンネル" },
  soon: { en: "Soon", zh: "即将推出", 'zh-TW': "即將推出", ja: "近日公開" },
  add_another_account: { en: "Add Another Account", zh: "添加其他账号", 'zh-TW': "新增其他帳號", ja: "別のアカウントを追加" },
  loading_accounts: { en: "Loading accounts...", zh: "正在加载账号...", 'zh-TW': "正在載入帳號...", ja: "アカウントを読み込み中..." },
  groups_label: { en: "Groups", zh: "群组", 'zh-TW': "群組", ja: "グループ" },
  accounts: { en: "Accounts", zh: "账号", 'zh-TW': "帳號", ja: "アカウント" },
  add: { en: "Add", zh: "添加", 'zh-TW': "新增", ja: "追加" },
  back_to_settings: { en: "Back to Settings", zh: "返回设置", 'zh-TW': "返回設定", ja: "設定に戻る" },
  // Right panel labels
  members_tab: { en: "Members", zh: "成员", 'zh-TW': "成員", ja: "メンバー" },
  media: { en: "Media", zh: "媒体", 'zh-TW': "媒體", ja: "メディア" },
  links_label: { en: "Links", zh: "链接", 'zh-TW': "連結", ja: "リンク" },
  manage: { en: "Manage", zh: "管理", 'zh-TW': "管理", ja: "管理" },
  location_label: { en: "Location", zh: "位置", 'zh-TW': "位置", ja: "場所" },
  notifications_label: { en: "Notifications", zh: "通知", 'zh-TW': "通知", ja: "通知" },
  bio_label: { en: "Bio", zh: "个人简介", 'zh-TW': "個人簡介", ja: "自己紹介" }
};

// Build a reverse index: text → { key, lang }
// This allows the TreeWalker to match text in ANY language and replace with the target language
const literalTextReverseIndex = {};
Object.entries(literalTextTranslations).forEach(([key, langs]) => {
  Object.entries(langs).forEach(([lang, text]) => {
    if (!literalTextReverseIndex[text]) {
      literalTextReverseIndex[text] = { key, lang };
    }
  });
});

function applyLiteralTextTranslations() {
  const root = document.getElementById('sidebar-container');
  if (!root) return;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || parent.closest('script, style, textarea, input')) {
        return NodeFilter.FILTER_REJECT;
      }
      const text = node.nodeValue.trim();
      return text && literalTextReverseIndex[text] ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
    }
  });

  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(node => {
    const original = node.nodeValue;
    const leading = original.match(/^\s*/)[0];
    const trailing = original.match(/\s*$/)[0];
    const match = literalTextReverseIndex[original.trim()];
    if (match && literalTextTranslations[match.key] && literalTextTranslations[match.key][currentLanguage]) {
      node.nodeValue = `${leading}${literalTextTranslations[match.key][currentLanguage]}${trailing}`;
    }
  });
}

const LANG_DISPLAY_NAMES = {
  'en': 'English',
  'zh': '简体中文',
  'zh-TW': '繁體中文',
  'ja': '日本語'
};

function applyLanguage() {
  const langDisplay = document.getElementById("lang-display-val");
  if (langDisplay) {
    langDisplay.textContent = LANG_DISPLAY_NAMES[currentLanguage] || 'English';
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

const LANG_CYCLE = ['en', 'zh', 'zh-TW', 'ja'];

window.toggleLanguage = function(targetLang) {
  if (targetLang && LANG_CYCLE.includes(targetLang)) {
    currentLanguage = targetLang;
  } else {
    const idx = LANG_CYCLE.indexOf(currentLanguage);
    currentLanguage = LANG_CYCLE[(idx + 1) % LANG_CYCLE.length];
  }
  localStorage.setItem('ichat_lang', currentLanguage);
  applyLanguage();
};

function getStatusTranslation(status) {
  if (!status) return "";
  const statusStr = String(status).toLowerCase();
  if (currentLanguage === 'zh' || currentLanguage === 'zh-TW') {
    if (statusStr === 'online') return currentLanguage === 'zh-TW' ? '線上' : '在线';
    if (statusStr === 'offline') return currentLanguage === 'zh-TW' ? '離線' : '离线';
    const match = statusStr.match(/^(\d+)\s+members$/);
    if (match) {
      return currentLanguage === 'zh-TW' ? `${match[1]} 位成員` : `${match[1]} 位成员`;
    }
    return status;
  }
  if (currentLanguage === 'ja') {
    if (statusStr === 'online') return 'オンライン';
    if (statusStr === 'offline') return 'オフライン';
    const match = statusStr.match(/^(\d+)\s+members$/);
    if (match) return `${match[1]} メンバー`;
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
  if (currentLanguage === 'zh-TW') {
    if (roleStr === 'creator') return '擁有者';
    if (roleStr === 'admin') return '管理員';
    if (roleStr === 'member') return '一般成員';
    return role;
  }
  if (currentLanguage === 'ja') {
    if (roleStr === 'creator') return '作成者';
    if (roleStr === 'admin') return '管理者';
    if (roleStr === 'member') return 'メンバー';
    return role;
  }
  return role;
}

function getSystemMessageTranslation(text) {
  if (!text) return "";
  const trimmed = text.trim();
  const isZh = currentLanguage === 'zh' || currentLanguage === 'zh-TW';
  const isTW = currentLanguage === 'zh-TW';
  const isJa = currentLanguage === 'ja';

  if (isZh || isJa) {
    if (trimmed === 'Today') return isJa ? '今日' : (isTW ? '今天' : '今天');
    if (trimmed === 'Yesterday') return isJa ? '昨日' : (isTW ? '昨天' : '昨天');
    if (trimmed === 'Monday') return isJa ? '月曜日' : (isTW ? '星期一' : '星期一');
    if (trimmed === 'Tuesday') return isJa ? '火曜日' : (isTW ? '星期二' : '星期二');
    if (trimmed === 'Wednesday') return isJa ? '水曜日' : (isTW ? '星期三' : '星期三');
    if (trimmed === 'Thursday') return isJa ? '木曜日' : (isTW ? '星期四' : '星期四');
    if (trimmed === 'Friday') return isJa ? '金曜日' : (isTW ? '星期五' : '星期五');
    if (trimmed === 'Saturday') return isJa ? '土曜日' : (isTW ? '星期六' : '星期六');
    if (trimmed === 'Sunday') return isJa ? '日曜日' : (isTW ? '星期日' : '星期日');
  }

  if (isZh) {
    if (trimmed.includes("Channel secured with ECDH + HKDF")) {
      return isTW ? "🔒 通道已通過 ECDH + HKDF 加密。零知識保護已啟用。" : "🔒 通道已通过 ECDH + HKDF 加密。零知识保护已启用。";
    }

    let match = trimmed.match(/^(.+?)\s+created group\s+\"(.+?)\"$/);
    if (match) {
      const creator = match[1] === "You" ? (isTW ? "你" : "你") : match[1];
      return isTW ? `${creator} 建立了群組 "${match[2]}"` : `${creator} 创建了群组 "${match[2]}"`;
    }

    match = trimmed.match(/^(.+?)\s+added\s+(.+?)\s+to the group$/);
    if (match) {
      const adder = match[1] === "You" ? (isTW ? "你" : "你") : match[1];
      const addee = match[2] === "You" ? (isTW ? "你" : "你") : match[2];
      return isTW ? `${adder} 將 ${addee} 加入群組` : `${adder} 将 ${addee} 添加到群组`;
    }

    match = trimmed.match(/^(.+?)\s+removed\s+(.+?)\s+from the group$/);
    if (match) {
      const remover = match[1] === "You" ? (isTW ? "你" : "你") : match[1];
      const removee = match[2] === "You" ? (isTW ? "你" : "你") : match[2];
      return isTW ? `${remover} 將 ${removee} 移出了群組` : `${remover} 将 ${removee} 移出了群组`;
    }

    const timeMatch = trimmed.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (timeMatch) {
      const period = timeMatch[3].toUpperCase() === 'AM' ? (isTW ? '上午' : '上午') : (isTW ? '下午' : '下午');
      return `${period} ${timeMatch[1]}:${timeMatch[2]}`;
    }
  }

  if (isJa) {
    if (trimmed.includes("Channel secured with ECDH + HKDF")) {
      return "🔒 チャンネルは ECDH + HKDF で暗号化されました。ゼロ知識保護が有効です。";
    }

    let match = trimmed.match(/^(.+?)\s+created group\s+\"(.+?)\"$/);
    if (match) {
      const creator = match[1] === "You" ? "あなた" : match[1];
      return `${creator} がグループ "${match[2]}" を作成しました`;
    }

    match = trimmed.match(/^(.+?)\s+added\s+(.+?)\s+to the group$/);
    if (match) {
      const adder = match[1] === "You" ? "あなた" : match[1];
      const addee = match[2] === "You" ? "あなた" : match[2];
      return `${adder} が ${addee} をグループに追加しました`;
    }

    match = trimmed.match(/^(.+?)\s+removed\s+(.+?)\s+from the group$/);
    if (match) {
      const remover = match[1] === "You" ? "あなた" : match[1];
      const removee = match[2] === "You" ? "あなた" : match[2];
      return `${remover} が ${removee} をグループから削除しました`;
    }

    const timeMatch = trimmed.match(/^(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (timeMatch) {
      const period = timeMatch[3].toUpperCase() === 'AM' ? '午前' : '午後';
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

window.toggleSettingsHomeMoreMenu = function(e, forceClose) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("settings-home-more-dropdown");
  const btn = document.getElementById("settings-home-more-btn");
  if (!dropdown) return;

  const shouldOpen = !forceClose && dropdown.classList.contains("hidden");
  if (shouldOpen) {
    dropdown.classList.remove("hidden");
    if (btn) btn.classList.add("active");
    if (window.lucide) {
      window.lucide.createIcons();
    }
  } else {
    dropdown.classList.add("hidden");
    if (btn) btn.classList.remove("active");
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
    if (submenu) {
      submenu.classList.add("hidden");
      submenu.style.left = "";
      submenu.style.top = "";
    }
    const moreBtn = document.getElementById("menu-more-btn");
    if (moreBtn) moreBtn.classList.remove("is-open");
  }
};

window.toggleMoreSubmenu = function(e) {
  if (e) e.stopPropagation();
  const submenu = document.getElementById("main-menu-more-submenu");
  const moreBtn = document.getElementById("menu-more-btn");
  if (!submenu) return;
  const isOpening = submenu.classList.contains("hidden");
  if (isOpening && submenu.parentElement !== document.body) {
    document.body.appendChild(submenu);
  }
  submenu.classList.toggle("hidden", !isOpening);
  if (moreBtn) moreBtn.classList.toggle("is-open", isOpening);
  if (isOpening && moreBtn) {
    const rect = moreBtn.getBoundingClientRect();
    const menuWidth = 190;
    const gap = 8;
    const viewportPadding = 12;
    let left = rect.right + gap;
    let top = rect.top - 2;

    if (left + menuWidth + viewportPadding > window.innerWidth) {
      left = Math.max(viewportPadding, rect.left - menuWidth - gap);
    }

    const estimatedHeight = 238;
    if (top + estimatedHeight + viewportPadding > window.innerHeight) {
      top = Math.max(viewportPadding, window.innerHeight - estimatedHeight - viewportPadding);
    }

    submenu.style.left = left + "px";
    submenu.style.top = top + "px";
  }
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
  
  const msg = _t4("Boost Group feature is not yet available", "助力群组功能暂未开放", "強化群組功能暫未開放", "グループブースト機能はまだ利用できません");
  window.showToast(msg);
};

window.triggerBlockContactAction = async function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");

  const chat = conversationsById[activeChatId];
  if (!chat || chat.type !== "single" || !chat.peer_id) {
    window.showToast(currentLanguage === "zh" ? "当前会话不能拉黑" : "This conversation cannot be blocked.");
    return;
  }

  try {
    const response = await fetch("/api/privacy/block/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken") || ""
      },
      body: JSON.stringify({ user_id: chat.peer_id })
    });
    if (!response.ok) {
      const data = await response.json().catch(function() { return {}; });
      throw new Error(data.error || "block_failed");
    }
  } catch (err) {
    window.showToast(currentLanguage === "zh" ? "拉黑失败" : "Failed to block user.");
    return;
  }

  window.showToast(currentLanguage === "zh" ? "已拉黑该联系人" : "User blocked.");
  conversations = conversations.filter(function(c) { return c.id !== activeChatId; });
  conversationsById = {};
  conversations.forEach(function(c) { conversationsById[c.id] = c; });
  renderChatList();
  const emptyState = document.getElementById("empty-state-window");
  const activeChatWindow = document.getElementById("active-chat-window");
  if (emptyState) emptyState.classList.remove("hidden");
  if (activeChatWindow) activeChatWindow.classList.add("hidden");
  activeChatId = null;
  loadBlockedUsersCount();
};

window.triggerMuteAction = async function(e) {
  if (e) e.stopPropagation();
  const dropdown = document.getElementById("chat-header-more-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  const btn = document.getElementById("chat-header-more-btn");
  if (btn) btn.classList.remove("active");
  
  const chat = conversationsById[activeChatId];
  if (!chat) return;
  
  const isMuted = chat.muted_until && new Date(chat.muted_until) > new Date();
  const nextMuted = !isMuted;
  try {
    const response = await fetch(`/api/conversations/${activeChatId}/mute/`, {
      method: nextMuted ? 'POST' : 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: nextMuted ? JSON.stringify({ duration_minutes: 10080 }) : undefined
    });
    if (!response.ok) {
      throw new Error('mute_endpoint_unavailable');
    }
    const data = await response.json();
    chat.muted_until = data.muted_until || null;
    chat.is_muted = !!chat.muted_until;
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
    if (chat.muted_until) {
      muteTextEl.setAttribute("data-i18n", "menu_unmute_group");
      muteTextEl.textContent = _t4("Unmute", "取消静音", "取消靜音", "ミュート解除");
      if (muteIconEl) {
        muteIconEl.setAttribute("data-lucide", "bell");
      }
    } else {
      muteTextEl.setAttribute("data-i18n", "menu_mute_group");
      muteTextEl.textContent = _t4("Mute...", "静音免打扰", "靜音免打擾", "ミュート...");
      if (muteIconEl) {
        muteIconEl.setAttribute("data-lucide", "bell-off");
      }
    }
    if (window.lucide) window.lucide.createIcons();
  }
  
  const toastMsg = chat.muted_until 
    ? _t4("Mute notifications enabled", "已开启群聊免打扰", "已開啟群組免打擾", "ミュート通知を有効にしました")
    : _t4("Mute notifications disabled", "已取消群聊免打扰", "已取消群組免打擾", "ミュート通知を無効にしました");
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
  
  // Keep the normal chat header visible; selection controls live in the footer.
  const headerNormal = document.getElementById("chat-header-normal");
  const headerSelect = document.getElementById("chat-header-select-mode");
  if (headerNormal && headerSelect) {
    headerNormal.classList.remove("hidden");
    headerSelect.classList.add("hidden");
    headerSelect.classList.remove("flex");
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
  const count = selectedMessageIds.length;
  const text = currentLanguage === 'zh'
    ? `已选择 ${count} 条消息`
    : `Selected ${count} messages`;
  const countEl = document.getElementById("select-mode-count");
  if (countEl) {
    countEl.textContent = text;
  }
  const footerCountEl = document.getElementById("select-mode-footer-count");
  if (footerCountEl) footerCountEl.textContent = text;
  const deleteBtn = document.getElementById("select-mode-delete-btn");
  const forwardBtn = document.getElementById("select-mode-forward-btn");
  if (deleteBtn) deleteBtn.disabled = count === 0;
  if (forwardBtn) forwardBtn.disabled = count === 0;
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

function getSelectedMessagesForAction() {
  const ids = new Set(selectedMessageIds.map(String));
  return (messages || []).filter(function(msg) {
    return msg && ids.has(String(msg.id)) && !msg.isSystem && !msg.decryptError;
  });
}

window.deleteSelectedMessages = async function() {
  const selected = getSelectedMessagesForAction();
  if (!selected.length || !activeChatId) return;
  const failed = [];
  for (const msg of selected) {
    try {
      await apiFetch('/api/conversations/' + activeChatId + '/messages/' + msg.id + '/', {
        method: 'DELETE',
      });
      msg.isDeleted = true;
      msg.text = currentLanguage === 'zh' ? '消息已删除' : 'message deleted';
      msg.isSystem = true;
    } catch (err) {
      failed.push(msg.id);
    }
  }
  exitSelectMode();
  renderMessages();
  if (failed.length) {
    window.showToast(currentLanguage === 'zh' ? '部分消息删除失败' : 'Some messages could not be deleted');
  } else {
    window.showToast(currentLanguage === 'zh' ? '已删除所选消息' : 'Selected messages deleted');
  }
};

window.forwardSelectedMessages = function() {
  const selected = getSelectedMessagesForAction();
  if (!selected.length) return;
  if (window.MessageActions && typeof window.MessageActions.forward === 'function') {
    window.MessageActions.forward(selected);
  }
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
  const toastMsg = _t4("Report has been submitted", "举报已提交", "檢舉已提交", "報告が送信されました");
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
  const chat = conversationsById[chatIdToDelete];
  const isGroup = chat && chat.type === "group";
  
  try {
    const response = await fetch(isGroup ? `/api/groups/${chatIdToDelete}/leave/` : `/api/conversations/${chatIdToDelete}/`, {
      method: isGroup ? 'POST' : 'DELETE',
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
  
  const toastMsg = _t4("Conversation deleted", "会话已删除", "會話已刪除", "会話を削除しました");
  window.showToast(isGroup
    ? (currentLanguage === "zh" ? "已退出群聊" : "Left group")
    : (currentLanguage === "zh" ? "会话已删除" : "Conversation deleted"));
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
  
  navigateSidebar('settings-home');
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
  
  const msg = _t4("Already the latest version", "当前已是最新版本", "目前已是最新版本", "既に最新バージョンです");
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
      window.location.href = '/login/';
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


// ── Phase 3: AI Assistant GUI Handlers ─────────────────────────────────────

const AI_MODEL_SETTINGS_KEY = 'ichat_ai_model_settings';
const AI_HISTORY_KEY = 'ichat_ai_history';
const AI_CONVERSATION_ID = 'ai-assistant';
const AI_ASSISTANTS_KEY = 'ichat_ai_assistants';
const AI_DEFAULT_MODEL_SETTINGS = {
  endpoint: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
  apiKey: '',
  model: 'qwen-plus'
};

function getAiAssistantSessions() {
  let sessions = [];
  try {
    sessions = JSON.parse(localStorage.getItem(AI_ASSISTANTS_KEY) || '[]');
  } catch (err) {
    sessions = [];
  }
  if (!Array.isArray(sessions)) sessions = [];
  if (!sessions.some(session => session.id === AI_CONVERSATION_ID)) {
    sessions.unshift({
      id: AI_CONVERSATION_ID,
      title: 'AI Assistant',
      created_at: new Date().toISOString(),
    });
  }
  localStorage.setItem(AI_ASSISTANTS_KEY, JSON.stringify(sessions));
  return sessions;
}

function getAiAssistantSession(sessionId = activeAiAssistantId) {
  return getAiAssistantSessions().find(session => session.id === sessionId) || getAiAssistantSessions()[0];
}

function getAiStorageSuffix(sessionId = activeAiAssistantId) {
  return sessionId === AI_CONVERSATION_ID ? '' : `:${sessionId}`;
}

function getAiHistoryKey(sessionId = activeAiAssistantId) {
  return `${AI_HISTORY_KEY}${getAiStorageSuffix(sessionId)}`;
}

function getAiModelSettingsKey(sessionId = activeAiAssistantId) {
  return `${AI_MODEL_SETTINGS_KEY}${getAiStorageSuffix(sessionId)}`;
}

function getAiHistory(sessionId = activeAiAssistantId) {
  try {
    return JSON.parse(localStorage.getItem(getAiHistoryKey(sessionId)) || '[]');
  } catch (err) {
    return [];
  }
}

function setAiHistory(history, sessionId = activeAiAssistantId) {
  localStorage.setItem(getAiHistoryKey(sessionId), JSON.stringify(history || []));
}

function getAiDisplayModel() {
  return getAiModelSettings().model || AI_DEFAULT_MODEL_SETTINGS.model;
}

function getAiProviderInfo(model) {
  const modelId = model || getAiDisplayModel();
  const key = String(modelId || '').toLowerCase();
  if (key.includes('claude')) {
    return {
      key: 'claude',
      model: modelId,
      providerName: 'Claude',
      displayName: 'Claude Assistant',
      shortLabel: 'C',
      iconUrl: '/static/images/ai-model-claude.svg',
    };
  }
  if (key.includes('gpt') || key.startsWith('o1') || key.startsWith('o3') || key.startsWith('o4') || key.includes('openai')) {
    return {
      key: 'gpt',
      model: modelId,
      providerName: 'GPT',
      displayName: 'GPT Assistant',
      shortLabel: 'GPT',
      iconUrl: '/static/images/ai-model-gpt.svg',
    };
  }
  if (key.includes('qwen') || key.includes('tongyi')) {
    return {
      key: 'qwen',
      model: modelId,
      providerName: 'Qwen',
      displayName: 'Qwen Assistant',
      shortLabel: 'Q',
      iconUrl: '/static/images/ai-model-qwen.svg',
    };
  }
  return {
    key: 'ai',
    model: modelId,
    providerName: 'AI',
    displayName: 'AI Assistant',
    shortLabel: 'AI',
    iconUrl: '',
  };
}

function renderAiAvatarInner(info) {
  if (info && info.iconUrl) {
    return `<img src="${escapeHtml(info.iconUrl)}" alt="${escapeHtml(info.providerName)}" class="ai-model-logo-img">`;
  }
  return escapeHtml((info && info.shortLabel) || 'AI');
}

function renderAiAvatarHtml(className = 'message-avatar', extraClass = '') {
  const info = getAiProviderInfo();
  const classes = info.iconUrl
    ? `${className} ai-model-avatar ${extraClass}`
    : `${className} bg-gradient-to-tr from-brand-light to-purple-500 text-white flex items-center justify-center font-bold text-xs ${extraClass}`;
  return `<div class="${classes}" title="${escapeHtml(info.displayName)}">${renderAiAvatarInner(info)}</div>`;
}

function applyAiAvatarToElement(el, sizeClass) {
  if (!el) return;
  const info = getAiProviderInfo();
  el.className = sizeClass + (info.iconUrl
    ? ' ai-model-avatar flex items-center justify-center'
    : ' bg-gradient-to-tr from-brand-light to-purple-500 text-white flex items-center justify-center font-bold shadow-sm');
  el.innerHTML = renderAiAvatarInner(info);
  el.title = info.displayName;
}

function updateAiHeaderModel() {
  const model = getAiDisplayModel();
  const info = getAiProviderInfo(model);
  const headerTitle = document.getElementById("ai-header-title");
  if (headerTitle) headerTitle.textContent = info.displayName;
  applyAiAvatarToElement(
    document.getElementById("ai-header-avatar"),
    "w-10 h-10 rounded-full flex-shrink-0 overflow-hidden"
  );
  applyAiAvatarToElement(
    document.getElementById("ai-greeting-avatar"),
    "message-avatar"
  );
  const headerStatus = document.getElementById("ai-header-model-status");
  if (headerStatus) {
    headerStatus.textContent = `${model} • Online`;
  }
  const detailsStatus = document.getElementById("details-status");
  if (detailsStatus && activeSpecialChatId === activeAiAssistantId) {
    const settings = getAiModelSettings();
    detailsStatus.textContent = currentLanguage === 'zh'
      ? `${model} • ${settings.apiKey ? '已配置' : '未配置 API Key'}`
      : `${model} • ${settings.apiKey ? 'Configured' : 'API key missing'}`;
  }
}

function getAiConversationListItem(sessionId = activeAiAssistantId) {
  const session = getAiAssistantSession(sessionId);
  const history = getAiHistory(sessionId);
  const lastTurn = history.length ? history[history.length - 1] : null;
  const lastDate = lastTurn && lastTurn.created_at ? new Date(lastTurn.created_at) : null;
  const model = getAiModelSettings(sessionId).model || AI_DEFAULT_MODEL_SETTINGS.model;
  const info = getAiProviderInfo(model);
  return {
    id: sessionId,
    type: 'ai',
    is_ai_assistant: true,
    name: session && session.title ? session.title : info.displayName,
    model_display_name: info.displayName,
    initials: info.shortLabel,
    avatar_url: info.iconUrl,
    avatar_fit: 'contain',
    avatar_color: '#5b6ee1',
    is_secure: false,
    unread: 0,
    last_message_preview: lastTurn
      ? (lastTurn.role === 'user' ? `${currentLanguage === 'zh' ? '你' : 'You'}: ${lastTurn.content}` : lastTurn.content)
      : `${model} • ${currentLanguage === 'zh' ? '在线' : 'Online'}`,
    last_message_at: lastDate && !Number.isNaN(lastDate.getTime()) ? lastDate.toISOString() : null,
  };
}

function refreshAiConversationListItem() {
  renderChatList();
}

function aiTurnToMessage(turn, index) {
  const id = turn.id || `ai-msg-${turn.role}-${index}`;
  const createdAt = turn.created_at || new Date().toISOString();
  return {
    id,
    text: turn.content || '',
    created_at: createdAt,
    time: formatClockTime(new Date(createdAt)),
    isSelf: turn.role === 'user',
    sender_name: turn.role === 'user' ? (currentLanguage === 'zh' ? '你' : 'You') : getAiProviderInfo().displayName,
    isAiAssistant: true,
    message_type: 'text',
  };
}

function syncAiMessagesForActions(history) {
  messages = (history || getAiHistory()).map(aiTurnToMessage);
}

function findAiTurnById(messageId) {
  return getAiHistory().find(function(turn, index) {
    return String(turn.id || `ai-msg-${turn.role}-${index}`) === String(messageId);
  });
}

window.deleteAiMessage = function(messageId) {
  const history = getAiHistory().filter(function(turn, index) {
    return String(turn.id || `ai-msg-${turn.role}-${index}`) !== String(messageId);
  });
  setAiHistory(history);
  syncAiMessagesForActions(history);
  renderAiHistory(history);
  refreshAiConversationListItem();
};

function createAiAssistantSession() {
  const sessions = getAiAssistantSessions();
  const id = `ai-assistant-${Date.now()}`;
  const count = sessions.length + 1;
  const currentSettings = getAiModelSettings(activeAiAssistantId);
  sessions.push({
    id,
    title: `AI Assistant ${count}`,
    created_at: new Date().toISOString(),
  });
  localStorage.setItem(AI_ASSISTANTS_KEY, JSON.stringify(sessions));
  setAiModelSettings(currentSettings, id);
  renderChatList();
  openAiAssistant(id);
}

function deleteAiAssistantSession(sessionId) {
  if (sessionId === AI_CONVERSATION_ID) {
    clearAiAssistantSession(sessionId);
    return;
  }
  if (!confirm(currentLanguage === 'zh' ? '确定删除这个 AI Assistant 吗？' : 'Delete this AI Assistant?')) return;
  const sessions = getAiAssistantSessions().filter(session => session.id !== sessionId);
  localStorage.setItem(AI_ASSISTANTS_KEY, JSON.stringify(sessions));
  localStorage.removeItem(getAiHistoryKey(sessionId));
  localStorage.removeItem(getAiModelSettingsKey(sessionId));
  if (activeAiAssistantId === sessionId) {
    activeAiAssistantId = AI_CONVERSATION_ID;
    renderChatList();
    openAiAssistant(AI_CONVERSATION_ID);
    return;
  }
  renderChatList();
}

function clearAiAssistantSession(sessionId = activeAiAssistantId) {
  localStorage.removeItem(getAiHistoryKey(sessionId));
  if (activeAiAssistantId === sessionId) {
    syncAiMessagesForActions([]);
    renderAiHistory([]);
  }
  renderChatList();
}

function renameAiAssistantSession(sessionId) {
  const sessions = getAiAssistantSessions();
  const session = sessions.find(item => item.id === sessionId);
  if (!session) return;
  const nextTitle = prompt(currentLanguage === 'zh' ? '输入新的 Assistant 名称' : 'Enter a new Assistant name', session.title || 'AI Assistant');
  if (!nextTitle || !nextTitle.trim()) return;
  session.title = nextTitle.trim();
  localStorage.setItem(AI_ASSISTANTS_KEY, JSON.stringify(sessions));
  renderChatList();
  if (activeAiAssistantId === sessionId) updateAiHeaderModel();
}

function showLegacyAiAssistantConversationMenu(e, conv) {
  if (!window.ContextMenu) return;
  const x = e.clientX || 0;
  const y = e.clientY || 0;
  window.ContextMenu.show(x, y, [
    {
      icon: 'sparkles',
      label: currentLanguage === 'zh' ? '新建 Assistant' : 'New Assistant',
      onClick: createAiAssistantSession,
    },
    {
      icon: 'edit-3',
      label: currentLanguage === 'zh' ? '重命名' : 'Rename',
      onClick: function() { renameAiAssistantSession(conv.id); },
    },
    { divider: true },
    {
      icon: 'x-circle',
      label: currentLanguage === 'zh' ? '清空历史' : 'Clear History',
      onClick: function() { clearAiAssistantSession(conv.id); },
    },
    {
      icon: 'trash-2',
      label: currentLanguage === 'zh' ? '删除 Assistant' : 'Delete Assistant',
      danger: true,
      onClick: function() { deleteAiAssistantSession(conv.id); },
    },
  ]);
}

function showAiAssistantConversationMenu(e, conv) {
  if (!window.ContextMenu || !conv) return;
  const x = e.clientX || 0;
  const y = e.clientY || 0;
  const items = [
    {
      icon: 'sparkles',
      label: currentLanguage === 'zh' ? '\u65b0\u5efa Assistant' : 'New Assistant',
      onClick: createAiAssistantSession,
    },
    {
      icon: 'edit-3',
      label: currentLanguage === 'zh' ? '\u91cd\u547d\u540d' : 'Rename',
      onClick: function() { renameAiAssistantSession(conv.id); },
    },
    { divider: true },
    {
      icon: 'x-circle',
      label: currentLanguage === 'zh' ? '\u6e05\u7a7a\u5386\u53f2' : 'Clear History',
      onClick: function() { clearAiAssistantSession(conv.id); },
    },
  ];

  if (conv.id !== AI_CONVERSATION_ID) {
    items.push({
      icon: 'trash-2',
      label: currentLanguage === 'zh' ? '\u5220\u9664 Assistant' : 'Delete Assistant',
      danger: true,
      onClick: function() { deleteAiAssistantSession(conv.id); },
    });
  }

  window.ContextMenu.show(x, y, items);
}

function bindAiMessageActionHandlers() {
  const container = document.getElementById("ai-history-container");
  if (!container || container.dataset.aiActionsBound === "1") return;
  container.dataset.aiActionsBound = "1";
  container.addEventListener("contextmenu", function(e) {
    const bubble = e.target.closest(".message-bubble-custom[data-message-id]");
    if (!bubble || !container.contains(bubble)) return;
    const msg = messages.find(function(item) {
      return String(item.id) === String(bubble.dataset.messageId);
    });
    if (!msg || !window.MessageActions || typeof window.MessageActions.showMenu !== 'function') return;
    e.preventDefault();
    e.stopPropagation();
    window.MessageActions.showMenu(e, msg, getAiConversationListItem());
  });
}

function getAiModelSettings(sessionId = activeAiAssistantId) {
  try {
    const saved = JSON.parse(localStorage.getItem(getAiModelSettingsKey(sessionId)) || '{}');
    return Object.assign({}, AI_DEFAULT_MODEL_SETTINGS, saved || {});
  } catch (err) {
    return Object.assign({}, AI_DEFAULT_MODEL_SETTINGS);
  }
}

function setAiModelSettings(settings, sessionId = activeAiAssistantId) {
  const normalized = Object.assign({}, AI_DEFAULT_MODEL_SETTINGS, settings || {});
  localStorage.setItem(getAiModelSettingsKey(sessionId), JSON.stringify(normalized));
  return normalized;
}

function syncAiModelSettingsForm() {
  const settings = getAiModelSettings();
  const endpointInput = document.getElementById("ai-model-endpoint");
  const apiKeyInput = document.getElementById("ai-model-api-key");
  const modelSelect = document.getElementById("ai-model-name");
  const status = document.getElementById("ai-model-settings-status");

  if (endpointInput) endpointInput.value = settings.endpoint || "";
  if (apiKeyInput) apiKeyInput.value = settings.apiKey || "";
  if (modelSelect) {
    const hasOption = Array.from(modelSelect.options).some(option => option.value === settings.model);
    if (!hasOption && settings.model) {
      modelSelect.add(new Option(settings.model, settings.model));
    }
    modelSelect.value = settings.model || AI_DEFAULT_MODEL_SETTINGS.model;
  }
  if (status) {
    status.textContent = settings.apiKey
      ? (currentLanguage === 'zh' ? '已保存模型设置，发送消息时将使用该配置。' : 'Model settings saved. New messages will use this configuration.')
      : (currentLanguage === 'zh' ? '未保存 API Key 时将使用本地 Mock 响应。' : 'Without an API key, the local mock response is used.');
  }
}

function saveAiModelSettings() {
  const endpointInput = document.getElementById("ai-model-endpoint");
  const apiKeyInput = document.getElementById("ai-model-api-key");
  const modelSelect = document.getElementById("ai-model-name");
  const settings = setAiModelSettings({
    endpoint: endpointInput ? endpointInput.value.trim() : AI_DEFAULT_MODEL_SETTINGS.endpoint,
    apiKey: apiKeyInput ? apiKeyInput.value.trim() : "",
    model: modelSelect ? modelSelect.value : AI_DEFAULT_MODEL_SETTINGS.model
  });
  syncAiModelSettingsForm();
  updateAiModelSummary(settings);
  updateAiHeaderModel();
  refreshAiConversationListItem();
  window.showToast && window.showToast(currentLanguage === 'zh' ? 'AI 模型设置已保存。' : 'AI model settings saved.');
}

function clearAiModelSettings() {
  localStorage.removeItem(getAiModelSettingsKey(activeAiAssistantId));
  syncAiModelSettingsForm();
  updateAiModelSummary(getAiModelSettings());
  updateAiHeaderModel();
  refreshAiConversationListItem();
  window.showToast && window.showToast(currentLanguage === 'zh' ? 'AI 模型设置已清空。' : 'AI model settings cleared.');
}

function getAiRequestConfigForSend() {
  const settings = getAiModelSettings();
  if (!settings.apiKey) return {};
  return {
    endpoint: settings.endpoint,
    api_key: settings.apiKey,
    model: settings.model
  };
}

function updateAiModelSummary(settings) {
  const config = settings || getAiModelSettings();
  setDetailsText("right-panel-username", config.model || AI_DEFAULT_MODEL_SETTINGS.model);
  const status = document.getElementById("details-status");
  if (status) {
    status.textContent = currentLanguage === 'zh'
      ? `${config.model || 'Qwen'} • ${config.apiKey ? '已配置' : '未配置 API Key'}`
      : `${config.model || 'Qwen'} • ${config.apiKey ? 'Configured' : 'API key missing'}`;
  }
}

function openAiAssistant(sessionId = AI_CONVERSATION_ID) {
  activeAiAssistantId = decodeURIComponent(String(sessionId || AI_CONVERSATION_ID));
  getAiAssistantSessions();
  // Save draft of currently active chat
  saveActiveConversationDraftFromInput();
  closeChatSearch();

  // De-select active chat item
  document.querySelectorAll(".chat-item-btn").forEach(item => item.classList.remove("active"));
  activeChatId = null;
  activeSpecialChatId = activeAiAssistantId;
  const aiListItem = document.getElementById(`chat-item-${activeAiAssistantId}`);
  if (aiListItem) aiListItem.classList.add("active");

  // Toggle layout window visibility
  const emptyState = document.getElementById("empty-state-window");
  if (emptyState) emptyState.classList.add("hidden");

  const activeWindow = document.getElementById("active-chat-window");
  if (activeWindow) activeWindow.classList.add("hidden");

  const aiWindow = document.getElementById("ai-assistant-window");
  if (aiWindow) aiWindow.classList.remove("hidden");

  // Close details panel if open
  if (window.rightPanelOpen) {
    window.toggleRightPanel();
  }

  // Load and render history from localStorage
  const history = getAiHistory();
  syncAiMessagesForActions(history);
  renderAiHistory(history);
  bindAiMessageActionHandlers();
  updateAiHeaderModel();

  // Set greeting time to current system time
  const greetingTime = document.getElementById("ai-greeting-time");
  if (greetingTime) {
    const now = new Date();
    greetingTime.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // Bind Enter key to textarea
  const textarea = document.getElementById("ai-input-textarea");
  if (textarea && !window.aiInputListenerBound) {
    window.aiInputListenerBound = true;
    textarea.addEventListener("keydown", function(e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendAiMessage();
      }
    });
  }

  // Mobile layout responsiveness
  if (window.innerWidth < 768) {
    document.getElementById("sidebar-container").classList.add("hidden");
    document.getElementById("chat-window-container").classList.remove("hidden");
    document.getElementById("chat-window-container").classList.add("w-full");
    window.location.hash = 'ai-assistant-open';
  }

  scrollAiToBottom();
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
}

function scrollAiToBottom() {
  const container = document.getElementById("ai-history-container");
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

function aiUsePrompt(promptText) {
  const textarea = document.getElementById("ai-input-textarea");
  if (textarea) {
    textarea.value = promptText;
    textarea.focus();
    adjustTextareaHeight(textarea);
  }
}

function clearAiChat() {
  const confirmMsg = currentLanguage === 'zh'
    ? '确定要清空与 AI 助手的对话历史吗？'
    : 'Are you sure you want to clear your AI Assistant chat history?';
    
  if (confirm(confirmMsg)) {
    localStorage.removeItem(getAiHistoryKey(activeAiAssistantId));
    syncAiMessagesForActions([]);
    renderAiHistory([]);
    refreshAiConversationListItem();
  }
}

function renderMarkdownText(text) {
  return renderMessageMarkdown(text);
}

function renderAiHistory(history) {
  const container = document.getElementById("ai-history-container");
  if (!container) return;
  syncAiMessagesForActions(history);

  // Clear everything except default greeting
  const greeting = document.getElementById("ai-greeting-bubble");
  container.innerHTML = "";
  if (greeting) {
    container.appendChild(greeting);
  }

  history.forEach((turn, index) => {
    const msgId = turn.id || `ai-msg-${turn.role}-${index}`;
    const createdAt = turn.created_at ? new Date(turn.created_at) : new Date();
    const timeStr = formatClockTime(createdAt);
    let bubbleHtml = "";
    if (turn.role === 'user') {
      bubbleHtml = `
        <div class="message-row message-row-self">
          <div class="message-bubble-custom bubble-self" data-message-id="${msgId}"><div class="message-text-content">${escapeHtml(turn.content)}</div><div class="message-meta-line"><span>${timeStr}</span></div></div>
        </div>
      `;
    } else if (turn.role === 'assistant') {
      bubbleHtml = `
        <div class="message-row message-row-peer">
          ${renderAiAvatarHtml('message-avatar')}
          <div class="message-bubble-custom bubble-peer" data-message-id="${msgId}"><div class="message-text-content">${renderMarkdownText(turn.content)}</div><div class="message-meta-line"><span>${timeStr}</span></div></div>
        </div>
      `;
    }
    container.insertAdjacentHTML('beforeend', bubbleHtml);
  });
  scrollAiToBottom();
}

async function sendAiMessage() {
  const textarea = document.getElementById("ai-input-textarea");
  if (!textarea) return;
  const text = textarea.value.trim();
  if (!text) return;

  // Clear text input
  textarea.value = "";
  textarea.style.height = "auto";

  const container = document.getElementById("ai-history-container");
  if (!container) return;

  const now = new Date();
  const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const userMsgId = 'ai-msg-user-' + Date.now();

  // 1. Append user message bubble
  const userHtml = `
    <div class="message-row message-row-self">
      <div class="message-bubble-custom bubble-self" data-message-id="${userMsgId}"><div class="message-text-content">${escapeHtml(text)}</div><div class="message-meta-line"><span>${timeStr}</span></div></div>
    </div>
  `;
  container.insertAdjacentHTML('beforeend', userHtml);
  scrollAiToBottom();

  // 2. Fetch history and update local history
  let aiHistory = getAiHistory();
  aiHistory.push({ role: 'user', content: text, id: userMsgId, created_at: now.toISOString() });
  setAiHistory(aiHistory);
  syncAiMessagesForActions(aiHistory);
  refreshAiConversationListItem();

  // 3. Append temporary typing bubble
  const typingId = "ai-typing-" + Date.now();
  const typingHtml = `
    <div class="message-row message-row-peer" id="${typingId}">
      ${renderAiAvatarHtml('message-avatar')}
      <div class="message-bubble-custom bubble-peer"><div class="message-text-content flex items-center space-x-1.5 py-1"><span class="w-1.5 h-1.5 bg-textSecondary rounded-full animate-bounce" style="animation-delay: 0ms"></span><span class="w-1.5 h-1.5 bg-textSecondary rounded-full animate-bounce" style="animation-delay: 150ms"></span><span class="w-1.5 h-1.5 bg-textSecondary rounded-full animate-bounce" style="animation-delay: 300ms"></span></div></div>
    </div>
  `;
  container.insertAdjacentHTML('beforeend', typingHtml);
  scrollAiToBottom();

  try {
    const response = await fetch('/api/ai/chat/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify({
        message: text,
        history: aiHistory.slice(0, -1).map(h => ({ role: h.role, content: h.content })),
        model_config: getAiRequestConfigForSend(),
        stream: true
      })
    });

    const typingBubble = document.getElementById(typingId);
    const replyMsgId = 'ai-msg-assistant-' + Date.now();
    const replyCreatedAt = new Date();
    const replyTimeStr = formatClockTime(replyCreatedAt);

    if (response.ok && response.body && (response.headers.get('Content-Type') || '').includes('text/event-stream')) {
      let reply = "";
      let displayedReply = "";
      if (typingBubble) {
        typingBubble.outerHTML = `
          <div class="message-row message-row-peer">
            ${renderAiAvatarHtml('message-avatar')}
            <div class="message-bubble-custom bubble-peer" data-message-id="${replyMsgId}"><div class="message-text-content"></div><div class="message-meta-line"><span>${replyTimeStr}</span></div></div>
          </div>
        `;
      }

      const replyBubble = document.querySelector(`.message-bubble-custom[data-message-id="${replyMsgId}"] .message-text-content`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let done = false;
      let renderLoopRunning = false;

      const renderNextCharacters = async () => {
        if (renderLoopRunning) return;
        renderLoopRunning = true;
        try {
          while (displayedReply.length < reply.length) {
            const remaining = reply.length - displayedReply.length;
            const step = remaining > 120 ? 6 : remaining > 40 ? 3 : 1;
            displayedReply = reply.slice(0, displayedReply.length + step);
            if (replyBubble) {
              replyBubble.innerHTML = renderMarkdownText(displayedReply);
            }
            scrollAiToBottom();
            await sleep(18);
          }
        } finally {
          renderLoopRunning = false;
        }
      };

      while (!done) {
        const readResult = await reader.read();
        done = readResult.done;
        buffer += decoder.decode(readResult.value || new Uint8Array(), { stream: !done });

        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const eventText of events) {
          const dataLines = eventText.split("\n")
            .filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trim());
          for (const dataLine of dataLines) {
            if (!dataLine || dataLine === "[DONE]") continue;
            const eventData = JSON.parse(dataLine);
            if (eventData.error) {
              throw new Error(eventData.detail || eventData.error);
            }
            if (eventData.delta) {
              reply += eventData.delta;
              renderNextCharacters();
            }
          }
        }
      }

      while (displayedReply.length < reply.length || renderLoopRunning) {
        await sleep(18);
      }
      if (replyBubble) {
        replyBubble.innerHTML = renderMarkdownText(reply);
      }

      if (!reply.trim()) {
        throw new Error("AI assistant returned an empty response.");
      }

      aiHistory.push({ role: 'assistant', content: reply, id: replyMsgId, created_at: replyCreatedAt.toISOString() });
      setAiHistory(aiHistory);
      syncAiMessagesForActions(aiHistory);
      refreshAiConversationListItem();
    } else {
      const data = await response.json();

      if (response.ok && data.response) {
        const reply = data.response;
        aiHistory.push({ role: 'assistant', content: reply, id: replyMsgId, created_at: replyCreatedAt.toISOString() });
        setAiHistory(aiHistory);
        syncAiMessagesForActions(aiHistory);
        refreshAiConversationListItem();

        if (typingBubble) {
          typingBubble.outerHTML = `
            <div class="message-row message-row-peer">
              ${renderAiAvatarHtml('message-avatar')}
              <div class="message-bubble-custom bubble-peer" data-message-id="${replyMsgId}"><div class="message-text-content">${renderMarkdownText(reply)}</div><div class="message-meta-line"><span>${replyTimeStr}</span></div></div>
            </div>
          `;
        }
      } else {
        const serverMessage = data.detail || data.error || 'Server error';
        throw new Error(serverMessage);
      }
    }
  } catch (err) {
    console.error("AI Assistant service failed:", err);
    const typingBubble = document.getElementById(typingId);
    if (typingBubble) {
      typingBubble.outerHTML = `
        <div class="message-row message-row-peer">
          ${renderAiAvatarHtml('message-avatar')}
          <div class="message-bubble-custom bubble-peer border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/20 text-red-600 dark:text-red-400"><div class="message-text-content">AI Assistant response failed. Please check the request address, API key, and model name in Model Info.<br><small class="opacity-80">${escapeHtml(err.message)}</small></div><div class="message-meta-line"><span>Error</span></div></div>
        </div>
      `;
    }
  }
  scrollAiToBottom();
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
}

// ── AI Assistant Right Panel & Dropdown ──

function toggleAiRightPanel() {
  const rightPanel = document.getElementById("right-panel");
  if (!rightPanel) return;

  // Populate right panel with AI Info
  updateDetailsPanelForAi();

  // Toggle collapse class if it is collapsed
  if (rightPanel.classList.contains("collapsed")) {
    window.toggleRightPanel();
  } else {
    // Toggle close if already showing AI Model Info
    const panelTitle = document.getElementById("right-panel-title");
    if (panelTitle && panelTitle.textContent === (currentLanguage === 'zh' ? '模型信息' : 'Model Info')) {
      window.toggleRightPanel();
    }
  }
}

function toggleAiMoreMenu(e) {
  if (e) {
    e.preventDefault();
    e.stopPropagation();
  }
  const menu = document.getElementById("ai-header-more-dropdown");
  if (menu) {
    menu.classList.toggle("hidden");
  }
}

async function updateDetailsPanelForAi() {
  const avatar = document.getElementById("details-avatar");
  const name = document.getElementById("details-name");
  const status = document.getElementById("details-status");
  const fp = document.getElementById("details-fingerprint");
  const fpWrapper = document.getElementById("right-panel-fingerprint-wrapper");
  const groupSection = document.getElementById("right-panel-group-section");
  const protocol = document.getElementById("right-panel-protocol");
  const resetKeyBtn = document.getElementById("right-panel-reset-key-btn");
  const verificationStatus = document.getElementById("right-panel-verification-status");

  setDetailsText("right-panel-title", currentLanguage === 'zh' ? '模型信息' : 'Model Info');
  setDetailsHidden("right-panel-ai-settings-card", false);
  syncAiModelSettingsForm();

  if (avatar) {
    applyAiAvatarToElement(avatar, "chat-details-avatar");
  }

  if (name) {
    const info = getAiProviderInfo();
    name.innerHTML = `<span>${escapeHtml(info.displayName)}</span><span class="user-role-badge badge-agent">${escapeHtml(info.providerName)}</span>`;
  }
  if (status) {
    status.textContent = currentLanguage === 'zh' ? 'Qwen 大语言模型 • 在线' : 'Qwen AI Model • Online';
  }

  updateAiModelSummary();
  setDetailsText("right-panel-username-label", currentLanguage === 'zh' ? '运行模型' : 'Running Model');
  setDetailsHidden("right-panel-username-row", false);

  setDetailsText("right-panel-bio", currentLanguage === 'zh'
    ? '基于通义千问大语言模型，为您提供智能文本总结、草稿润色、知识问答服务。所有对话数据在本地独立存储并隔离，不读取 E2EE 私密消息。'
    : 'Based on Qwen Large Language Model, providing text summarization, drafting, and QA services. All chat data is isolated locally and never reads E2EE private messages.');
  setDetailsText("right-panel-bio-label", currentLanguage === 'zh' ? '模型简介' : 'Model Description');
  setDetailsHidden("right-panel-bio-row", false);

  setDetailsHidden("right-panel-email-row", true);
  setDetailsHidden("right-panel-phone-row", true);
  setDetailsHidden("right-panel-location-row", true);
  setDetailsHidden("right-panel-link-row", true);
  setDetailsHidden("right-panel-notify-row", true);
  setDetailsHidden("right-panel-qr-btn", true);

  setDetailsHidden("right-panel-action-media", true);
  setDetailsHidden("right-panel-action-files", true);

  const encTitle = document.querySelector("#right-panel-encryption-card .chat-details-section-title");
  if (encTitle) encTitle.textContent = currentLanguage === 'zh' ? '安全与隐私边界' : 'Security & Sandbox';

  if (protocol) protocol.textContent = "HTTPS / TLS 1.3";
  if (resetKeyBtn) resetKeyBtn.classList.add("hidden");

  if (verificationStatus) {
    verificationStatus.className = "font-semibold text-green-500 flex items-center space-x-1";
    verificationStatus.innerHTML = '<i data-lucide="shield-check" class="w-3.5 h-3.5 mr-0.5 inline-block text-green-500"></i><span>' + (currentLanguage === 'zh' ? '已安全隔离' : 'Sandbox Isolated') + '</span>';
  }

  if (fpWrapper) fpWrapper.classList.remove("hidden");
  const fpLabel = fpWrapper.querySelector("span");
  if (fpLabel) fpLabel.textContent = currentLanguage === 'zh' ? '沙箱说明' : 'Sandbox Info';

  if (fp) {
    fp.textContent = currentLanguage === 'zh'
      ? "AI 助手对话在本地沙箱内处理。除了您在输入框主动提交给 AI 的内容外，任何私聊、群聊消息或密钥等敏感数据均不会被上传或访问。"
      : "AI Chat is processed in a local sandbox. No private key, E2EE conversation message, or contact list will be accessed or sent to the model.";
  }

  const verifyFpBtn = fpWrapper.querySelector(".chat-details-verify-btn");
  if (verifyFpBtn) verifyFpBtn.classList.add("hidden");

  if (groupSection) groupSection.classList.add("hidden");

  if (window.lucide) window.lucide.createIcons();
}

// ── AI Assistant Search Handlers ──

let aiSearchResults = [];
let aiSearchIndex = -1;

function getAiSearchEls() {
  return {
    overlay: document.getElementById("ai-search-overlay"),
    input: document.getElementById("ai-search-input"),
    results: document.getElementById("ai-search-results"),
    prev: document.getElementById("ai-search-prev"),
    next: document.getElementById("ai-search-next"),
    close: document.getElementById("ai-search-close")
  };
}

function isAiSearchOpen() {
  const overlay = document.getElementById("ai-search-overlay");
  return !!overlay && !overlay.classList.contains("hidden");
}

function openAiSearch() {
  const els = getAiSearchEls();
  if (!els.overlay || !els.input) return;
  const header = document.getElementById("ai-header-normal");
  if (header) header.classList.add("chat-search-active");
  els.overlay.classList.remove("hidden");
  els.input.focus();
  els.input.select();
  runAiSearch(false);
  if (window.lucide) window.lucide.createIcons();
}

function closeAiSearch() {
  const els = getAiSearchEls();
  const header = document.getElementById("ai-header-normal");
  if (header) header.classList.remove("chat-search-active");
  clearAiSearchHighlight();
  aiSearchResults = [];
  aiSearchIndex = -1;
  if (els.input) els.input.value = "";
  if (els.results) {
    els.results.innerHTML = "";
    els.results.classList.add("hidden");
  }
  if (els.prev) els.prev.classList.add("hidden");
  if (els.next) els.next.classList.add("hidden");
  if (els.overlay) els.overlay.classList.add("hidden");
}

function clearAiSearchHighlight() {
  document.querySelectorAll(".message-search-hit").forEach(function(el) {
    el.classList.remove("message-search-hit");
  });
}

function runAiSearch(activateFirst) {
  const els = getAiSearchEls();
  if (!els.input) return;
  const query = els.input.value.trim();
  clearAiSearchHighlight();
  aiSearchIndex = -1;

  const history = JSON.parse(localStorage.getItem('ichat_ai_history') || '[]');

  if (!query) {
    aiSearchResults = [];
    renderAiSearchResults(query);
    return;
  }

  const lowerQuery = query.toLowerCase();
  aiSearchResults = [];
  history.forEach(function(turn, index) {
    if (!turn) return;
    if (String(turn.content || "").toLowerCase().includes(lowerQuery)) {
      aiSearchResults.push({
        id: turn.id || `ai-msg-${turn.role}-${index}`,
        role: turn.role,
        content: turn.content,
        timestamp: turn.timestamp
      });
    }
  });

  renderAiSearchResults(query);
  if (aiSearchResults.length && activateFirst) {
    activateAiSearchResult(0);
  }
}

function renderAiSearchResults(query) {
  const els = getAiSearchEls();
  if (!els.results) return;
  if (!query || !aiSearchResults.length) {
    els.results.classList.add("hidden");
    els.results.innerHTML = "";
    if (els.prev) els.prev.classList.add("hidden");
    if (els.next) els.next.classList.add("hidden");
    return;
  }
  if (els.prev) els.prev.classList.remove("hidden");
  if (els.next) els.next.classList.remove("hidden");

  els.results.classList.remove("hidden");
  els.results.innerHTML = aiSearchResults.map(function(msg, index) {
    const sender = msg.role === 'user' ? (currentLanguage === 'zh' ? '我' : 'Me') : 'AI';
    const cleanText = getSearchableMessageText({ text: msg.content });
    return `<button type="button" class="chat-search-result-item" data-search-index="${index}">
      <span class="chat-search-result-sender">${escapeHtml(sender)}</span>
      <span class="chat-search-result-text">${escapeHtml(cleanText)}</span>
    </button>`;
  }).join("");
}

function activateAiSearchResult(index) {
  if (!aiSearchResults.length) return;
  if (index < 0) index = aiSearchResults.length - 1;
  if (index >= aiSearchResults.length) index = 0;
  aiSearchIndex = index;
  clearAiSearchHighlight();

  const msg = aiSearchResults[aiSearchIndex];
  const bubbles = Array.from(document.querySelectorAll(".message-bubble-custom[data-message-id]"));
  const bubble = bubbles.find(function(el) {
    return String(el.dataset.messageId) === String(msg.id);
  });
  const row = bubble ? bubble.closest(".message-row") : null;
  if (row) row.classList.add("message-search-hit");
  if (bubble) {
    bubble.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const els = getAiSearchEls();
  if (els.results) {
    els.results.querySelectorAll(".chat-search-result-item").forEach(function(item, itemIndex) {
      item.classList.toggle("is-active", itemIndex === aiSearchIndex);
    });
  }
}

