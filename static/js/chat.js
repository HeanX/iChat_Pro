// iChat Pro - Client-side Encrypted Chat Engine
// Vanilla JavaScript utilizing Web Crypto API for ECDH + HKDF + AES-GCM

// Mock Database of Initial Chats
const defaultMockChats = {
  1: {
    id: 1,
    name: "Alice Vance",
    avatar: "AV",
    avatarBg: "bg-purple-600",
    status: "online",
    isEncrypted: true,
    fingerprint: "ECC: 9F8D 7E6A 5B4C 3D2E 1F0A 9B8C 7D6E 5F4A",
    messages: [
      { id: 100, text: "Today", isSystem: true },
      { id: 101, text: "Hey! Did you verify the security fingerprint yet?", time: "10:30", isSelf: false },
      { id: 102, text: "Not yet, let's open the profile details to check.", time: "10:32", isSelf: true },
      { id: 103, text: "Sounds good, make sure the SHA-256 hash matches.", time: "10:35", isSelf: false }
    ],
    unread: 1
  },
  2: {
    id: 2,
    name: "Bob Builder",
    avatar: "BB",
    avatarBg: "bg-blue-600",
    status: "offline",
    isEncrypted: true,
    fingerprint: "ECC: A1B2 C3D4 E5F6 7890 0987 6543 21FE DCBA",
    messages: [
      { id: 200, text: "Yesterday", isSystem: true },
      { id: 201, text: "The staging environment is deployed with secure WebSockets.", time: "16:45", isSelf: false, sender: "Bob Builder" },
      { id: 202, text: "Excellent. Did you configure AES-GCM 256-bit keys?", time: "16:48", isSelf: true },
      { id: 203, text: "Yes, HKDF handles the derivation properly.", time: "16:50", isSelf: false, sender: "Bob Builder" }
    ],
    unread: 0
  },
  3: {
    id: 3,
    name: "Core Dev Team",
    avatar: "CD",
    avatarBg: "bg-emerald-600",
    status: "3 members",
    isEncrypted: true,
    fingerprint: "ECC: DE3F 8A9B 7C6D 5E4F 3A2B 1C0D 9E8F 7A6B",
    messages: [
      { id: 300, text: "Monday", isSystem: true },
      { id: 3001, text: "🔒 Channel secured with ECDH + HKDF. Zero-knowledge active.", isSystem: true },
      { id: 3002, text: "Alice Vance created group \"Core Dev Team\"", isSystem: true },
      { id: 3003, text: "Alice Vance added Bob Builder to the group", isSystem: true },
      { id: 301, text: "Weekly sync starts in 10 minutes.", time: "10:35", isSelf: false, sender: "Alice Vance" },
      { id: 302, text: "Bring the latest WebSocket logs too.", time: "10:36", isSelf: false, sender: "Alice Vance" },
      { id: 303, text: "I'll join shortly. Finishing up the cryptographic pipeline.", time: "10:38", isSelf: true },
      { id: 304, text: "I'll bring the deployment checklist.", time: "10:39", isSelf: false, sender: "Bob Builder" },
      { id: 305, text: "Owner review is done on my side.", time: "10:40", isSelf: false, sender: "Bob Builder" }
    ],
    unread: 0
  },
  4: {
    id: 4,
    name: "Security Sentinel Bot",
    avatar: "SB",
    avatarBg: "bg-zinc-700",
    status: "online",
    isEncrypted: true,
    fingerprint: "ECC: 55AA BB66 CC77 DD88 EE99 FF00 1122 3344",
    messages: [
      { id: 400, text: "Today", isSystem: true },
      { id: 401, text: "Hello! I am the iChat Pro Cryptographic Test Bot.", time: "10:00", isSelf: false },
      { id: 402, text: "Send any message, and I will reply with an integrity signature.", time: "10:01", isSelf: false }
    ],
    unread: 2
  }
};

// Global Interactive States
let mockChats = {};
let activeChatId = null;
let currentLanguage = localStorage.getItem('ichat_lang') || 'en';
let isSelectingMessages = false;
let selectedMessageIds = [];

// Cryptographic engine states
let localPrivateKeyCrypto = null;
let localPublicKeyCrypto = null;
let activeSessionKey = null;
let activeSessionKeyHexHash = null;
let contactKeys = {}; // Key map: { chatId: { privateKey: JWK, publicKey: JWK } }

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
  if (Array.isArray(chat.messages)) {
    chat.messages.forEach(message => {
      if (message.time) message.time = normalizeTimeLabel(message.time);
    });
  }
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

// Helper: Calculate mock thumbprint of public key (visual aid)
async function getJwkThumbprint(jwk) {
  if (!jwk || !jwk.x) return "N/A";
  const encoder = new TextEncoder();
  const data = encoder.encode(jwk.x + ":" + jwk.y);
  const hash = await window.crypto.subtle.digest("SHA-256", data);
  return arrayBufferToHex(hash).substring(0, 16) + "...";
}

// Helper: Generate random security fingerprint string
function generateFingerprint() {
  const hexChars = "0123456789ABCDEF";
  let fp = "ECC:";
  for (let i = 0; i < 8; i++) {
    let chunk = "";
    for (let j = 0; j < 4; j++) {
      chunk += hexChars[Math.floor(Math.random() * 16)];
    }
    fp += " " + chunk;
  }
  return fp;
}

// Render Sidebar Chat List
function renderChatList() {
  const chatListContainer = document.querySelector('#sidebar-chat-view .overflow-y-auto');
  if (chatListContainer) {
    chatListContainer.innerHTML = "";
    Object.values(mockChats).forEach(chat => {
      appendChatItemToSidebar(chat);
    });
  }
}

