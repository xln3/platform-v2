import {
  terminalErrorCode,
  validatePairingBundle,
  validateDeviceLabel,
  validateTaskProjection,
} from './protocol.mjs';
import { detectSingleQrValue } from './qr-decoder.mjs';

const status = document.querySelector('#status');
const qrInput = document.querySelector('#pairing-qr');
const labelInput = document.querySelector('#label');
const task = document.querySelector('#task');
const pairButton = document.querySelector('#pair');
const clearButton = document.querySelector('#clear-pairing');
const completeButton = document.querySelector('#complete');
const failButton = document.querySelector('#fail');
const rejectButton = document.querySelector('#reject');
let pendingBundle = null;
let interactionEpoch = 0;

const errorMessages = {
  device_label_invalid: '设备标签无效，请勿填写手机号、验证码、token 或其他秘密。',
  host_permission_denied: '未授予本次 API 与目标平台域名权限。',
  pairing_bundle_invalid: '配对二维码不是受支持的 GEO 配对包。',
  pairing_qr_invalid: '未识别到唯一有效的 GEO 配对二维码。',
  pairing_qr_not_supported: '此浏览器不支持本地二维码识别，请联系管理员使用受控终端。',
  pairing_scope_invalid: '配对二维码中的平台范围无效。',
  pairing_token_invalid: '配对二维码已失效或格式无效。',
  server_key_pin_mismatch: '服务器签名密钥与配对二维码不一致。',
  server_task_signature_invalid: '任务签名验证失败。',
  terminal_result_invalid: '终端结果回执未通过任务绑定校验，请在客户页面刷新真实状态。',
  terminal_task_expired: '任务已过期，请重新发起配对。',
};

function publicErrorMessage(error) {
  const code = terminalErrorCode(error);
  return errorMessages[code] ?? '受控终端未能完成操作，请重新发起或联系管理员。';
}

function clearPendingBundle() {
  pendingBundle = null;
  qrInput.value = '';
  pairButton.disabled = true;
  clearButton.disabled = true;
}

function showActiveTask(rawPayload) {
  const payload = validateTaskProjection(rawPayload);
  pendingBundle = null;
  qrInput.value = '';
  qrInput.disabled = true;
  labelInput.disabled = true;
  pairButton.disabled = true;
  clearButton.disabled = true;
  completeButton.disabled = false;
  failButton.disabled = false;
  rejectButton.disabled = false;
  task.textContent = `${payload.action} · ${payload.allowed_domain} · ${payload.challenge_type}`;
  return payload;
}

function showPairingControls() {
  qrInput.disabled = false;
  labelInput.disabled = false;
  completeButton.disabled = true;
  failButton.disabled = true;
  rejectButton.disabled = true;
}

async function decodePairingQr(file) {
  if (
    !file ||
    file.size > 2 * 1024 * 1024 ||
    !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)
  ) {
    throw new Error('pairing_qr_invalid');
  }
  if (!('createImageBitmap' in globalThis)) throw new Error('pairing_qr_not_supported');
  const bitmap = await createImageBitmap(file);
  try {
    const decodedValue = await detectSingleQrValue(bitmap);
    let decoded;
    try {
      decoded = JSON.parse(decodedValue);
    } catch {
      throw new Error('pairing_qr_invalid');
    }
    return validatePairingBundle(decoded);
  } finally {
    bitmap.close();
  }
}

async function requestOrigins(bundle) {
  const api = new URL(bundle.api_base);
  return chrome.permissions.request({
    origins: [`${api.origin}/*`, `https://${bundle.allowed_domain}/*`],
  });
}

qrInput.addEventListener('change', async () => {
  interactionEpoch += 1;
  const file = qrInput.files?.[0];
  clearPendingBundle();
  status.textContent = '正在本地识别配对二维码；图像不会上传或保留…';
  try {
    pendingBundle = await decodePairingQr(file);
    qrInput.value = '';
    task.textContent = `${pendingBundle.action} · ${pendingBundle.allowed_domain} · ${pendingBundle.challenge_type}`;
    pairButton.disabled = false;
    clearButton.disabled = false;
    status.textContent = '配对范围已读取。请核对目标域名，再授权本次连接。';
  } catch (error) {
    clearPendingBundle();
    task.textContent = '';
    status.textContent = `失败：${publicErrorMessage(error)}`;
  }
});

