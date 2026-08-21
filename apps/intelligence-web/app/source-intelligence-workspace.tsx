import { useEffect, useState } from 'react';
import {
  getInternalAnswerUvw,
  getInternalSourceInspectionEvidence,
  getInternalSourceUrlDetail,
  listInternalSourceInspections,
  listInternalSourceOccurrences,
  listInternalSourceSites,
  listInternalSourceSnapshots,
  listInternalSourceUrls,
  listInternalSourceWChunks,
  reviewInternalWChunk,
  type InternalAnswerUvw,
  type InternalSourceInspection,
  type InternalSourceInspectionEvidence,
  type InternalSourceOccurrence,
  type InternalSourceSite,
  type InternalSourceSnapshot,
  type InternalSourceUrl,
  type InternalSourceUrlDetail,
  type InternalWChunk,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { Badge, StatePanel, useOptionalExperienceContext } from '@geo/design-system';

type LoadState = 'idle' | 'loading' | 'ready' | 'failed' | 'forbidden';

const fixtureSites: InternalSourceSite[] = [
  {
    sitePubId: 'sit_fixture_example',
    host: 'example.com',
    distinctUrlCount: 3,
    uOccurrenceCount: 12,
    distinctAnswerCount: 8,
    vCount: 5,
    wCount: 2,
    uObservation: 'observed',
    vObservation: 'observed',
    wObservation: 'partial',
    latestCaptureAt: '2026-08-20T02:00:00Z',
  },
  {
    sitePubId: 'sit_fixture_media',
    host: 'media.example.cn',
    distinctUrlCount: 1,
    uOccurrenceCount: 0,
    distinctAnswerCount: 4,
    vCount: 0,
    wCount: 0,
    uObservation: 'unobserved',
    vObservation: 'unobserved',
    wObservation: 'unobserved',
    latestCaptureAt: '2026-08-19T02:00:00Z',
  },
];

const fixtureUrls: InternalSourceUrl[] = [
  {
    urlPubId: 'url_fixture_article',
    canonicalUrl: 'https://example.com/article',
    uOccurrenceCount: 7,
    distinctAnswerCount: 5,
    vCount: 3,
    wCount: 1,
    uObservation: 'observed',
    vObservation: 'observed',
    wObservation: 'partial',
    latestCaptureAt: '2026-08-20T02:00:00Z',
    fetchState: 'succeeded',
    analysisState: 'confirmed',
  },
  {
    urlPubId: 'url_fixture_news',
    canonicalUrl: 'https://example.com/news',
    uOccurrenceCount: 0,
    distinctAnswerCount: 3,
    vCount: 0,
    wCount: 0,
    uObservation: 'unobserved',
    vObservation: 'unobserved',
    wObservation: 'unobserved',
    latestCaptureAt: '2026-08-19T02:00:00Z',
    fetchState: 'blocked',
    analysisState: 'no_evidence',
  },
];

const fixtureOccurrences: InternalSourceOccurrence[] = ['a', 'b'].map((suffix, index) => ({
  occurrencePubId: `uoc_fixture_${suffix}`,
  answerPubId: `ans_fixture_${suffix}`,
  urlPubId: 'url_fixture_article',
  canonicalUrl: 'https://example.com/article',
  host: 'example.com',
  capturedAt: `2026-08-${20 - index}T02:00:00Z`,
  platform: index === 0 ? 'doubao' : 'deepseek',
  model: index === 0 ? 'doubao-search' : 'deepseek-web',
  region: 'CN-East',
  mode: 'search',
  question: index === 0 ? '目标品牌是否值得推荐？' : '该领域有哪些代表厂商？',
  query: index === 0 ? '目标品牌 评测' : '领域 代表厂商',
  uState: 'observed',
  uRank: index + 1,
  vState: index === 0 ? 'entered' : 'unobserved',
  vOpenOrder: index === 0 ? 1 : null,
  finalReferenceState: index === 0 ? 'referenced' : 'unobserved',
  wState: index === 0 ? 'confirmed' : 'unobserved',
  wWeight: index === 0 ? 0.82 : null,
  evidenceState: 'linked',
}));

const fixtureDetail: InternalSourceUrlDetail = {
  urlPubId: 'url_fixture_article',
  host: 'example.com',
  canonicalUrl: 'https://example.com/article',
  normalizationVersion: 'citation-normalizer-v1',
  uOccurrenceCount: 7,
  distinctAnswerCount: 5,
  vCount: 3,
  wCount: 1,
  uObservation: 'observed',
  vObservation: 'observed',
  wObservation: 'partial',
  fetchAttemptCount: 2,
  latestSnapshot: {
    snapshotPubId: 'snp_fixture_article',
    state: 'succeeded',
    capturedAt: '2026-08-20T02:01:00Z',
    textSha256: 'a'.repeat(64),
    extractorVersion: 'density-extract-v1',
  },
  pageInspectionCount: 2,
  findingCount: 1,
};

const fixtureSnapshots: InternalSourceSnapshot[] = [
  {
    snapshotPubId: 'snp_fixture_article',
    state: 'succeeded',
    capturedAt: '2026-08-20T02:01:00Z',
    textSha256: 'a'.repeat(64),
    extractorVersion: 'density-extract-v1',
    finalUrl: 'https://example.com/article',
    httpStatus: 200,
    title: '目标品牌评测',
    siteName: 'Example',
    author: '编辑部',
    accountName: null,
    publishedAt: '2026-08-19T02:00:00Z',
    fetchAttemptPubId: 'fat_fixture_article',
    fetchState: 'succeeded',
    fetchErrorCode: null,
  },
];

const fixtureInspections: InternalSourceInspection[] = [
  {
    inspectionPubId: 'pgi_fixture_article',
    sourceDocumentPubId: 'srd_fixture_article',
    status: 'completed',
    policyVersion: 'page-inspection-v1',
    promptVersion: 'page-inspection-prompt-v1',
    model: 'audit-model',
    contentSha256: 'a'.repeat(64),
    findingCount: 1,
    createdAt: '2026-08-20T02:03:00Z',
  },
];

const fixtureWChunks: InternalWChunk[] = [
  {
    chunkPubId: 'wch_fixture_article',
    analysisPubId: 'wca_fixture_article',
    occurrencePubId: 'uoc_fixture_a',
    snapshotPubId: 'snp_fixture_article',
    analysisCreatedAt: '2026-08-20T02:02:00Z',
    ordinal: 1,
    sourceTextStart: 20,
    sourceTextEnd: 36,
    sourceQuote: '目标品牌在该项对比中排名靠后。',
    sourceQuoteHash: 'b'.repeat(64),
    answerTextStart: 8,
    answerTextEnd: 24,
    answerQuote: '目标品牌在该项对比中排名靠后。',
    answerQuoteHash: 'b'.repeat(64),
    basis: 'explicit_citation',
    contributionScore: 0.82,
    confidence: 0.94,
    model: 'deterministic',
    promptVersion: 'content-contribution-prompt-v1',
    policyVersion: 'content-contribution-exact-v1',
    algorithmVersion: 'exact-span-v1',
    verificationState: 'exact',
    reviewState: 'unreviewed',
    reviewCount: 0,
    latestReview: null,
  },
];

const fixtureInspectionEvidence: InternalSourceInspectionEvidence = {
  inspectionPubId: 'pgi_fixture_article',
  sourceDocumentPubId: 'srd_fixture_article',
  contentSha256: 'a'.repeat(64),
  status: 'completed',
  findings: [
    {
      findingPubId: 'pgf_fixture_article',
      code: 'A2',
      ledger: 'statement',
      status: 'confirmed',
      summary: '页面包含可回查的比较性表述。',
      action: '人工复核事实依据',
      evidenceChain: [
        {
          connector: 'because',
          factType: 'source_quote',
          explanation: '结论直接来自页面逐字原文。',
          quote: '目标品牌在该项对比中排名靠后。',
        },
      ],
      spans: [
        {
          spanPubId: 'pgs_fixture_article',
          quote: '目标品牌在该项对比中排名靠后。',
          textStart: 20,
          textEnd: 36,
          quoteHash: 'b'.repeat(64),
        },
      ],
    },
  ],
};

const observationLabel: Record<string, string> = {
  observed: '可观察',
  partial: '部分可观察',
  unobserved: '不可观察',
  entered: '已进入',
  not_entered: '未进入',
  referenced: '已引用',
  not_referenced: '未引用',
  pending: '待分析',
  confirmed: '有可验证内容',
  no_evidence: '无可验证内容',
};

const label = (value: string): string => observationLabel[value] ?? value;

const stageCount = (stage: 'U' | 'V' | 'W', count: number, observation: string): string =>
  observation === 'unobserved'
    ? `${stage} 不可观察`
    : `${stage} ${count}${observation === 'partial' ? '（部分）' : ''}`;

export function SourceIntelligenceWorkspace() {
  const experience = useOptionalExperienceContext();
  const fixture = experience?.source !== 'live';
  const projectPubId = experience?.projectPubId ?? '';
  const [sites, setSites] = useState<InternalSourceSite[]>(fixture ? fixtureSites : []);
  const [sitesState, setSitesState] = useState<LoadState>(fixture ? 'ready' : 'loading');
  const [sitesCursor, setSitesCursor] = useState<string | null>(null);
  const [selectedSite, setSelectedSite] = useState<string>('');
  const [urls, setUrls] = useState<InternalSourceUrl[]>([]);
  const [urlsState, setUrlsState] = useState<LoadState>('idle');
  const [urlsCursor, setUrlsCursor] = useState<string | null>(null);
  const [selectedUrl, setSelectedUrl] = useState<string>('');
  const [detail, setDetail] = useState<InternalSourceUrlDetail | null>(null);
  const [occurrences, setOccurrences] = useState<InternalSourceOccurrence[]>([]);
  const [urlState, setUrlState] = useState<LoadState>('idle');
  const [occurrenceCursor, setOccurrenceCursor] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<InternalSourceSnapshot[]>([]);
  const [snapshotCursor, setSnapshotCursor] = useState<string | null>(null);
  const [inspections, setInspections] = useState<InternalSourceInspection[]>([]);
  const [inspectionCursor, setInspectionCursor] = useState<string | null>(null);
  const [historyState, setHistoryState] = useState<LoadState>('idle');
  const [wChunks, setWChunks] = useState<InternalWChunk[]>([]);
  const [wChunkCursor, setWChunkCursor] = useState<string | null>(null);
  const [wChunkState, setWChunkState] = useState<LoadState>('idle');
  const [reviewRationales, setReviewRationales] = useState<Record<string, string>>({});
  const [reviewingChunk, setReviewingChunk] = useState<string | null>(null);
  const [reviewMessage, setReviewMessage] = useState<string>('');
  const [inspectionEvidence, setInspectionEvidence] =
    useState<InternalSourceInspectionEvidence | null>(null);
  const [inspectionEvidenceState, setInspectionEvidenceState] = useState<LoadState>('idle');
  const [answer, setAnswer] = useState<InternalAnswerUvw | null>(null);
  const [answerState, setAnswerState] = useState<LoadState>('idle');
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    setSelectedSite('');
    setSelectedUrl('');
    setUrls([]);
    setDetail(null);
    setOccurrences([]);
    setSnapshots([]);
    setInspections([]);
    setWChunks([]);
    setReviewRationales({});
    setReviewMessage('');
    setInspectionEvidence(null);
    setAnswer(null);
    if (fixture) {
      setSites(fixtureSites);
      setSitesState('ready');
      setSitesCursor(null);
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId) {
      setSitesState('failed');
      return;
    }
    let cancelled = false;
    setSitesState('loading');
    void listInternalSourceSites(headers, projectPubId).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setSites(result.data.data);
        setSitesCursor(result.data.nextCursor);
        setSitesState('ready');
      } else {
        setSites([]);
        setSitesState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [fixture, projectPubId, retryKey]);

  useEffect(() => {
    setSelectedUrl('');
    setDetail(null);
    setOccurrences([]);
    setSnapshots([]);
    setInspections([]);
    setWChunks([]);
    setReviewRationales({});
    setReviewMessage('');
    setInspectionEvidence(null);
    setAnswer(null);
    if (!selectedSite) {
      setUrls([]);
      setUrlsState('idle');
      return;
    }
    if (fixture) {
      setUrls(fixtureUrls);
      setUrlsCursor(null);
      setUrlsState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId) {
      setUrlsState('failed');
      return;
    }
    let cancelled = false;
    setUrlsState('loading');
    void listInternalSourceUrls(headers, projectPubId, selectedSite).then((result) => {
      if (cancelled) return;
      if (result.kind === 'ready') {
        setUrls(result.data.data);
        setUrlsCursor(result.data.nextCursor);
        setUrlsState('ready');
      } else {
        setUrls([]);
        setUrlsState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [fixture, projectPubId, selectedSite]);

  useEffect(() => {
    setAnswer(null);
    setInspectionEvidence(null);
    setInspectionEvidenceState('idle');
    if (!selectedUrl) {
      setDetail(null);
      setOccurrences([]);
      setSnapshots([]);
      setInspections([]);
      setWChunks([]);
      setUrlState('idle');
      setHistoryState('idle');
      setWChunkState('idle');
      return;
    }
    if (fixture) {
      setDetail(fixtureDetail);
      setOccurrences(fixtureOccurrences);
      setOccurrenceCursor(null);
      setSnapshots(fixtureSnapshots);
      setSnapshotCursor(null);
      setInspections(fixtureInspections);
      setInspectionCursor(null);
      setWChunks(fixtureWChunks);
      setWChunkCursor(null);
      setUrlState('ready');
      setHistoryState('ready');
      setWChunkState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId) {
      setUrlState('failed');
      setHistoryState('failed');
      setWChunkState('failed');
      return;
    }
    let cancelled = false;
    setUrlState('loading');
    setHistoryState('loading');
    setWChunkState('loading');
    void Promise.all([
      getInternalSourceUrlDetail(headers, projectPubId, selectedUrl),
      listInternalSourceOccurrences(headers, projectPubId, selectedUrl),
      listInternalSourceSnapshots(headers, projectPubId, selectedUrl),
      listInternalSourceInspections(headers, projectPubId, selectedUrl),
      listInternalSourceWChunks(headers, projectPubId, selectedUrl),
    ]).then(([detailResult, occurrenceResult, snapshotResult, inspectionResult, wChunkResult]) => {
      if (cancelled) return;
      if (detailResult.kind === 'ready' && occurrenceResult.kind === 'ready') {
        setDetail(detailResult.data);
        setOccurrences(occurrenceResult.data.data);
        setOccurrenceCursor(occurrenceResult.data.nextCursor);
        setUrlState('ready');
      } else {
        setDetail(null);
        setOccurrences([]);
        setUrlState(
          detailResult.kind === 'forbidden' || occurrenceResult.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
        );
      }
      if (snapshotResult.kind === 'ready' && inspectionResult.kind === 'ready') {
        setSnapshots(snapshotResult.data.data);
        setSnapshotCursor(snapshotResult.data.nextCursor);
        setInspections(inspectionResult.data.data);
        setInspectionCursor(inspectionResult.data.nextCursor);
        setHistoryState('ready');
      } else {
        setSnapshots([]);
        setInspections([]);
        setHistoryState(
          snapshotResult.kind === 'forbidden' || inspectionResult.kind === 'forbidden'
            ? 'forbidden'
            : 'failed',
        );
      }
      if (wChunkResult.kind === 'ready') {
        setWChunks(wChunkResult.data.data);
        setWChunkCursor(wChunkResult.data.nextCursor);
        setWChunkState('ready');
      } else {
        setWChunks([]);
        setWChunkState(wChunkResult.kind === 'forbidden' ? 'forbidden' : 'failed');
      }
    });
    return () => {
      cancelled = true;
    };
  }, [fixture, projectPubId, selectedUrl]);

  async function loadMoreSites() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !sitesCursor) return;
    const result = await listInternalSourceSites(headers, projectPubId, sitesCursor);
    if (result.kind === 'ready') {
      setSites((current) => [...current, ...result.data.data]);
      setSitesCursor(result.data.nextCursor);
    }
  }

  async function loadMoreUrls() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedSite || !urlsCursor) return;
    const result = await listInternalSourceUrls(headers, projectPubId, selectedSite, urlsCursor);
    if (result.kind === 'ready') {
      setUrls((current) => [...current, ...result.data.data]);
      setUrlsCursor(result.data.nextCursor);
    }
  }

  async function loadMoreOccurrences() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedUrl || !occurrenceCursor) return;
    const result = await listInternalSourceOccurrences(
      headers,
      projectPubId,
      selectedUrl,
      occurrenceCursor,
    );
    if (result.kind === 'ready') {
      setOccurrences((current) => [...current, ...result.data.data]);
      setOccurrenceCursor(result.data.nextCursor);
    }
  }

  async function loadMoreSnapshots() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedUrl || !snapshotCursor) return;
    const result = await listInternalSourceSnapshots(
      headers,
      projectPubId,
      selectedUrl,
      snapshotCursor,
    );
    if (result.kind === 'ready') {
      setSnapshots((current) => [...current, ...result.data.data]);
      setSnapshotCursor(result.data.nextCursor);
    }
  }

  async function loadMoreInspections() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedUrl || !inspectionCursor) return;
    const result = await listInternalSourceInspections(
      headers,
      projectPubId,
      selectedUrl,
      inspectionCursor,
    );
    if (result.kind === 'ready') {
      setInspections((current) => [...current, ...result.data.data]);
      setInspectionCursor(result.data.nextCursor);
    }
  }

  async function loadMoreWChunks() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedUrl || !wChunkCursor) return;
    const result = await listInternalSourceWChunks(
      headers,
      projectPubId,
      selectedUrl,
      wChunkCursor,
    );
    if (result.kind === 'ready') {
      setWChunks((current) => [...current, ...result.data.data]);
      setWChunkCursor(result.data.nextCursor);
    }
  }

  async function reviewChunk(chunkPubId: string, decision: 'accepted' | 'rejected') {
    const rationale = (reviewRationales[chunkPubId] ?? '').trim();
    if (!rationale) {
      setReviewMessage('请先填写复核依据。');
      return;
    }
    if (fixture) {
      const reviewedAt = new Date().toISOString();
      const reviewedChunk = wChunks.find((chunk) => chunk.chunkPubId === chunkPubId);
      const nextChunks = wChunks.map((chunk) =>
        chunk.chunkPubId === chunkPubId
          ? {
              ...chunk,
              reviewState: decision,
              reviewCount: chunk.reviewCount + 1,
              latestReview: {
                reviewPubId: 'wcr_fixture_review',
                decision,
                rationale,
                reviewerPubId: 'usr_fixture_reviewer',
                reviewedAt,
              },
            }
          : chunk,
      );
      setWChunks(nextChunks);
      if (reviewedChunk) {
        const occurrenceChunks = nextChunks.filter(
          (chunk) => chunk.occurrencePubId === reviewedChunk.occurrencePubId,
        );
        const latestAnalysis = [...occurrenceChunks].sort(
          (left, right) =>
            right.analysisCreatedAt.localeCompare(left.analysisCreatedAt) ||
            right.analysisPubId.localeCompare(left.analysisPubId),
        )[0];
        const eligibleChunks = latestAnalysis
          ? occurrenceChunks.filter(
              (chunk) =>
                chunk.analysisPubId === latestAnalysis.analysisPubId &&
                ((chunk.verificationState === 'exact' && chunk.reviewState !== 'rejected') ||
                  chunk.reviewState === 'accepted'),
            )
          : [];
        const nextWeight =
          eligibleChunks.length === 0
            ? null
            : Math.max(...eligibleChunks.map((chunk) => chunk.contributionScore));
        const nextState = nextWeight === null ? 'no_evidence' : 'confirmed';
        const previousOccurrence = occurrences.find(
          (occurrence) => occurrence.occurrencePubId === reviewedChunk.occurrencePubId,
        );
        setOccurrences((current) =>
          current.map((occurrence) =>
            occurrence.occurrencePubId === reviewedChunk.occurrencePubId
              ? {
                  ...occurrence,
                  wState: nextState,
                  wWeight: nextWeight,
                }
              : occurrence,
          ),
        );
        setAnswer((current) =>
          current
            ? {
                ...current,
                occurrences: current.occurrences.map((occurrence) =>
                  occurrence.occurrencePubId === reviewedChunk.occurrencePubId
                    ? { ...occurrence, wState: nextState, wWeight: nextWeight }
                    : occurrence,
                ),
              }
            : current,
        );
        const countDelta =
          Number(nextState === 'confirmed') - Number(previousOccurrence?.wState === 'confirmed');
        if (countDelta !== 0) {
          setDetail((current) =>
            current ? { ...current, wCount: Math.max(0, current.wCount + countDelta) } : current,
          );
          setUrls((current) =>
            current.map((url) =>
              url.urlPubId === selectedUrl
                ? { ...url, wCount: Math.max(0, url.wCount + countDelta) }
                : url,
            ),
          );
          setSites((current) =>
            current.map((site) =>
              site.sitePubId === selectedSite
                ? { ...site, wCount: Math.max(0, site.wCount + countDelta) }
                : site,
            ),
          );
        }
      }
      setReviewMessage(decision === 'accepted' ? 'W 片段已复核通过。' : 'W 片段已复核驳回。');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !selectedUrl) return;
    setReviewingChunk(chunkPubId);
    setReviewMessage('');
    const result = await reviewInternalWChunk(
      headers,
      projectPubId,
      chunkPubId,
      decision,
      rationale,
      `w-review-${globalThis.crypto.randomUUID()}`,
    );
    setReviewingChunk(null);
    if (result.kind !== 'ready') {
      setReviewMessage(
        result.kind === 'forbidden' ? '当前账号没有 W 人工复核权限。' : '复核提交失败，请重试。',
      );
      return;
    }
    const [refreshedChunks, refreshedOccurrences, refreshedDetail] = await Promise.all([
      listInternalSourceWChunks(headers, projectPubId, selectedUrl),
      listInternalSourceOccurrences(headers, projectPubId, selectedUrl),
      getInternalSourceUrlDetail(headers, projectPubId, selectedUrl),
    ]);
    if (refreshedChunks.kind === 'ready') {
      setWChunks(refreshedChunks.data.data);
      setWChunkCursor(refreshedChunks.data.nextCursor);
    } else {
      setWChunks((current) =>
        current.map((chunk) =>
          chunk.chunkPubId === chunkPubId
            ? {
                ...chunk,
                reviewState: result.data.decision,
                reviewCount: chunk.reviewCount + 1,
                latestReview: result.data,
              }
            : chunk,
        ),
      );
    }
    if (refreshedOccurrences.kind === 'ready') {
      setOccurrences(refreshedOccurrences.data.data);
      setOccurrenceCursor(refreshedOccurrences.data.nextCursor);
    }
    if (refreshedDetail.kind === 'ready') {
      const countDelta =
        refreshedDetail.data.wCount - (detail?.wCount ?? refreshedDetail.data.wCount);
      setDetail(refreshedDetail.data);
      setUrls((current) =>
        current.map((url) =>
          url.urlPubId === selectedUrl
            ? {
                ...url,
                wCount: refreshedDetail.data.wCount,
                wObservation: refreshedDetail.data.wObservation,
              }
            : url,
        ),
      );
      if (countDelta !== 0) {
        setSites((current) =>
          current.map((site) =>
            site.sitePubId === selectedSite
              ? { ...site, wCount: Math.max(0, site.wCount + countDelta) }
              : site,
          ),
        );
      }
    }
    if (answer) {
      const refreshedAnswer = await getInternalAnswerUvw(headers, projectPubId, answer.answerPubId);
      if (refreshedAnswer.kind === 'ready') {
        setAnswer((current) =>
          current?.answerPubId === refreshedAnswer.data.answerPubId
            ? refreshedAnswer.data
            : current,
        );
      }
    }
    setReviewMessage(
      result.data.decision === 'accepted' ? 'W 片段已复核通过。' : 'W 片段已复核驳回。',
    );
  }

  async function openInspection(inspectionPubId: string) {
    if (fixture) {
      setInspectionEvidence(fixtureInspectionEvidence);
      setInspectionEvidenceState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId) return;
    setInspectionEvidenceState('loading');
    const result = await getInternalSourceInspectionEvidence(
      headers,
      projectPubId,
      inspectionPubId,
    );
    if (result.kind === 'ready') {
      setInspectionEvidence(result.data);
      setInspectionEvidenceState('ready');
    } else {
      setInspectionEvidence(null);
      setInspectionEvidenceState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
    }
  }

  async function openAnswer(answerPubId: string) {
    if (fixture) {
      const selected = fixtureOccurrences.find((row) => row.answerPubId === answerPubId);
      if (!selected) return;
      setAnswer({
        answerPubId,
        question: selected.question,
        platform: selected.platform,
        model: selected.model,
        region: selected.region,
        mode: selected.mode,
        captureTime: selected.capturedAt,
        uObservation: 'observed',
        vObservation: selected.vState === 'unobserved' ? 'unobserved' : 'observed',
        finalReferenceObservation:
          selected.finalReferenceState === 'unobserved' ? 'unobserved' : 'observed',
        occurrences: fixtureOccurrences.filter((row) => row.answerPubId === answerPubId),
        nextCursor: null,
        hasMore: false,
      });
      setAnswerState('ready');
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId) return;
    setAnswerState('loading');
    const result = await getInternalAnswerUvw(headers, projectPubId, answerPubId);
    if (result.kind === 'ready') {
      setAnswer(result.data);
      setAnswerState('ready');
    } else {
      setAnswer(null);
      setAnswerState(result.kind === 'forbidden' ? 'forbidden' : 'failed');
    }
  }

  async function loadMoreAnswerOccurrences() {
    const headers = getValidatedIdentityHeaders();
    if (!headers || !projectPubId || !answer?.nextCursor) return;
    const result = await getInternalAnswerUvw(
      headers,
      projectPubId,
      answer.answerPubId,
      answer.nextCursor,
    );
    if (result.kind === 'ready') {
      setAnswer((current) =>
        current && current.answerPubId === result.data.answerPubId
          ? {
              ...current,
              occurrences: [...current.occurrences, ...result.data.occurrences],
              nextCursor: result.data.nextCursor,
              hasMore: result.data.hasMore,
            }
          : current,
      );
    }
  }

  return (
    <div className="workspace-stack">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Project → Site → URL → Occurrence → Answer</span>
            <h2>信源洞察</h2>
            <p className="panel-subtitle">
              U 是平台实际返回的全部候选；V/W 未暴露时明确显示“不可观察”，不折算为 0。
            </p>
          </div>
          <Badge tone="info">内部权限</Badge>
        </div>
      </section>

      <section className="panel">
        <h2>网站</h2>
        <p className="panel-subtitle">默认按 distinct URL、U occurrence、最近出现时间排序。</p>
        {sitesState === 'loading' ? (
          <StatePanel state="loading" />
        ) : sitesState === 'failed' ? (
          <StatePanel state="failed" onRetry={() => setRetryKey((value) => value + 1)} />
        ) : sitesState === 'forbidden' ? (
          <StatePanel state="forbidden" />
        ) : sites.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <div className="table-scroll" tabIndex={0} aria-label="网站列表滚动区域">
            <table className="data-table">
              <thead>
                <tr>
                  <th>网站</th>
                  <th>distinct URL</th>
                  <th>U occurrence</th>
                  <th>问答</th>
                  <th>V / W</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {sites.map((site) => (
                  <tr key={site.sitePubId}>
                    <td>{site.host}</td>
                    <td>{site.distinctUrlCount}</td>
                    <td>{stageCount('U', site.uOccurrenceCount, site.uObservation)}</td>
                    <td>{site.distinctAnswerCount}</td>
                    <td>
                      {stageCount('V', site.vCount, site.vObservation)} /{' '}
                      {stageCount('W', site.wCount, site.wObservation)}
                    </td>
                    <td>
                      <button type="button" onClick={() => setSelectedSite(site.sitePubId)}>
                        查看 URL
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {sitesCursor ? (
          <button type="button" className="button button-secondary" onClick={loadMoreSites}>
            加载更多网站
          </button>
        ) : null}
      </section>

      {selectedSite ? (
        <section className="panel">
          <h2>网站下的 URL</h2>
          <p className="panel-subtitle">默认按 U occurrence 次数倒序。</p>
          {urlsState === 'loading' ? (
            <StatePanel state="loading" />
          ) : urlsState === 'failed' ? (
            <StatePanel state="failed" />
          ) : urlsState === 'forbidden' ? (
            <StatePanel state="forbidden" />
          ) : urls.length === 0 ? (
            <StatePanel state="empty" />
          ) : (
            <div className="table-scroll" tabIndex={0} aria-label="URL 列表滚动区域">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>U / 问答</th>
                    <th>V / W</th>
                    <th>抓取</th>
                    <th>分析</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {urls.map((url) => (
                    <tr key={url.urlPubId}>
                      <td>{url.canonicalUrl}</td>
                      <td>
                        {stageCount('U', url.uOccurrenceCount, url.uObservation)} /{' '}
                        {url.distinctAnswerCount}
                      </td>
                      <td>
                        {stageCount('V', url.vCount, url.vObservation)} /{' '}
                        {stageCount('W', url.wCount, url.wObservation)}
                      </td>
                      <td>{url.fetchState}</td>
                      <td>{url.analysisState}</td>
                      <td>
                        <button type="button" onClick={() => setSelectedUrl(url.urlPubId)}>
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {urlsCursor ? (
            <button type="button" className="button button-secondary" onClick={loadMoreUrls}>
              加载更多 URL
            </button>
          ) : null}
        </section>
      ) : null}

      {selectedUrl ? (
        urlState === 'loading' ? (
          <StatePanel state="loading" />
        ) : urlState === 'failed' ? (
          <StatePanel state="failed" />
        ) : urlState === 'forbidden' ? (
          <StatePanel state="forbidden" />
        ) : detail ? (
          <>
            <section className="panel">
              <div className="account-head">
                <div>
                  <span className="overline">URL detail</span>
                  <h2>{detail.host}</h2>
                  <a href={detail.canonicalUrl} target="_blank" rel="noreferrer noopener">
                    {detail.canonicalUrl}
                  </a>
                </div>
                <Badge tone={detail.latestSnapshot?.state === 'succeeded' ? 'positive' : 'warning'}>
                  {detail.latestSnapshot?.state ?? '尚无快照'}
                </Badge>
              </div>
              <dl className="case-summary">
                <div>
                  <dt>U occurrence / distinct answer</dt>
                  <dd>
                    {stageCount('U', detail.uOccurrenceCount, detail.uObservation)} /{' '}
                    {detail.distinctAnswerCount}
                  </dd>
                </div>
                <div>
                  <dt>V / W</dt>
                  <dd>
                    {stageCount('V', detail.vCount, detail.vObservation)} /{' '}
                    {stageCount('W', detail.wCount, detail.wObservation)}
                  </dd>
                </div>
                <div>
                  <dt>抓取尝试 / 页面版本</dt>
                  <dd>
                    {detail.fetchAttemptCount} / {detail.latestSnapshot ? '≥1' : '0'}
                  </dd>
                </div>
                <div>
                  <dt>体检 / finding</dt>
                  <dd>
                    {detail.pageInspectionCount} / {detail.findingCount}
                  </dd>
                </div>
              </dl>
            </section>

            <section className="panel">
              <h2>页面快照、历史版本与风险体检</h2>
              <p className="panel-subtitle">
                快照和体检结论按版本保留；逐字 span 必须与对应页面内容哈希一致。
              </p>
              {historyState === 'loading' ? (
                <StatePanel state="loading" />
              ) : historyState === 'failed' ? (
                <StatePanel state="failed" />
              ) : historyState === 'forbidden' ? (
                <StatePanel state="forbidden" />
              ) : (
                <>
                  <h3>页面快照</h3>
                  {snapshots.length === 0 ? (
                    <p className="panel-subtitle">尚无页面快照；抓取状态仍保留在处理分母中。</p>
                  ) : (
                    <div className="table-scroll" tabIndex={0} aria-label="页面快照滚动区域">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>抓取时间</th>
                            <th>状态 / HTTP</th>
                            <th>标题 / 作者</th>
                            <th>提取器</th>
                            <th>内容哈希</th>
                          </tr>
                        </thead>
                        <tbody>
                          {snapshots.map((snapshot) => (
                            <tr key={snapshot.snapshotPubId}>
                              <td>{snapshot.capturedAt}</td>
                              <td>
                                {snapshot.state} / {snapshot.httpStatus ?? '—'}
                                {snapshot.fetchErrorCode ? ` · ${snapshot.fetchErrorCode}` : ''}
                              </td>
                              <td>
                                {snapshot.title ?? '—'} / {snapshot.author ?? '—'}
                              </td>
                              <td>{snapshot.extractorVersion ?? '不可用'}</td>
                              <td>{snapshot.textSha256 ?? '不可用'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {snapshotCursor ? (
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={loadMoreSnapshots}
                    >
                      加载更多快照
                    </button>
                  ) : null}

                  <h3>版本化风险体检</h3>
                  {inspections.length === 0 ? (
                    <p className="panel-subtitle">尚无页面体检版本。</p>
                  ) : (
                    <div className="table-scroll" tabIndex={0} aria-label="页面体检滚动区域">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>创建时间</th>
                            <th>状态</th>
                            <th>策略 / 提示词 / 模型</th>
                            <th>finding</th>
                            <th>证据链</th>
                          </tr>
                        </thead>
                        <tbody>
                          {inspections.map((inspection) => (
                            <tr key={inspection.inspectionPubId}>
                              <td>{inspection.createdAt}</td>
                              <td>{inspection.status}</td>
                              <td>
                                {inspection.policyVersion} / {inspection.promptVersion} /{' '}
                                {inspection.model}
                              </td>
                              <td>{inspection.findingCount}</td>
                              <td>
                                <button
                                  type="button"
                                  onClick={() => void openInspection(inspection.inspectionPubId)}
                                >
                                  查看逐字证据
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {inspectionCursor ? (
                    <button
                      type="button"
                      className="button button-secondary"
                      onClick={loadMoreInspections}
                    >
                      加载更多体检版本
                    </button>
                  ) : null}
                </>
              )}
            </section>

            <section className="panel">
              <h2>W 内容片段、版本与人工复核</h2>
              <p className="panel-subtitle">
                W 只展示可回校验的页面正文片段；最终引用 URL 本身不会自动成为
                W。复核记录追加保存，历史分析版本不会被覆盖。
              </p>
              {wChunkState === 'loading' ? (
                <StatePanel state="loading" />
              ) : wChunkState === 'failed' ? (
                <StatePanel state="failed" />
              ) : wChunkState === 'forbidden' ? (
                <StatePanel state="forbidden" />
              ) : wChunks.length === 0 ? (
                <p className="panel-subtitle">当前页面没有可验证的 W 内容片段。</p>
              ) : (
                <div className="workspace-stack">
                  {wChunks.map((chunk) => (
                    <article className="case-card" key={chunk.chunkPubId}>
                      <div className="account-head">
                        <div>
                          <h3>
                            W #{chunk.ordinal} · {chunk.basis}
                          </h3>
                          <p className="panel-subtitle">
                            {chunk.policyVersion} / {chunk.promptVersion} / {chunk.model}
                          </p>
                        </div>
                        <Badge
                          tone={
                            chunk.reviewState === 'rejected'
                              ? 'warning'
                              : chunk.reviewState === 'accepted'
                                ? 'positive'
                                : 'neutral'
                          }
                        >
                          {chunk.verificationState} · {chunk.reviewState}
                        </Badge>
                      </div>
                      <blockquote>
                        “{chunk.sourceQuote}”
                        <footer className="evidence-digest">
                          source exact [{chunk.sourceTextStart}, {chunk.sourceTextEnd}) ·{' '}
                          {chunk.sourceQuoteHash}
                        </footer>
                      </blockquote>
                      {chunk.answerQuote ? (
                        <blockquote>
                          答案：“{chunk.answerQuote}”
                          <footer className="evidence-digest">
                            answer [{chunk.answerTextStart}, {chunk.answerTextEnd}) ·{' '}
                            {chunk.answerQuoteHash}
                          </footer>
                        </blockquote>
                      ) : null}
                      <p className="panel-subtitle">
                        贡献 {chunk.contributionScore.toFixed(2)} · 置信度{' '}
                        {chunk.confidence.toFixed(2)} · 算法 {chunk.algorithmVersion} · 分析时间{' '}
                        {chunk.analysisCreatedAt}
                      </p>
                      {chunk.latestReview ? (
                        <p className="panel-subtitle">
                          最近复核：{chunk.latestReview.decision} · {chunk.latestReview.rationale} ·{' '}
                          {chunk.latestReview.reviewerPubId} · {chunk.latestReview.reviewedAt}（累计{' '}
                          {chunk.reviewCount} 次）
                        </p>
                      ) : (
                        <p className="panel-subtitle">尚未人工复核。</p>
                      )}
                      <label>
                        复核依据
                        <textarea
                          aria-label={`复核依据 ${chunk.chunkPubId}`}
                          value={reviewRationales[chunk.chunkPubId] ?? ''}
                          maxLength={4000}
                          onChange={(event) => {
                            const value = event.currentTarget.value;
                            setReviewRationales((current) => ({
                              ...current,
                              [chunk.chunkPubId]: value,
                            }));
                          }}
                        />
                      </label>
                      <div className="action-row">
                        <button
                          type="button"
                          disabled={
                            reviewingChunk === chunk.chunkPubId ||
                            chunk.verificationState === 'rejected'
                          }
                          onClick={() => void reviewChunk(chunk.chunkPubId, 'accepted')}
                        >
                          复核通过
                        </button>
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={
                            reviewingChunk === chunk.chunkPubId ||
                            chunk.verificationState === 'rejected'
                          }
                          onClick={() => void reviewChunk(chunk.chunkPubId, 'rejected')}
                        >
                          复核驳回
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {reviewMessage ? <p role="status">{reviewMessage}</p> : null}
              {wChunkCursor ? (
                <button type="button" className="button button-secondary" onClick={loadMoreWChunks}>
                  加载更多 W 版本
                </button>
              ) : null}
            </section>

            <section className="panel">
              <h2>全部问答 occurrence</h2>
              <div className="table-scroll" tabIndex={0} aria-label="问答 occurrence 滚动区域">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>时间 / 平台</th>
                      <th>问题 / 检索词</th>
                      <th>U</th>
                      <th>V</th>
                      <th>W</th>
                      <th>证据</th>
                      <th>问答</th>
                    </tr>
                  </thead>
                  <tbody>
                    {occurrences.map((row) => (
                      <tr key={row.occurrencePubId}>
                        <td>
                          {row.capturedAt}
                          <br />
                          {row.platform} · {row.model}
                        </td>
                        <td>
                          {row.question}
                          <br />
                          {row.query ?? '检索词不可观察'}
                        </td>
                        <td>{row.uRank === null ? label(row.uState) : `#${row.uRank}`}</td>
                        <td>
                          {label(row.vState)}
                          {row.vOpenOrder === null ? '' : ` · #${row.vOpenOrder}`}
                        </td>
                        <td>
                          {label(row.wState)}
                          {row.wWeight === null ? '' : ` · ${row.wWeight.toFixed(2)}`}
                        </td>
                        <td>{label(row.evidenceState)}</td>
                        <td>
                          <button type="button" onClick={() => void openAnswer(row.answerPubId)}>
                            查看问答
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {occurrenceCursor ? (
                <button
                  type="button"
                  className="button button-secondary"
                  onClick={loadMoreOccurrences}
                >
                  加载更多 occurrence
                </button>
              ) : null}
            </section>
          </>
        ) : (
          <StatePanel state="empty" />
        )
      ) : null}

      {inspectionEvidenceState === 'loading' ? <StatePanel state="loading" /> : null}
      {inspectionEvidenceState === 'failed' ? <StatePanel state="failed" /> : null}
      {inspectionEvidenceState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {inspectionEvidence ? (
        <section className="panel">
          <div className="account-head">
            <div>
              <span className="overline">Inspection evidence chain</span>
              <h2>风险发现与逐字证据</h2>
              <p className="panel-subtitle">
                页面内容哈希：{inspectionEvidence.contentSha256} · {inspectionEvidence.status}
              </p>
            </div>
            <button type="button" onClick={() => setInspectionEvidence(null)}>
              关闭
            </button>
          </div>
          {inspectionEvidence.findings.length === 0 ? (
            <StatePanel state="empty" />
          ) : (
            <div className="workspace-stack">
              {inspectionEvidence.findings.map((finding) => (
                <article className="case-card" key={finding.findingPubId}>
                  <div className="account-head">
                    <h3>
                      {finding.code} · {finding.ledger}
                    </h3>
                    <Badge tone={finding.status === 'confirmed' ? 'positive' : 'warning'}>
                      {finding.status}
                    </Badge>
                  </div>
                  <p>{finding.summary}</p>
                  <p className="panel-subtitle">建议动作：{finding.action}</p>
                  <ol>
                    {finding.evidenceChain.map((link, index) => (
                      <li key={`${finding.findingPubId}-chain-${index}`}>
                        {link.connector} · {link.factType} · {link.explanation}
                        {link.quote ? <blockquote>{link.quote}</blockquote> : null}
                      </li>
                    ))}
                  </ol>
                  {finding.spans.map((span) => (
                    <blockquote key={span.spanPubId}>
                      “{span.quote}”
                      <footer>
                        exact [{span.textStart}, {span.textEnd}) · {span.quoteHash}
                      </footer>
                    </blockquote>
                  ))}
                </article>
              ))}
            </div>
          )}
        </section>
      ) : null}

      {answerState === 'loading' ? <StatePanel state="loading" /> : null}
      {answerState === 'failed' ? <StatePanel state="failed" /> : null}
      {answerState === 'forbidden' ? <StatePanel state="forbidden" /> : null}
      {answer ? (
        <section className="panel">
          <div className="account-head">
            <div>
              <span className="overline">Internal answer detail</span>
              <h2>{answer.question}</h2>
              <p className="panel-subtitle">
                {answer.platform} · {answer.model} · {answer.captureTime}
              </p>
            </div>
            <button type="button" onClick={() => setAnswer(null)}>
              关闭
            </button>
          </div>
          <dl className="case-summary">
            <div>
              <dt>U 可观察性</dt>
              <dd>{label(answer.uObservation)}</dd>
            </div>
            <div>
              <dt>V 可观察性</dt>
              <dd>{label(answer.vObservation)}</dd>
            </div>
            <div>
              <dt>最终引用可观察性</dt>
              <dd>{label(answer.finalReferenceObservation)}</dd>
            </div>
            <div>
              <dt>本页 occurrence</dt>
              <dd>{answer.occurrences.length}</dd>
            </div>
          </dl>
          <div className="table-scroll" tabIndex={0} aria-label="回答完整 UVW 滚动区域">
            <table className="data-table" aria-label="回答完整 UVW 滚动区域">
              <thead>
                <tr>
                  <th>URL</th>
                  <th>检索词 / U</th>
                  <th>V</th>
                  <th>W</th>
                  <th>最终引用</th>
                </tr>
              </thead>
              <tbody>
                {answer.occurrences.map((row) => (
                  <tr key={row.occurrencePubId}>
                    <td>{row.canonicalUrl}</td>
                    <td>
                      {row.query ?? '不可观察'} /{' '}
                      {row.uRank === null ? label(row.uState) : `#${row.uRank}`}
                    </td>
                    <td>
                      {label(row.vState)}
                      {row.vOpenOrder === null ? '' : ` · #${row.vOpenOrder}`}
                    </td>
                    <td>
                      {label(row.wState)}
                      {row.wWeight === null ? '' : ` · ${row.wWeight.toFixed(2)}`}
                    </td>
                    <td>{label(row.finalReferenceState)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {answer.hasMore && answer.nextCursor ? (
            <button
              type="button"
              className="button button-secondary"
              onClick={loadMoreAnswerOccurrences}
            >
              加载该回答更多 UVW occurrence
            </button>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
