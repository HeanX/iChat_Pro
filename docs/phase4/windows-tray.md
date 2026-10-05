# T17 Windows window and tray acceptance

T17 is P1 (Issue #144). Minimize hides the existing window when a tray is
available; restoring it must not reload the renderer. The window X and tray
Exit terminate the application. This preserves T15's close-without-processes
contract and T20's in-memory connection/outbox state during minimize/restore.

## Entry-point requirements

- Import Electron `Tray` and resolve the T16 ICO with `getBrandIconPath(app)`.
- Acquire the single-instance lock before `whenReady`, any origin probe or
  Django spawn. A secondary process quits without creating windows, trays,
  timers or a dev server. The primary restores its existing window.
- Create the tray in online, offline and unconfigured modes. A second launch
  during the initial probe must not create an extra window before startup ends.
- Keep one tray reference. If construction or menu setup fails, dispose of any
  partial tray and retain normal taskbar minimize/restore behavior.
- Before quitting, destroy the tray, clear both offline polling and connectivity
  monitoring, and stop the dev Django process tree. Late asynchronous probe
  results must not navigate, inject UI or create another window after quitting.
- Offline recovery must retain the handle of its existing connectivity monitor
  so shutdown can clear it.

The lock ordering follows the
[Electron single-instance example](https://www.electronjs.org/docs/latest/api/app#apprequestsingleinstancelockadditionaldata).

## Automated evidence

```powershell
# Repository root: source entry point, configuration and branding wiring
npm run test:e2ee

# desktop/: rebuild a real archive and rerun the lifecycle tests against it
npm run test:branding
npm run test:installer
```

`chat/tests/js/desktop_tray.test.js` executes the actual entry point and its
configuration/branding modules with injected OS, network and timer boundaries.
It covers 15 scenarios, including all restore menu/event paths, renderer identity,
normal X close, hidden tray Exit, failures, secondary launches, pending probes,
offline recovery and dev shutdown. Each case has a 5-second timeout.

The Windows build test extracts the same modules from the newly produced
`app.asar` and runs those tests with real packaged icon paths. This guards
imports, shipped files and resource resolution as well as source wiring.
It does not substitute for clicking the actual Windows notification-area icon.

Review baseline (PR #319 archive): 14/15 cases fail; tray setup reports
`Cannot read properties of undefined (reading 'isPackaged')`. Adding the missing
`app` alone also leaves the unimported `Tray` constructor. After repair, 15/15
pass against source and a freshly built archive. The full JS regression suite,
native branding resources and 24 NSIS isolation assertions pass locally.

## Native Windows acceptance record

Test the rebuilt packaged app, not a previous installed executable:

1. Open the cloud login page, then minimize. Verify the taskbar window disappears
   while one iChat Pro notification-area icon remains.
2. Restore by icon click, double click and Open main window. Repeat; verify the
   same window/page survives and icons do not multiply. Test a real logged-in
   session with an outstanding message to verify T20 state preservation.
3. Launch the executable again while hidden. Verify the same window returns and
   no second root process remains. Also exercise a cold offline launch.
4. Close with X. Confirm the notification icon disappears and no app processes
   remain after shutdown completes.
5. Minimize again on a fresh launch, choose Exit from the notification-area menu,
   and check both process and icon cleanup.

The review build has been launched against `chat.20060810.xyz:8443`. Native
minimize and second-launch restoration were exercised with the same window
handle and unchanged root/renderer process IDs. Clicking X then left zero app
processes.

## Native acceptance result (2026-10-05, Asia/Shanghai)

Baseline: main `93c0190`, installer SHA-256 `01F801A5…EEFE94C` (full value in
the close-out record). The packaged build was driven against the cloud login
page with the real notification-area icon:

1. Minimize, single click on the tray icon: original window (ID 68330)
   restored, all four app PIDs unchanged, one tray icon, no page reload.
2. Minimize, double click: same window restored again, PIDs unchanged, icons
   did not multiply.
3. Hidden window, real tray context menu, Exit chosen by the user: agent
   verified the iChat process count dropped to 0 and the window list no longer
   contained the window.
4. Logged-in outbox preservation across minimize/restore and delivery after
   network recovery: verified manually by the user.

Two early automation double-click attempts missed due to tray flyout focus and
were not counted as passes. The session combined the assistant's direct
process/window evidence with the user's manual confirmation ("均可用") and is
recorded in `T17-native-record.md` (assistant working notes, 2026-10-05).
T17 acceptance is closed; Issue #144 closed on this basis.
