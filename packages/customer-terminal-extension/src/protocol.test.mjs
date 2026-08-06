import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.atob = (value) => Buffer.from(value, 'base64').toString('binary');
globalThis.btoa = (value) => Buffer.from(value, 'binary').toString('base64');

const {
  base64url,
  canonicalJson,
  sha256Hex,
  terminalErrorCode,
  terminalResultPayload,
  validatePairingBundle,
  validateDeviceLabel,
  validateStoredTerminalTask,
  validateTerminalResultView,
  validateTaskProjection,
  verifyTask,
} = await import('./protocol.mjs');

test('verifies a pinned, signed and scoped terminal task', async () => {
  const key = await crypto.subtle.generateKey({ name: 'Ed25519' }, false, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', key.publicKey));
  const now = Date.now();
  const payload = {
    account_pub_id: 'pac_test',
    action: 'read',
    allowed_domain: 'platform.example',
    challenge_type: 'passkey',
    device_binding_pub_id: 'dev_test',
    expires_at: new Date(now + 120_000).toISOString(),
    intervention_pub_id: 'int_test',
    nonce: 'nonce-with-safe-length',
    task_pub_id: 'ttk_test',
    version: 1,
    cookie: 'server-must-not-retain-this',
    profile_path: '/server/must/not/retain',
  };
  const signature = new Uint8Array(
    await crypto.subtle.sign(
      'Ed25519',
      key.privateKey,
      new TextEncoder().encode(canonicalJson(payload)),
    ),
  );
  const task = {
    task_pub_id: payload.task_pub_id,
    device_binding_pub_id: payload.device_binding_pub_id,
    payload,
    server_signature: base64url(signature),
    server_public_key: base64url(publicKey),
    expires_at: payload.expires_at,
  };
  const verified = await verifyTask(task, {
    expectedFingerprint: await sha256Hex(publicKey),
    expectedIntervention: 'int_test',
    expectedDevice: 'dev_test',
    now,
  });
  assert.deepEqual(verified.payload, {
    action: 'read',
    allowed_domain: 'platform.example',
    challenge_type: 'passkey',
    expires_at: payload.expires_at,
    task_pub_id: 'ttk_test',
    version: 1,
  });
  assert.equal(verified.payload_sha256, await sha256Hex(canonicalJson(payload)));
  assert.equal('cookie' in verified.payload, false);
  assert.equal('profile_path' in verified.payload, false);
  await assert.rejects(
    verifyTask(task, {
      expectedFingerprint: '0'.repeat(64),
      expectedIntervention: 'int_test',
      expectedDevice: 'dev_test',
      now,
    }),
    /pin_mismatch/,
  );
  const expected = {
    expectedFingerprint: await sha256Hex(publicKey),
    expectedIntervention: 'int_test',
    expectedDevice: 'dev_test',
    now,
  };
  await assert.rejects(
    verifyTask({ ...task, cookie: 'must-not-survive' }, expected),
    /terminal_task_invalid/,
  );
  await assert.rejects(
    verifyTask({ ...task, expires_at: new Date(now + 120_000).toUTCString() }, expected),
    /terminal_task_invalid/,
  );
  await assert.rejects(
    verifyTask({ ...task, expires_at: new Date(now + 180_000).toISOString() }, expected),
    /terminal_task_scope_invalid/,
  );
});

test('rejects unsafe bundle/task values and emits only enumerated terminal results', async () => {
  assert.throws(
    () =>
      validatePairingBundle({
        api_base: 'http://platform.example/',
        tenant_pub_id: 'tnt_test',
        intervention_pub_id: 'int_test',
        pairing_token: 'secret',
        server_public_key_sha256: 'a'.repeat(64),
      }),
    /api_base/,
  );
  assert.deepEqual(
    terminalResultPayload(
      { task_pub_id: 'ttk_test', payload_sha256: 'b'.repeat(64) },
      'a'.repeat(64),
    ),
    {
      evidence_hash: 'a'.repeat(64),
      result: 'challenge_completed',
      task_payload_sha256: 'b'.repeat(64),
      task_pub_id: 'ttk_test',
      version: 1,
    },
  );
  assert.deepEqual(
    terminalResultPayload(
      { task_pub_id: 'ttk_test', payload_sha256: 'b'.repeat(64) },
      'a'.repeat(64),
      'rejected',
    ),
    {
      evidence_hash: 'a'.repeat(64),
      result: 'rejected',
      task_payload_sha256: 'b'.repeat(64),
      task_pub_id: 'ttk_test',
      version: 1,
    },
  );
  assert.throws(
    () =>
      terminalResultPayload(
        { task_pub_id: 'ttk_test', payload_sha256: 'b'.repeat(64) },
        'a'.repeat(64),
        'verified',
      ),
    /terminal_result_invalid/,
  );
});

test('projects pairing data, rejects sensitive labels and collapses unsafe errors', () => {
  const projected = validatePairingBundle({
    action: 'query',
    allowed_domain: 'platform.example',
    api_base: 'https://api.example/',
    challenge_type: 'push',
    intervention_pub_id: 'int_test',
    pairing_token: 'a'.repeat(43),
    server_public_key_sha256: 'b'.repeat(64),
    tenant_pub_id: 'tnt_test',
    cookie: 'must-not-survive',
    profile_path: '/must/not/survive',
  });
  assert.deepEqual(projected, {
    action: 'query',
    allowed_domain: 'platform.example',
    api_base: 'https://api.example',
    challenge_type: 'push',
    intervention_pub_id: 'int_test',
    pairing_token: 'a'.repeat(43),
    server_public_key_sha256: 'b'.repeat(64),
    tenant_pub_id: 'tnt_test',
  });
  assert.equal(validateDeviceLabel('Customer browser'), 'Customer browser');
  assert.throws(() => validateDeviceLabel('OTP 394820'), /device_label_invalid/);
  assert.throws(() => validateDeviceLabel('13800138000'), /device_label_invalid/);
  assert.equal(terminalErrorCode(new Error('Bearer top-secret')), 'terminal_error');
  assert.equal(terminalErrorCode(new Error('terminal_http_410')), 'terminal_http_410');
});

test('restores only an exact unexpired terminal task projection', () => {
  const now = Date.now();
  const payload = {
    action: 'query',
    allowed_domain: 'platform.example',
    challenge_type: 'passkey',
    expires_at: new Date(now + 120_000).toISOString(),
    task_pub_id: 'ttk_resume',
    version: 1,
  };
  const stored = {
    api_base: 'https://api.example',
    payload,
    payload_sha256: 'a'.repeat(64),
    task_pub_id: 'ttk_resume',
    tenant_pub_id: 'tnt_resume',
  };
  assert.deepEqual(validateTaskProjection(payload, now), payload);
  assert.deepEqual(validateStoredTerminalTask(stored, now), stored);
  assert.throws(
    () =>
      validateStoredTerminalTask(
        {
          ...stored,
          cookie: 'must-not-survive',
        },
        now,
      ),
    /terminal_task_invalid/,
  );
  assert.throws(
    () =>
      validateStoredTerminalTask(
        {
          ...stored,
          payload: { ...payload, profile_path: '/must/not/survive' },
        },
        now,
      ),
    /terminal_task_invalid/,
  );
  assert.throws(
    () =>
      validateStoredTerminalTask(
        {
          ...stored,
          payload: { ...payload, expires_at: new Date(now - 1).toISOString() },
        },
        now,
      ),
    /terminal_task_expired/,
  );
  assert.throws(
    () =>
      validateStoredTerminalTask(
        {
          ...stored,
          payload: { ...payload, expires_at: new Date(now + 120_000).toUTCString() },
        },
        now,
      ),
    /terminal_task_invalid/,
  );
});

test('accepts only an exact task-bound and result-bound terminal write receipt', () => {
  const receipt = {
    completed_at: '2026-07-26T14:00:00.123456Z',
    intervention_pub_id: 'int_receipt',
    platform_result: 'rejected',
    state: 'completed',
    task_pub_id: 'ttk_receipt',
  };
  assert.deepEqual(validateTerminalResultView(receipt, 'ttk_receipt', 'rejected'), {
    completed_at: receipt.completed_at,
    platform_result: 'rejected',
    state: 'completed',
    task_pub_id: 'ttk_receipt',
  });
  for (const unsafe of [
    { ...receipt, task_pub_id: 'ttk_other' },
    { ...receipt, platform_result: 'failed' },
    { ...receipt, state: 'rejected' },
    { ...receipt, completed_at: '2026-02-31T14:00:00Z' },
    { ...receipt, cookie: 'must-not-survive' },
  ]) {
    assert.throws(
      () => validateTerminalResultView(unsafe, 'ttk_receipt', 'rejected'),
      /terminal_result_invalid/,
    );
  }
});
