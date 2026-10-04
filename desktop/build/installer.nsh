!include "${BUILD_RESOURCES_DIR}\uninstall-helpers.nsh"

!macro customUnInstall
  ; Default No and silent/upgrade uninstalls preserve user data.
  StrCpy $R4 "no"
  IfSilent ichat_keep_data 0
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "是否同时删除您的应用数据（设置、缓存等）？$\n$\n选择【否】将保留数据（重装后可继续使用）。" IDNO ichat_keep_data
    StrCpy $R4 "yes"
  ichat_keep_data:
  !insertmacro iChatApplyDataChoice "$R4" "$APPDATA\ichat-pro-desktop"

  ; The native uninstaller starts with $INSTDIR as its working directory.
  SetOutPath "$TEMP"
  RMDir /r "$INSTDIR"

  ; Match the complete executable, not a sibling such as iChat Pro-old.
  !insertmacro iChatRemoveOwnedRegistration HKCU "${UNINSTALL_REGISTRY_KEY}" "${INSTALL_REGISTRY_KEY}"
  !insertmacro iChatRemoveOwnedRegistration HKLM "${UNINSTALL_REGISTRY_KEY}" "${INSTALL_REGISTRY_KEY}"
!macroend
