const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { build, Platform, Arch } = require('electron-builder');
const asar = require('@electron/asar');
const pkg = require('../package.json');
const { PRODUCT_NAME } = require('../app-branding');
const verifyIcon = require('./verify-icon.cjs');

async function main() {
  assert.equal(process.platform, 'win32', 'Branding verification requires Windows');
  assert.equal(pkg.build.productName, PRODUCT_NAME);
  const desktopRoot = path.resolve(__dirname, '..');
  const tempRoot = await fs.realpath(os.tmpdir());
  const output = await fs.mkdtemp(path.join(tempRoot, 'ichat-branding-test-'));
  const timeout = setTimeout(() => {
    console.error('Windows branding build exceeded 3 minutes');
    process.exit(1);
  }, 180000);
  try {
    // Exercise the real packager, afterPack hook and packaged resource paths.
    await build({
      projectDir: desktopRoot,
      targets: Platform.WINDOWS.createTarget('dir', Arch.x64),
      config: { directories: { output } },
      publish: 'never',
    });
    const appDir = path.join(output, 'win-unpacked');
    const executable = path.join(appDir, `${PRODUCT_NAME}.exe`);
    const resources = path.join(appDir, 'resources');
    const archive = path.join(resources, 'app.asar');
    const packedPkg = JSON.parse(asar.extractFile(archive, 'package.json'));
    assert.equal(packedPkg.version, pkg.version, 'Runtime version must match package version');
    assert.equal(packedPkg.name, 'ichat-pro-desktop', 'Keep the existing user-data profile name');
    assert.ok(asar.extractFile(archive, 'app-branding.js').length, 'Branding module must ship');
    // Exercise entry-point behavior from the archive, not just source files.
    const lifecycle = spawnSync(process.execPath, [
      '--test', path.join(desktopRoot, '../chat/tests/js/desktop_tray.test.js'),
      path.join(desktopRoot, '../chat/tests/js/desktop_notifications.test.js'),
    ], {
      env: { ...process.env, ICHAT_DESKTOP_TEST_ARCHIVE: archive },
      encoding: 'utf8', timeout: 30000, windowsHide: true,
    });
    if (lifecycle.error) throw lifecycle.error;
    process.stdout.write(lifecycle.stdout || '');
    assert.equal(lifecycle.status, 0, lifecycle.stderr || 'Packaged entry-point lifecycle check failed');
    for (const extension of ['ico', 'png']) {
      const expected = await fs.readFile(path.join(desktopRoot, 'build', `icon.${extension}`));
      const actual = await fs.readFile(path.join(resources, 'branding', `icon.${extension}`));
      assert.deepEqual(actual, expected, `Packaged ${extension} icon must match the source`);
    }
    const executableBytes = await fs.readFile(executable);
    const iconBytes = await fs.readFile(path.join(desktopRoot, 'build', 'icon.ico'));
    const sourceEntries = Array.from({ length: iconBytes.readUInt16LE(4) }, (_, i) => 6 + i * 16);
    const aboutEntry = sourceEntries.find(entry => iconBytes[entry] === 48 && iconBytes[entry + 1] === 48);
    assert.notEqual(aboutEntry, undefined, 'Require a 48px About icon frame');
    const aboutStart = iconBytes.readUInt32LE(aboutEntry + 12);
    assert.deepEqual(await fs.readFile(path.join(desktopRoot, 'build', 'icon.png')),
      iconBytes.subarray(aboutStart, aboutStart + iconBytes.readUInt32LE(aboutEntry + 8)),
      'About PNG must match the 48px ICO frame');
    const iconFrames = verifyIcon(executableBytes, iconBytes);
    assert.equal(iconFrames, 7, 'Verify all seven embedded icon sizes');
    const damagedIcon = Buffer.from(iconBytes);
    damagedIcon[damagedIcon.length - 1] ^= 1;
    assert.throws(() => verifyIcon(executableBytes, damagedIcon), /every source ICO frame/,
      'A changed icon must fail the resource comparison');
    const verification = spawnSync('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-File', path.join(__dirname, 'verify-branding.ps1'),
      '-Executable', executable,
      '-ExpectedVersion', pkg.version,
    ], { encoding: 'utf8', timeout: 30000, windowsHide: true });
    if (verification.error) throw verification.error;
    process.stdout.write(verification.stdout || '');
    assert.equal(verification.status, 0, verification.stderr || 'Native resource check failed');
    const result = JSON.parse(verification.stdout.trim());
    assert.equal(result.ProductName, PRODUCT_NAME, 'Require a native metadata verification result');
    console.log(`Native PE icon: ${iconFrames} source frames matched exactly`);
    console.log('Windows branding: packaged module/icons, version, profile name and native PE resources passed');
  } finally {
    clearTimeout(timeout);
    // Only remove the unique temporary output created above.
    const resolvedOutput = await fs.realpath(output);
    assert.equal(path.dirname(resolvedOutput).toLowerCase(), tempRoot.toLowerCase());
    assert.ok(path.basename(resolvedOutput).startsWith('ichat-branding-test-'));
    await fs.rm(resolvedOutput, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
