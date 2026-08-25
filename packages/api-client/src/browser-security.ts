const clientSecretValuePattern =
  /(?:bearer\s+|session\s*=|cookie(?:\s|=|:)|token(?:\s|=|:)|otp(?:\s|=|:)|password(?:\s|=|:)|proxy(?:[_ -]?password)?(?:\s|=|:)|profile(?:s|[_ /-]?(?:path|dir|directory))?(?:\s|=|:|\\|\/)|biometric|dlp-canary|(?:^|[^\w])\d{6}(?:[^\w]|$)|(?:^|[^\w])\d{3}[\s.-]\d{3}(?:[^\w]|$)|1[3-9]\d{9}|1[3-9](?:[\s().-]?\d){9}|(?:[A-Za-z]:\\|\\\\)[^\r\n]{0,1024}(?:profiles?|user[ _-]?data)(?:\\[^\r\n]{0,1024})?|\/[^\s]*profile(?:s?\/[^\s]*)?)/i;
const clientSecretKeyPattern =
  /cookie|authorization|token|otp|password|phone|profile|biometric|storage.?state|qr/i;
const clientSecretInvisiblePattern = /[\u200b-\u200d\u2060\ufeff]/g;
const unsafeClientControlPattern =
  /[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/u;

const normalizeClientSecretCandidate = (value: string): string => {
  let normalized = value.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const decoded = decodeURIComponent(normalized);
      if (decoded === normalized) break;
      normalized = decoded.normalize('NFKC').replace(clientSecretInvisiblePattern, '');
    } catch {
      break;
    }
  }
  return normalized;
};

/** Detects normalized secret-shaped values before browser retention or transport. */
export const containsClientSecret = (value: string): boolean =>
  clientSecretValuePattern.test(normalizeClientSecretCandidate(value));

/** Detects normalized secret-shaped property and parameter names. */
export const containsClientSecretKey = (value: string): boolean =>
  clientSecretKeyPattern.test(normalizeClientSecretCandidate(value));

/** Rejects C0/C1 controls, Unicode line separators, and bidi override/isolate controls. */
export const containsUnsafeClientControlCharacter = (value: string): boolean =>
  unsafeClientControlPattern.test(value);
