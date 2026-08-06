import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import test from 'node:test';
import { gzipSync } from 'node:zlib';

globalThis.chrome = {
  runtime: {
    id: 'runtime-test-extension',
    onMessage: { addListener() {} },
  },
};

const nativeFetch = globalThis.fetch;
const { strictFetch } = await import('./background.mjs');

test.afterEach(() => {
  globalThis.fetch = nativeFetch;
});

test('accepts only bounded application/json responses with hardened request options', async () => {
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({ state: 'ready' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
  };

  assert.deepEqual(
    await strictFetch('https://api.example/api/v2/terminal/task', {
      method: 'POST',
      headers: { 'X-Tenant-Id': 'tnt_test' },
      body: '{}',
    }),
    { state: 'ready' },
  );
  assert.equal(request.url, 'https://api.example/api/v2/terminal/task');
  assert.equal(request.options.headers.get('Accept'), 'application/json');
  assert.equal(request.options.headers.get('X-Tenant-Id'), 'tnt_test');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.credentials, 'omit');
  assert.equal(request.options.redirect, 'error');
  assert.equal(request.options.referrerPolicy, 'no-referrer');
});

test('rejects JSON-shaped bodies declared as a non-contract media type', async () => {
  for (const contentType of [null, 'text/html', 'application/problem+json']) {
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ task_pub_id: 'ttk_mime_confusion' }), {
        status: 200,
        headers: contentType ? { 'Content-Type': contentType } : {},
      });
    await assert.rejects(
      strictFetch('https://api.example/api/v2/terminal/task', { method: 'POST' }),
      /terminal_response_invalid/,
    );
  }
});

test('preserves safe HTTP status errors and rejects oversized JSON before parsing', async () => {
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ error: { code: 'gone' } }), {
      status: 410,
      headers: { 'Content-Type': 'application/json' },
    });
  await assert.rejects(
    strictFetch('https://api.example/api/v2/terminal/task', { method: 'POST' }),
    /terminal_http_410/,
  );

  globalThis.fetch = async () =>
    new Response('{}', {
      status: 200,
      headers: {
        'Content-Length': String(64 * 1024 + 1),
        'Content-Type': 'application/json',
      },
    });
  await assert.rejects(
    strictFetch('https://api.example/api/v2/terminal/task', { method: 'POST' }),
    /terminal_response_too_large/,
  );
});

test('bounds a genuinely gzip-compressed response by decoded bytes before parsing', async () => {
  const decoded = Buffer.from(
    JSON.stringify({
      state: 'ready',
      token: 'Bearer terminal-gzip-boundary-canary',
      padding: 'x'.repeat(64 * 1024),
    }),
  );
  const compressed = gzipSync(decoded);
  assert.ok(decoded.byteLength > 64 * 1024);
  assert.ok(compressed.byteLength < 64 * 1024);

  let requestCount = 0;
  const server = createServer((_request, response) => {
    requestCount += 1;
    response
      .writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Encoding': 'gzip',
        'Content-Length': String(compressed.byteLength),
        'Content-Type': 'application/json',
      })
      .end(compressed);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  assert.ok(address && typeof address === 'object');

  try {
    await assert.rejects(
      strictFetch(`http://127.0.0.1:${address.port}/api/v2/terminal/task`, {
        method: 'POST',
      }),
      (error) => {
        assert.equal(error.message, 'terminal_response_too_large');
        assert.doesNotMatch(error.message, /terminal-gzip-boundary-canary|Bearer/i);
        return true;
      },
    );
    assert.equal(requestCount, 1);
  } finally {
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
});
