import { spawn } from 'node:child_process';
import { createServer } from 'node:https';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { request as httpRequest } from 'node:http';
import AxeBuilder from '@axe-core/playwright';
import { chromium } from '@playwright/test';

const root = resolve(import.meta.dirname, '..');
const evidencePath = join(root, 'tests/s04-evidence/customer-terminal-extension-runtime.json');
const screenshotPath = join(root, 'tests/visual-evidence/s03/customer-terminal-resumed-task.png');
const apiPort = 18021;
const tlsPort = 18443;
const apiHttp = `http://127.0.0.1:${apiPort}`;
const apiHttps = `https://127.0.0.1:${tlsPort}`;
const workspace = await mkdtemp(join(tmpdir(), 'geo-terminal-runtime-'));
const children = [];
const terminalRequests = [];
let proxy;
let context;

function run(command, args, { input, ...options } = {}) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      env: process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
      ...options,
    });
    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (chunk) => (stdout += chunk));
    child.stderr?.on('data', (chunk) => (stderr += chunk));
    child.stdin?.end(input);
    child.once('error', reject);
    child.once('exit', (code) => {
      if (code === 0) resolveRun({ stdout, stderr });
      else reject(new Error(`${command}_failed_${code}: ${stderr.slice(-1000)}`));
    });
  });
}

async function waitForApi(child) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null) throw new Error('development_api_exited');
    try {
      const response = await fetch(`${apiHttp}/openapi.json`);
      if (response.ok) return;
    } catch {
      // The process is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error('development_api_start_timeout');
}

async function api(path, { method = 'GET', headers = {}, body } = {}) {
  const response = await fetch(`${apiHttp}${path}`, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(`api_${response.status}_${payload?.error?.code ?? 'failed'}`);
  return payload;
}

function adminHeaders(tenant, actor, suffix) {
  return {
    'X-Tenant-Id': tenant,
    'X-Actor-Id': actor,
    'X-Actor-Role': 'admin',
    'Idempotency-Key': `terminal-runtime-${suffix}-${crypto.randomUUID()}`,
  };
}

async function indexedKeyFacts(worker) {
  return worker.evaluate(async () => {
    const database = await new Promise((resolveDb, reject) => {
      const request = indexedDB.open('geo-customer-terminal', 1);
      request.onsuccess = () => resolveDb(request.result);
      request.onerror = () => reject(new Error('indexeddb_open_failed'));
    });
    const pair = await new Promise((resolveKey, reject) => {
      const request = database.transaction('device-keys').objectStore('device-keys').get('primary');
      request.onsuccess = () => resolveKey(request.result);
      request.onerror = () => reject(new Error('indexeddb_read_failed'));
    });
    if (!pair) return { stored: false };
    let privateExportRejected = false;
    try {
      await crypto.subtle.exportKey('pkcs8', pair.privateKey);
    } catch {
      privateExportRejected = true;
    }
    const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey));
    return {
      stored: true,
      algorithm: pair.privateKey.algorithm.name,
      private_extractable: pair.privateKey.extractable,
      private_export_rejected: privateExportRejected,
      public_fingerprint: [...digest].map((value) => value.toString(16).padStart(2, '0')).join(''),
    };
  });
}

function deriveExtensionId(publicKey) {
  const digest = createHash('sha256')
    .update(Buffer.from(publicKey, 'base64'))
    .digest('hex')
    .slice(0, 32);
  return [...digest]
    .map((value) => String.fromCharCode('a'.charCodeAt(0) + Number.parseInt(value, 16)))
    .join('');
}

async function axePasses(page) {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  if (result.violations.length > 0) {
    throw new Error(
      `terminal_popup_accessibility_failed: ${result.violations
        .map((violation) => violation.id)
        .join(',')}`,
    );
  }
  return true;
}

async function acceptFocusedBrowserPrompt() {
  const source = `
import ctypes, time
x11 = ctypes.CDLL("libX11.so")
xtst = ctypes.CDLL("libXtst.so")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XFlush.argtypes = [ctypes.c_void_p]
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
xtst.XTestFakeKeyEvent.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong
]
display = x11.XOpenDisplay(None)
if not display:
    raise SystemExit("x_display_unavailable")
time.sleep(0.8)
xtst.XTestFakeKeyEvent(display, 23, True, 0)
xtst.XTestFakeKeyEvent(display, 23, False, 0)
xtst.XTestFakeKeyEvent(display, 36, True, 0)
xtst.XTestFakeKeyEvent(display, 36, False, 0)
x11.XFlush(display)
x11.XCloseDisplay(display)
`;
  await run('python3', ['-c', source]);
}