// 1. Initialize E2E Cryptographic Engine & Load Stored Data
async function initializeE2EEngine() {
  try {
    // A. Generate/load active user's ECDH key pair
    let privKeyJwk = localStorage.getItem('ichat_ecdh_private_key');
    let pubKeyJwk = localStorage.getItem('ichat_ecdh_public_key');

    if (!privKeyJwk || !pubKeyJwk) {
      logToCryptoConsole("[Engine Initialization] Generating new ECDH P-256 key pair...");
      const keyPair = await window.crypto.subtle.generateKey(
        { name: 'ECDH', namedCurve: 'P-256' },
        true,
        ['deriveKey', 'deriveBits']
      );
      
      const priv = await window.crypto.subtle.exportKey('jwk', keyPair.privateKey);
      const pub = await window.crypto.subtle.exportKey('jwk', keyPair.publicKey);

      localStorage.setItem('ichat_ecdh_private_key', JSON.stringify(priv));
      localStorage.setItem('ichat_ecdh_public_key', JSON.stringify(pub));

      localPrivateKeyCrypto = keyPair.privateKey;
      localPublicKeyCrypto = keyPair.publicKey;
      logToCryptoConsole("[Engine Initialization] Successfully generated and stored new local key pair.");
    } else {
      logToCryptoConsole("[Engine Initialization] Found existing local key pair in localStorage.");
      localPrivateKeyCrypto = await window.crypto.subtle.importKey(
        'jwk',
        JSON.parse(privKeyJwk),
        { name: 'ECDH', namedCurve: 'P-256' },
        true,
        ['deriveKey', 'deriveBits']
      );
      localPublicKeyCrypto = await window.crypto.subtle.importKey(
        'jwk',
        JSON.parse(pubKeyJwk),
        { name: 'ECDH', namedCurve: 'P-256' },
        true,
        []
      );
    }

    logToCryptoConsole(`[Engine Initialization] Local Public Key Fingerprint: ${await getJwkThumbprint(JSON.parse(localStorage.getItem('ichat_ecdh_public_key')))}`);

    // B. Initialize/load mock remote contact keys for simulating exchange
    let contactKeysStr = localStorage.getItem('ichat_contact_keys');
    if (contactKeysStr) {
      contactKeys = JSON.parse(contactKeysStr);
    } else {
      contactKeys = {};
    }

    // Populate default contacts with valid keys if not present
    const defaultIds = [1, 2, 3, 4];
    let keysUpdated = false;
    for (const id of defaultIds) {
      if (!contactKeys[id]) {
        logToCryptoConsole(`[Engine Initialization] Generating mock ECDH P-256 key pair for contact ID ${id}...`);
        const pair = await window.crypto.subtle.generateKey(
          { name: 'ECDH', namedCurve: 'P-256' },
          true,
          ['deriveKey', 'deriveBits']
        );
        const privJwk = await window.crypto.subtle.exportKey('jwk', pair.privateKey);
        const pubJwk = await window.crypto.subtle.exportKey('jwk', pair.publicKey);

        contactKeys[id] = {
          privateKey: privJwk,
          publicKey: pubJwk
        };
        keysUpdated = true;
      }
    }
    if (keysUpdated) {
      localStorage.setItem('ichat_contact_keys', JSON.stringify(contactKeys));
    }

    // C. Initialize/load mockChats from localStorage
    const storedChats = localStorage.getItem('ichat_chats');
    if (storedChats) {
      mockChats = JSON.parse(storedChats);
      // Ensure group messages are always updated to contain E2EE system messages for Core Dev Team (id 3)
      if (mockChats[3]) {
        mockChats[3].isEncrypted = true;
        mockChats[3].messages = defaultMockChats[3].messages;
      }
    } else {
      mockChats = defaultMockChats;
      localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
    }

    Object.values(mockChats).forEach(normalizeChatData);
    localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
    
    // Dynamically append chats to sidebar list
    renderChatList();

    logToCryptoConsole("[Engine Initialization] Cryptographic system online.");
  } catch (err) {
    console.error("Failed to initialize cryptographic engine:", err);
    logToCryptoConsole(`[Engine Error] Initialization failed: ${err.message}`);
  }
}

// 2. Perform ECDH Key Agreement on Select Chat
async function deriveActiveSessionKey(chatId) {
  const chat = mockChats[chatId];
  if (!chat || !chat.isEncrypted) {
    activeSessionKey = null;
    activeSessionKeyHexHash = null;
    logToCryptoConsole(`[ECDH Key Agreement] Selected non-encrypted channel: ${chat ? chat.name : "N/A"}`);
    return;
  }

  try {
    logToCryptoConsole(`[ECDH Key Agreement] Computing shared secret for conversation ID: ${chatId} (${chat.name})`);
    
    const contactKeyData = contactKeys[chatId];
    if (!contactKeyData) {
      throw new Error(`Public key for contact ID ${chatId} is not initialized.`);
    }

    // Import remote public key
    const remotePubKey = await window.crypto.subtle.importKey(
      'jwk',
      contactKeyData.publicKey,
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      []
    );

    // 1. Perform ECDH Key Agreement to derive shared secret bits
    const sharedSecretBits = await window.crypto.subtle.deriveBits(
      {
        name: 'ECDH',
        public: remotePubKey
      },
      localPrivateKeyCrypto,
      256
    );

    // 2. Import shared secret bits as an HKDF master key
    const hkdfMasterKey = await window.crypto.subtle.importKey(
      'raw',
      sharedSecretBits,
      { name: 'HKDF' },
      false,
      ['deriveKey']
    );

    // 3. Derive 256-bit AES-GCM session key using HKDF
    const saltBytes = new TextEncoder().encode(chatId.toString());
    const infoBytes = new TextEncoder().encode('chat-message-encryption-v1');

    const derivedKey = await window.crypto.subtle.deriveKey(
      {
        name: 'HKDF',
        hash: 'SHA-256',
        salt: saltBytes,
        info: infoBytes
      },
      hkdfMasterKey,
      {
        name: 'AES-GCM',
        length: 256
      },
      true, // extractable to calculate visual hash
      ['encrypt', 'decrypt']
    );

    activeSessionKey = derivedKey;

    // Calculate derived key SHA-256 hash for logging
    const rawKey = await window.crypto.subtle.exportKey("raw", derivedKey);
    const hashBuffer = await window.crypto.subtle.digest("SHA-256", rawKey);
    activeSessionKeyHexHash = arrayBufferToHex(hashBuffer);

    logToCryptoConsole(`[ECDH Key Agreement] Handshake completed successfully.`);
    logToCryptoConsole(`[HKDF Key Derivation] Session key derived with salt: "${chatId}"`);
    logToCryptoConsole(`[HKDF Key Derivation] Derived Key Hash (SHA-256): ${activeSessionKeyHexHash}`);
  } catch (err) {
    console.error("ECDH + HKDF session key derivation failed:", err);
    logToCryptoConsole(`[ECDH Error] Derivation failed: ${err.message}`);
    activeSessionKey = null;
    activeSessionKeyHexHash = null;
  }
}

