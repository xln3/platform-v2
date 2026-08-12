import { describe, expect, it } from 'vitest';
import {
  evidenceDownloadLabel,
  formatByteSize,
  groupAnswerEvidenceByPurpose,
  groupEvidenceByKind,
  isImageEvidence,
  isTraceUnavailable,
  mimeExtension,
  platformDisplayName,
  projectAiOpenedPages,
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

describe('groupAnswerEvidenceByPurpose', () => {
  const asset = (
    pub_id: string,
    kind: string,
    relation_type: string,
    anchors: AnswerEvidence['anchors'] = [],
  ): AnswerEvidence =>
    ({
      pub_id,
      kind,
      relation_type,
      access_class: 'customer_private',
      sha256: 'a'.repeat(64),
      mime_type: kind === 'share_link' ? 'application/json' : 'image/png',
      byte_size: 512,
      source_url: 'https://example.com/page',
      capture_time: '2026-08-12T00:00:00Z',
      anchors,
    }) as AnswerEvidence;

  it('keeps official share, runtime, AI-open preview, brand proof and legacy review distinct', () => {
    const bboxAnchor = {
      pub_id: 'anch_1',
      text_start: 10,
      text_end: 14,
      bbox: {
        x: 100,
        y: 80,
        width: 200,
        height: 40,
        confidence: 1,
        image_width: 700,
        image_height: 300,
      },
      page_number: 1,
      quote_hash: 'b'.repeat(64),
    } as AnswerEvidence['anchors'][number];
    const groups = groupAnswerEvidenceByPurpose([
      asset('evd_share_image', 'share_image', 'official_share_image'),
      asset('evd_share_link', 'share_link', 'official_share_link'),
      asset('evd_runtime', 'answer_screenshot', 'answer_page'),
      asset('evd_open', 'source_screenshot', 'ai_opened_source_preview'),
      asset('evd_brand', 'source_screenshot', 'brand_mention_source_snapshot', [bboxAnchor]),
      asset('evd_legacy', 'source_screenshot', 'cited_source_snapshot'),
      asset('evd_fake_brand', 'source_screenshot', 'brand_mention_source_snapshot'),
    ]);

    expect(groups.officialShareImages.map((item) => item.pub_id)).toEqual(['evd_share_image']);
    expect(groups.officialShareLinks.map((item) => item.pub_id)).toEqual(['evd_share_link']);
    expect(groups.runtimeAnswerScreenshots.map((item) => item.pub_id)).toEqual(['evd_runtime']);
    expect(groups.aiOpenedPagePreviews.map((item) => item.pub_id)).toEqual(['evd_open']);
    expect(groups.brandMentionScreenshots.map((item) => item.pub_id)).toEqual(['evd_brand']);
    expect(groups.sourceReviewScreenshots.map((item) => item.pub_id)).toEqual([
      'evd_legacy',
      'evd_fake_brand',
    ]);
  });
});

describe('projectAiOpenedPages', () => {
  const trace = (value: Record<string, unknown>) =>
    value as unknown as Parameters<typeof projectAiOpenedPages>[0];

  it('accepts only explicitly observed opened_page rows using the API rank field', () => {
    const projected = projectAiOpenedPages(
      trace({
        opened_pages_observed: true,
        opened_pages: [
          {
            rank: 1,
            title: '真实打开页',
            url: 'https://example.com/opened',
            site: 'example.com',
            summary: '平台 TOOL_OPEN 摘要',
            status: 'opened_page',
          },
        ],
      }),
    );
    expect(projected).toEqual({
      observed: true,
      invalid: false,
      pages: [
        {
          ordinal: 1,
          title: '真实打开页',
          url: 'https://example.com/opened',
          site: 'example.com',
          summary: '平台 TOOL_OPEN 摘要',
        },
      ],
    });
  });

  it('never infers opened pages when observation is absent and rejects search-hit rows', () => {
    expect(projectAiOpenedPages(trace({ opened_pages: [] }))).toEqual({
      observed: false,
      pages: [],
      invalid: false,
    });
    expect(
      projectAiOpenedPages(
        trace({
          opened_pages_observed: true,
          opened_pages: [
            {
              rank: 1,
              title: '仅检索命中',
              url: 'https://example.com/hit',
              site: null,
              summary: '',
              status: 'search_hit',
            },
          ],
        }),
      ),
    ).toEqual({ observed: true, pages: [], invalid: true });
  });
});
