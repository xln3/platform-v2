import { describe, expect, it } from 'vitest';
import {
  evidenceDownloadLabel,
  formatByteSize,
  groupEvidenceByKind,
  isImageEvidence,
  isTraceUnavailable,
  mimeExtension,
  platformDisplayName,
  truncateText,
  type AnswerEvidence,
} from './AnswerExplorer';

describe('platformDisplayName', () => {
  it('maps the five collection platforms to Chinese display names', () => {
    expect(platformDisplayName('doubao')).toBe('豆包');
    expect(platformDisplayName('deepseek')).toBe('DeepSeek');
    expect(platformDisplayName('yiyan')).toBe('文心一言');
    expect(platformDisplayName('tongyi')).toBe('通义千问');
    expect(platformDisplayName('yuanbao')).toBe('腾讯元宝');
  });

  it('passes unknown platforms through unchanged', () => {
    expect(platformDisplayName('api-test')).toBe('api-test');
    expect(platformDisplayName('')).toBe('');
  });
});

describe('truncateText', () => {
  it('keeps text at or below the limit unchanged', () => {
    expect(truncateText('短问题')).toBe('短问题');
    expect(truncateText('x'.repeat(60))).toBe('x'.repeat(60));
  });

  it('truncates beyond 60 chars with an ellipsis by default', () => {
    const long = '问'.repeat(61);
    expect(truncateText(long)).toBe(`${'问'.repeat(60)}…`);
  });

  it('honours a custom limit', () => {
    expect(truncateText('abcdef', 3)).toBe('abc…');
  });
});

describe('formatByteSize', () => {
  it('formats bytes, KB and MB', () => {
    expect(formatByteSize(0)).toBe('0 B');
    expect(formatByteSize(512)).toBe('512 B');
    expect(formatByteSize(1024)).toBe('1.0 KB');
    expect(formatByteSize(1536)).toBe('1.5 KB');
    expect(formatByteSize(1024 * 1024)).toBe('1.0 MB');
  });

  it('renders invalid sizes as —', () => {
    expect(formatByteSize(-1)).toBe('—');
    expect(formatByteSize(Number.NaN)).toBe('—');
  });
});

describe('isTraceUnavailable', () => {
  it('treats the three trace 404 codes as neutral empty states', () => {
    expect(isTraceUnavailable('sse_evidence_missing')).toBe(true);
    expect(isTraceUnavailable('sse_blob_missing')).toBe(true);
    expect(isTraceUnavailable('task_not_found')).toBe(true);
  });

  it('treats any other code as a real failure', () => {
    expect(isTraceUnavailable('http_500')).toBe(false);
    expect(isTraceUnavailable('answer_not_found')).toBe(false);
    expect(isTraceUnavailable('')).toBe(false);
  });
});

describe('mimeExtension', () => {
  it('maps the evidence mime types used by the viewer', () => {
    expect(mimeExtension('image/png')).toBe('png');
    expect(mimeExtension('application/json')).toBe('json');
    expect(mimeExtension('text/plain')).toBe('txt');
    expect(mimeExtension('application/octet-stream')).toBe('bin');
  });

  it('maps the raw-capture evidence mime types (2026-08-10)', () => {
    expect(mimeExtension('application/har+json')).toBe('json');
    expect(mimeExtension('text/event-stream')).toBe('txt');
  });
});

describe('evidenceDownloadLabel', () => {
  it('labels the raw-capture evidence kinds (2026-08-10)', () => {
    expect(evidenceDownloadLabel('har')).toBe('下载 HAR 流量记录 JSON');
    expect(evidenceDownloadLabel('sse_raw')).toBe('下载原始 SSE 响应');
  });

  it('keeps the existing kind labels and the fallback', () => {
    expect(evidenceDownloadLabel('sse')).toBe('下载结构化 trace JSON');
    expect(evidenceDownloadLabel('share_link')).toBe('下载 JSON');
    expect(evidenceDownloadLabel('answer_screenshot')).toBe('下载');
  });
});

describe('isImageEvidence', () => {
  it('accepts the three screenshot kinds and any image mime type', () => {
    expect(isImageEvidence({ kind: 'answer_screenshot', mime_type: 'image/png' })).toBe(true);
    expect(isImageEvidence({ kind: 'share_image', mime_type: 'image/png' })).toBe(true);
    expect(isImageEvidence({ kind: 'source_screenshot', mime_type: 'image/png' })).toBe(true);
    expect(isImageEvidence({ kind: 'other', mime_type: 'image/jpeg' })).toBe(true);
  });

  it('rejects non-image assets', () => {
    expect(isImageEvidence({ kind: 'sse', mime_type: 'application/json' })).toBe(false);
    expect(isImageEvidence({ kind: 'share_link', mime_type: 'application/json' })).toBe(false);
    expect(isImageEvidence({ kind: 'answer_text', mime_type: 'text/plain' })).toBe(false);
  });
});

describe('groupEvidenceByKind', () => {
  const asset = (pub_id: string, kind: string): AnswerEvidence =>
    ({
      pub_id,
      kind,
      relation_type: 'captures',
      access_class: 'public',
      sha256: 'a'.repeat(64),
      mime_type: 'application/json',
      byte_size: 1,
      source_url: null,
      capture_time: '2026-08-10T00:00:00Z',
      anchors: [],
    }) as AnswerEvidence;

  it('groups by kind preserving first-seen order', () => {
    const groups = groupEvidenceByKind([
      asset('evd_1', 'sse'),
      asset('evd_2', 'answer_screenshot'),
      asset('evd_3', 'sse'),
    ]);
    expect(groups.map(([kind]) => kind)).toEqual(['sse', 'answer_screenshot']);
    expect(groups[0]?.[1].map((item) => item.pub_id)).toEqual(['evd_1', 'evd_3']);
  });

  it('returns an empty list for no evidence', () => {
    expect(groupEvidenceByKind([])).toEqual([]);
  });
});