// 3. Append Chat Item UI to Sidebar List
function appendChatItemToSidebar(chat) {
  const chatListContainer = document.querySelector('#sidebar-chat-view .overflow-y-auto');
  if (!chatListContainer) return;

  const wrapper = document.createElement("div");
  wrapper.id = `chat-item-wrapper-${chat.id}`;
  wrapper.className = "w-full";

  const lastMsg = chat.messages.length ? chat.messages[chat.messages.length - 1] : null;
  const lastMsgText = lastMsg ? (lastMsg.isSystem ? getSystemMessageTranslation(lastMsg.text) : lastMsg.text) : (currentLanguage === 'zh' ? "暂无消息" : "No messages");
  const lastMsgTime = lastMsg ? getSystemMessageTranslation(normalizeTimeLabel(lastMsg.time)) : "";
  const unreadCount = Number(chat.unread || 0);

  wrapper.innerHTML = `
    <button id="chat-item-${chat.id}" onclick="selectChat('${chat.id}')"
      class="chat-item-btn w-full flex items-center px-4 py-3 border-b border-borderColor hover:bg-bgSearch transition-all text-left focus:outline-none relative group select-none">
      
      <div class="relative flex-shrink-0">
        <div class="w-12 h-12 rounded-full ${chat.avatarBg} text-white flex items-center justify-center font-bold text-base shadow-sm">
          ${chat.avatar}
        </div>
        ${chat.status === 'online' ? '<span class="absolute bottom-0 right-0 block h-3.5 w-3.5 rounded-full bg-green-500 border-2 border-bgSidebar" title="Online"></span>' : ''}
      </div>

      <div class="ml-3.5 flex-1 min-w-0">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-textMain truncate flex items-center space-x-1">
            <span>${chat.name}</span>
            ${chat.isEncrypted ? '<i data-lucide="lock" class="w-3.5 h-3.5 text-brand-light dark:text-brand-dark inline-block flex-shrink-0" title="End-to-End Encrypted" data-i18n-title="e2ee_badge"></i>' : ''}
          </h3>
          <span id="chat-time-${chat.id}" class="chat-item-time flex-shrink-0">${lastMsgTime}</span>
        </div>
        
        <div class="flex items-center justify-between mt-1">
          <p id="last-msg-${chat.id}" class="text-xs text-textSecondary truncate pr-4 leading-tight">
            ${lastMsgText}
          </p>
          
          <span id="unread-badge-${chat.id}" class="${unreadCount > 0 ? "" : "hidden"} unread-badge flex-shrink-0">
            ${unreadCount}
          </span>
        </div>
      </div>
    </button>
  `;

  chatListContainer.appendChild(wrapper);
  lucide.createIcons();
}

function updateSidebarPreview(chat, text, time) {
  if (!chat) return;
  const lastMsgEl = document.getElementById(`last-msg-${chat.id}`);
  const timeEl = document.getElementById(`chat-time-${chat.id}`);
  if (lastMsgEl) lastMsgEl.textContent = text.includes("You removed") || text.includes("created group") ? getSystemMessageTranslation(text) : text;
  if (timeEl) timeEl.textContent = getSystemMessageTranslation(normalizeTimeLabel(time));
}

// 4. Handle Chat Selecting
async function selectChat(chatId) {
  activeChatId = parseInt(chatId);
  const chat = mockChats[activeChatId];
  if (!chat) return;

  // Highlight active chat in sidebar list in Telegram Web style
  document.querySelectorAll(".chat-item-btn").forEach(item => {
    item.classList.remove("active");
  });
  const activeItem = document.getElementById(`chat-item-${chatId}`);
  if (activeItem) {
    activeItem.classList.add("active");
  }

  // Clear unread indicator badge
  const badge = document.getElementById(`unread-badge-${chatId}`);
  if (badge) {
    badge.classList.add("hidden");
    badge.textContent = "0";
  }
  chat.unread = 0;
  localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

  // Close header operations dropdown if open
  const headerDropdown = document.getElementById("chat-header-more-dropdown");
  const headerMoreBtn = document.getElementById("chat-header-more-btn");
  if (headerDropdown) headerDropdown.classList.add("hidden");
  if (headerMoreBtn) headerMoreBtn.classList.remove("bg-bgSearch", "text-textMain");

  // Run ECDH key agreement to derive active session key
  await deriveActiveSessionKey(activeChatId);

  // Populate header details
  document.getElementById("chat-header-avatar").textContent = chat.avatar;
  // Clear any dynamic background classes and apply specific one
  document.getElementById("chat-header-avatar").className = `w-10 h-10 rounded-full ${chat.avatarBg} text-white flex items-center justify-center font-bold text-sm shadow-sm`;
  document.getElementById("chat-header-name").textContent = chat.name;

  // Update header operations leave/delete group wording dynamically
  const leaveTextEl = document.getElementById("menu-delete-chat-text");
  if (leaveTextEl) {
    const isGroup = chat.status && chat.status.includes("members");
    if (isGroup) {
      leaveTextEl.setAttribute("data-i18n", "menu_leave_group");
      leaveTextEl.textContent = currentLanguage === 'zh' ? "退出群聊" : "Leave Group";
    } else {
      leaveTextEl.setAttribute("data-i18n", "menu_delete_chat");
      leaveTextEl.textContent = currentLanguage === 'zh' ? "删除聊天" : "Delete Chat";
    }
  }

  // Update header operations mute wording dynamically
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
  
  // Custom header status displaying E2EE dynamically for group/private chat
  if (chat.isEncrypted) {
    const statusText = getStatusTranslation(chat.status);
    const e2eeText = currentLanguage === 'zh' ? '🔒 端到端加密' : '🔒 End-to-end encrypted';
    document.getElementById("chat-header-status").innerHTML = `${statusText} &middot; <span class='text-brand-light dark:text-brand-dark font-semibold'>${e2eeText}</span>`;
  } else {
    document.getElementById("chat-header-status").textContent = getStatusTranslation(chat.status);
  }

  // Toggle E2EE input security banner
  const securityBanner = document.getElementById("chat-input-security-banner");
  if (securityBanner) {
    if (chat.isEncrypted) {
      securityBanner.classList.remove("hidden");
    } else {
      securityBanner.classList.add("hidden");
    }
  }

  // Handle lock icon visual state
  const lockBtn = document.getElementById("chat-header-lock");
  if (lockBtn) {
    if (chat.isEncrypted) {
      lockBtn.classList.remove("hidden");
    } else {
      lockBtn.classList.add("hidden");
    }
  }

  // Show Chat Viewport and Hide Empty State
  document.getElementById("active-chat-window").classList.remove("hidden");
  const emptyState = document.getElementById("empty-state-window");
  if (emptyState) {
    emptyState.classList.add("hidden");
  }

  // Adjust responsive layout for mobile viewport
  if (window.innerWidth < 768) {
    document.getElementById("sidebar-container").classList.add("hidden");
    document.getElementById("chat-window-container").classList.remove("hidden");
    document.getElementById("chat-window-container").classList.add("w-full");
    window.location.hash = 'chat-open';
  }

  // Update profile details panel
  updateDetailsPanel(chat);

  // Render conversation history
  renderMessages();
  scrollToBottom();
}

