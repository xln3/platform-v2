import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';

await rm('dist', { recursive: true, force: true });
await mkdir('dist');
await cp('manifest.json', 'dist/manifest.json');
await cp('popup.html', 'dist/popup.html');
await cp('popup.css', 'dist/popup.css');
await cp('src/protocol.mjs', 'dist/protocol.mjs');
await cp('src/background.mjs', 'dist/background.js');
await cp('node_modules/jsqr/LICENSE', 'dist/LICENSE-jsQR.txt');
await build({
  bundle: true,
  entryPoints: ['src/popup.mjs'],
  format: 'esm',
  legalComments: 'eof',
  minify: false,
  outfile: 'dist/popup.js',
  platform: 'browser',
  sourcemap: false,
  target: ['chrome120'],
});

const forbidden = [
  'chrome.cookies',
  'chrome.proxy',
  'localStorage',
  'sessionStorage',
  'webRequest',
  'Cookie',
  'otp_value',
  'biometric_image',
  'body?.error?.code',
  'bundleInput',
];
for (const file of ['dist/protocol.mjs', 'dist/background.js', 'dist/popup.js']) {
  const content = await readFile(file, 'utf8');
  for (const marker of forbidden) {
    if (content.includes(marker))
      throw new Error(`forbidden terminal capability in ${file}: ${marker}`);
  }
}
const popupHtml = await readFile('dist/popup.html', 'utf8');
for (const marker of ['<textarea', 'id="bundle"', 'name="pairing_token"']) {
  if (popupHtml.includes(marker))
    throw new Error(`forbidden terminal capability input in dist/popup.html: ${marker}`);
}
const popupBundle = await readFile('dist/popup.js');
if (popupBundle.byteLength > 400 * 1024)
  throw new Error('customer terminal popup bundle exceeds 400 KiB');
const manifest = JSON.parse(await readFile('dist/manifest.json', 'utf8'));
if (
  JSON.stringify(manifest.permissions) !== JSON.stringify(['storage', 'tabs']) ||
  JSON.stringify(manifest.optional_host_permissions) !== JSON.stringify(['https://*/*']) ||
  manifest.content_scripts ||
  manifest.externally_connectable ||
  manifest.web_accessible_resources
) {
  throw new Error('forbidden terminal manifest capability');
}
await writeFile(
  'dist/BUILD-POLICY.txt',
  'Non-extractable Ed25519 key; allow-listed task projection; no pasted capability, cookie/proxy/page-content/OTP/biometric APIs.\n',
);
