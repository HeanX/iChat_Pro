/**
 * iChat Pro — Message Actions (T13)
 *
 * Provides context-menu handlers for message bubbles:
 * copy, reply, forward, delete (local), recall (within 30 min), resend (failed),
 * and select mode integration.
 *
 * Depends on: window.ContextMenu, global messages[], conversationsById{},
 *              activeChatId, myUserId, showToast(), currentLanguage, getCookie(),
 *              sendMessage(), renderMessages(), scrollToBottom()
 */

(function () {
  'use strict';

  // --- Helpers ---

  function getCookie(name) {
    var value = '; ' + document.cookie;
    var parts = value.split('; ' + name + '=');
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
  }

  function t(key, fallback) {
    if (typeof window.translations !== 'undefined' &&
        window.translations[currentLanguage] &&
        window.translations[currentLanguage][key]) {
      return window.translations[currentLanguage][key];
    }
    return fallback || key;
  }

  function csrfHeaders() {
    var headers = { 'Content-Type': 'application/json' };
    var token = getCookie('csrftoken');
    if (token) headers['X-CSRFToken'] = token;
    return headers;
  }

  async function apiPost(url, body) {
    var resp = await fetch(url, {
      method: 'POST',
      headers: csrfHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
      var errText = '';
      try { errText = await resp.text(); } catch (_) {}
      throw new Error('API error: ' + resp.status);
    }
    return resp.json().catch(function () { return null; });
  }

  async function apiDelete(url) {
    var resp = await fetch(url, {
      method: 'DELETE',
      headers: csrfHeaders(),
    });
    if (!resp.ok) {
      var errText = '';
      try { errText = await resp.text(); } catch (_) {}
      throw new Error('API error: ' + resp.status);
    }
    return resp.json().catch(function () { return null; });
  }

  // --- Actions ---

  function copyMessageText(msg) {
    var text = msg.text || '';
    if (!text) {
      window.showToast(t('msgNothingToCopy', 'Nothing to copy'));
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        window.showToast(t('msgCopied', 'Copied'));
      }).catch(function () {
        // Fallback for older browsers
        fallbackCopy(text);
      });
    } else {
      fallbackCopy(text);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      window.showToast(t('msgCopied', 'Copied'));
    } catch (e) {
      window.showToast(t('msgCopyFailed', 'Copy failed'));
    }
    document.body.removeChild(ta);
  }

  function replyToMessage(msg, conv) {
    var senderName = msg.isSelf
      ? (currentLanguage === 'zh' ? '你' : 'You')
      : (msg.sender_name || conv.name || 'Unknown');
    var preview = typeof window.getMessageReplyPreviewText === 'function'
      ? window.getMessageReplyPreviewText(msg)
      : (msg.text || '');
    if (preview.length > 80) preview = preview.substring(0, 80) + '...';

    window.replyToMessage = {
      id: msg.id,
      sender_name: senderName,
      text_preview: preview,
    };

    // Update the reply banner
    renderReplyBanner();

    // Focus the textarea
    var textarea = document.getElementById('chat-input-textarea');
    if (textarea) textarea.focus();
  }

  function renderReplyBanner() {
    var banner = document.getElementById('reply-quote-banner');
    if (!window.replyToMessage) {
      if (banner) banner.style.display = 'none';
      return;
    }

    if (!banner) {
      // Create the banner dynamically
      banner = document.createElement('div');
      banner.id = 'reply-quote-banner';
      banner.className = 'reply-quote-banner';
      banner.innerHTML =
        '<div class="reply-quote-preview">' +
        '<span class="reply-quote-sender"></span>' +
        '<span class="reply-quote-text"></span>' +
        '</div>' +
        '<button class="reply-quote-close" title="' + t('msgCancelReply', 'Cancel reply') + '">' +
        '<i data-lucide="x" class="w-4 h-4"></i>' +
        '</button>';

      // Insert before the chat-input-normal-wrapper
      var normalWrapper = document.getElementById('chat-input-normal-wrapper');
      if (normalWrapper && normalWrapper.parentNode) {
        normalWrapper.parentNode.insertBefore(banner, normalWrapper);
      }

      // Wire close button
      var closeBtn = banner.querySelector('.reply-quote-close');
      if (closeBtn) {
        closeBtn.addEventListener('click', function (e) {
          e.preventDefault();
          cancelReply();
        });
      }

      // Wire click-to-scroll
      banner.addEventListener('click', function (e) {
        if (e.target.closest('.reply-quote-close')) return;
        if (window.replyToMessage && window.replyToMessage.id) {
          scrollToMessage(window.replyToMessage.id);
        }
      });
    }

    var r = window.replyToMessage;
    var senderEl = banner.querySelector('.reply-quote-sender');
    var textEl = banner.querySelector('.reply-quote-text');
    if (senderEl) senderEl.textContent = r.sender_name + ': ';
    if (textEl) textEl.textContent = r.text_preview;
    banner.style.display = 'flex';

    // Re-render icons
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons({ nodes: banner.querySelectorAll('[data-lucide]') });
    }
  }

  function cancelReply() {
    window.replyToMessage = null;
    renderReplyBanner();
  }

  function scrollToMessage(msgId) {
    var bubble = document.querySelector('.message-bubble-custom[data-message-id="' + msgId + '"]');
    if (!bubble) {
      bubble = document.querySelector('[data-message-id="' + msgId + '"]');
    }
    if (bubble) {
      bubble.scrollIntoView({ behavior: 'smooth', block: 'center' });
      bubble.style.boxShadow = '0 0 0 2px var(--color-primary)';
      setTimeout(function () { bubble.style.boxShadow = ''; }, 2000);
    }
  }

  function forwardMessage(msg) {
    var isBatchForward = Array.isArray(msg);
    var forwardItems = isBatchForward ? msg.filter(Boolean) : [msg];
    if (!forwardItems.length) {
      window.showToast(t('msgNothingToCopy', 'Nothing to forward'));
      return;
    }
    var convs = window.conversations || [];
    var allowSameConversation = getForwardAllowSameConversation();
    var targets = getForwardTargets(convs, allowSameConversation);

    // Build a simple picker modal
    var modal = document.getElementById('forward-picker-modal');
    if (!modal) {
      modal = createForwardModal();
    }

    var listEl = modal.querySelector('.forward-picker-list');
    var searchEl = modal.querySelector('.forward-picker-search');
    var emptyEl = modal.querySelector('.forward-picker-empty');
    var sameConvEl = modal.querySelector('.forward-picker-same-conversation');

    function selectTarget(target) {
      return function () {
        modal.classList.remove('flex');
        modal.classList.add('hidden');
        if (isBatchForward) {
          doForwardMany(forwardItems, target);
        } else {
          doForward(forwardItems[0], target);
        }
      };
    }

    function renderTargets(items, searchResults) {
      listEl.innerHTML = '';
      var allItems = (items || []).slice();
      (searchResults || []).forEach(function (user) {
        var existingConv = convs.find(function (c) {
          return c.type === 'single' && String(c.peer_id) === String(user.id);
        });
        if (existingConv && (allowSameConversation || String(existingConv.id) !== String(window.activeChatId))) {
          if (!allItems.some(function (c) { return String(c.id) === String(existingConv.id); })) {
            allItems.push(normalizeForwardTargetAvatar(Object.assign({}, existingConv, {
              avatar_url: existingConv.avatar_url || user.avatar_url || '',
              avatar_color: existingConv.avatar_color || user.avatar_color || '#5c6bc0',
            })));
          }
          return;
        }
        allItems.push(normalizeForwardTargetAvatar({
          type: 'single',
          is_user_target: true,
          peer_id: user.id,
          name: user.nickname || user.username || 'Unknown',
          username: user.username,
          peer_user_type: user.user_type || 'user',
          initials: (user.nickname || user.username || '?').slice(0, 2).toUpperCase(),
          avatar_url: firstNonEmpty(user.avatar_url, user.peer_avatar_url, user.profile_avatar_url),
          avatar_color: user.avatar_color || '#5c6bc0',
        }));
      });

      if (!allItems.length) {
        if (emptyEl) emptyEl.classList.remove('hidden');
        return;
      }
      if (emptyEl) emptyEl.classList.add('hidden');

      allItems.map(normalizeForwardTargetAvatar).forEach(function (target) {
        var item = document.createElement('button');
        var color = /^#[0-9a-fA-F]{6}$/.test(target.avatar_color || '') ? target.avatar_color : '#5c6bc0';
        item.className = 'flex items-center gap-3 w-full px-3 py-2.5 rounded-lg hover:bg-bgSearch transition-colors text-left border-none bg-transparent cursor-pointer';
        var isAiTarget = Boolean(target.is_ai_assistant);
        var avatarClass = 'w-9 h-9 rounded-full overflow-hidden text-white flex items-center justify-center font-bold text-xs flex-shrink-0';
        var avatarStyle = 'background-color:' + color;
        var avatarInner = '';
        if (target.avatar_url) {
          if (isAiTarget) {
            avatarClass += ' ai-model-avatar';
            avatarStyle = 'background-color:#ffffff';
            avatarInner = '<img src="' + escapeForwardText(target.avatar_url) + '" class="ai-model-logo-img" alt="">';
          } else {
            avatarStyle = 'background-color:transparent';
            avatarInner = '<img src="' + escapeForwardText(target.avatar_url) + '" class="w-full h-full object-cover rounded-full" alt="">';
          }
        } else {
          avatarInner = escapeForwardText(target.initials || '??');
        }
        var label = isAiTarget
          ? (t('assistantLabel', 'Assistant') || 'Assistant')
          : target.type === 'group'
          ? t('groupChat', 'Group')
          : (target.peer_user_type === 'bot'
            ? (t('botLabel', 'Bot') || 'Bot')
            : (target.peer_user_type === 'agent' ? (t('agentLabel', 'Agent') || 'Agent') : t('privateChat', 'Private')));
        item.innerHTML =
          '<div class="' + avatarClass + '" style="' + avatarStyle + '">' +
          avatarInner +
          '</div>' +
          '<div class="min-w-0 flex-1">' +
          '<span class="block text-sm font-medium text-textMain truncate">' + escapeForwardText(target.name || 'Unknown') + '</span>' +
          '<span class="block text-xs text-textSecondary">' + escapeForwardText(label) + '</span>' +
          '</div>';
        item.addEventListener('click', function () {
          if (target.is_user_target) {
            createForwardConversation(target).then(function (conv) {
              modal.classList.remove('flex');
              modal.classList.add('hidden');
              if (isBatchForward) {
                return doForwardMany(forwardItems, conv);
              }
              return doForward(forwardItems[0], conv);
            }).catch(function (err) {
              console.error('Create forward conversation failed:', err);
              window.showToast(t('msgForwardFailed', 'Forward failed'));
            });
            return;
          }
          selectTarget(target)();
        });
        listEl.appendChild(item);
      });
    }

    renderTargets(targets, []);
    if (sameConvEl) {
      sameConvEl.checked = allowSameConversation;
      sameConvEl.onchange = function () {
        allowSameConversation = sameConvEl.checked;
        setForwardAllowSameConversation(allowSameConversation);
        targets = getForwardTargets(convs, allowSameConversation);
        if (searchEl && searchEl.value.trim().length >= 2) {
          searchEl.dispatchEvent(new Event('input', { bubbles: true }));
        } else {
          renderTargets(targets, []);
        }
      };
    }
    if (searchEl) {
      searchEl.value = '';
      searchEl.oninput = debounceForwardSearch(function () {
        var query = searchEl.value.trim();
        if (query.length < 2) {
          renderTargets(targets, []);
          return;
        }
        searchForwardTargets(query).then(function (users) {
          renderTargets(targets, users);
        }).catch(function (err) {
          console.error('Forward target search failed:', err);
          renderTargets(targets, []);
        });
      }, 250);
    }

    modal.classList.remove('hidden');
    modal.classList.add('flex');
    if (!targets.length && searchEl) {
      setTimeout(function () { searchEl.focus(); }, 30);
    }
  }

  function createForwardModal() {
    var modal = document.createElement('div');
    modal.id = 'forward-picker-modal';
    modal.className = 'hidden fixed inset-0 bg-black/55 backdrop-blur-[2px] z-[200] items-center justify-center p-4';
    modal.innerHTML =
      '<div class="bg-bgSidebar border border-borderColor rounded-custom-lg shadow-2xl w-full max-w-[380px] max-h-[60vh] flex flex-col">' +
      '<div class="flex items-center justify-between px-5 py-4 border-b border-borderColor">' +
      '<h3 class="text-base font-bold text-textMain">' + t('msgForwardTitle', 'Forward to...') + '</h3>' +
      '<button class="p-1.5 rounded-full hover:bg-bgSearch text-textSecondary transition-colors forward-picker-close">' +
      '<i data-lucide="x" class="w-5 h-5"></i>' +
      '</button>' +
      '</div>' +
      '<div class="px-3 py-2 border-b border-borderColor">' +
      '<input type="search" class="forward-picker-search w-full bg-bgSearch text-textMain text-sm rounded-lg px-3 py-2 outline-none" placeholder="' + escapeForwardText(t('searchUsers', 'Search users or bots...')) + '">' +
      '<label class="mt-2 flex items-center gap-2 text-xs text-textSecondary cursor-pointer select-none">' +
      '<input type="checkbox" class="forward-picker-same-conversation accent-brand-light">' +
      '<span>' + escapeForwardText(forwardSameConversationLabel()) + '</span>' +
      '</label>' +
      '</div>' +
      '<div class="forward-picker-empty hidden px-5 py-8 text-center text-sm text-textSecondary">' + escapeForwardText(t('msgNoForwardTargets', 'No conversations to forward to')) + '</div>' +
      '<div class="flex-1 overflow-y-auto p-2 forward-picker-list"></div>' +
      '</div>';
    document.body.appendChild(modal);

    // Wire close
    modal.addEventListener('click', function (e) {
      if (e.target === modal || e.target.closest('.forward-picker-close')) {
        modal.classList.remove('flex');
        modal.classList.add('hidden');
      }
    });

    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons({ nodes: modal.querySelectorAll('[data-lucide]') });
    }

    return modal;
  }

  function getForwardTargets(convs, allowSameConversation) {
    var targets = (convs || []).filter(function (c) {
      if (!(c.type === 'single' || c.type === 'group')) return false;
      return allowSameConversation || String(c.id) !== String(window.activeChatId);
    }).map(normalizeForwardTargetAvatar);
    if (typeof window.getAiAssistantForwardTargets === 'function') {
      window.getAiAssistantForwardTargets().forEach(function(aiTarget) {
        if (!targets.some(function(c) { return String(c.id) === String(aiTarget.id); })) {
          targets.unshift(aiTarget);
        }
      });
    }
    return targets;
  }

  function firstNonEmpty() {
    for (var i = 0; i < arguments.length; i++) {
      if (arguments[i]) return arguments[i];
    }
    return '';
  }

  function findConversationForForwardTarget(target) {
    if (!target) return null;
    if (target.id && window.conversationsById && window.conversationsById[target.id]) {
      return window.conversationsById[target.id];
    }
    var convs = window.conversations || [];
    if (target.peer_id) {
      var byPeer = convs.find(function (c) {
        return c.type === 'single' && String(c.peer_id) === String(target.peer_id);
      });
      if (byPeer) return byPeer;
    }
    if (target.username) {
      var byUsername = convs.find(function (c) {
        return c.type === 'single' && String(c.peer_username || '') === String(target.username);
      });
      if (byUsername) return byUsername;
    }
    return null;
  }

  function normalizeForwardTargetAvatar(target) {
    if (!target) return target;
    var conv = findConversationForForwardTarget(target);
    var avatarUrl = firstNonEmpty(
      target.avatar_url,
      target.peer_avatar_url,
      target.profile_avatar_url,
      target.sender_avatar_url,
      conv && conv.avatar_url
    );
    var avatarColor = firstNonEmpty(
      target.avatar_color,
      target.peer_avatar_color,
      conv && conv.avatar_color,
      '#5c6bc0'
    );
    if (avatarUrl === target.avatar_url && avatarColor === target.avatar_color) {
      return target;
    }
    return Object.assign({}, target, {
      avatar_url: avatarUrl,
      avatar_color: avatarColor,
    });
  }

  function getForwardAllowSameConversation() {
    try {
      return localStorage.getItem('ichat.forward.allowSameConversation') === 'true';
    } catch (e) {
      return false;
    }
  }

  function setForwardAllowSameConversation(value) {
    try {
      localStorage.setItem('ichat.forward.allowSameConversation', value ? 'true' : 'false');
    } catch (e) {
      // Ignore storage errors; the current modal state still works.
    }
  }

  function forwardSameConversationLabel() {
    if (window.currentLanguage === 'zh') return '允许转发到当前对话';
    if (window.currentLanguage === 'zh-hant') return '允許轉寄到目前對話';
    if (window.currentLanguage === 'ja') return '現在のチャットへの転送を許可';
    return 'Allow forwarding to this chat';
  }

  function escapeForwardText(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function debounceForwardSearch(fn, delay) {
    var timer = null;
    return function () {
      clearTimeout(timer);
      timer = setTimeout(fn, delay);
    };
  }

  async function searchForwardTargets(query) {
    var data = await apiFetch('/contacts/search/?q=' + encodeURIComponent(query));
    var activePeerId = null;
    var activeConv = window.conversationsById && window.activeChatId
      ? window.conversationsById[window.activeChatId]
      : null;
    if (activeConv && activeConv.type === 'single') {
      activePeerId = activeConv.peer_id;
    }
    return (data.results || []).filter(function (user) {
      return String(user.id) !== String(window.myUserId)
        && String(user.id) !== String(activePeerId)
        && (user.is_contact || user.user_type === 'bot' || user.user_type === 'agent');
    });
  }

  async function createForwardConversation(target) {
    var data = await apiFetch('/api/conversations/create/', {
      method: 'POST',
      body: JSON.stringify({ peer_id: target.peer_id || target.id })
    });
    if (typeof fetchConversations === 'function') {
      await fetchConversations();
    }
    var conv = window.conversationsById && window.conversationsById[data.conversation_id];
    if (!conv && window.conversations) {
      conv = window.conversations.find(function (c) {
        return String(c.id) === String(data.conversation_id);
      });
    }
    if (!conv) {
      throw new Error('Forward conversation was created but not loaded.');
    }
    return conv;
  }

  async function doForwardMany(items, targetConv) {
    if (targetConv && targetConv.is_ai_assistant) {
      try {
        if (typeof window.forwardMessagesToAiAssistant !== 'function') {
          throw new Error('AI forward handler is not available');
        }
        await window.forwardMessagesToAiAssistant(items, targetConv.id);
        if (typeof window.exitSelectMode === 'function') {
          window.exitSelectMode();
        }
        window.showToast(t('msgForwarded', 'Forwarded'));
      } catch (e) {
        console.error('Forward selected messages to AI failed:', e);
        window.showToast(t('msgForwardFailed', 'Forward failed'));
      }
      return;
    }

    var ok = 0;
    var failed = 0;
    for (var i = 0; i < items.length; i++) {
      try {
        await doForward(items[i], targetConv, { silent: true });
        ok += 1;
      } catch (e) {
        failed += 1;
        console.error('Forward selected message failed:', e);
      }
    }
    if (typeof fetchConversations === 'function') {
      await fetchConversations();
    }
    if (typeof window.exitSelectMode === 'function') {
      window.exitSelectMode();
    }
    if (failed) {
      window.showToast(t('msgForwardFailed', 'Forward failed') + ' (' + ok + '/' + items.length + ')');
    } else {
      window.showToast(t('msgForwarded', 'Forwarded'));
    }
  }

  async function doForward(msg, targetConv, options) {
    options = options || {};
    if (targetConv && targetConv.is_ai_assistant) {
      try {
        if (typeof window.forwardMessagesToAiAssistant !== 'function') {
          throw new Error('AI forward handler is not available');
        }
        await window.forwardMessagesToAiAssistant([msg], targetConv.id);
        if (!options.silent) {
          window.showToast(t('msgForwarded', 'Forwarded'));
        }
        return true;
      } catch (e) {
        console.error('Forward to AI failed:', e);
        if (options.silent) throw e;
        window.showToast(t('msgForwardFailed', 'Forward failed'));
        return false;
      }
    }

    var convId = window.activeChatId;
    var plaintext = msg.text || '';
    var sourceFileId = msg.file_id || (msg.file && msg.file.file_id) || null;
    var sourceConv = (window.conversationsById && window.conversationsById[convId]) || null;
    var isFileForward = Boolean(sourceFileId);
    if (!plaintext && !isFileForward) {
      window.showToast(t('msgNothingToCopy', 'Nothing to forward'));
      return;
    }
    if (msg.isAiAssistant && msg.aiRole !== 'user' && plaintext && !/^\/md(?:[ \t]+|\r?\n|$)/i.test(plaintext)) {
      plaintext = '/md\n' + plaintext;
    }

    try {
      var clientMsgId = 'fwd-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      var payload = {
        original_message_id: msg.isAiAssistant ? null : msg.id,
        original_conversation_id: msg.isAiAssistant ? null : convId,
        client_message_id: clientMsgId,
        message_type: isFileForward ? (msg.message_type || (msg.file && msg.file.message_kind) || 'file') : 'text',
      };
      if (isFileForward) {
        payload.file_id = sourceFileId;
        payload.file_keys = [];
        if (!window.iChatFileTransfer || !window.iChatFileTransfer.fetchFileKeyBytes) {
          throw new Error('File transfer module not loaded');
        }
        var keyResult = await window.iChatFileTransfer.fetchFileKeyBytes(
          sourceFileId,
          sourceConv && sourceConv.type === 'group' ? 'group' : 'single'
        );
        var fileKeyBytes = keyResult.fileKeyBytes;
        var targetE2EE = targetConv.type === 'group' ? window.iChatGroupE2EE : window.iChatPrivateE2EE;
        if (!targetE2EE || typeof targetE2EE.wrapFileKey !== 'function') {
          throw new Error('File key wrapping module not loaded');
        }
        var selfMetadata = targetConv.type === 'group'
          ? {
              group_id: targetConv.id,
              membership_version: targetConv.membership_version || 1,
              sender_id: window.myUserId,
              receiver_id: window.myUserId,
              sender_key_version: window.localKeyVersion || 1,
              receiver_key_version: 0,
            }
          : {
              conversation_id: targetConv.id,
              sender_id: window.myUserId,
              receiver_id: window.myUserId,
              sender_key_version: window.localKeyVersion || 1,
              receiver_key_version: 0,
            };
        var selfWrapped = await targetE2EE.wrapFileKey(fileKeyBytes, sourceFileId, window.myUserId, selfMetadata);
        if (selfWrapped) payload.file_keys.push(selfWrapped);
      }

      if (targetConv.type === 'group') {
        // Group target: encrypt for each member
        if (!window.iChatGroupE2EE || !window.iChatGroupE2EE.encryptGroupMessage) {
          throw new Error('Group E2EE module not loaded');
        }
        var memberIds = typeof window.fetchGroupMemberIds === 'function'
          ? await window.fetchGroupMemberIds(targetConv.id)
          : [];
        var result = window.encryptGroupMessageWithTrustRetry
          ? await window.encryptGroupMessageWithTrustRetry({
              plaintext: plaintext,
              conv: targetConv,
              memberIds: memberIds,
            })
          : await window.iChatGroupE2EE.encryptGroupMessage({
              plaintext: plaintext,
              groupId: targetConv.id,
              membershipVersion: targetConv.membership_version || 1,
              memberIds: memberIds,
            });
        payload.algorithm = result.algorithm;
        payload.sender_key_version = result.sender_key_version;
        payload.membership_version = result.membership_version;
        if (isFileForward && typeof window.iChatGroupE2EE.wrapFileKey === 'function') {
          for (var i = 0; i < memberIds.length; i++) {
            var holderId = memberIds[i];
            if (Number(holderId) === Number(window.myUserId)) continue;
            payload.file_keys.push(await window.iChatGroupE2EE.wrapFileKey(
              fileKeyBytes,
              sourceFileId,
              holderId,
              {
                group_id: targetConv.id,
                membership_version: result.membership_version,
                sender_id: window.myUserId,
                receiver_id: holderId,
                sender_key_version: result.sender_key_version,
                receiver_key_version: 0,
              }
            ));
          }
        }
        // Enrich each recipient with fields the REST forward view expects:
        // receiver_id (not user_id), sender_key_version, algorithm
        payload.recipients = result.recipients.map(function(r) {
          return {
            receiver_id: r.user_id || r.receiver_id,
            ciphertext: r.ciphertext,
            nonce: r.nonce,
            auth_tag: r.auth_tag,
            sender_ephemeral_public_key: r.sender_ephemeral_public_key,
            algorithm: result.algorithm,
            sender_key_version: result.sender_key_version,
            receiver_key_version: r.receiver_key_version,
          };
        });
      } else {
        // Private chat target: encrypt for the single peer
        if (!window.iChatPrivateE2EE || !window.iChatPrivateE2EE.encryptPrivateMessage || !targetConv.peer_id) {
          throw new Error('Private E2EE module or peer info missing');
        }
        payload.peer_id = targetConv.peer_id;
        if (isFileForward && typeof window.iChatPrivateE2EE.wrapFileKey === 'function') {
          payload.file_keys.push(await window.iChatPrivateE2EE.wrapFileKey(
            fileKeyBytes,
            sourceFileId,
            targetConv.peer_id,
            {
              conversation_id: targetConv.id,
              sender_id: window.myUserId,
              receiver_id: targetConv.peer_id,
              sender_key_version: window.localKeyVersion || 1,
              receiver_key_version: 0,
            }
          ));
        }
        var encResult = await window.iChatPrivateE2EE.encryptPrivateMessage({
          plaintext: plaintext,
          conversationId: targetConv.id,
          receiverId: targetConv.peer_id,
        });
        payload.ciphertext = encResult.ciphertext;
        payload.nonce = encResult.nonce;
        payload.auth_tag = encResult.auth_tag;
        payload.sender_ephemeral_public_key = encResult.sender_ephemeral_public_key;
        payload.sender_copy = encResult.sender_copy;
        payload.algorithm = encResult.algorithm;
        payload.sender_key_version = encResult.sender_key_version;
        payload.receiver_key_version = encResult.receiver_key_version;
      }

      await apiPost('/api/conversations/' + targetConv.id + '/messages/forward/', payload);
      if (!options.silent && typeof fetchConversations === 'function') {
        await fetchConversations();
      }
      if (!options.silent) {
        window.showToast(t('msgForwarded', 'Forwarded'));
      }
      return true;
    } catch (e) {
      console.error('Forward failed:', e);
      if (options.silent) throw e;
      window.showToast(t('msgForwardFailed', 'Forward failed'));
      return false;
    }
  }

  function deleteMessage(msg) {
    if (msg && msg.isAiAssistant) {
      if (typeof window.deleteAiMessage === 'function') {
        window.deleteAiMessage(msg.id);
        window.showToast(t('msgDeletedToast', 'Message deleted'));
      }
      return;
    }

    var convId = window.activeChatId;
    if (!convId) return;

    apiDelete('/api/conversations/' + convId + '/messages/' + msg.id + '/')
      .then(function () {
        // Mark deleted locally
        msg.isDeleted = true;
        msg.text = t('msgDeleted', 'message deleted');
        msg.isSystem = true;
        if (typeof window.patchMessageRowInPlace === 'function') window.patchMessageRowInPlace(msg);
        window.showToast(t('msgDeletedToast', 'Message deleted'));
      })
      .catch(function () {
        window.showToast(t('msgActionFailed', 'Action failed'));
      });
  }

  function recallMessage(msg) {
    var convId = window.activeChatId;
    if (!convId) return;

    apiPost('/api/conversations/' + convId + '/messages/' + msg.id + '/recall/')
      .then(function () {
        msg.isRecalled = true;
        msg.text = msg.isSelf
          ? t('msgYouRecalled', 'You recalled a message')
          : t('msgRecalled', 'message recalled');
        msg.isSystem = true;
        if (typeof window.patchMessageRowInPlace === 'function') window.patchMessageRowInPlace(msg);
        window.showToast(t('msgRecalledToast', 'Message recalled'));
      })
      .catch(function () {
        window.showToast(t('msgRecallFailed', 'Recall failed'));
      });
  }

  function resendMessage(msg) {
    // Remove the failed message from the array and its DOM row
    var msgs = window.messages || [];
    var idx = msgs.indexOf(msg);
    if (idx >= 0) msgs.splice(idx, 1);

    var row = document.querySelector('.message-bubble-custom[data-message-id="' + msg.id + '"]');
    if (row) {
      var msgRow = row.closest('.message-row');
      if (msgRow) msgRow.remove();
    }

    // Fill textarea and send
    var textarea = document.getElementById('chat-input-textarea');
    if (textarea && msg.text) {
      textarea.value = msg.text;
      if (typeof window.adjustTextareaHeight === 'function') {
        window.adjustTextareaHeight(textarea);
      }
      if (typeof window.sendMessage === 'function') {
        window.sendMessage();
      }
    }
  }

  function selectMessage(msg) {
    // Enter select mode and toggle this message
    if (typeof window.isSelectingMessages !== 'undefined' && !window.isSelectingMessages) {
      if (typeof window.triggerSelectMessagesAction === 'function') {
        window.triggerSelectMessagesAction();
      }
    }
    if (typeof window.selectedMessageIds !== 'undefined' && typeof window.toggleMessageSelection === 'function') {
      window.toggleMessageSelection(msg.id);
    }
  }

  // --- Context menu builder ---

  /**
   * Show the message context menu at (x, y) viewport coordinates.
   * Called on right‑click or long‑press of a message bubble.
   *
   * @param {MouseEvent|Touch} e   — event (clientX / clientY)
   * @param {object}            msg  — message object from messages[]
   * @param {object}            conv — active conversation
   */
  function showMessageMenu(e, msg, conv) {
    if (!msg || !window.ContextMenu) return;

    var x = e.clientX || 0;
    var y = e.clientY || 0;

    var isSelf = msg.isSelf;
    var isSystem = msg.isSystem;
    var isRecalled = msg.isRecalled;
    var isFailed = msg.status === 'failed' || msg.client_status === 'failed';

    if (msg.isAiAssistant) {
      var aiItems = [
        {
          icon: 'copy',
          label: t('msgCopy', 'Copy'),
          onClick: function () { copyMessageText(msg); },
        },
        {
          icon: 'corner-up-right',
          label: t('msgForward', 'Forward'),
          onClick: function () { forwardMessage(msg); },
        },
        { divider: true },
        {
          icon: 'trash-2',
          label: t('msgDelete', 'Delete'),
          danger: true,
          onClick: function () { deleteMessage(msg); },
        },
      ];
      window.ContextMenu.show(x, y, aiItems);
      return;
    }

    // System / recalled messages have very limited menu
    if (isSystem || isRecalled) {
      if (!msg.text) return;
      window.ContextMenu.show(x, y, [
        {
          icon: 'copy',
          label: t('msgCopy', 'Copy'),
          onClick: function () { copyMessageText(msg); },
        },
      ]);
      return;
    }

    var items = [];

    // Copy
    items.push({
      icon: 'copy',
      label: t('msgCopy', 'Copy'),
      onClick: function () { copyMessageText(msg); },
    });

    // Reply
    items.push({
      icon: 'corner-up-left',
      label: t('msgReply', 'Reply'),
      onClick: function () { replyToMessage(msg, conv); },
    });

    // Forward
    items.push({
      icon: 'corner-up-right',
      label: t('msgForward', 'Forward'),
      onClick: function () { forwardMessage(msg); },
    });

    items.push({ divider: true });

    // Select
    items.push({
      icon: 'check-circle',
      label: t('msgSelect', 'Select'),
      onClick: function () { selectMessage(msg); },
    });

    items.push({ divider: true });

    // Delete (always available for own messages, local only)
    items.push({
      icon: 'trash-2',
      label: t('msgDelete', 'Delete'),
      danger: true,
      onClick: function () { deleteMessage(msg); },
    });

    // Recall (own messages within 30 minutes)
    if (isSelf && !isFailed) {
      var msgTime = msg.created_at ? new Date(msg.created_at).getTime() : 0;
      var now = Date.now();
      var within30min = !msg.created_at || (now - msgTime) < 30 * 60 * 1000;
      if (within30min) {
        items.push({
          icon: 'rotate-ccw',
          label: t('msgRecall', 'Recall'),
          danger: true,
          onClick: function () { recallMessage(msg); },
        });
      }
    }

    // Resend (failed messages only)
    if (isFailed && isSelf) {
      items.push({
        icon: 'refresh-cw',
        label: t('msgResend', 'Resend'),
        onClick: function () { resendMessage(msg); },
      });
    }

    window.ContextMenu.show(x, y, items);
  }

  // --- Long-press support (mobile) ---

  var longPressTimer = null;
  var longPressTarget = null;

  function initLongPress() {
    var container = document.getElementById('message-history-container');
    if (!container) return;

    container.addEventListener('touchstart', function (e) {
      var bubble = e.target.closest('.message-bubble-custom');
      if (!bubble) return;

      longPressTarget = bubble;
      longPressTimer = setTimeout(function () {
        var msgId = longPressTarget.getAttribute('data-message-id');
        if (!msgId) return;
        var msg = findMessageById(msgId);
        var conv = window.conversationsById && window.activeChatId
          ? window.conversationsById[window.activeChatId]
          : null;
        if (msg) {
          // Use last touch position
          var touch = e.touches[0] || e.changedTouches[0];
          showMessageMenu(
            { clientX: touch ? touch.clientX : 100, clientY: touch ? touch.clientY : 100 },
            msg,
            conv
          );
        }
        longPressTarget = null;
      }, 500);
    }, { passive: false });

    container.addEventListener('touchend', function () {
      clearTimeout(longPressTimer);
      longPressTarget = null;
    });

    container.addEventListener('touchmove', function () {
      clearTimeout(longPressTimer);
      longPressTarget = null;
    });
  }

  function findMessageById(msgId) {
    var msgs = window.messages || [];
    for (var i = 0; i < msgs.length; i++) {
      if (msgs[i].id === msgId || String(msgs[i].id) === String(msgId)) {
        return msgs[i];
      }
    }
    return null;
  }

  // --- Expose ---

  window.MessageActions = {
    showMenu: showMessageMenu,
    copyText: copyMessageText,
    reply: replyToMessage,
    forward: forwardMessage,
    delete: deleteMessage,
    recall: recallMessage,
    resend: resendMessage,
    select: selectMessage,
    cancelReply: cancelReply,
    renderReplyBanner: renderReplyBanner,
  };

  // Initialize long-press when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLongPress);
  } else {
    initLongPress();
  }
})();