// 5. Update Contact Info & Details Panel
function updateDetailsPanel(chat) {
  const avatar = document.getElementById("details-avatar");
  const name = document.getElementById("details-name");
  const status = document.getElementById("details-status");
  const fp = document.getElementById("details-fingerprint");
  const fpWrapper = document.getElementById("right-panel-fingerprint-wrapper");
  const groupSection = document.getElementById("right-panel-group-section");
  const membersList = document.getElementById("right-panel-members-list");
  const membersCount = document.getElementById("right-panel-members-count");
  const protocol = document.getElementById("right-panel-protocol");
  
  if (avatar) {
    avatar.className = `w-20 h-20 rounded-full ${chat.avatarBg} text-white flex items-center justify-center font-bold text-2xl shadow-sm mb-3`;
    avatar.textContent = chat.avatar;
  }
  if (name) name.textContent = chat.name;
  if (status) status.textContent = getStatusTranslation(chat.status);
  
  if (chat.isEncrypted) {
    if (fpWrapper) fpWrapper.classList.remove("hidden");
    if (fp) fp.textContent = chat.fingerprint || `ECC: 9F8D 7E6A 5B4C 3D2E 1F0A 9B8C 7D6E 5F4A`;
    if (protocol) protocol.textContent = "ECDH + HKDF + AES-GCM";
  } else {
    if (fpWrapper) fpWrapper.classList.add("hidden");
  }

  // Populate dynamic group members in right details panel
  const isGroup = chat.status && chat.status.includes("members");
  if (isGroup) {
    if (groupSection) groupSection.classList.remove("hidden");
    
    // Calculate actual active member count by filtering out removed members
    const isAliceRemoved = chat.messages.some(m => m.text && m.text.includes("removed Alice Vance"));
    const isBobRemoved = chat.messages.some(m => m.text && m.text.includes("removed Bob Builder"));
    
    let activeMemberCount = 3;
    if (isAliceRemoved) activeMemberCount--;
    if (isBobRemoved) activeMemberCount--;
    
    if (membersCount) {
      membersCount.textContent = currentLanguage === 'zh' ? `群组成员 (${activeMemberCount})` : `Group Members (${activeMemberCount})`;
    }
    
    if (membersList) {
      membersList.innerHTML = "";
      
      const mockMembers = [
        { name: "You (Owner)", role: "Creator", status: "online", avatar: "U", bg: "bg-brand-light" }
      ];
      if (!isAliceRemoved) {
        mockMembers.push({ name: "Alice Vance", role: "Admin", status: "online", avatar: "AV", bg: "bg-purple-600" });
      }
      if (!isBobRemoved) {
        mockMembers.push({ name: "Bob Builder", role: "Member", status: "offline", avatar: "BB", bg: "bg-blue-600" });
      }
      
      mockMembers.forEach(m => {
        const item = document.createElement("div");
        item.className = "flex items-center justify-between py-2 border-b border-borderColor/30 last:border-none";
        
        let actionHtml = "";
        const displayName = m.name === "You (Owner)" && currentLanguage === 'zh' ? "你 (所有者)" : m.name;
        const translatedStatus = getStatusTranslation(m.status);
        const translatedRole = getRoleTranslation(m.role);
        
        if (m.name !== "You (Owner)") {
          const removeBtnText = currentLanguage === 'zh' ? '移除' : 'Remove';
          actionHtml = `<button onclick="window.removeGroupMember('${m.name}')" class="text-[10px] text-red-500 hover:underline hover:text-red-600 transition-colors font-medium">${removeBtnText}</button>`;
        } else {
          actionHtml = `<span class="text-[9px] bg-bgSearch text-textSecondary px-2 py-0.5 rounded font-mono">${translatedRole}</span>`;
        }

        item.innerHTML = `
          <div class="flex items-center space-x-2.5">
            <div class="w-8 h-8 rounded-full ${m.bg} text-white flex items-center justify-center font-bold text-xs">
              ${m.avatar}
            </div>
            <div class="leading-tight">
              <div class="text-xs font-semibold text-textMain">${displayName}</div>
              <div class="text-[9px] text-textSecondary">${translatedStatus}</div>
            </div>
          </div>
          ${actionHtml}
        `;
        membersList.appendChild(item);
      });
    }
  } else {
    if (groupSection) groupSection.classList.add("hidden");
  }
}

