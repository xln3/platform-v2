import { spawn } from 'node:child_process';
import { chmod, readFile, rename, rm, stat, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { chromium } from '@playwright/test';

const root = resolve(import.meta.dirname, '..');
const extension = resolve(root, 'packages/customer-terminal-extension');
const dist = resolve(extension, 'dist');
const keyPath =
  process.env.GEO_EXTENSION_SIGNING_KEY ??
  '/etc/geo-platform-v2/extension-signing/customer-terminal.pem';
const packedSource = `${dist}.crx`;
const packedTarget = resolve(dist, 'customer-terminal-extension.crx');
const metadataTarget = resolve(dist, 'release.json');

function run(command, args) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.on('data', (chunk) => (stderr += chunk));
    child.once('error', reject);
    child.once('exit', (code) => {
      if (code === 0) resolveRun();
      else reject(new Error(`extension_pack_failed_${code}: ${stderr.slice(-1000)}`));
    });
  });
}

function extensionId(publicKey) {
  const digest = createHash('sha256')
    .update(Buffer.from(publicKey, 'base64'))
    .digest('hex')
    .slice(0, 32);
  return [...digest]
    .map((value) => String.fromCharCode('a'.charCodeAt(0) + Number.parseInt(value, 16)))
    .join('');
}

if (process.env.GEO_SKIP_EXTENSION_BUILD !== '1') {
  await run('pnpm', ['--filter', '@geo/customer-terminal-extension', 'build']);
}
const keyStat = await stat(keyPath);
if ((keyStat.mode & 0o077) !== 0) throw new Error('extension_signing_key_permissions_unsafe');
await Promise.all([
  rm(packedSource, { force: true }),
  rm(packedTarget, { force: true }),
  rm(metadataTarget, { force: true }),
]);
await run(chromium.executablePath(), [
  `--pack-extension=${dist}`,
  `--pack-extension-key=${keyPath}`,
  '--no-sandbox',
]);
await rename(packedSource, packedTarget);
await chmod(packedTarget, 0o644);

const manifest = JSON.parse(await readFile(resolve(dist, 'manifest.json'), 'utf8'));
const artifact = await readFile(packedTarget);
const metadata = {
  schema_version: 1,
  extension_id: extensionId(manifest.key),
  version: manifest.version,
  crx_sha256: createHash('sha256').update(artifact).digest('hex'),
  crx_bytes: artifact.byteLength,
  signing_private_key_embedded: false,
};
await writeFile(metadataTarget, `${JSON.stringify(metadata, null, 2)}\n`, { mode: 0o644 });
process.stdout.write(`${JSON.stringify(metadata, null, 2)}\n`);
