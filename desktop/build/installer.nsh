; T15: explicit app-data choice on uninstall (review: the silent
; deleteAppDataOnUninstall deleted user data without asking).
; Runs during uninstall: asks the user whether to also remove app data
; (Electron userData: settings, caches, key backups) - default NO
; (MB_DEFBUTTON2: pressing Enter keeps the data).

!macro customUnInstall
  ${IfNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDYES delete_app_data
    Goto done
    delete_app_data:
      RMDir /r "$APPDATA\ichat-pro-desktop"
    done:

  ; Review: force-clean leftovers that the standard uninstaller can miss.
  ; - runtime logs etc. keep $INSTDIR alive as an empty folder
  ; - a stale uninstall registration can point at the removed uninstaller
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
!macroend