// Global Group Member Removal Action
window.removeGroupMember = function(memberName) {
  const confirmMsg = currentLanguage === 'zh'
    ? `您确定要将 ${memberName} 从群组中移除吗？`
    : `Are you sure you want to remove ${memberName} from the group?`;
    
  if (confirm(confirmMsg)) {
    logToCryptoConsole(`[Group Admin Action] Removing member: "${memberName}"`);
    const chat = mockChats[activeChatId];
    if (chat) {
      const time = formatClockTime();
      chat.messages.push({
        id: Date.now(),
        text: `You removed ${memberName} from the group`,
        time: time,
        isSystem: true
      });
      localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
      renderMessages();
      scrollToBottom();
      
      // Recalculate member count
      let currentCount = 3;
      if (chat.messages.some(m => m.text && m.text.includes("removed Alice Vance"))) currentCount--;
      if (chat.messages.some(m => m.text && m.text.includes("removed Bob Builder"))) currentCount--;
      
      chat.status = `${currentCount} members`;
      const statusText = getStatusTranslation(chat.status);
      const e2eeText = currentLanguage === 'zh' ? '🔒 端到端加密' : '🔒 End-to-end encrypted';
      document.getElementById("chat-header-status").innerHTML = `${statusText} &middot; <span class="text-brand-light dark:text-brand-dark font-semibold">${e2eeText}</span>`;
      
      // Refresh Details panel
      updateDetailsPanel(chat);
    }
  }
};

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

// 7. Render Messages into Viewport
function renderMessages() {
  const container = document.getElementById("message-history-container");
  if (!container) return;
  container.innerHTML = "";

  const chat = mockChats[activeChatId];
  if (!chat) return;

  chat.messages.forEach((msg, index) => {
    const groupMeta = getMessageGroupMeta(chat.messages, index, chat);
    container.appendChild(createMessageBubbleElement(msg, groupMeta));
  });
}

// Helper to derive initials and background color for user avatars based on name
function getSenderAvatarInfo(senderName) {
  let initials = "";
  if (senderName) {
    const parts = senderName.split(" ");
    if (parts.length > 1) {
      initials = (parts[0][0] + parts[1][0]).toUpperCase();
    } else {
      initials = senderName.substring(0, 2).toUpperCase();
    }
  } else {
    initials = "AV";
  }
  
  // Hash sender initials to select a background color class
  const colors = ["bg-red-500", "bg-orange-500", "bg-yellow-500", "bg-green-500", "bg-teal-500", "bg-blue-500", "bg-indigo-500", "bg-purple-500", "bg-pink-500"];
  let hash = 0;
  for (let i = 0; i < initials.length; i++) {
    hash = initials.charCodeAt(i) + ((hash << 5) - hash);
  }
  const colorClass = colors[Math.abs(hash) % colors.length];
  
  return { initials, colorClass };
}

