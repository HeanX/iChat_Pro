; T15: explicit app-data choice on uninstall (review: the silent
; deleteAppDataOnUninstall deleted user data without asking).
; Runs during uninstall: asks the user whether to also remove app data
; (Electron userData: settings, caches, key backups) - default NO.

!macro customUnInstall
  ${IfNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDYES delete_app_data
    Goto done
    delete_app_data:
      RMDir /r "$APPDATA\iChat Pro"
    done:
  ${EndIf}
!macroend
