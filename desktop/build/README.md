# Windows uninstall validation

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
