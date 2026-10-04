'use strict';
// Execute the real NSIS helper macros against disposable files and HKCU
// test keys. This harness never runs the application's official uninstaller.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

assert.equal(process.platform, 'win32', 'NSIS runtime tests require Windows');
const desktop = path.resolve(__dirname, '..');
const installer = fs.readFileSync(path.join(desktop, 'build', 'installer.nsh'), 'utf8');
// Protect the actual prompt wiring as well as the helper runtime semantics.
assert.match(installer, /MB_DEFBUTTON2[^\r\n]*IDNO ichat_keep_data/);
assert.match(installer, /StrCpy \$R4 "no"\s+IfSilent ichat_keep_data 0/);
assert.match(installer, /IDNO ichat_keep_data\s+StrCpy \$R4 "yes"\s+ichat_keep_data:/);
assert.match(installer, /iChatRemoveOwnedRegistration HKCU/);
assert.match(installer, /iChatRemoveOwnedRegistration HKLM/);
assert(installer.indexOf('SetOutPath "$TEMP"') < installer.indexOf('RMDir /r "$INSTDIR"'));

const candidates = [process.env.MAKENSIS,
  path.join(process.env.LOCALAPPDATA || '', 'electron-builder', 'Cache', 'nsis', 'nsis-3.0.4.1', 'makensis.exe'),
  path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'NSIS', 'makensis.exe'),
  path.join(process.env.ProgramFiles || 'C:\\Program Files', 'NSIS', 'makensis.exe')];
const compiler = candidates.find(p => p && fs.existsSync(p));
assert(compiler, 'Install NSIS or set MAKENSIS to its compiler; missing tools must fail, not skip');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ichat-nsis-test-'));
const registryRoot = 'Software\\iChat-Pro-InstallerTests\\' + crypto.randomUUID();
const literal = value => value.split('$').join('$$').replaceAll('"', '$\\"');
const lines = [
  'Unicode true', 'Name "iChat installer regression harness"',
  'OutFile "' + literal(path.join(temp, 'helper-tests.exe')) + '"',
  'RequestExecutionLevel user', 'SilentInstall silent',
  '!define UNINSTALL_FILENAME "Uninstall iChat Pro.exe"',
  '!include "' + literal(path.join(desktop, 'build', 'uninstall-helpers.nsh')) + '"',
  'Var installMode',
  '!macro Assert EXPECTED ACTUAL LABEL',
  '  ${If} ${ACTUAL} != "${EXPECTED}"',
  '    FileWrite $R9 "FAIL ${LABEL}: ${ACTUAL}$\\r$\\n"',
  '    IntOp $0 $0 + 1',
  '  ${Else}',
  '    FileWrite $R9 "PASS ${LABEL}$\\r$\\n"',
  '  ${EndIf}',
  '!macroend',
  'Section',
  '  StrCpy $0 0',
  '  StrCpy $INSTDIR "' + literal(path.join(temp, 'iChat Pro')) + '"',
  '  SetOutPath "' + literal(temp) + '"',
  '  FileOpen $R9 "' + literal(path.join(temp, 'results.txt')) + '" w',
];
let count = 0;
function check(expected, actual, label) {
  lines.push('  !insertmacro Assert "' + expected + '" "' + actual + '" "' + label + '"');
  count++;
}
const commands = [
  ['quoted-args', '$\\"$INSTDIR\\Uninstall iChat Pro.exe$\\" /currentuser', 1],
  ['quoted-no-args', '$\\"$INSTDIR\\Uninstall iChat Pro.exe$\\"', 1],
  ['quoted-all-users', '$\\"$INSTDIR\\Uninstall iChat Pro.exe$\\" /allusers /S', 1],
  ['unquoted-args', '$INSTDIR\\Uninstall iChat Pro.exe /currentuser', 1],
  ['unquoted-no-args', '$INSTDIR\\Uninstall iChat Pro.exe', 1],
  ['case-insensitive-windows-path', '$INSTDIR\\UNINSTALL ICHAT PRO.EXE /currentuser', 1],
  ['sibling-install', '$\\"$INSTDIR-old\\Uninstall iChat Pro.exe$\\" /currentuser', 0],
  ['nested-other-executable', '$\\"$INSTDIR\\tools\\Uninstall iChat Pro.exe$\\"', 0],
  ['exe-suffix', '$INSTDIR\\Uninstall iChat Pro.exe.old /currentuser', 0],
  ['quoted-suffix', '$\\"$INSTDIR\\Uninstall iChat Pro.exe$\\".old', 0],
  ['missing-closing-quote', '$\\"$INSTDIR\\Uninstall iChat Pro.exe /currentuser', 0],
  ['empty-command', '', 0],
];
for (const [label, command, expected] of commands) {
  lines.push('  StrCpy $R0 \'' + command + '\'', '  !insertmacro iChatUninstallCommandMatches "$R0" $R4');
  check(expected, '$R4', label);
}
// Both logical installation records use unique HKCU fixture paths: no
// elevation and no real HKCU/HKLM application registration is touched.
const ownU = registryRoot + '\\own\\uninstall';
const ownI = registryRoot + '\\own\\install';
const otherU = registryRoot + '\\other\\uninstall';
const otherI = registryRoot + '\\other\\install';
lines.push(
  '  WriteRegStr HKCU "' + ownU + '" "UninstallString" \'$\\"$INSTDIR\\Uninstall iChat Pro.exe$\\" /currentuser\'',
  '  WriteRegStr HKCU "' + ownI + '" "InstallLocation" "$INSTDIR"',
  '  WriteRegStr HKCU "' + otherU + '" "UninstallString" \'$\\"$INSTDIR-old\\Uninstall iChat Pro.exe$\\" /allusers\'',
  '  WriteRegStr HKCU "' + otherI + '" "InstallLocation" "$INSTDIR-old"',
  '  !insertmacro iChatRemoveOwnedRegistration HKCU "' + ownU + '" "' + ownI + '"',
  '  !insertmacro iChatRemoveOwnedRegistration HKCU "' + otherU + '" "' + otherI + '"',
  '  ReadRegStr $R0 HKCU "' + ownU + '" "UninstallString"');
