import { cpSync, createReadStream, existsSync, mkdtempSync, rmSync, statSync } from 'node:fs';
import { createServer, request as httpRequest } from 'node:http';
import { tmpdir } from 'node:os';
import { basename, extname, join, normalize } from 'node:path';
import { gzipSync } from 'node:zlib';

const portBase = Number(process.env.GEO_E2E_PORT_BASE ?? '45100');
if (!Number.isSafeInteger(portBase) || portBase < 1024 || portBase > 65530) {
  throw new Error('GEO_E2E_PORT_BASE must reserve five valid consecutive ports.');
}
const apiPort = Number(process.env.GEO_E2E_API_PORT ?? '45200');
if (!Number.isSafeInteger(apiPort) || apiPort < 1024 || apiPort > 65535) {
  throw new Error('GEO_E2E_API_PORT must be a valid non-privileged port.');
}

const apps = [
  ['customer', 'apps/customer-web/build-e2e/client'],
  ['operations', 'apps/operations-web/build-e2e/client'],
  ['reports', 'apps/report-studio/build-e2e/client'],
  ['intelligence', 'apps/intelligence-web/build-e2e/client'],
  ['intake-form', 'apps/intake-form/build-e2e/client'],
];
const oversizedGzipJsonHeader = 'x-geo-e2e-decoded-json-boundary';
const jsonResponseLimitBytes = 25 * 1024 * 1024;
const appRoles = new Map([
  ['customer', 'customer'],
  ['operations', 'operator'],
  ['reports', 'analyst'],
  ['intelligence', 'reviewer'],
]);
const oversizedGzipIdentityResponses = new Map(
  [...appRoles].map(([app, role]) => {
    const decoded = Buffer.from(
      JSON.stringify({
        tenant_pub_id: 'tnt_oversized_gzip_safe',
        user_pub_id: 'usr_oversized_gzip_safe',
        role,
        permissions: ['project:read'],
        token: 'Bearer oversized-gzip-browser-canary',
        profile_path: '/secret/browser/profile/oversized-gzip-canary',
        padding: 'x'.repeat(jsonResponseLimitBytes),
      }),
    );
    const compressed = gzipSync(decoded);
    if (
      decoded.byteLength <= jsonResponseLimitBytes ||
      compressed.byteLength >= jsonResponseLimitBytes
    ) {
      throw new Error('Unable to prepare the bounded E2E JSON response.');
    }
    return [app, { compressed, decodedLength: decoded.byteLength }];
  }),
);
const snapshotRoot = mkdtempSync(join(tmpdir(), 'geo-e2e-build-'));
const snapshottedApps = apps.map(([app, root]) => {
  const snapshot = join(snapshotRoot, basename(root), app);
  cpSync(root, snapshot, { recursive: true });
  return [app, snapshot];
});
const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.map', 'application/json; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.wasm', 'application/wasm'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
]);

const servers = snapshottedApps.map(([app, root], index) => {
  const absoluteRoot = root;
  const indexFile = join(absoluteRoot, 'index.html');
  if (!existsSync(indexFile)) throw new Error(`Missing E2E SPA build: ${indexFile}`);
  const base = `/platform/${app}/`;
  const server = createServer((request, response) => {
    if (request.url === '/favicon.ico') {
      response.writeHead(204).end();
      return;
    }
    const appRole = appRoles.get(app);
    const oversizedGzipIdentity = oversizedGzipIdentityResponses.get(app);
    if (
      request.method === 'GET' &&
      request.url === '/api/v2/identity/session' &&
      appRole !== undefined &&
      request.headers[oversizedGzipJsonHeader] === appRole &&
      oversizedGzipIdentity !== undefined
    ) {
      response
        .writeHead(200, {
          'Cache-Control': 'no-store',
          'Content-Encoding': 'gzip',
          'Content-Length': String(oversizedGzipIdentity.compressed.byteLength),
          'Content-Type': 'application/json',
          'X-Geo-E2E-Decoded-Length': String(oversizedGzipIdentity.decodedLength),
        })
        .end(oversizedGzipIdentity.compressed);
      return;
    }
    if (request.url?.startsWith('/api/')) {
      const headers = { ...request.headers, host: `127.0.0.1:${apiPort}` };
      delete headers.connection;
      const upstream = httpRequest(
        {
          hostname: '127.0.0.1',
          port: apiPort,
          path: request.url,
          method: request.method,
          headers,
        },
        (upstreamResponse) => {
          response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
          upstreamResponse.pipe(response);
        },
      );
      upstream.on('error', () => response.writeHead(502).end());
      request.pipe(upstream);
      return;
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.writeHead(405, { Allow: 'GET, HEAD' }).end();
      return;
    }
    let pathname;
    try {
      pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://localhost').pathname);
    } catch {
      response.writeHead(400).end();
      return;
    }
    if (!pathname.startsWith(base)) {
      response.writeHead(404).end();
      return;
    }
    const relative = normalize(pathname.slice(base.length)).replace(/^(\.\.(\/|\\|$))+/, '');
    let file = join(absoluteRoot, relative);
    if (!existsSync(file) || !statSync(file).isFile()) file = indexFile;
    const headers = {
      'Cache-Control': 'no-store',
      'Content-Type': contentTypes.get(extname(file)) ?? 'application/octet-stream',
    };
    response.writeHead(200, headers);
    if (request.method === 'HEAD') response.end();
    else createReadStream(file).pipe(response);
  });
  server.listen(portBase + index + 1, '127.0.0.1');
  return server;
});

const shutdown = () => {
  let remaining = servers.length;
  for (const server of servers) {
    server.close(() => {
      remaining -= 1;
      if (remaining === 0) {
        rmSync(snapshotRoot, { force: true, recursive: true });
        process.exit(0);
      }
    });
  }
  setTimeout(() => process.exit(1), 2_000).unref();
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
