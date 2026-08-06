import jsQR from 'jsqr';

const MAX_DIMENSION = 2048;
const MAX_PIXELS = 16_000_000;
const MAX_RAW_BYTES = 4096;

function rawValue(value) {
  if (
    typeof value !== 'string' ||
    !value ||
    new TextEncoder().encode(value).length > MAX_RAW_BYTES
  ) {
    throw new Error('pairing_qr_invalid');
  }
  return value;
}

function browserCanvas(width, height) {
  if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(width, height);
  if (typeof document !== 'undefined') {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    return canvas;
  }
  throw new Error('pairing_qr_not_supported');
}

async function nativeValue(bitmap, Detector) {
  if (typeof Detector !== 'function' || typeof Detector.getSupportedFormats !== 'function') {
    return null;
  }
  let supported;
  try {
    supported = await Detector.getSupportedFormats();
  } catch {
    return null;
  }
  if (!Array.isArray(supported) || !supported.includes('qr_code')) return null;
  let matches;
  try {
    matches = await new Detector({ formats: ['qr_code'] }).detect(bitmap);
  } catch {
    return null;
  }
  if (!Array.isArray(matches) || matches.length === 0) return null;
  if (matches.length !== 1) throw new Error('pairing_qr_invalid');
  return rawValue(matches[0]?.rawValue);
}

function maskDetectedCode(imageData, location) {
  const points = Object.values(location ?? {}).filter(
    (point) =>
      point && Number.isFinite(point.x) && Number.isFinite(point.y) && point.x >= 0 && point.y >= 0,
  );
  if (points.length < 4) throw new Error('pairing_qr_invalid');
  const padding = 4;
  const left = Math.max(0, Math.floor(Math.min(...points.map((point) => point.x)) - padding));
  const right = Math.min(
    imageData.width - 1,
    Math.ceil(Math.max(...points.map((point) => point.x)) + padding),
  );
  const top = Math.max(0, Math.floor(Math.min(...points.map((point) => point.y)) - padding));
  const bottom = Math.min(
    imageData.height - 1,
    Math.ceil(Math.max(...points.map((point) => point.y)) + padding),
  );
  for (let y = top; y <= bottom; y += 1) {
    const start = (y * imageData.width + left) * 4;
    const end = (y * imageData.width + right + 1) * 4;
    imageData.data.fill(255, start, end);
  }
}

function fallbackValue(bitmap, { canvasFactory, decoder }) {
  if (
    !Number.isSafeInteger(bitmap?.width) ||
    !Number.isSafeInteger(bitmap?.height) ||
    bitmap.width <= 0 ||
    bitmap.height <= 0 ||
    bitmap.width * bitmap.height > MAX_PIXELS
  ) {
    throw new Error('pairing_qr_invalid');
  }
  const scale = Math.min(1, MAX_DIMENSION / Math.max(bitmap.width, bitmap.height));
  const width = Math.max(1, Math.round(bitmap.width * scale));
  const height = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = canvasFactory(width, height);
  const context = canvas?.getContext?.('2d', { alpha: false, willReadFrequently: true });
  if (!context) throw new Error('pairing_qr_not_supported');
  let imageData;
  try {
    context.drawImage(bitmap, 0, 0, width, height);
    imageData = context.getImageData(0, 0, width, height);
    const first = decoder(imageData.data, width, height, { inversionAttempts: 'attemptBoth' });
    if (!first) throw new Error('pairing_qr_invalid');
    const firstValue = rawValue(first.data);
    maskDetectedCode(imageData, first.location);
    const second = decoder(imageData.data, width, height, { inversionAttempts: 'attemptBoth' });
    if (second) throw new Error('pairing_qr_invalid');
    return firstValue;
  } catch (error) {
    if (
      error instanceof Error &&
      ['pairing_qr_invalid', 'pairing_qr_not_supported'].includes(error.message)
    ) {
      throw error;
    }
    throw new Error('pairing_qr_invalid');
  } finally {
    imageData?.data.fill(0);
    canvas.width = 1;
    canvas.height = 1;
  }
}

export async function detectSingleQrValue(
  bitmap,
  { Detector = globalThis.BarcodeDetector, canvasFactory = browserCanvas, decoder = jsQR } = {},
) {
  const detected = await nativeValue(bitmap, Detector);
  if (detected !== null) return detected;
  return fallbackValue(bitmap, { canvasFactory, decoder });
}