clearButton.addEventListener('click', () => {
  interactionEpoch += 1;
  clearPendingBundle();
  task.textContent = '';
  status.textContent = '配对二维码已从终端内存清除。';
});

pairButton.addEventListener('click', async () => {
  interactionEpoch += 1;
  status.textContent = '正在验证配对…';
  try {
    if (!pendingBundle) throw new Error('pairing_bundle_invalid');
    const bundle = pendingBundle;
    if (!(await requestOrigins(bundle))) throw new Error('host_permission_denied');
    const deviceLabel = validateDeviceLabel(labelInput.value || 'Customer browser');
    clearPendingBundle();
    const response = await chrome.runtime.sendMessage({
      type: 'pair',
      bundle,
      deviceLabel,
    });
    if (!response.ok) throw new Error(response.error);
    const payload = showActiveTask(response.value.payload);
    status.textContent = '任务签名与作用域已验证。请仅在目标平台原生页面完成人工验证。';
    await chrome.tabs.create({ url: `https://${payload.allowed_domain}` });
  } catch (error) {
    clearPendingBundle();
    status.textContent = `失败：${publicErrorMessage(error)}`;
  }
});

async function submitTaskResult(type) {
  interactionEpoch += 1;
  completeButton.disabled = true;
  failButton.disabled = true;
  rejectButton.disabled = true;
  status.textContent =
    type === 'reject'
      ? '正在提交签名拒绝回执…'
      : type === 'fail'
        ? '正在提交签名失败结果…'
        : '正在提交脱敏结果…';
  const response = await chrome.runtime.sendMessage({ type });
  if (!response.ok) {
    if (terminalErrorCode(response.error) === 'terminal_task_expired') {
      showPairingControls();
      task.textContent = '';
    } else {
      completeButton.disabled = false;
      failButton.disabled = false;
      rejectButton.disabled = false;
    }
    status.textContent = `失败：${publicErrorMessage(response.error)}`;
    return;
  }
  showPairingControls();
  task.textContent = '';
  status.textContent =
    type === 'reject'
      ? '本次任务已拒绝；签名拒绝回执已提交，现有授权与会话未改变。'
      : type === 'fail'
        ? '原生验证失败结果已签名提交；未提升任何平台准入等级。'
        : '终端结果已签名提交；仍需平台回调或身份探针确认。';
}

completeButton.addEventListener('click', async () => {
  await submitTaskResult('complete');
});

failButton.addEventListener('click', async () => {
  await submitTaskResult('fail');
});

rejectButton.addEventListener('click', async () => {
  await submitTaskResult('reject');
});

async function restoreActiveTask() {
  const startingEpoch = interactionEpoch;
  const response = await chrome.runtime.sendMessage({ type: 'resume' });
  if (interactionEpoch !== startingEpoch) return;
  if (!response?.ok) {
    status.textContent = `失败：${publicErrorMessage(response?.error)}`;
    return;
  }
  if (response.value?.state === 'none') return;
  if (response.value?.state === 'expired') {
    showPairingControls();
    task.textContent = '';
    status.textContent = '任务已过期，请重新发起配对。';
    return;
  }
  if (response.value?.state !== 'ready' || !response.value.payload) {
    status.textContent = '失败：受控终端未能恢复任务，请重新发起或联系管理员。';
    return;
  }
  try {
    showActiveTask(response.value.payload);
    status.textContent = '已恢复待完成任务。请仅在目标平台原生页面完成人工验证。';
  } catch (error) {
    showPairingControls();
    task.textContent = '';
    status.textContent = `失败：${publicErrorMessage(error)}`;
  }
}

void restoreActiveTask();
