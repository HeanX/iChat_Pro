/**
 * ChatNotifications — P4 T18 shared gating module for incoming-message
 * notifications (Issue #145).
 *
 * The renderer decrypts messages, so this module only decides WHETHER a
 * notification may be shown and WHAT it may contain:
 *   - per-user switches (display / private / group, content preview)
 *     loaded from /api/settings/notifications/ by the caller;
 *   - per-conversation mute (muted_until) from the conversation list;
 *   - focus: a visible, focused window already shows the message inline;
 *   - source: only realtime WebSocket pushes notify — sync catch-up after
 *     a reconnect only updates badges, so a burst of missed messages
 *     cannot flood the OS notification center.
 *
 * Plaintext never leaves this gating layer: the caller receives the
 * (possibly truncated) preview through its own `show` callback, which in
 * the desktop shell forwards it over the narrow iChatDesktop bridge.
 *
 * UMD: usable from the browser (window.ChatNotifications) and Node (tests).
 */
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.ChatNotifications = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Defaults mirror the server model (UserNotificationSettings): everything
  // on except channel notifications (not used here). Applied while the real
  // settings request is in flight or when it fails, so a transient API error
  // never silences notifications.
  var DEFAULT_SETTINGS = {
    display_notifications: true,
    private_chat_notifications: true,
    group_chat_notifications: true,
    message_preview_private: true,
    message_preview_group: true,
  };

  var TITLE_MAX = 80;
  var BODY_MAX = 160;

  function isConversationMuted(conv, nowMs) {
    if (!conv || !conv.muted_until) return false;
    var until = new Date(conv.muted_until);
    if (isNaN(until.getTime())) return false;
    return until.getTime() > (nowMs || Date.now());
  }

  function normalizeSettings(raw) {
    var merged = Object.assign({}, DEFAULT_SETTINGS);
    if (raw && typeof raw === "object") {
      for (var key in DEFAULT_SETTINGS) {
        if (typeof raw[key] === "boolean") merged[key] = raw[key];
      }
    }
    return merged;
  }

  function clampText(value, max) {
    if (typeof value !== "string") return "";
    var text = value.replace(/\s+/g, " ").trim();
    if (text.length > max) text = text.slice(0, max - 1) + "…";
    return text;
  }

  /**
   * @param {Object} options
   * @param {Function} options.getSettings          () -> settings object or null
   * @param {Function} options.getConversation      (conversationId) -> conv|null
   * @param {Function} options.isAppUnfocused       () -> boolean; true when the
   *        window is hidden/minimized OR visible but not focused
   * @param {Function} options.show                 (payload) -> void; delivers
   *        { conversationId, conversationType, title, body }
   * @param {Function} options.translate            (en, zh, zhTW, ja) -> string
   */
  function createNotificationCenter(options) {
    var getSettings = options.getSettings;
    var getConversation = options.getConversation;
    var isAppUnfocused = options.isAppUnfocused;
    var show = options.show;
    var translate = options.translate;

    function genericBody(convType) {
      return convType === "group"
        ? translate("New group message", "新群消息", "新群組訊息", "新しいグループメッセージ")
        : translate("New message", "新消息", "新訊息", "新しいメッセージ");
    }

    /**
     * @param {Object} meta
     * @param {number} meta.convId
     * @param {string} meta.convType          'single' | 'group'
     * @param {boolean} meta.isSelf
     * @param {boolean} meta.decryptError     plaintext is unusable
     * @param {boolean} meta.isFileMsg
     * @param {string}  meta.text             decrypted plaintext (may be empty)
     * @param {string}  meta.senderName       best-effort display name
     * @param {string}  meta.source           'realtime' (default) | 'sync'
     */
    function notifyIncoming(meta) {
      if (!show) return false;
      if (!meta || meta.isSelf) return false;
      if (meta.source && meta.source !== "realtime") return false;
      if (!isAppUnfocused()) return false; // user is looking at the app

      var settings = normalizeSettings(getSettings ? getSettings() : null);
      if (!settings.display_notifications) return false;
      if (meta.convType === "group" && !settings.group_chat_notifications) return false;
      if (meta.convType !== "group" && !settings.private_chat_notifications) return false;

      var conv = getConversation ? getConversation(meta.convId) : null;
      if (isConversationMuted(conv)) return false;

      var title = clampText(meta.senderName || (conv && conv.name) || "iChat Pro", TITLE_MAX);
      if (meta.convType === "group" && conv && conv.name && meta.senderName) {
        title = clampText(conv.name + " · " + meta.senderName, TITLE_MAX);
      }

      var previewAllowed = meta.convType === "group"
        ? settings.message_preview_group
        : settings.message_preview_private;
      var body = (previewAllowed && !meta.decryptError && !meta.isFileMsg && meta.text)
        ? clampText(meta.text, BODY_MAX)
        : genericBody(meta.convType);

      show({
        conversationId: meta.convId,
        conversationType: meta.convType,
        title: title,
        body: body,
      });
      return true;
    }

    return { notifyIncoming: notifyIncoming };
  }

  return {
    DEFAULT_SETTINGS: DEFAULT_SETTINGS,
    TITLE_MAX: TITLE_MAX,
    BODY_MAX: BODY_MAX,
    isConversationMuted: isConversationMuted,
    normalizeSettings: normalizeSettings,
    createNotificationCenter: createNotificationCenter,
  };
});