async function terminalQrImages(bundle, label) {
  const pairingQrPath = join(workspace, `${label}-pairing-qr.png`);
  const multipleQrPath = join(workspace, `${label}-multiple-pairing-qr.png`);
  const source = `
import cv2, numpy, sys
payload = sys.stdin.read()
parameters = cv2.QRCodeEncoder_Params()
parameters.version = 15
parameters.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_L
parameters.mode = cv2.QRCodeEncoder_MODE_BYTE
image = cv2.QRCodeEncoder_create(parameters).encode(payload)
image = cv2.resize(image, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
image = cv2.copyMakeBorder(image, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
if not cv2.imwrite(sys.argv[1], image):
    raise SystemExit("qr_write_failed")
gutter = numpy.full((image.shape[0], 64), 255, dtype=numpy.uint8)
multiple = numpy.concatenate((image, gutter, image), axis=1)
if not cv2.imwrite(sys.argv[2], multiple):
    raise SystemExit("multiple_qr_write_failed")
`;
  await run('python3', ['-c', source, pairingQrPath, multipleQrPath], {
    input: JSON.stringify(bundle),
  });
  const result = {
    pairing: await readFile(pairingQrPath),
    multiple: await readFile(multipleQrPath),
  };
  await Promise.all([rm(pairingQrPath), rm(multipleQrPath)]);
  return result;
}

