import {
  base64url,
  canonicalJson,
  sha256Hex,
  terminalErrorCode,
  terminalResultPayload,
  validatePairingBundle,
  validateDeviceLabel,
  validateStoredTerminalTask,
  validateTerminalResultView,
  verifyTask,
} from './protocol.mjs';

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const DATABASE = 'geo-customer-terminal';
const KEY_STORE = 'device-keys';
const MAX_RESPONSE_BYTES = 64 * 1024;

function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(KEY_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('device_key_store_unavailable'));
  });
}

async function deviceKey() {
  const db = await database();
  const existing = await new Promise((resolve, reject) => {
    const request = db.transaction(KEY_STORE).objectStore(KEY_STORE).get('primary');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('device_key_read_failed'));
  });
  if (existing) return existing;
  const generated = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
  await new Promise((resolve, reject) => {
    const request = db
      .transaction(KEY_STORE, 'readwrite')
      .objectStore(KEY_STORE)
      .put(generated, 'primary');
    request.onsuccess = () => resolve();
    request.onerror = () => reject(new Error('device_key_write_failed'));
  });
  return generated;
}

async function boundedResponseText(response) {
  if (!response.body) return '';
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    length += value.byteLength;
    if (length > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new Error('terminal_response_too_large');
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return decoder.decode(bytes);
}

export async function strictFetch(url, options) {
  const headers = new Headers(options?.headers);
  headers.set('Accept', 'application/json');
  const response = await fetch(url, {
    ...options,
    headers,
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  });
  const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase();
  if (mediaType !== 'application/json') {
    await response.body?.cancel();
    throw new Error('terminal_response_invalid');
  }
  const declaredLength = Number(response.headers.get('content-length'));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) {
    throw new Error('terminal_response_too_large');
  }
  const raw = await boundedResponseText(response);
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new Error('terminal_response_invalid');
  }
  if (!response.ok) throw new Error(`terminal_http_${response.status}`);
  if (body === null || typeof body !== 'object' || Array.isArray(body))
    throw new Error('terminal_response_invalid');
  return body;
}

async function pair(message) {
  const bundle = validatePairingBundle(message.bundle);
  const deviceLabel = validateDeviceLabel(message.deviceLabel);
  const key = await deviceKey();
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', key.publicKey));
  const tokenHash = await sha256Hex(bundle.pairing_token);
  const proof = canonicalJson({
    intervention_pub_id: bundle.intervention_pub_id,
    pairing_token_sha256: tokenHash,
    purpose: 'geo-terminal-bind',
    tenant_pub_id: bundle.tenant_pub_id,
    version: 1,
  });
  const proofSignature = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key.privateKey, encoder.encode(proof)),
  );
  const task = await strictFetch(
    `${bundle.api_base}/api/v2/terminal/interventions/${encodeURIComponent(
      bundle.intervention_pub_id,
    )}/bind`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': bundle.tenant_pub_id },
      body: JSON.stringify({
        pairing_token: bundle.pairing_token,
        device_label: deviceLabel,
        device_public_key: base64url(publicKey),
        proof_signature: base64url(proofSignature),
      }),
    },
  );
  const verified = await verifyTask(task, {
    expectedFingerprint: bundle.server_public_key_sha256,
    expectedIntervention: bundle.intervention_pub_id,
    expectedDevice: task.device_binding_pub_id,
  });
  const storedTask = {
    api_base: bundle.api_base,
    tenant_pub_id: bundle.tenant_pub_id,
    task_pub_id: task.task_pub_id,
    payload: verified.payload,
    payload_sha256: verified.payload_sha256,
  };
  await chrome.storage.session.set({ terminalTask: storedTask });
  return { payload: verified.payload };
}

async function storedTask() {
  const { terminalTask } = await chrome.storage.session.get('terminalTask');
  if (!terminalTask) return { state: 'none', task: null };
  try {
    return { state: 'ready', task: validateStoredTerminalTask(terminalTask) };
  } catch (error) {
    await chrome.storage.session.remove('terminalTask');
    return {
      state: terminalErrorCode(error) === 'terminal_task_expired' ? 'expired' : 'none',
      task: null,
    };
  }
}

async function resume() {
  const stored = await storedTask();
  return { state: stored.state, payload: stored.task?.payload ?? null };
}

async function submitResult(result) {
  const stored = await storedTask();
  if (!stored.task) {
    throw new Error(stored.state === 'expired' ? 'terminal_task_expired' : 'terminal_task_invalid');
  }
  const task = stored.task;
  const localReceipt = canonicalJson({
    challenge_type: task.payload.challenge_type,
    observed_at: new Date().toISOString(),
    origin: `https://${task.payload.allowed_domain}`,
    outcome: result,
    task_pub_id: task.task_pub_id,
  });
  const evidenceHash = await sha256Hex(localReceipt);
  const payload = terminalResultPayload(task, evidenceHash, result);
  const key = await deviceKey();
  const signature = new Uint8Array(
    await crypto.subtle.sign('Ed25519', key.privateKey, encoder.encode(canonicalJson(payload))),
  );
  const response = await strictFetch(
    `${task.api_base}/api/v2/terminal/tasks/${encodeURIComponent(task.task_pub_id)}/complete`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': task.tenant_pub_id },
      body: JSON.stringify({
        result: payload.result,
        evidence_hash: payload.evidence_hash,
        terminal_signature: base64url(signature),
      }),
    },
  );
  validateTerminalResultView(response, task.task_pub_id, result);
  await chrome.storage.session.remove('terminalTask');
  return { state: 'submitted' };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (
    sender.id !== chrome.runtime.id ||
    !sender.url?.startsWith(`chrome-extension://${chrome.runtime.id}/`)
  ) {
    sendResponse({ ok: false, error: 'terminal_channel_invalid' });
    return false;
  }
  const operation =
    message?.type === 'pair'
      ? pair(message)
      : message?.type === 'resume'
        ? resume()
        : message?.type === 'complete'
          ? submitResult('challenge_completed')
          : message?.type === 'fail'
            ? submitResult('failed')
            : message?.type === 'reject'
              ? submitResult('rejected')
              : null;
  if (!operation) return false;
  operation
    .then((value) => sendResponse({ ok: true, value }))
    .catch((error) => {
      sendResponse({ ok: false, error: terminalErrorCode(error) });
    });
  return true;
});