// Helper to look up member role in a group chat
function getGroupMemberRole(senderName) {
  if (senderName === "Alice Vance") return "Admin";
  if (senderName === "Bob Builder") return "Owner";
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

function getMessageSenderKey(msg, chat) {
  if (msg.isSystem) return "System";
  if (msg.isSelf) return "Self";
  const isGroup = chat && chat.status && chat.status.includes("members");
  return isGroup ? (msg.sender || "Unknown") : (chat ? chat.name : "Peer");
}

function isConsecutiveMessage(msg) {
  const chat = mockChats[activeChatId];
  if (!chat || msg.isSystem) return false;

  const index = chat.messages.findIndex(item => item.id === msg.id);
  if (index <= 0) return false;

  const prev = chat.messages[index - 1];
  if (!prev || prev.isSystem) return false;

  return getMessageSenderKey(prev, chat) === getMessageSenderKey(msg, chat);
}

function getMessageGroupMeta(messages, index, chat) {
  const msg = messages[index];
  if (!msg || msg.isSystem) {
    return { isConsecutive: false, isFirstInGroup: true, isLastInGroup: true };
  }

  const prev = messages[index - 1];
  const next = messages[index + 1];
  const senderKey = getMessageSenderKey(msg, chat);
  const hasSamePrev = prev && !prev.isSystem && getMessageSenderKey(prev, chat) === senderKey;
  const hasSameNext = next && !next.isSystem && getMessageSenderKey(next, chat) === senderKey;

  return {
    isConsecutive: Boolean(hasSamePrev),
    isFirstInGroup: !hasSamePrev,
    isLastInGroup: !hasSameNext
  };
}

// 8. Create Message Bubble DOM Node
function createMessageBubbleElement(msg, groupMeta) {
  if (typeof groupMeta === "boolean") {
    groupMeta = {
      isConsecutive: groupMeta,
      isFirstInGroup: !groupMeta,
      isLastInGroup: true
    };
  } else if (!groupMeta) {
    const isConsecutive = isConsecutiveMessage(msg);
    groupMeta = {
      isConsecutive,
      isFirstInGroup: !isConsecutive,
      isLastInGroup: true
    };
  }

  const { isConsecutive, isFirstInGroup, isLastInGroup } = groupMeta;

  const div = document.createElement("div");
  div.className = `message-row ${isConsecutive ? "message-row-grouped" : ""} ${isFirstInGroup ? "message-row-group-first" : ""} ${isLastInGroup ? "message-row-group-last" : ""}`;

  if (msg.isSystem) {
    div.className += " message-row-system";
    const isCrypto = msg.text.includes("🔒") || msg.text.includes("secured") || msg.text.includes("encrypted");
    const translatedText = getSystemMessageTranslation(msg.text);
    if (isCrypto) {
      div.innerHTML = `
        <div class="system-capsule system-capsule-secure">
          <i data-lucide="lock" class="w-3.5 h-3.5 text-emerald-500 inline-block"></i>
          <span>${escapeHtml(translatedText)}</span>
        </div>
      `;
    } else {
      div.innerHTML = `
        <div class="system-capsule">
          <span>${escapeHtml(translatedText)}</span>
        </div>
      `;
    }
    
    // Instantly initialize lucide icons inside the system badge if needed
    setTimeout(() => {
      if (div.querySelector("[data-lucide]")) {
        lucide.createIcons();
      }
    }, 0);
    
    return div;
  }

  if (!msg.isSystem) {
    div.onclick = (e) => {
      if (isSelectingMessages) {
        e.stopPropagation();
        toggleMessageSelection(msg.id);
      }
    };
  }

  const checkboxHtml = `<div class="message-select-checkbox select-none ${isSelectingMessages ? '' : 'hidden'}" id="msg-select-check-${msg.id}"><i data-lucide="${selectedMessageIds.includes(msg.id) ? 'check-circle-2' : 'circle'}" class="w-5 h-5 text-textSecondary"></i></div>`;

  const chat = mockChats[activeChatId];
  const isGroup = chat && chat.status && chat.status.includes("members");
  const senderName = getMessageSenderKey(msg, chat);
  const messageText = escapeHtml(msg.text);
  const messageTime = escapeHtml(msg.time || "");

  if (msg.isSelf) {
    div.className += " message-row-self";
    if (isSelectingMessages) div.className += " message-row-selecting";
    div.innerHTML = `
      ${checkboxHtml}
      <div class="message-bubble-custom bubble-self" data-message-id="${msg.id}">
        <p class="message-text-content">${messageText}</p>
        <div class="message-meta-line">
          <span>${messageTime}</span>
          <i data-lucide="check-check" class="w-3.5 h-3.5"></i>
        </div>
      </div>
    `;
  } else {
    div.className += " message-row-peer";
    if (isSelectingMessages) div.className += " message-row-selecting";
    
    let avatarHtml = "";
    if (isLastInGroup) {
      const avatarInfo = getSenderAvatarInfo(senderName);
      avatarHtml = `
        <div class="message-avatar ${avatarInfo.colorClass}" title="${escapeHtml(senderName)}">
          ${avatarInfo.initials}
        </div>
      `;
    } else {
      avatarHtml = `<div class="message-avatar-spacer" aria-hidden="true"></div>`;
    }
    
    let senderNameHtml = "";
    if (isFirstInGroup) {
      const senderDisplayId = isGroup ? senderName : getSenderAvatarInfo(senderName).initials;
      const role = getGroupMemberRole(senderName);
      const translatedRole = getRoleTranslation(role);
      const roleClass = role === "Owner" ? "role-owner" : role === "Admin" ? "role-admin" : "role-member";
      senderNameHtml = `
        <div class="message-sender-line">
          <span class="message-sender-name">${escapeHtml(senderDisplayId)}</span>
          ${isGroup ? `<span class="message-role-tag ${roleClass}">${translatedRole}</span>` : ""}
        </div>
      `;
    }

    div.innerHTML = `
      ${checkboxHtml}
      ${avatarHtml}
      <div class="message-bubble-custom bubble-peer" data-message-id="${msg.id}">
        ${senderNameHtml}
        <p class="message-text-content">${messageText}</p>
        <div class="message-meta-line">
          <span>${messageTime}</span>
        </div>
      </div>
    `;
  }

  // Instantly initialize newly appended Lucide icons inside the bubble
  setTimeout(() => {
    if (div.querySelector("[data-lucide]")) {
      lucide.createIcons();
    }
  }, 0);

  return div;
}

// 9. Encrypt & Send Message
async function sendMessage() {
  const textarea = document.getElementById("chat-input-textarea");
  if (!textarea) return;

  const text = textarea.value.trim();
  if (!text) return;

  const chat = mockChats[activeChatId];
  if (!chat) return;

  const time = formatClockTime();
  let textToDisplay = text;

  // Perform cryptographic encryption pipeline if chat is E2EE
  if (chat.isEncrypted) {
    if (!activeSessionKey) {
      logToCryptoConsole("[Encryption Error] Session key not derived. Message cannot be encrypted.");
      alert("Cryptographic session key not derived yet. Please wait.");
      return;
    }

    try {
      logToCryptoConsole(`[Encryption Pipeline] Initializing AES-GCM encryption...`);
      logToCryptoConsole(`[Encryption Pipeline] Plaintext input: "${text}"`);

      // Generate a random 12-byte initialization vector (IV)
      const iv = window.crypto.getRandomValues(new Uint8Array(12));
      const ivHex = arrayBufferToHex(iv.buffer);

      const plaintextEncoded = new TextEncoder().encode(text);

      // Perform encryption
      const encryptedBuffer = await window.crypto.subtle.encrypt(
        {
          name: "AES-GCM",
          iv: iv,
          tagLength: 128
        },
        activeSessionKey,
        plaintextEncoded
      );

      const fullBytes = new Uint8Array(encryptedBuffer);
      const ciphertextBytes = fullBytes.slice(0, fullBytes.length - 16);
      const authTagBytes = fullBytes.slice(fullBytes.length - 16);

      const ciphertextHex = arrayBufferToHex(ciphertextBytes.buffer);
      const authTagHex = arrayBufferToHex(authTagBytes.buffer);

      logToCryptoConsole(`[Encryption Pipeline] IV (Nonce) generated: ${ivHex}`);
      logToCryptoConsole(`[Encryption Pipeline] Derived Session Key Hash: ${activeSessionKeyHexHash}`);
      logToCryptoConsole(`[Encryption Pipeline] Ciphertext: ${ciphertextHex}`);
      logToCryptoConsole(`[Encryption Pipeline] Authentication Tag (MAC): ${authTagHex}`);
      logToCryptoConsole(`[Encryption Pipeline] Encryption successful.`);
    } catch (err) {
      console.error("AES-GCM encryption failed:", err);
      logToCryptoConsole(`[Encryption Error] Encryption failed: ${err.message}`);
      return;
    }
  }

  // Store and render local decrypted bubble (simulates user sending & decrypting)
  const newMsg = {
    id: Date.now(),
    text: textToDisplay,
    time: time,
    isSelf: true
  };

  chat.messages.push(newMsg);
  localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

  // Reset Input
  textarea.value = "";
  textarea.style.height = "auto";

  // Render and update sidebar
  const container = document.getElementById("message-history-container");
  if (container) {
    container.appendChild(createMessageBubbleElement(newMsg));
    scrollToBottom();
  }

  updateSidebarPreview(chat, textToDisplay, time);

  // Simulate Remote Automated Reply
  simulateReply(chat, text);
}

// 10. Simulate Remote Cryptographic Automated Reply
function simulateReply(chat, userText) {
  const replyDelay = 1200;

  if (!chat.isEncrypted) {
    // Standard Plaintext Automated reply simulation
    setTimeout(() => {
      const time = formatClockTime();
      const replyMsg = {
        id: Date.now(),
        text: `Automated reply from ${chat.name} regarding: "${userText}"`,
        time: time,
        isSelf: false
      };
      
      chat.messages.push(replyMsg);
      localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

      if (activeChatId === chat.id) {
        const container = document.getElementById("message-history-container");
        if (container) {
          container.appendChild(createMessageBubbleElement(replyMsg));
          scrollToBottom();
        }
      } else {
        triggerUnreadCount(chat.id);
      }

      updateSidebarPreview(chat, replyMsg.text, time);
    }, replyDelay);
    return;
  }

  // Encrypted Reply Simulation
  setTimeout(async () => {
    try {
      let plaintextReply = "";
      if (chat.id === 4) {
        // Test Bot reply: Compute real SHA-256 signature of user's message
        const encodedText = new TextEncoder().encode(userText);
        const digestBuffer = await window.crypto.subtle.digest("SHA-256", encodedText);
        const digestHex = arrayBufferToHex(digestBuffer);
        plaintextReply = `🔒 E2E Integrity Secured.\n\nPayload Integrity: ${digestHex.substring(0, 32)}...\nVerification Timestamp: ${new Date().toISOString()}`;
      } else {
        plaintextReply = `Security handshake verified. Processing payload: "${userText.substring(0, 10)}${userText.length > 10 ? '...' : ''}"`;
      }

      logToCryptoConsole(`[Simulation: ${chat.name} Sends Encrypted Response]`);
      logToCryptoConsole(`[Simulation] Plaintext payload to encrypt: "${plaintextReply}"`);

      // Encrypt reply using derived session key
      const iv = window.crypto.getRandomValues(new Uint8Array(12));
      const ivHex = arrayBufferToHex(iv.buffer);
      const plaintextEncoded = new TextEncoder().encode(plaintextReply);

      const encryptedBuffer = await window.crypto.subtle.encrypt(
        {
          name: "AES-GCM",
          iv: iv,
          tagLength: 128
        },
        activeSessionKey,
        plaintextEncoded
      );

      const fullBytes = new Uint8Array(encryptedBuffer);
      const ciphertextBytes = fullBytes.slice(0, fullBytes.length - 16);
      const authTagBytes = fullBytes.slice(fullBytes.length - 16);

      const ciphertextHex = arrayBufferToHex(ciphertextBytes.buffer);
      const authTagHex = arrayBufferToHex(authTagBytes.buffer);

      logToCryptoConsole(`[Simulation] Encrypted Response Payload successfully generated.`);
      logToCryptoConsole(`[Simulation] IV: ${ivHex}`);
      logToCryptoConsole(`[Simulation] Ciphertext: ${ciphertextHex}`);
      logToCryptoConsole(`[Simulation] Auth Tag (MAC): ${authTagHex}`);

      // Now client receives the ciphertext payload and runs actual decryption
      logToCryptoConsole(`[Decryption Pipeline] Received encrypted response payload from ${chat.name}...`);
      logToCryptoConsole(`[Decryption Pipeline] Ciphertext Input (Hex): ${ciphertextHex}`);
      logToCryptoConsole(`[Decryption Pipeline] IV (Nonce): ${ivHex}`);
      logToCryptoConsole(`[Decryption Pipeline] Authentication Tag: ${authTagHex}`);

      // Reassemble ciphertext & tag for SubtleCrypto input
      const combinedPayload = new Uint8Array(ciphertextBytes.length + authTagBytes.length);
      combinedPayload.set(ciphertextBytes, 0);
      combinedPayload.set(authTagBytes, ciphertextBytes.length);

      // Decrypt
      const decryptedBuffer = await window.crypto.subtle.decrypt(
        {
          name: "AES-GCM",
          iv: iv,
          tagLength: 128
        },
        activeSessionKey,
        combinedPayload.buffer
      );

      const decryptedText = new TextDecoder().decode(decryptedBuffer);

      logToCryptoConsole(`[Decryption Pipeline] Authentication Tag verified. Decrypted successfully.`);
      logToCryptoConsole(`[Decryption Pipeline] Plaintext Output: "${decryptedText}"`);

      // Store in memory & render
      const time = formatClockTime();
      const replyMsg = {
        id: Date.now(),
        text: decryptedText,
        time: time,
        isSelf: false,
        sender: chat.status && chat.status.includes("members") ? "Alice Vance" : undefined
      };

      chat.messages.push(replyMsg);
      localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

      if (activeChatId === chat.id) {
        const container = document.getElementById("message-history-container");
        if (container) {
          container.appendChild(createMessageBubbleElement(replyMsg));
          scrollToBottom();
        }
      } else {
        triggerUnreadCount(chat.id);
      }

      updateSidebarPreview(chat, decryptedText, time);
    } catch (err) {
      console.error("Simulation decryption failed:", err);
      logToCryptoConsole(`[Decryption Pipeline Error] Decryption failed / Auth Tag mismatch: ${err.message}`);
    }
  }, replyDelay);
}

// Helper: Handle Unread Message Badge increment
function triggerUnreadCount(chatId) {
  const badge = document.getElementById(`unread-badge-${chatId}`);
  if (badge) {
    const chat = mockChats[chatId];
    if (chat) {
      chat.unread = Number(chat.unread || 0) + 1;
      localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
      badge.textContent = chat.unread;
    } else {
      badge.textContent = parseInt(badge.textContent || "0", 10) + 1;
    }
    badge.classList.remove("hidden");
  }
}

// 11. Add Contact Modal Logic
async function handleAddContact(username) {
  const newId = Date.now().toString(); // Consistent string keys

  logToCryptoConsole(`[ECDH Key Exchange] Initiating E2EE identity handshake with dynamic contact: ${username}...`);

  try {
    // Generate new keys for contact to simulate exchange
    const pair = await window.crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      ['deriveKey', 'deriveBits']
    );
    const privJwk = await window.crypto.subtle.exportKey('jwk', pair.privateKey);
    const pubJwk = await window.crypto.subtle.exportKey('jwk', pair.publicKey);

    contactKeys[newId] = {
      privateKey: privJwk,
      publicKey: pubJwk
    };
    localStorage.setItem('ichat_contact_keys', JSON.stringify(contactKeys));

    logToCryptoConsole(`[ECDH Key Exchange] Exchanged public identity keys with ${username}.`);
    logToCryptoConsole(`[ECDH Key Exchange] E2E crypt link ready. Fingerprints computed.`);

    const fpStr = generateFingerprint();

    const newChat = {
      id: parseInt(newId),
      name: username,
      avatar: username.substring(0, 2).toUpperCase(),
      avatarBg: "bg-orange-600",
      status: "online",
      isEncrypted: true,
      fingerprint: fpStr,
      messages: [
        {
          id: Date.now(),
          text: `System: End-to-end encrypted channel initialized with ${username}. Safety keys verified.`,
          time: formatClockTime(),
          isSystem: true
        }
      ]
    };

    mockChats[newId] = newChat;
    localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

    appendChatItemToSidebar(newChat);
    selectChat(newId);
  } catch (err) {
    console.error("Add contact failed:", err);
    logToCryptoConsole(`[ECDH Error] Dynamic key exchange failed: ${err.message}`);
  }
}

