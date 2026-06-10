/**
 * iChat Pro — Conversation Actions (T12)
 *
 * Provides context-menu handlers for sidebar conversation items:
 * pin / unpin, mute / unmute, archive / unarchive, clear history,
 * delete (hide), mark read / unread.
 *
 * Depends on: window.ContextMenu, global conversations[], conversationsById{},
 *              fetchConversations(), renderChatList(), selectChat(), showToast(),
 *              currentLanguage, getCookie()
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
    var headers = {
      'Content-Type': 'application/json',
    };
    var token = getCookie('csrftoken');
    if (token) {
      headers['X-CSRFToken'] = token;
    }
    return headers;
  }

  // --- API calls ---

  async function apiPost(url, body) {
    var resp = await fetch(url, {
      method: 'POST',
      headers: csrfHeaders(),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!resp.ok) {
      var errText = '';
      try { errText = await resp.text(); } catch (_) {}
      throw new Error('API error ' + resp.status + ': ' + errText);
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
      throw new Error('API error ' + resp.status + ': ' + errText);
    }
    return resp.json().catch(function () { return null; });
  }

  // --- Actions ---

  function pinConversation(conv) {
    var isPinned = conv.is_pinned;
    var url = '/api/conversations/' + conv.id + '/pin/';
    var promise = isPinned ? apiDelete(url) : apiPost(url);
    promise
      .then(function () {
        window.showToast(isPinned ? t('convUnpinned', 'Unpinned') : t('convPinned', 'Pinned'));
        if (typeof window.fetchConversations === 'function') {
          window.fetchConversations();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function muteConversation(conv, durationMinutes) {
    var url = '/api/conversations/' + conv.id + '/mute/';
    apiPost(url, { duration_minutes: durationMinutes })
      .then(function () {
        conv.muted_until = new Date(Date.now() + durationMinutes * 60000).toISOString();
        window.showToast(t('convMuted', 'Muted'));
        if (typeof window.renderChatList === 'function') {
          window.renderChatList();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function unmuteConversation(conv) {
    var url = '/api/conversations/' + conv.id + '/mute/';
    apiDelete(url)
      .then(function () {
        conv.muted_until = null;
        window.showToast(t('convUnmuted', 'Unmuted'));
        if (typeof window.renderChatList === 'function') {
          window.renderChatList();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function archiveConversation(conv) {
    var url = '/api/conversations/' + conv.id + '/archive/';
    apiPost(url)
      .then(function () {
        conv.archived_at = new Date().toISOString();
        window.showToast(t('convArchived', 'Archived'));
        if (typeof window.fetchConversations === 'function') {
          window.fetchConversations();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function unarchiveConversation(conv) {
    var url = '/api/conversations/' + conv.id + '/unarchive/';
    apiPost(url)
      .then(function () {
        conv.archived_at = null;
        window.showToast(t('convUnarchived', 'Unarchived'));
        if (typeof window.fetchConversations === 'function') {
          window.fetchConversations();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function clearConversation(conv) {
    var confirmed = window.confirm(t('convClearConfirm', 'Clear all messages in this chat?'));
    if (!confirmed) return;
    var url = '/api/conversations/' + conv.id + '/clear/';
    apiPost(url)
      .then(function () {
        conv.cleared_at = new Date().toISOString();
        window.showToast(t('convCleared', 'History cleared'));
        // If this is the active chat, clear messages
        if (typeof window.activeChatId !== 'undefined' && window.activeChatId === conv.id) {
          if (typeof window.messages !== 'undefined') window.messages = [];
          if (typeof window.renderMessages === 'function') window.renderMessages();
        }
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function deleteConversation(conv) {
    var confirmed = window.confirm(t('convDeleteConfirm', 'Delete this conversation?'));
    if (!confirmed) return;
    var url = '/api/conversations/' + conv.id + '/';
    apiDelete(url)
      .then(function () {
        // Remove from local state
        if (typeof window.conversations !== 'undefined') {
          window.conversations = window.conversations.filter(function (c) { return c.id !== conv.id; });
          window.conversationsById = {};
          window.conversations.forEach(function (c) { window.conversationsById[c.id] = c; });
        }
        if (typeof window.renderChatList === 'function') window.renderChatList();
        // Reset active chat if needed
        if (typeof window.activeChatId !== 'undefined' && window.activeChatId === conv.id) {
          window.activeChatId = null;
          var emptyState = document.getElementById('empty-state-window');
          var activeWindow = document.getElementById('active-chat-window');
          if (emptyState) emptyState.classList.remove('hidden');
          if (activeWindow) activeWindow.classList.add('hidden');
        }
        window.showToast(t('convDeleted', 'Conversation deleted'));
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function markRead(conv) {
    var url = '/api/conversations/' + conv.id + '/read/';
    apiPost(url)
      .then(function () {
        conv.unread = 0;
        var badge = document.getElementById('unread-badge-' + conv.id);
        if (badge) { badge.classList.add('hidden'); badge.textContent = '0'; }
        window.showToast(t('convMarkedRead', 'Marked as read'));
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  function markUnread(conv) {
    var url = '/api/conversations/' + conv.id + '/unread/';
    apiPost(url, { unread_count: 1 })
      .then(function () {
        conv.unread = 1;
        var badge = document.getElementById('unread-badge-' + conv.id);
        if (badge) { badge.textContent = '1'; badge.classList.remove('hidden'); }
        window.showToast(t('convMarkedUnread', 'Marked as unread'));
      })
      .catch(function () {
        window.showToast(t('convActionFailed', 'Action failed'));
      });
  }

  // --- Context menu builder ---

  /**
   * Show the conversation context menu at (x, y) viewport coordinates.
   * Called from chat.js on right‑click of a .chat-item-btn.
   *
   * @param {MouseEvent|Touch} e  — event (or object with clientX / clientY)
   * @param {object}            conv — conversation object from conversations[]
   */
  function showConversationMenu(e, conv) {
    if (!conv || !window.ContextMenu) return;

    var x = e.clientX || 0;
    var y = e.clientY || 0;

    var hasUnread = (conv.unread || 0) > 0;
    var isPinned = conv.is_pinned;
    var isMuted = conv.muted_until && new Date(conv.muted_until) > new Date();
    var isArchived = conv.archived_at != null;

    var items = [];

    // Mark Read / Mark Unread
    if (hasUnread) {
      items.push({
        icon: 'check-check',
        label: t('convMarkRead', 'Mark as Read'),
        onClick: function () { markRead(conv); },
      });
    } else {
      items.push({
        icon: 'message-circle',
        label: t('convMarkUnread', 'Mark as Unread'),
        onClick: function () { markUnread(conv); },
      });
    }

    items.push({ divider: true });

    // Pin / Unpin
    if (isPinned) {
      items.push({
        icon: 'pin-off',
        label: t('convUnpin', 'Unpin'),
        onClick: function () { pinConversation(conv); },
      });
    } else {
      items.push({
        icon: 'pin',
        label: t('convPin', 'Pin'),
        onClick: function () { pinConversation(conv); },
      });
    }

    // Mute sub-menu: we use a sub-label and handle it in the click
    if (isMuted) {
      items.push({
        icon: 'bell',
        label: t('convUnmute', 'Unmute'),
        onClick: function () { unmuteConversation(conv); },
      });
    } else {
      // Mute with sub-durations — show as separate items for simplicity
      items.push({
        icon: 'bell-off',
        label: t('convMute1h', 'Mute for 1 hour'),
        onClick: function () { muteConversation(conv, 60); },
      });
      items.push({
        icon: 'clock',
        label: t('convMute8h', 'Mute for 8 hours'),
        onClick: function () { muteConversation(conv, 480); },
      });
      items.push({
        icon: 'clock',
        label: t('convMute24h', 'Mute for 24 hours'),
        onClick: function () { muteConversation(conv, 1440); },
      });
      items.push({
        icon: 'infinity',
        label: t('convMuteForever', 'Mute forever'),
        onClick: function () { muteConversation(conv, 10080); },
      });
    }

    // Archive / Unarchive
    items.push({ divider: true });
    if (isArchived) {
      items.push({
        icon: 'archive-restore',
        label: t('convUnarchive', 'Unarchive'),
        onClick: function () { unarchiveConversation(conv); },
      });
    } else {
      items.push({
        icon: 'archive',
        label: t('convArchive', 'Archive'),
        onClick: function () { archiveConversation(conv); },
      });
    }

    // Clear & Delete
    items.push({ divider: true });
    items.push({
      icon: 'x-circle',
      label: t('convClear', 'Clear History'),
      onClick: function () { clearConversation(conv); },
    });
    items.push({
      icon: 'trash-2',
      label: t('convDelete', 'Delete Chat'),
      danger: true,
      onClick: function () { deleteConversation(conv); },
    });

    window.ContextMenu.show(x, y, items);
  }

  /**
   * Re-render status icons for a single conversation item in the sidebar.
   * Called after pin/mute/archive state changes.
   */
  function updateConversationStatusIcons(conv) {
    var wrapper = document.getElementById('chat-item-wrapper-' + conv.id);
    if (!wrapper) return;

    var statusContainer = wrapper.querySelector('.conv-status-icons');
    if (!statusContainer) {
      // Create the container if it doesn't exist
      var nameEl = wrapper.querySelector('.chat-item-btn h3 span');
      if (!nameEl) return;
      statusContainer = document.createElement('span');
      statusContainer.className = 'conv-status-icons';
      nameEl.parentNode.appendChild(statusContainer);
    }

    var html = '';
    if (conv.is_pinned) {
      html += '<i data-lucide="pin" class="conv-icon-pin"></i>';
    }
    var isMuted = conv.muted_until && new Date(conv.muted_until) > new Date();
    if (isMuted) {
      html += '<i data-lucide="bell-off" class="conv-icon-muted"></i>';
    }

    statusContainer.innerHTML = html;
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons({ nodes: statusContainer.querySelectorAll('[data-lucide]') });
    }
  }

  /**
   * Refresh all conversation status icons. Call after fetchConversations().
   */
  function refreshAllStatusIcons() {
    if (typeof window.conversations === 'undefined') return;
    window.conversations.forEach(function (conv) {
      updateConversationStatusIcons(conv);
    });
  }

  // --- Expose to global scope ---
  window.ConversationActions = {
    showMenu: showConversationMenu,
    updateStatusIcons: updateConversationStatusIcons,
    refreshAllStatusIcons: refreshAllStatusIcons,
    pin: pinConversation,
    mute: muteConversation,
    unmute: unmuteConversation,
    archive: archiveConversation,
    unarchive: unarchiveConversation,
    clear: clearConversation,
    delete: deleteConversation,
    markRead: markRead,
    markUnread: markUnread,
  };
})();
