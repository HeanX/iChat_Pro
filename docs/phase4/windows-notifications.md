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
- Settings source: `/api/settings/notifications/` (UserNotificationSettings).
  Unknown or malformed permission fields disable notifications and previews.
  A successful load/save from the embedded settings page updates the current
  chat document immediately, without reloading the page or losing its outbox.
  Settings are bound to the logged-in user; a late initial response cannot
  overwrite a newer settings update. Browser Notification permission is not
  requested for the Electron IPC notification path.
- A message for an unknown conversation refreshes the list before checking
  mute metadata; no toast is shown if the conversation remains unknown. Clicks
  also refresh missing conversation metadata before selecting the target.
  Optional notification work never blocks message application or sync cursors;
  bridge throws/rejections are contained. Group decryption failures may show a
  generic realtime toast, while sync still reports the failed application.
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
- AppUserModelID: the runtime AUMID is set to the electron-builder
  `build.appId` (`pro.ichat.desktop`) before any notification. Without the
  match, Windows displays the toast but never routes its click activation —
  the 2026-10-05 isolated-build native record showed toasts sourced from the
  default `electron.app.iChat Pro` with zero click events. Click acceptance
  therefore REQUIRES the installed build (Start Menu shortcut + matched
  AUMID); `win-unpacked` and isolated test builds cannot deliver toast
  clicks by design.

## Automated evidence

```powershell
# Repository root
node chat/tests/js/chat_notifications.test.js   # 13 gating scenarios
node chat/tests/js/desktop_notifications.test.js # 7 shell/IPC scenarios
node --test chat/tests/js/notification_integration.test.js # 12 real chat/settings wiring scenarios
node chat/tests/js/desktop_tray.test.js          # 15 lifecycle regressions
npm run test:e2ee                              # includes ALL notification suites

# desktop/ - real packager: tray + notification shell tests against the new archive
npm run test:branding
# Real Chromium layout/focus regression (temporary profile, mocked settings API)
npm run test:layout
# Disposable NSIS helper checks; this does not test notifications
npm run test:installer
```

`chat_notifications.test.js` covers the gating matrix: master/type switches,
conversation mute (active and expired), focused-window suppression, self and
sync-source suppression, preview-off/decrypt-failure/file fallback bodies,
group title composition ("Group · Sender"), clamping with ellipsis, settings
normalization, unavailable settings and unknown conversation suppression.

`notification_integration.test.js` evaluates the actual `chat.js` notification
wiring and private/group handlers, together with the embedded settings page's
script in the same global scope. It covers pending/failed settings loads,
live switches and preview updates, stale response protection, Electron's
independence from browser permission, synchronous/asynchronous bridge failures,
generic decrypt-failure/missing-module toasts with silent/failed sync, unknown-conversation
mute resolution, nonblocking message application, refreshed click routing and
contained selection errors. It does not copy the production gate/handlers.

The original PR #324 added tests without registering them in `test:e2ee` or the
packaged archive suite. Both CI entry points now execute notification regressions:
Ubuntu's JS job runs all three notification suites; Windows' `test:branding`
runs `desktop_notifications.test.js` and tray tests against its fresh `app.asar`.

Review verification (2026-10-05, base `5025b08`): running the 12 integration
cases against the base sources produced 10 assertion failures plus an unhandled
selection rejection (exit 1); the repaired sources passed 12/12. The full root
JS command passed (62 Node-test cases plus the existing E2EE/connection/config
runners). A fresh Windows archive passed 22/22 notification/tray shell cases
and native branding resource verification. `manage.py check` reported no issues.

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
5. Focus the visible app and receive a message: no toast; move focus to another
   app or minimize to tray: toast returns. Suppression is window-wide, including
   when another conversation is selected in the focused window.
6. Check `通知` settings in Windows: toasts come from the iChat Pro app entry
   with the brand icon; disabling notifications for the app suppresses toasts
   at the OS level.

Limitations recorded for review: per-message toasts are not coalesced per
conversation; notification sounds follow the OS default (the in-app
`notification_sound`/`volume` settings are not applied to toasts); messages
that arrive while the app is fully offline appear only as unread badges after
catch-up, without toasts.

Installed-build reviewer confirmation (2026-10-05, installer SHA-256
`ac468df305bd43c73179555671b3aaf80fca06773427f64a42e14a2ad299d5f9`):
the user confirmed toast click restoration/conversation routing, the Windows
source name **iChat Pro**, OS notification on/off behavior, and generic toast
content with message preview disabled. These are manual installed-build results;
the isolated fixture and mocked suites alone do not establish native acceptance.

The same preview-switch test exposed two renderer layout defects: focusing an
unconstrained absolutely positioned checkbox scrolled the sidebar shell (about
695px), and the chat header did not use its existing menu/search component
styles. The fix anchors the focus target inside its own notification row and
connects the header classes, with a bounded, shrinking search wrapper. The
`chat.css` URL is versioned `20261005-t18-layout` so cached desktop clients fetch
the repaired stylesheet.

`desktop/scripts/test-sidebar-layout.cjs` loads the production notification
markup/script, sidebar header/navigation, CSS and event dispatcher in a real,
hidden Electron Chromium renderer. It uses only a new temporary profile and
a loopback fixture with a mocked settings API; no cloud requests, existing
account settings or cryptographic keys are touched. It checks private/group
preview off/on, saving without reload, keyboard focus/Space, sidebar-shell
position and menu/search alignment across 1260x720, 900x480 and 400x720 in both
themes (36 checks). The Windows CI job runs `npm run test:layout`.

Against base `908ba19`, the same test fails with shell scrollTop 694 instead of
0. The repaired sources pass 36/36; `npm run test:e2ee` passes all existing
runners and 62 Node-test cases, and `manage.py check` reports no issues.
Deployment follow-up (2026-10-07): PR #328 merged as `c23127f` and was deployed through the stable deployment script. DB/cache readiness, public live/ready/login endpoints returned 200, the production template uses stylesheet version `20261005-t18-layout`, and served CSS matches merged source after CRLF/LF normalization. The real Chromium layout suite was rerun and passed 36/36. The in-app browser created the login tab but repeated connection attempts timed out; no logged-in production UI interaction was completed. Keep #145 open for final installed-client preview-switch/return-to-chat confirmation.
