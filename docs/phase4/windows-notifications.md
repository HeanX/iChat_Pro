# T18 Windows system notifications acceptance

T18 is P1 (Issue #145, NEW-WIN-008). Incoming messages raise a Windows toast
when the app is not the focus; the notification respects mute and privacy
settings; clicking it restores the same window (no reload) and opens the
conversation. Notifications are a desktop-shell feature; the web client gains
the same gating module but does not request browser notification permission.

## Architecture

The renderer decrypts, so it is the only place that knows whether a message is
readable, for which conversation, and whether that conversation is muted.
Gating therefore lives in a shared module, `static/js/chat-notifications.js`,
used by `chat.js`; the Electron shell only re-validates and displays.

- Renderer gating (`ChatNotifications.createNotificationCenter`):
  - master switch `display_notifications`, then per-type switches
    `private_chat_notifications` / `group_chat_notifications`;
  - per-conversation mute from the conversation list (`muted_until` in the
    future suppresses; an expired window does not);
  - focus: no notification while the window is visible and focused — the
    message is already on screen. Hidden (tray) or unfocused windows notify;
  - source: only realtime WebSocket pushes notify. Sync catch-up after a
    reconnect applies silently and only updates unread badges, so a burst of
    missed messages cannot flood the OS notification center. This is a
    documented limitation, not an oversight;
  - content preview honors `message_preview_private` / `message_preview_group`;
    undecryptable messages and file messages always show the generic label.
- Settings source: `/api/settings/notifications/` (UserNotificationSettings)
  loaded once during chat init. Failure keeps the server-model defaults; the
  settings page applies changes on its next chat-page load.
- Desktop bridge (preload `iChatDesktop.notifications`):
  - `show(payload)` invokes `ichat:notifications:show`;
  - `onClicked(cb)` receives `ichat:notifications:clicked` with the
    conversation target and routes to `selectChat`.
- Main process (`registerNotificationIpc`): same trust model as the T21
  safeStorage bridge — only the app's own frames may invoke; payloads are
  validated and clamped (integer conversation id, `single|group` type, title
  ≤ 80, body ≤ 200, empty rejected); the toast carries the T16 brand icon;
  clicking it calls the tray `showMainWindow` path (same window, no reload)
  and forwards the conversation id.

## Automated evidence

```powershell
# Repository root
node chat/tests/js/chat_notifications.test.js   # 12 gating scenarios
node chat/tests/js/desktop_notifications.test.js # 7 shell/IPC scenarios
node chat/tests/js/desktop_tray.test.js          # 15 lifecycle regressions

# desktop/ - packaging: same tests against the built archive
npm run test:installer
```

`chat_notifications.test.js` covers the gating matrix: master/type switches,
conversation mute (active and expired), focused-window suppression, self and
sync-source suppression, preview-off/decrypt-failure/file fallback bodies,
group title composition ("Group · Sender"), clamping with ellipsis, settings
normalization and unknown conversations.

`desktop_notifications.test.js` executes the real `main.js` and `preload.js`
with injected OS/network/timer boundaries (same harness style as the tray
suite): the preload bridge shape and channel list, IPC registration next to
the secure-storage handlers, branded-toast display for a valid payload,
rejection of untrusted senders and malformed payloads, title/body clamping,
click-to-restore with conversation routing without a reload, and the no-window
/ quitting path.

## Native Windows acceptance record

Test with the packaged app (T16-branded build), not `npm start`:

1. Log in, minimize to tray (T17 behavior), send a message from another
   account: a toast appears with the sender/conversation title; clicking it
   restores the same window and opens the conversation.
2. Repeat with the conversation muted: no toast. Unmute: toast returns.
3. Toggle 私聊/群聊通知 off in the notification settings page: the matching
   type stops notifying.
4. Turn off message preview: the toast shows the generic label, not content.
5. Focus the app on the conversation and receive a message: no toast (the
   message is already visible); focus another conversation or another app:
   toast returns.
6. Check `通知` settings in Windows: toasts come from the iChat Pro app entry
   with the brand icon; disabling notifications for the app suppresses toasts
   at the OS level.

Limitations recorded for review: per-message toasts are not coalesced per
conversation; notification sounds follow the OS default (the in-app
`notification_sound`/`volume` settings are not applied to toasts); messages
that arrive while the app is fully offline appear only as unread badges after
catch-up, without toasts.
