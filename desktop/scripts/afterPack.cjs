/**
 * T16: stamp exe metadata (name/version) after packing. Runs rcedit on the
 * unpacked exe before NSIS bundles it; the installer exe gets its metadata
 * from the NSIS config instead.
 */
const path = require('path');
const { rcedit } = require('rcedit');
const pkg = require(path.join(__dirname, '..', 'package.json'));

exports.default = async function (context) {
  if (context.electronPlatformName !== 'win32') return;
  const exePath = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.exe');
  await rcedit(exePath, {
    'version-string': {
      FileDescription: 'iChat Pro desktop client',
      ProductName: 'iChat Pro',
      CompanyName: 'iChat Pro Team',
      LegalCopyright: 'Copyright (c) 2026 iChat Pro Team',
    },
    'file-version': pkg.version,
    'product-version': pkg.version,
    icon: path.join(__dirname, '..', 'build', 'icon.ico'),
  });
};
