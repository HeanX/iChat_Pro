# Windows uninstall validation

> 更新日期：2026-10-07；核对代码基线：`f2f60ec`。现行说明已按代码和验收证据更新。
> 当前状态见 [项目现状](../../docs/current-status.md)；文档用途与归档规则见 [文档维护索引](../../docs/documentation-status.md)。

## 本次更新

T15 lifecycle acceptance is complete (#142), including interactive No/Yes choices and removal of owned registration. The isolated NSIS harness passed 24/24 assertions in CI run 37630595919. The installed profile is %APPDATA%\ichat-pro-desktop; default No and silent uninstall preserve data, interactive Yes clears the current user profile. Future installer changes still require both harness regression and native lifecycle evidence. Brand/layout runners are npm run test:branding and npm run test:layout.

Run `npm run test:installer` from `desktop` on Windows. The runner requires
NSIS (`MAKENSIS` can point to a compiler); a missing compiler is a failure.
It compiles the production helper macros into a silent regression harness
and checks every reported assertion, with a 30-second process timeout.

The harness uses a unique temporary directory and disposable HKCU keys
under `Software\iChat-Pro-InstallerTests`. It never executes the application's
official uninstaller, modifies a real installation registration, or deletes
the user's iChat data. It covers complete quoted/unquoted executable paths,
command arguments, sibling-directory isolation, both independent registry
records, and yes/no/silent data choices in both installation modes.

The actual uninstall prompt remains default No. Silent upgrades preserve
data. Interactive Yes deletes the current user's `ichat-pro-desktop` data,
including for an all-users installation, then restores the shell context.
Registration cleanup requires either the complete expected uninstall
executable path or the independent install record's exact InstallLocation.

These regressions supplement the T15 lifecycle test. Installation, cloud
startup, process exit, the official uninstall UI, and post-uninstall residual
checks must still be recorded on the final package. A passing harness alone
does not close #142.