try {
  await run('pnpm', ['--filter', '@geo/customer-terminal-extension', 'build']);
  const keyPath = join(workspace, 'tls.key');
  const certPath = join(workspace, 'tls.crt');
  await run('openssl', [
    'req',
    '-x509',
    '-newkey',
    'rsa:2048',
    '-nodes',
    '-days',
    '1',
    '-subj',
    '/CN=127.0.0.1',
    '-addext',
    'subjectAltName=IP:127.0.0.1',
    '-keyout',
    keyPath,
    '-out',
    certPath,
  ]);

  const apiProcess = spawn(
    join(root, '.venv/bin/uvicorn'),
    ['geo_platform.main:app', '--host', '127.0.0.1', '--port', String(apiPort)],
    {
      cwd: join(root, 'api'),
      env: {
        ...process.env,
        GEO_ENV: 'development',
        GEO_IDENTITY_MODE: 'trusted_headers',
        GEO_BOOTSTRAP_SECRET: 'development-bootstrap',
      },
      stdio: ['ignore', 'ignore', 'ignore'],
    },
  );
  children.push(apiProcess);
  await waitForApi(apiProcess);

  proxy = createServer(
    { key: await readFile(keyPath), cert: await readFile(certPath) },
    (incoming, outgoing) => {
      if (incoming.method === 'POST' && incoming.url?.startsWith('/api/v2/terminal/')) {
        terminalRequests.push({
          accept: incoming.headers.accept ?? null,
          content_type: incoming.headers['content-type'] ?? null,
          cookie_present: incoming.headers.cookie !== undefined,
          path: incoming.url,
          referer_present: incoming.headers.referer !== undefined,
        });
      }
      const upstream = httpRequest(
        {
          hostname: '127.0.0.1',
          port: apiPort,
          path: incoming.url,
          method: incoming.method,
          headers: incoming.headers,
        },
        (response) => {
          outgoing.writeHead(response.statusCode ?? 502, response.headers);
          response.pipe(outgoing);
        },
      );
      upstream.on('error', () => {
        if (!outgoing.headersSent) outgoing.writeHead(502);
        outgoing.end();
      });
      incoming.pipe(upstream);
    },
  );
  await new Promise((resolveListen, reject) => {
    proxy.once('error', reject);
    proxy.listen(tlsPort, '127.0.0.1', resolveListen);
  });

  const actor = `terminal-runtime-${crypto.randomUUID()}`;
  const bootstrap = await api('/api/v2/identity/bootstrap', {
    method: 'POST',
    headers: { 'X-Bootstrap-Secret': 'development-bootstrap' },
    body: { tenant_name: actor, subject: actor, display_name: 'Runtime verifier' },
  });
  const tenant = bootstrap.tenant_pub_id;
  const account = await api('/api/v2/platform-accounts', {
    method: 'POST',
    headers: adminHeaders(tenant, actor, 'account'),
    body: {
      platform_slug: 'runtime-fixture',
      platform_name: 'Runtime fixture',
      account_mask: 'runtime-***',
      owner_pub_id: 'own_runtime_fixture',
      purpose: 'measure',
      responsible_pub_id: 'usr_runtime_fixture',
      custody_mode: 'customer_device',
      region: 'CN-BJ',
    },
  });
  const authorizationNow = Date.now();
  await api(`/api/v2/platform-accounts/${account.pub_id}/authorizations`, {
    method: 'POST',
    headers: adminHeaders(tenant, actor, 'authorization'),
    body: {
      forbidden_actions: [],
      regions: ['CN-BJ'],
      scopes: ['query'],
      valid_from: new Date(authorizationNow - 60_000).toISOString(),
      valid_until: new Date(authorizationNow + 15 * 60_000).toISOString(),
    },
  });
  const issueTerminalBundle = async (suffix) => {
    const intervention = await api(`/api/v2/platform-accounts/${account.pub_id}/interventions`, {
      method: 'POST',
      headers: adminHeaders(tenant, actor, `${suffix}-intervention`),
      body: { challenge_type: 'passkey', allowed_domain: '127.0.0.1', action: 'query' },
    });
    const pairing = await api(`/api/v2/interventions/${intervention.pub_id}/pair`, {
      method: 'POST',
      headers: adminHeaders(tenant, actor, `${suffix}-pair`),
    });
    return {
      intervention,
      pairing,
      bundle: {
        api_base: apiHttps,
        tenant_pub_id: tenant,
        intervention_pub_id: intervention.pub_id,
        pairing_token: pairing.pairing_token,
        server_public_key_sha256: pairing.server_public_key_sha256,
        allowed_domain: pairing.allowed_domain,
        action: pairing.action,
        challenge_type: pairing.challenge_type,
      },
    };
  };
  const initialTask = await issueTerminalBundle('initial');
  const { intervention, pairing, bundle } = initialTask;

  const extensionPath = join(root, 'packages/customer-terminal-extension/dist');
  const extensionManifest = JSON.parse(
    await readFile(join(extensionPath, 'manifest.json'), 'utf8'),
  );
  const expectedExtensionId = deriveExtensionId(extensionManifest.key);
  const initialQrImages = await terminalQrImages(bundle, 'runtime-initial');
  const pairingQr = initialQrImages.pairing;
  const multipleQr = initialQrImages.multiple;
  const profilePath = join(workspace, 'chromium-profile');
  const launchBrowser = () =>
    chromium.launchPersistentContext(profilePath, {
      headless: false,
      ignoreHTTPSErrors: true,
      args: [
        `--disable-extensions-except=${extensionPath}`,
        `--load-extension=${extensionPath}`,
        '--no-first-run',
        '--disable-default-apps',
        '--ignore-certificate-errors',
      ],
    });
  context = await launchBrowser();
  let worker = context.serviceWorkers()[0];
  if (!worker) worker = await context.waitForEvent('serviceworker');
  const extensionId = new URL(worker.url()).host;
  let popup = await context.newPage();
  await popup.setViewportSize({ width: 390, height: 844 });
  await popup.addInitScript(() => {
    Object.defineProperty(globalThis, 'BarcodeDetector', {
      configurable: true,
      value: undefined,
    });
  });
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  const initialA11yPassed = await axePasses(popup);
  const initialNoOverflow = await popup.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  );
  await popup.evaluate(() => document.activeElement?.blur());
  await popup.keyboard.press('Tab');
  const initialKeyboardFocusVisible = await popup.locator('#pairing-qr').evaluate((input) => {
    const style = getComputedStyle(input);
    return document.activeElement === input && Number.parseFloat(style.outlineWidth) >= 2;
  });
  await popup.locator('#pairing-qr').setInputFiles({
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
      'base64',
    ),
    mimeType: 'image/png',
    name: 'runtime-empty.png',
  });
  await popup
    .locator('#status')
    .filter({ hasText: '未识别到唯一有效的 GEO 配对二维码' })
    .waitFor({ timeout: 5_000 });
  const emptyQrFailedClosed =
    (await popup.locator('#pair').isDisabled()) &&
    (await popup.locator('#pairing-qr').evaluate((input) => input.files?.length ?? 0)) === 0;
  await popup.locator('#pairing-qr').setInputFiles({
    buffer: multipleQr,
    mimeType: 'image/png',
    name: 'runtime-multiple-pairing-qr.png',
  });
  multipleQr.fill(0);
  await popup
    .locator('#status')
    .filter({ hasText: '未识别到唯一有效的 GEO 配对二维码' })
    .waitFor({ timeout: 5_000 });
  const multipleQrFailedClosed =
    (await popup.locator('#pair').isDisabled()) &&
    (await popup.locator('#pairing-qr').evaluate((input) => input.files?.length ?? 0)) === 0 &&
    !(await popup.locator('body').textContent())?.includes(pairing.pairing_token);
  await popup.locator('#pairing-qr').setInputFiles({
    buffer: pairingQr,
    mimeType: 'image/png',
    name: 'runtime-pairing-qr.png',
  });
  pairingQr.fill(0);
  try {
    await popup
      .locator('#status')
      .filter({ hasText: '配对范围已读取' })
      .waitFor({ timeout: 5_000 });
  } catch (error) {
    const visibleStatus = await popup.locator('#status').textContent();
    throw new Error(`pairing_qr_ui_failed: ${visibleStatus}`, { cause: error });
  }
  const safePairingDom =
    (await popup.locator('textarea').count()) === 0 &&
    (await popup.locator('#pairing-qr').evaluate((input) => input.files?.length ?? 0)) === 0 &&
    !(await popup.locator('body').textContent())?.includes(pairing.pairing_token);
  await popup.locator('#label').fill('Chromium runtime fixture');
  await popup.locator('#pair').click();
  await acceptFocusedBrowserPrompt();
  try {
    await popup
      .locator('#status')
      .filter({ hasText: '任务签名与作用域已验证' })
      .waitFor({ timeout: 15_000 });
  } catch (error) {
    const visibleStatus = await popup.locator('#status').textContent();
    throw new Error(`pairing_ui_failed: ${visibleStatus}`, { cause: error });
  }
  const firstKey = await indexedKeyFacts(worker);
  const storedTaskProjection = await worker.evaluate(async () => {
    const { terminalTask } = await chrome.storage.session.get('terminalTask');
    return {
      root_keys: Object.keys(terminalTask ?? {}).sort(),
      payload_keys: Object.keys(terminalTask?.payload ?? {}).sort(),
      serialized: JSON.stringify(terminalTask ?? {}),
    };
  });
  const safeStoredTask =
    JSON.stringify(storedTaskProjection.root_keys) ===
      JSON.stringify(['api_base', 'payload', 'payload_sha256', 'task_pub_id', 'tenant_pub_id']) &&
    JSON.stringify(storedTaskProjection.payload_keys) ===
      JSON.stringify([
        'action',
        'allowed_domain',
        'challenge_type',
        'expires_at',
        'task_pub_id',
        'version',
      ]) &&
    !/(?:pairing_token|cookie|otp|nonce|profile_path|account_pub_id)/i.test(
      storedTaskProjection.serialized,
    );

  await popup.close();
  popup = await context.newPage();
  await popup.setViewportSize({ width: 390, height: 844 });
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup
    .locator('#status')
    .filter({ hasText: '已恢复待完成任务' })
    .waitFor({ timeout: 5_000 });
  const resumedPopupSafe =
    (await popup.locator('#complete').isEnabled()) &&
    (await popup.locator('#fail').isEnabled()) &&
    (await popup.locator('#reject').isEnabled()) &&
    (await popup.locator('#pairing-qr').isDisabled()) &&
    (await popup.locator('#label').isDisabled()) &&
    (await popup.locator('#pairing-qr').evaluate((input) => input.files?.length ?? 0)) === 0 &&
    !(await popup.locator('body').textContent())?.includes(pairing.pairing_token);
  const resumedA11yPassed = await axePasses(popup);
  const resumedNoOverflow = await popup.evaluate(
    () => document.documentElement.scrollWidth <= innerWidth,
  );
  await popup.evaluate(() => document.activeElement?.blur());
  await popup.keyboard.press('Tab');
  const resumedKeyboardFocusVisible = await popup.locator('#complete').evaluate((button) => {
    const style = getComputedStyle(button);
    return document.activeElement === button && Number.parseFloat(style.outlineWidth) >= 2;
  });
  await mkdir(join(root, 'tests/visual-evidence/s03'), { recursive: true });
  await popup.screenshot({ path: screenshotPath, fullPage: true });
  const screenshotSha256 = createHash('sha256')
    .update(await readFile(screenshotPath))
    .digest('hex');

  await popup.locator('#reject').click();
  await popup.locator('#status').filter({ hasText: '本次任务已拒绝' }).waitFor({ timeout: 15_000 });
  let views = await api('/api/v2/interventions', {
    headers: adminHeaders(tenant, actor, 'read'),
  });
  const rejectedView = views.find((candidate) => candidate.pub_id === intervention.pub_id);
  if (!rejectedView) throw new Error('rejected_intervention_not_found');

  const pairAndRestoreTask = async (issued, label) => {
    const qrImages = await terminalQrImages(issued.bundle, label);
    const qr = qrImages.pairing;
    qrImages.multiple.fill(0);
    await popup.locator('#pairing-qr').setInputFiles({
      buffer: qr,
      mimeType: 'image/png',
      name: `${label}-pairing-qr.png`,
    });
    qr.fill(0);
    await popup.locator('#status').filter({ hasText: '配对范围已读取' }).waitFor({
      timeout: 5_000,
    });
    await popup.locator('#pair').click();
    await popup
      .locator('#status')
      .filter({ hasText: '任务签名与作用域已验证' })
      .waitFor({ timeout: 15_000 });
    await popup.close();
    popup = await context.newPage();
    await popup.setViewportSize({ width: 390, height: 844 });
    await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    await popup
      .locator('#status')
      .filter({ hasText: '已恢复待完成任务' })
      .waitFor({ timeout: 5_000 });
    if ((await popup.locator('body').textContent())?.includes(issued.pairing.pairing_token)) {
      throw new Error('restored_pairing_token_exposed');
    }
  };

  const failedTask = await issueTerminalBundle('failure');
  await pairAndRestoreTask(failedTask, 'runtime-failure');
  await popup.locator('#fail').click();
  await popup
    .locator('#status')
    .filter({ hasText: '原生验证失败结果已签名提交' })
    .waitFor({ timeout: 15_000 });
  views = await api('/api/v2/interventions', {
    headers: adminHeaders(tenant, actor, 'failure-read'),
  });
  const failedView = views.find((candidate) => candidate.pub_id === failedTask.intervention.pub_id);
  if (!failedView) throw new Error('failed_intervention_not_found');

  const completionTask = await issueTerminalBundle('completion');
  await pairAndRestoreTask(completionTask, 'runtime-completion');
  await popup.evaluate(() => document.activeElement?.blur());
  await popup.keyboard.press('Tab');
  await popup.keyboard.press('Enter');
  await popup
    .locator('#status')
    .filter({ hasText: '仍需平台回调或身份探针确认' })
    .waitFor({ timeout: 15_000 });
  views = await api('/api/v2/interventions', {
    headers: adminHeaders(tenant, actor, 'completion-read'),
  });
  const view = views.find((candidate) => candidate.pub_id === completionTask.intervention.pub_id);
  if (!view) throw new Error('completed_intervention_not_found');

  await worker.evaluate(
    async ({ apiBase, tenantPubId }) => {
      await chrome.storage.session.set({
        terminalTask: {
          api_base: apiBase,
          payload: {
            action: 'query',
            allowed_domain: '127.0.0.1',
            challenge_type: 'passkey',
            expires_at: new Date(Date.now() - 1_000).toISOString(),
            task_pub_id: 'ttk_expired_runtime',
            version: 1,
          },
          payload_sha256: 'a'.repeat(64),
          task_pub_id: 'ttk_expired_runtime',
          tenant_pub_id: tenantPubId,
        },
      });
    },
    { apiBase: apiHttps, tenantPubId: tenant },
  );
  await popup.close();
  popup = await context.newPage();
  await popup.setViewportSize({ width: 390, height: 844 });
  await popup.goto(`chrome-extension://${extensionId}/popup.html`);
  await popup
    .locator('#status')
    .filter({ hasText: '任务已过期，请重新发起配对' })
    .waitFor({ timeout: 5_000 });
  const expiredTaskRecoverySafe =
    (await popup.locator('#complete').isDisabled()) &&
    (await popup.locator('#fail').isDisabled()) &&
    (await popup.locator('#reject').isDisabled()) &&
    (await popup.locator('#pairing-qr').isEnabled()) &&
    ((await worker.evaluate(async () => chrome.storage.session.get('terminalTask'))).terminalTask ??
      null) === null &&
    !(await popup.locator('body').textContent())?.includes('ttk_expired_runtime');

  await context.close();
  context = await launchBrowser();
  worker = context.serviceWorkers()[0];
  if (!worker) worker = await context.waitForEvent('serviceworker');
  const secondKey = await indexedKeyFacts(worker);
  const keyPersisted =
    firstKey.stored &&
    secondKey.stored &&
    firstKey.public_fingerprint === secondKey.public_fingerprint;
  const hardenedTerminalRequests =
    terminalRequests.length === 6 &&
    terminalRequests.every(
      (request) =>
        request.accept === 'application/json' &&
        request.content_type === 'application/json' &&
        request.cookie_present === false &&
        request.referer_present === false,
    );

  const evidence = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    scope: 'local integration fixture; not a customer-authorized native-platform canary',
    passed:
      firstKey.algorithm === 'Ed25519' &&
      extensionId === expectedExtensionId &&
      extensionManifest.version === '0.1.6' &&
      firstKey.private_extractable === false &&
      firstKey.private_export_rejected === true &&
      initialA11yPassed &&
      initialNoOverflow &&
      initialKeyboardFocusVisible &&
      emptyQrFailedClosed &&
      multipleQrFailedClosed &&
      safePairingDom &&
      safeStoredTask &&
      resumedPopupSafe &&
      resumedA11yPassed &&
      resumedNoOverflow &&
      resumedKeyboardFocusVisible &&
      rejectedView.state === 'rejected' &&
      rejectedView.platform_result === 'rejected' &&
      failedView.state === 'failed' &&
      failedView.platform_result === 'failed' &&
      expiredTaskRecoverySafe &&
      keyPersisted &&
      hardenedTerminalRequests &&
      view.state === 'awaiting_platform_probe',
    assertions: {
      manifest_v3_loaded_in_real_chromium: true,
      stable_signed_extension_id_matched: extensionId === expectedExtensionId,
      extension_id: extensionId,
      extension_version: extensionManifest.version,
      hardened_terminal_json_requests: hardenedTerminalRequests,
      terminal_request_count: terminalRequests.length,
      initial_popup_wcag_aa: initialA11yPassed,
      initial_popup_no_horizontal_overflow_390x844: initialNoOverflow,
      initial_popup_keyboard_focus_visible: initialKeyboardFocusVisible,
      exact_optional_origins_granted_by_user_gesture: true,
      pairing_qr_decoded_without_text_capability_input: safePairingDom,
      local_jsqr_fallback_decoded_real_qr: true,
      empty_qr_failed_closed: emptyQrFailedClosed,
      multiple_qr_failed_closed: multipleQrFailedClosed,
      signed_pairing_task_verified: true,
      session_storage_allowlist_enforced: safeStoredTask,
      popup_reopen_restores_safe_task: resumedPopupSafe,
      signed_customer_rejection_submitted: rejectedView.state === 'rejected',
      rejection_platform_result: rejectedView.platform_result,
      signed_native_failure_submitted: failedView.state === 'failed',
      failure_platform_result: failedView.platform_result,
      expired_task_announced_and_removed: expiredTaskRecoverySafe,
      resumed_popup_wcag_aa: resumedA11yPassed,
      resumed_popup_no_horizontal_overflow_390x844: resumedNoOverflow,
      resumed_popup_keyboard_focus_visible: resumedKeyboardFocusVisible,
      resumed_popup_screenshot_sha256: screenshotSha256,
      device_key_algorithm: firstKey.algorithm,
      device_private_key_extractable: firstKey.private_extractable,
      device_private_key_export_rejected: firstKey.private_export_rejected,
      device_key_persisted_across_worker_restart: keyPersisted,
      signed_minimal_result_submitted: true,
      post_completion_state: view.state,
      extension_cannot_self_attest_platform_verification: view.state === 'awaiting_platform_probe',
    },
    sensitive_values_recorded: false,
  };
  if (!evidence.passed) throw new Error(`runtime_assertion_failed: ${JSON.stringify(evidence)}`);
  await writeFile(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
} finally {
  await context?.close().catch(() => {});
  await new Promise((resolveClose) => proxy?.close(resolveClose) ?? resolveClose());
  for (const child of children) {
    if (child.exitCode === null) child.kill('SIGTERM');
  }
  await rm(workspace, { recursive: true, force: true });
}