check('', '$R0', 'owned-uninstall-registration-removed-without-InstallLocation');
lines.push('  ReadRegStr $R0 HKCU "' + ownI + '" "InstallLocation"');
check('', '$R0', 'owned-install-registration-removed');
lines.push('  ReadRegStr $R0 HKCU "' + otherI + '" "InstallLocation"');
check('$INSTDIR-old', '$R0', 'independent-install-registration-preserved');
lines.push('  ReadRegStr $R0 HKCU "' + otherU + '" "UninstallString"',
  '  !insertmacro iChatUninstallCommandMatches "$R0" $R4');
check(0, '$R4', 'independent-uninstall-command-does-not-match');
lines.push('  StrLen $R4 $R0');
lines.push('  ${If} $R4 > 0', '    StrCpy $R4 1', '  ${EndIf}');
check(1, '$R4', 'independent-uninstall-registration-preserved');
// Create only disposable sentinel directories inside this harness's temp.
for (const [choice, mode] of [['no', 'CurrentUser'], ['silent', 'CurrentUser'], ['yes', 'CurrentUser'], ['no', 'all'], ['silent', 'all'], ['yes', 'all']]) {
  const label = choice + '-' + mode;
  const dir = path.join(temp, label);
  lines.push('  CreateDirectory "' + literal(dir) + '"',
    '  FileOpen $R1 "' + literal(path.join(dir, 'sentinel.txt')) + '" w',
    '  FileWrite $R1 "test data"', '  FileClose $R1',
    '  StrCpy $installMode "' + mode + '"',
    '  !insertmacro iChatApplyDataChoice "' + choice + '" "' + literal(dir) + '"',
    '  StrCpy $R4 0',
    '  IfFileExists "' + literal(path.join(dir, 'sentinel.txt')) + '" 0 +2',
    '  StrCpy $R4 1');
  check(choice === 'yes' ? 0 : 1, '$R4', 'data-' + label);
}
// Prove all-users deletion restores its shell context afterwards;
// no actual APPDATA content is modified.
lines.push('  SetShellVarContext all', '  StrCpy $R3 "$APPDATA"',
  '  !insertmacro iChatApplyDataChoice "yes" "' + literal(path.join(temp, 'absent')) + '"',
  '  StrCpy $R4 "$APPDATA"');
check('$R3', '$R4', 'all-users-shell-context-restored');
lines.push('  SetShellVarContext current',
  '  DeleteRegKey HKCU "' + registryRoot + '"',
  '  FileClose $R9', '  SetErrorLevel $0', 'SectionEnd');
try {
  const source = path.join(temp, 'tests.nsi');
  fs.writeFileSync(source, lines.join('\r\n'), 'utf8');
  for (const [exe, args] of [[compiler, ['/V2', source]], [path.join(temp, 'helper-tests.exe'), ['/S']]]) {
    const run = spawnSync(exe, args, { encoding: 'utf8', timeout: 30000, windowsHide: true });
    assert.ifError(run.error);
    assert.equal(run.status, 0, (run.stdout || '') + (run.stderr || '') + '\n' + (fs.existsSync(path.join(temp, 'results.txt')) ? fs.readFileSync(path.join(temp, 'results.txt'), 'utf8') : 'No result file'));
  }
  const results = fs.readFileSync(path.join(temp, 'results.txt'), 'utf8').trim().split(/\r?\n/);
  assert.equal(results.length, count, 'Every native assertion must report a result');
  assert(results.every(line => line.startsWith('PASS ')), results.join('\n'));
  console.log(results.join('\n'));
  console.log('NSIS runtime: ' + count + '/' + count + ' assertions passed; actual application/data/registration untouched');
} finally {
  // Delete only the unique temporary directory created above.
  assert.equal(path.dirname(path.resolve(temp)), path.resolve(os.tmpdir()));
  assert(path.basename(temp).startsWith('ichat-nsis-test-'));
  fs.rmSync(temp, { recursive: true, force: true });
}
