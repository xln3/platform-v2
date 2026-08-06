const ACTIONS = new Set(['read', 'query', 'draft', 'publish']);
const CHALLENGES = new Set(['otp', 'qr', 'push', 'passkey', 'face', 'graphical']);
const TERMINAL_RESULTS = new Set(['challenge_completed', 'failed', 'expired', 'rejected']);
const HOSTNAME =
  /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/;
const PAIRING_TOKEN = /^[A-Za-z0-9_-]{32,128}$/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;
const SENSITIVE_LABEL =
  /(?:authorization|bearer|cookie|otp|one[-_ ]?time[-_ ]?password|pass(?:word|code)|pairing[-_ ]?token|profile[-_ ]?path|session[-_ ]?token)/i;
const FULL_PHONE = /(?:^|\D)(?:\+?86[- ]?)?1[3-9]\d{9}(?:\D|$)/;
const PUBLIC_ERROR_CODES = new Set([
  'api_base_invalid',
  'device_key_read_failed',
  'device_key_store_unavailable',
  'device_key_write_failed',
  'device_label_invalid',
  'device_proof_invalid',
  'evidence_hash_invalid',
  'host_permission_denied',
  'invalid_base64url',
  'pairing_bundle_invalid',
  'pairing_intervention_pub_id_invalid',
  'pairing_qr_invalid',
  'pairing_qr_not_supported',
  'pairing_scope_invalid',
  'pairing_tenant_pub_id_invalid',
  'pairing_token_invalid',
  'server_key_fingerprint_invalid',
  'server_key_pin_mismatch',
  'server_task_signature_invalid',
  'terminal_channel_invalid',
  'terminal_error',
  'terminal_response_invalid',
  'terminal_response_too_large',
  'terminal_result_invalid',
  'terminal_task_expired',
  'terminal_task_invalid',
  'terminal_task_scope_invalid',
]);
const TASK_PROJECTION_KEYS = [
  'action',
  'allowed_domain',
  'challenge_type',
  'expires_at',
  'task_pub_id',
  'version',
];
const TERMINAL_TASK_VIEW_KEYS = [
  'device_binding_pub_id',
  'expires_at',
  'payload',
  'server_public_key',
  'server_signature',
  'task_pub_id',
];
const STORED_TASK_KEYS = ['api_base', 'payload', 'payload_sha256', 'task_pub_id', 'tenant_pub_id'];
const TERMINAL_RESULT_VIEW_KEYS = [
  'completed_at',
  'intervention_pub_id',
  'platform_result',
  'state',
  'task_pub_id',
];
const STRICT_ISO_TIMESTAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-](\d{2}):(\d{2}))$/;

function plainRecord(value) {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}

function identifier(value, errorCode) {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) throw new Error(errorCode);
  return value;
}

function hasExactKeys(value, keys) {
  return (
    plainRecord(value) &&
    JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort())
  );
}

function boundedScalarRecord(value, { maxBytes, maxKeys, errorCode }) {
  if (!plainRecord(value) || Object.keys(value).length > maxKeys) throw new Error(errorCode);
  for (const item of Object.values(value)) {
    if (item !== null && !['string', 'number', 'boolean'].includes(typeof item)) {
      throw new Error(errorCode);
    }
  }
  let serialized;
  try {
    serialized = JSON.stringify(value);
  } catch {
    throw new Error(errorCode);
  }
  if (typeof serialized !== 'string' || new TextEncoder().encode(serialized).length > maxBytes) {
    throw new Error(errorCode);
  }
}

function strictIsoTimestamp(value) {
  if (typeof value !== 'string' || value.length > 80) return null;
  const match = STRICT_ISO_TIMESTAMP.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const offsetHour = match[8] === undefined ? 0 : Number(match[8]);
  const offsetMinute = match[9] === undefined ? 0 : Number(match[9]);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > days[month - 1] ||
    hour > 23 ||
    minute > 59 ||
    second > 59 ||
    offsetHour > 23 ||
    offsetMinute > 59 ||
    !Number.isFinite(Date.parse(value))
  ) {
    return null;
  }
  return value;
}

function apiOrigin(value) {
  let api;
  try {
    api = new URL(value);
  } catch {
    throw new Error('api_base_invalid');
  }
  if (
    api.protocol !== 'https:' ||
    api.username ||
    api.password ||
    api.pathname !== '/' ||
    api.search ||
    api.hash
  ) {
    throw new Error('api_base_invalid');
  }
  return api.origin;
}

