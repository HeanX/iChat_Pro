; T15: explicit app-data choice on uninstall (review: the silent
; deleteAppDataOnUninstall deleted user data without asking).
; Default NO (MB_DEFBUTTON2: pressing Enter keeps the data).
; All raw NSIS instructions (no LogicLib): a raw Goto crossing
; ${IfNot}/${EndIf} desyncs LogicLib's generated labels.
; Interactive: ask about app data; silent: preserve data.
; Both paths force-clean leftovers and stale registrations.

!macro customUnInstall
  IfSilent silent_prompt_done 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDYES delete_app_data
    RMDir /r "$APPDATA\ichat-pro-desktop"
  delete_app_data:
  silent_prompt_done:
  RMDir /r "$INSTDIR"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
!macroend
