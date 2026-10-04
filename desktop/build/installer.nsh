; T15: explicit app-data choice on uninstall (review: the silent
; deleteAppDataOnUninstall deleted user data without asking).
; Default NO (MB_DEFBUTTON2: pressing Enter keeps the data).
; All raw NSIS instructions (no LogicLib): a raw Goto crossing
; ${IfNot}/${EndIf} desyncs LogicLib's generated labels.
; Interactive: ask about app data; silent: preserve data.
; Both paths force-clean leftovers and stale registrations.

!macro customUnInstall
  IfSilent silent_prompt_done 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDNO keep_data
    RMDir /r "$APPDATA\ichat-pro-desktop"
  keep_data:
  silent_prompt_done:

  ; Force-clean leftovers that the standard uninstaller can miss:
  ; runtime logs keep $INSTDIR alive as an empty folder.
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"

  ; Remove ONLY registrations whose UninstallString lived inside $INSTDIR
  ; (review: a stale registration pointed at the removed uninstaller).
  ; Registry strings are usually quoted - quotes are stripped before the
  ; prefix comparison. HKCU and HKLM are checked independently, so an
  ; independent install elsewhere is never unregistered.
  ReadRegStr $R0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}" "UninstallString"
  ${If} $R0 != ""
    StrCpy $R2 $R0 1
    ${If} $R2 == '"'
      StrLen $R3 $R0
      IntOp $R3 $R3 - 2
      StrCpy $R0 $R0 $R3 1
    ${EndIf}
    StrLen $R1 "$INSTDIR"
    StrCpy $R2 $R0 $R1
    ${If} $R2 == "$INSTDIR"
      DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
    ${EndIf}
  ${EndIf}
  ReadRegStr $R0 HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}" "UninstallString"
  ${If} $R0 != ""
    StrCpy $R2 $R0 1
    ${If} $R2 == '"'
      StrLen $R3 $R0
      IntOp $R3 $R3 - 2
      StrCpy $R0 $R0 $R3 1
    ${EndIf}
    StrLen $R1 "$INSTDIR"
    StrCpy $R2 $R0 $R1
    ${If} $R2 == "$INSTDIR"
      DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
    ${EndIf}
  ${EndIf}
!macroend