export function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(',')}}`;
}

export function base64url(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

export function decodeBase64url(value, expectedLength) {
  if (
    typeof value !== 'string' ||
    !value ||
    value.includes('=') ||
    /\s/.test(value) ||
    !/^[A-Za-z0-9_-]+$/.test(value)
  ) {
    throw new Error('invalid_base64url');
  }
  const padded =
    value.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  if (bytes.length !== expectedLength || base64url(bytes) !== value)
    throw new Error('invalid_base64url');
  return bytes;
}

export async function sha256Hex(value, subtle = crypto.subtle) {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  const digest = new Uint8Array(await subtle.digest('SHA-256', bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function validatePairingBundle(bundle) {
  if (!plainRecord(bundle) || Object.keys(bundle).length > 16)
    throw new Error('pairing_bundle_invalid');
  let serialized;
  try {
    serialized = JSON.stringify(bundle);
  } catch {
    throw new Error('pairing_bundle_invalid');
  }
  if (typeof serialized !== 'string' || new TextEncoder().encode(serialized).length > 4096)
    throw new Error('pairing_bundle_invalid');
  const normalizedApiOrigin = apiOrigin(bundle.api_base);
  const tenantPubId = identifier(bundle.tenant_pub_id, 'pairing_tenant_pub_id_invalid');
  const interventionPubId = identifier(
    bundle.intervention_pub_id,
    'pairing_intervention_pub_id_invalid',
  );
  if (typeof bundle.pairing_token !== 'string' || !PAIRING_TOKEN.test(bundle.pairing_token)) {
    throw new Error('pairing_token_invalid');
  }
  if (!/^[a-f0-9]{64}$/.test(bundle.server_public_key_sha256 ?? '')) {
    throw new Error('server_key_fingerprint_invalid');
  }
  const allowedDomain =
    typeof bundle.allowed_domain === 'string' ? bundle.allowed_domain.toLowerCase() : '';
  if (
    !HOSTNAME.test(allowedDomain) ||
    allowedDomain !== bundle.allowed_domain ||
    !ACTIONS.has(bundle.action) ||
    !CHALLENGES.has(bundle.challenge_type)
  ) {
    throw new Error('pairing_scope_invalid');
  }
  return Object.freeze({
    action: bundle.action,
    allowed_domain: allowedDomain,
    api_base: normalizedApiOrigin,
    challenge_type: bundle.challenge_type,
    intervention_pub_id: interventionPubId,
    pairing_token: bundle.pairing_token,
    server_public_key_sha256: bundle.server_public_key_sha256,
    tenant_pub_id: tenantPubId,
  });
}

export function validateDeviceLabel(value) {
  const normalized = typeof value === 'string' ? value.trim() : '';
  if (
    !normalized ||
    normalized.length > 80 ||
    CONTROL_CHARACTER.test(normalized) ||
    SENSITIVE_LABEL.test(normalized) ||
    FULL_PHONE.test(normalized)
  ) {
    throw new Error('device_label_invalid');
  }
  return normalized;
}

export function validateTaskProjection(payload, now = Date.now()) {
  if (!hasExactKeys(payload, TASK_PROJECTION_KEYS)) throw new Error('terminal_task_invalid');
  const taskPubId = identifier(payload.task_pub_id, 'terminal_task_invalid');
  if (
    payload.version !== 1 ||
    !ACTIONS.has(payload.action) ||
    !CHALLENGES.has(payload.challenge_type) ||
    !HOSTNAME.test(payload.allowed_domain)
  ) {
    throw new Error('terminal_task_invalid');
  }
  const expiresAtText = strictIsoTimestamp(payload.expires_at);
  if (!expiresAtText) throw new Error('terminal_task_invalid');
  const expiresAt = Date.parse(expiresAtText);
  if (expiresAt <= now || expiresAt - now > 5 * 60 * 1000) {
    throw new Error('terminal_task_expired');
  }
  return Object.freeze({
    action: payload.action,
    allowed_domain: payload.allowed_domain,
    challenge_type: payload.challenge_type,
    expires_at: payload.expires_at,
    task_pub_id: taskPubId,
    version: 1,
  });
}

export function validateStoredTerminalTask(task, now = Date.now()) {
  if (!hasExactKeys(task, STORED_TASK_KEYS)) throw new Error('terminal_task_invalid');
  const apiBase = apiOrigin(task.api_base);
  const tenantPubId = identifier(task.tenant_pub_id, 'terminal_task_invalid');
  const taskPubId = identifier(task.task_pub_id, 'terminal_task_invalid');
  if (
    apiBase !== task.api_base ||
    !/^[a-f0-9]{64}$/.test(task.payload_sha256 ?? '') ||
    task?.payload?.task_pub_id !== taskPubId
  ) {
    throw new Error('terminal_task_invalid');
  }
  return Object.freeze({
    api_base: apiBase,
    tenant_pub_id: tenantPubId,
    task_pub_id: taskPubId,
    payload: validateTaskProjection(task.payload, now),
    payload_sha256: task.payload_sha256,
  });
}

export async function verifyTask(
  task,
  {
    expectedFingerprint,
    expectedIntervention,
    expectedDevice,
    now = Date.now(),
    subtle = crypto.subtle,
  },
) {
  if (!hasExactKeys(task, TERMINAL_TASK_VIEW_KEYS) || !plainRecord(task.payload)) {
    throw new Error('terminal_task_invalid');
  }
  const rootTaskPubId = identifier(task.task_pub_id, 'terminal_task_invalid');
  const rootDeviceBindingPubId = identifier(task.device_binding_pub_id, 'terminal_task_invalid');
  const rootExpiresAt = strictIsoTimestamp(task.expires_at);
  if (!rootExpiresAt) throw new Error('terminal_task_invalid');
  boundedScalarRecord(task.payload, {
    maxBytes: 16_384,
    maxKeys: 24,
    errorCode: 'terminal_task_invalid',
  });
  const canonicalPayload = canonicalJson(task.payload);
  const publicKeyBytes = decodeBase64url(task.server_public_key, 32);
  if ((await sha256Hex(publicKeyBytes, subtle)) !== expectedFingerprint) {
    throw new Error('server_key_pin_mismatch');
  }
  const signature = decodeBase64url(task.server_signature, 64);
  const publicKey = await subtle.importKey('raw', publicKeyBytes, { name: 'Ed25519' }, false, [
    'verify',
  ]);
  if (
    !(await subtle.verify(
      'Ed25519',
      publicKey,
      signature,
      new TextEncoder().encode(canonicalPayload),
    ))
  ) {
    throw new Error('server_task_signature_invalid');
  }
  const payload = task.payload;
  const taskPubId = identifier(payload.task_pub_id, 'terminal_task_scope_invalid');
  const deviceBindingPubId = identifier(
    payload.device_binding_pub_id,
    'terminal_task_scope_invalid',
  );
  identifier(payload.account_pub_id, 'terminal_task_scope_invalid');
  identifier(payload.intervention_pub_id, 'terminal_task_scope_invalid');
  if (
    typeof payload.nonce !== 'string' ||
    payload.nonce.length < 16 ||
    payload.nonce.length > 128 ||
    !/^[A-Za-z0-9_-]+$/.test(payload.nonce)
  ) {
    throw new Error('terminal_task_scope_invalid');
  }
  if (
    payload.version !== 1 ||
    payload.intervention_pub_id !== expectedIntervention ||
    deviceBindingPubId !== expectedDevice ||
    taskPubId !== task.task_pub_id ||
    !ACTIONS.has(payload.action) ||
    !CHALLENGES.has(payload.challenge_type) ||
    !HOSTNAME.test(payload.allowed_domain)
  ) {
    throw new Error('terminal_task_scope_invalid');
  }
  const payloadExpiresAt = strictIsoTimestamp(payload.expires_at);
  if (
    !payloadExpiresAt ||
    rootTaskPubId !== taskPubId ||
    rootDeviceBindingPubId !== deviceBindingPubId ||
    Date.parse(rootExpiresAt) !== Date.parse(payloadExpiresAt)
  ) {
    throw new Error('terminal_task_scope_invalid');
  }
  const expiresAt = Date.parse(payloadExpiresAt);
  if (expiresAt <= now || expiresAt - now > 5 * 60 * 1000) {
    throw new Error('terminal_task_expired');
  }
  return Object.freeze({
    payload: validateTaskProjection(
      {
        action: payload.action,
        allowed_domain: payload.allowed_domain,
        challenge_type: payload.challenge_type,
        expires_at: payload.expires_at,
        task_pub_id: taskPubId,
        version: 1,
      },
      now,
    ),
    payload_sha256: await sha256Hex(new TextEncoder().encode(canonicalPayload), subtle),
  });
}

export function terminalResultPayload(task, evidenceHash, result = 'challenge_completed') {
  if (!/^[a-f0-9]{64}$/.test(evidenceHash)) throw new Error('evidence_hash_invalid');
  identifier(task?.task_pub_id, 'terminal_task_invalid');
  if (!/^[a-f0-9]{64}$/.test(task?.payload_sha256 ?? '')) throw new Error('terminal_task_invalid');
  if (!TERMINAL_RESULTS.has(result)) throw new Error('terminal_result_invalid');
  return {
    evidence_hash: evidenceHash,
    result,
    task_payload_sha256: task.payload_sha256,
    task_pub_id: task.task_pub_id,
    version: 1,
  };
}

export function validateTerminalResultView(value, expectedTaskPubId, expectedResult) {
  if (!hasExactKeys(value, TERMINAL_RESULT_VIEW_KEYS)) {
    throw new Error('terminal_result_invalid');
  }
  const taskPubId = identifier(value.task_pub_id, 'terminal_result_invalid');
  identifier(value.intervention_pub_id, 'terminal_result_invalid');
  const completedAt = strictIsoTimestamp(value.completed_at);
  if (
    taskPubId !== expectedTaskPubId ||
    !TERMINAL_RESULTS.has(expectedResult) ||
    value.state !== 'completed' ||
    value.platform_result !== expectedResult ||
    !completedAt
  ) {
    throw new Error('terminal_result_invalid');
  }
  return Object.freeze({
    completed_at: completedAt,
    platform_result: expectedResult,
    state: 'completed',
    task_pub_id: taskPubId,
  });
}

export function terminalErrorCode(error) {
  const candidate =
    error instanceof Error ? error.message : typeof error === 'string' ? error : 'terminal_error';
  if (/^terminal_http_(?:4\d\d|5\d\d)$/.test(candidate)) return candidate;
  return PUBLIC_ERROR_CODES.has(candidate) ? candidate : 'terminal_error';
}
