!ifndef ICHAT_UNINSTALL_HELPERS
!define ICHAT_UNINSTALL_HELPERS
!include LogicLib.nsh

; RESULT must not be one of the scratch registers $R5..$R8.
; Match the complete quoted or legacy unquoted executable path, then
; require end of command or a space before the arguments.
!macro iChatUninstallCommandMatches COMMAND RESULT
  Push $R5
  Push $R6
  Push $R7
  Push $R8
  StrCpy $R5 ${COMMAND}
  StrCpy ${RESULT} "0"
  StrCpy $R6 "$INSTDIR\${UNINSTALL_FILENAME}"
  StrCpy $R8 $R5 1
  ${If} $R8 == '$\"'
    StrCpy $R6 '$\"$INSTDIR\${UNINSTALL_FILENAME}$\"'
  ${EndIf}
  StrLen $R8 $R6
  StrCpy $R7 $R5 $R8
  ${If} $R7 == $R6
    StrCpy $R7 $R5 1 $R8
    ${If} $R7 == ""
    ${OrIf} $R7 == " "
      StrCpy ${RESULT} "1"
    ${EndIf}
  ${EndIf}
  Pop $R8
  Pop $R7
  Pop $R6
  Pop $R5
!macroend

!macro iChatRemoveOwnedRegistration ROOT UNINSTALL_KEY INSTALL_KEY
  ReadRegStr $R0 ${ROOT} "${UNINSTALL_KEY}" "UninstallString"
  !insertmacro iChatUninstallCommandMatches "$R0" $R4
  ${If} $R4 == "1"
    DeleteRegKey ${ROOT} "${UNINSTALL_KEY}"
  ${EndIf}

  ; The independent install record also survived the real lifecycle test.
  ; Its InstallLocation belongs here, not in the Uninstall key.
  ReadRegStr $R0 ${ROOT} "${INSTALL_KEY}" "InstallLocation"
  ${If} $R0 == "$INSTDIR"
    DeleteRegKey ${ROOT} "${INSTALL_KEY}"
  ${EndIf}
!macroend

!macro iChatApplyDataChoice CHOICE DATA_DIRECTORY
  ${If} ${CHOICE} == "yes"
    ; Electron data is per user even for an all-users installation.
    SetShellVarContext current
    RMDir /r "${DATA_DIRECTORY}"
    ${If} $installMode == "all"
      SetShellVarContext all
    ${EndIf}
  ${EndIf}
!macroend
!endif
