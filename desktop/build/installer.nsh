; T15 review: leave $INSTDIR before removing it (the uninstaller sets it
; as the CWD, and RMDir cannot delete the current directory); registry
; deletions are scoped to the registration whose InstallLocation matches
; $INSTDIR (per-machine and per-user installs are independent).

!macro customUnInstall
  ; Interactive: ask about app data (default NO via MB_DEFBUTTON2 -
  ; Enter keeps the data). Silent: preserve data.
  IfSilent silent_prompt 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDNO keep_data
    RMDir /r "$APPDATA\ichat-pro-desktop"
  keep_data:
  silent_prompt:

  ; Force-clean leftovers that the standard uninstaller can miss:
  ; runtime logs keep $INSTDIR alive as an empty folder, and a stale
  ; uninstall registration can point at the removed uninstaller.
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"
  ReadRegStr $R0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}" "InstallLocation"
  ${If} $R0 == "$INSTDIR"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
  ${EndIf}
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}" "InstallLocation"
  ${If} $R0 == "$INSTDIR"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
  ${EndIf}
!macroend
