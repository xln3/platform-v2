import { describe, expect, it } from 'vitest';
import { getSopAccess } from './access';

describe('SOP workspace access', () => {
  it('admits analysts with the write capability granted by the API policy', () => {
    expect(getSopAccess(['analyst'])).toEqual({ canRead: true, canWrite: true });
  });

  it('keeps reviewers read-only and rejects unrelated roles', () => {
    expect(getSopAccess(['reviewer'])).toEqual({ canRead: true, canWrite: false });
    expect(getSopAccess(['customer'])).toEqual({ canRead: false, canWrite: false });
  });
});