// 12. Create Group Modal Logic
function handleCreateGroup(groupName) {
  const newId = Date.now().toString();

  const checkedBoxes = document.querySelectorAll(".group-member-checkbox:checked");
  const memberIds = Array.from(checkedBoxes).map(cb => cb.value);
  const memberNames = memberIds.map(id => mockChats[id].name);

  const membersTextList = ["You", ...memberNames].join(", ");

  const newChat = {
    id: parseInt(newId),
    name: groupName,
    avatar: groupName.substring(0, 2).toUpperCase(),
    avatarBg: "bg-emerald-600",
    status: `${memberIds.length + 1} members`,
    isEncrypted: false,
    messages: [
      {
        id: Date.now(),
        text: `System: Group chat "${groupName}" created. Members: ${membersTextList}`,
        time: formatClockTime(),
        isSystem: true
      }
    ]
  };

  mockChats[newId] = newChat;
  localStorage.setItem('ichat_chats', JSON.stringify(mockChats));

  appendChatItemToSidebar(newChat);
  selectChat(newId);
}

// Populate contact list inside the Create Group modal
function populateGroupMembersList() {
  const listEl = document.getElementById("group-members-list");
  if (!listEl) return;
  listEl.innerHTML = "";

  Object.values(mockChats).forEach(chat => {
    if (chat.isEncrypted && chat.id !== 4) { // Only real human contacts
      const item = document.createElement("div");
      item.className = "flex items-center space-x-3 py-1.5 px-2 hover:bg-bgSearch/40 rounded cursor-pointer";
      item.innerHTML = `
        <input type="checkbox" class="group-member-checkbox rounded border-borderColor text-brand-light focus:ring-brand-light w-4 h-4" value="${chat.id}" id="member-chk-${chat.id}">
        <div class="w-8 h-8 rounded-full ${chat.avatarBg} text-white flex items-center justify-center font-bold text-xs">
          ${chat.avatar}
        </div>
        <label class="text-sm font-medium text-textMain cursor-pointer flex-1" for="member-chk-${chat.id}">
          ${chat.name}
        </label>
      `;
      listEl.appendChild(item);
    }
  });
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
      showSettingsPanel();
      toggleDrawer();
    });
  }
  if (menuProfile) {
    menuProfile.addEventListener("click", () => {
      showSettingsPanel();
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
  Object.values(mockChats).forEach(chat => {
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

function showSettingsPanel() {
  const sidebarChat = document.getElementById("sidebar-chat-view");
  const sidebarSettings = document.getElementById("sidebar-settings-view");
  if (sidebarChat && sidebarSettings) {
    sidebarChat.classList.add("hidden");
    sidebarSettings.classList.remove("hidden");
  }
}

function hideSettingsPanel() {
  const sidebarChat = document.getElementById("sidebar-chat-view");
  const sidebarSettings = document.getElementById("sidebar-settings-view");
  if (sidebarChat && sidebarSettings) {
    sidebarChat.classList.remove("hidden");
    sidebarSettings.classList.add("hidden");
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
    const maxByViewport = Math.max(420, window.innerWidth - 420);
    return Math.min(Math.max(width, 280), Math.min(680, maxByViewport));
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

function showFingerprintModal() {
  if (!activeChatId) return;
  const chat = mockChats[activeChatId];
  if (!chat || !chat.isEncrypted) return;

  document.getElementById("fp-modal-name").textContent = chat.name;
  document.getElementById("fp-modal-key").textContent = chat.fingerprint || "N/A";
  
  const modal = document.getElementById("fingerprint-modal");
  if (modal) {
    modal.classList.remove("hidden");
    modal.classList.add("flex");
  }
}

function closeFingerprintModal() {
  const modal = document.getElementById("fingerprint-modal");
  if (modal) {
    modal.classList.remove("flex");
    modal.classList.add("hidden");
  }
}

function adjustTextareaHeight(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = textarea.scrollHeight + "px";
}

function toggleEmojiDropdown() {
  const picker = document.getElementById("emoji-picker-mock");
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
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  setupSidebarResizer();
  initializeE2EEngine();
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
    empty_item1: "Messages are ciphered locally using X25519 protocols.",
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
    empty_item1: "消息使用 X25519 协议在本地进行加密。",
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

  // Re-render sidebar previews and selected chat UI
  if (activeChatId && mockChats[activeChatId]) {
    const chat = mockChats[activeChatId];
    selectChat(activeChatId.toString());
    updateDetailsPanel(chat);
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
  toast.innerHTML = `<i data-lucide="info" class="w-4 h-4 text-brand-light dark:text-brand-dark"></i><span>${message}</span>`;
  
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
  
  const chat = mockChats[activeChatId];
  if (!chat) return;
  
  chat.isMuted = !chat.isMuted;
  localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
  
  // Sync mute state via simulated API or actual POST
  try {
    const response = await fetch(`/api/conversations/${activeChatId}/mute/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: JSON.stringify({ mute: chat.isMuted })
    });
    if (response.ok) {
      console.log('Mute status synced to backend');
    }
  } catch (err) {
    console.log('Mute status synced locally:', err);
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
    if (response.ok) {
      console.log('Report submitted to backend');
    }
  } catch (err) {
    console.log('Report submitted locally:', err);
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
  
  // API Call
  try {
    const response = await fetch(`/api/conversations/${chatIdToDelete}/hide/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || ''
      }
    });
    if (response.ok) {
      console.log('Conversation hidden in backend');
    }
  } catch (err) {
    console.log('Conversation hidden locally:', err);
  }
  
  // Local deletion
  delete mockChats[chatIdToDelete];
  localStorage.setItem('ichat_chats', JSON.stringify(mockChats));
  
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

// Helper function to extract cookies (e.g. csrftoken for Django)
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
