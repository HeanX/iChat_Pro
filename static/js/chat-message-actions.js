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
    var preview = msg.text || '';
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
    // For now: show a contact picker by reusing existing contacts list
    var convs = window.conversations || [];
    var targets = convs.filter(function (c) {
      return c.type === 'single' && c.id !== window.activeChatId;
    });

    if (targets.length === 0) {
      window.showToast(t('msgNoForwardTargets', 'No conversations to forward to'));
      return;
    }

    // Build a simple picker modal
    var modal = document.getElementById('forward-picker-modal');
    if (!modal) {
      modal = createForwardModal();
    }

    var listEl = modal.querySelector('.forward-picker-list');
    listEl.innerHTML = '';
    targets.forEach(function (target) {
      var item = document.createElement('button');
      var color = /^#[0-9a-fA-F]{6}$/.test(target.avatar_color || '') ? target.avatar_color : '#5c6bc0';
      item.className = 'flex items-center gap-3 w-full px-3 py-2.5 rounded-lg hover:bg-bgSearch transition-colors text-left border-none bg-transparent cursor-pointer';
      item.innerHTML =
        '<div class="w-9 h-9 rounded-full text-white flex items-center justify-center font-bold text-xs flex-shrink-0" style="background-color:' + color + '">' +
        (target.initials || '??') +
        '</div>' +
        '<span class="text-sm font-medium text-textMain truncate">' + (target.name || 'Unknown') + '</span>';
      item.addEventListener('click', function () {
        modal.classList.remove('flex');
        modal.classList.add('hidden');
        doForward(msg, target);
      });
      listEl.appendChild(item);
    });

    modal.classList.remove('hidden');
    modal.classList.add('flex');
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

  async function doForward(msg, targetConv) {
    var convId = window.activeChatId;
    var plaintext = msg.text || '';
    if (!plaintext) {
      window.showToast(t('msgNothingToCopy', 'Nothing to forward'));
      return;
    }

    try {
      var clientMsgId = 'fwd-' + Date.now() + '-' + Math.random().toString(16).slice(2);
      var payload = {
        original_message_id: msg.id,
        original_conversation_id: convId,
        client_message_id: clientMsgId,
        message_type: 'text',
      };

      if (targetConv.type === 'group') {
        // Group target: encrypt for each member
        if (!window.iChatGroupE2EE || !window.iChatGroupE2EE.encryptGroupMessage) {
          throw new Error('Group E2EE module not loaded');
        }
        var memberIds = typeof window.fetchGroupMemberIds === 'function'
          ? await window.fetchGroupMemberIds(targetConv.id)
          : [];
        var result = await window.iChatGroupE2EE.encryptGroupMessage({
          plaintext: plaintext,
          groupId: targetConv.id,
          membershipVersion: targetConv.membership_version || 1,
          memberIds: memberIds,
        });
        payload.algorithm = result.algorithm;
        payload.sender_key_version = result.sender_key_version;
        payload.membership_version = result.membership_version;
        // Enrich each recipient with fields the REST forward view expects:
        // receiver_id (not user_id), sender_key_version, algorithm
        payload.recipients = result.recipients.map(function(r) {
          return {
            receiver_id: r.user_id || r.receiver_id,
            ciphertext: r.ciphertext,
            nonce: r.nonce,
            auth_tag: r.auth_tag,
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
        var encResult = await window.iChatPrivateE2EE.encryptPrivateMessage({
          plaintext: plaintext,
          conversationId: targetConv.id,
          receiverId: targetConv.peer_id,
        });
        payload.ciphertext = encResult.ciphertext;
        payload.nonce = encResult.nonce;
        payload.auth_tag = encResult.auth_tag;
        payload.algorithm = encResult.algorithm;
        payload.sender_key_version = encResult.sender_key_version;
        payload.receiver_key_version = encResult.receiver_key_version;
      }

      await apiPost('/api/conversations/' + targetConv.id + '/messages/forward/', payload);
      window.showToast(t('msgForwarded', 'Forwarded'));
    } catch (e) {
      console.error('Forward failed:', e);
      window.showToast(t('msgForwardFailed', 'Forward failed'));
    }
  }

  function deleteMessage(msg) {
    var convId = window.activeChatId;
    if (!convId) return;

    apiDelete('/api/conversations/' + convId + '/messages/' + msg.id + '/')
      .then(function () {
        // Mark deleted locally
        msg.isDeleted = true;
        msg.text = t('msgDeleted', 'message deleted');
        msg.isSystem = true;
        if (typeof window.renderMessages === 'function') window.renderMessages();
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
        if (typeof window.renderMessages === 'function') window.renderMessages();
        window.showToast(t('msgRecalledToast', 'Message recalled'));
      })
      .catch(function () {
        window.showToast(t('msgRecallFailed', 'Recall failed'));
      });
  }

  function resendMessage(msg) {
    // Remove the failed message and retry
    var msgs = window.messages || [];
    var idx = msgs.indexOf(msg);
    if (idx >= 0) msgs.splice(idx, 1);

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
    if (typeof window.renderMessages === 'function') window.renderMessages();
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
