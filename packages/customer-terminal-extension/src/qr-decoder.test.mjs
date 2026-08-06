import assert from 'node:assert/strict';
import test from 'node:test';

const { detectSingleQrValue } = await import('./qr-decoder.mjs');

const location = {
  bottomLeftCorner: { x: 10, y: 80 },
  bottomRightCorner: { x: 80, y: 80 },
  topLeftCorner: { x: 10, y: 10 },
  topRightCorner: { x: 80, y: 10 },
};

function canvasFixture() {
  const bytes = new Uint8ClampedArray(100 * 100 * 4).fill(127);
  const canvas = {
    height: 100,
    width: 100,
    getContext: () => ({
      drawImage() {},
      getImageData: () => ({ data: bytes, height: 100, width: 100 }),
    }),
  };
  return { bytes, canvas };
}

test('uses the native single-QR fast path without allocating fallback pixels', async () => {
  class Detector {
    static async getSupportedFormats() {
      return ['qr_code'];
    }

    async detect() {
      return [{ rawValue: 'native-pairing-bundle' }];
    }
  }
  let fallbackAllocations = 0;
  assert.equal(
    await detectSingleQrValue(
      { height: 100, width: 100 },
      {
        Detector,
        canvasFactory: () => {
          fallbackAllocations += 1;
        },
      },
    ),
    'native-pairing-bundle',
  );
  assert.equal(fallbackAllocations, 0);
});

test('falls back locally, rejects a second QR and clears decoded pixels', async () => {
  const firstFixture = canvasFixture();
  let calls = 0;
  assert.equal(
    await detectSingleQrValue(
      { height: 100, width: 100 },
      {
        Detector: undefined,
        canvasFactory: () => firstFixture.canvas,
        decoder: () => {
          calls += 1;
          return calls === 1 ? { data: 'fallback-pairing-bundle', location } : null;
        },
      },
    ),
    'fallback-pairing-bundle',
  );
  assert.equal(calls, 2);
  assert.equal(
    firstFixture.bytes.every((value) => value === 0),
    true,
  );
  assert.equal(firstFixture.canvas.width, 1);
  assert.equal(firstFixture.canvas.height, 1);

  const multipleFixture = canvasFixture();
  await assert.rejects(
    detectSingleQrValue(
      { height: 100, width: 100 },
      {
        Detector: undefined,
        canvasFactory: () => multipleFixture.canvas,
        decoder: () => ({ data: 'another-pairing-bundle', location }),
      },
    ),
    /pairing_qr_invalid/,
  );
  assert.equal(
    multipleFixture.bytes.every((value) => value === 0),
    true,
  );
});

test('fails closed for absent, oversized and unsupported QR surfaces', async () => {
  const absentFixture = canvasFixture();
  await assert.rejects(
    detectSingleQrValue(
      { height: 100, width: 100 },
      {
        Detector: undefined,
        canvasFactory: () => absentFixture.canvas,
        decoder: () => null,
      },
    ),
    /pairing_qr_invalid/,
  );
  await assert.rejects(
    detectSingleQrValue(
      { height: 5000, width: 5000 },
      { Detector: undefined, canvasFactory: () => canvasFixture().canvas },
    ),
    /pairing_qr_invalid/,
  );
  await assert.rejects(
    detectSingleQrValue(
      { height: 100, width: 100 },
      { Detector: undefined, canvasFactory: () => null },
    ),
    /pairing_qr_not_supported/,
  );
});

test('rejects multiple or oversized native QR results without a fallback', async () => {
  class MultipleDetector {
    static async getSupportedFormats() {
      return ['qr_code'];
    }

    async detect() {
      return [{ rawValue: 'first' }, { rawValue: 'second' }];
    }
  }
  await assert.rejects(
    detectSingleQrValue({ height: 100, width: 100 }, { Detector: MultipleDetector }),
    /pairing_qr_invalid/,
  );

  class OversizedDetector {
    static async getSupportedFormats() {
      return ['qr_code'];
    }

    async detect() {
      return [{ rawValue: 'x'.repeat(4097) }];
    }
  }
  await assert.rejects(
    detectSingleQrValue({ height: 100, width: 100 }, { Detector: OversizedDetector }),
    /pairing_qr_invalid/,
  );
});
