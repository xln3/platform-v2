import { Pagination } from '@geo/design-system';
import { useEffect, useMemo, useState } from 'react';
import { PlatformBadge, platformDisplayName } from '../../platforms';
import { usePageWindow } from '../../pagination';
import { type CurrentConfig, type FrozenConfig } from '../execution/api';
import { servicesApi, type SessionContext } from './api';

const SURFACE_LABELS = {
  provider_api: 'API',
  consumer_web: '网页端应用',
  consumer_app: '移动端 APP',
} as const;

const MODE_LABELS: Record<string, string> = {
  normal: '快速模式',
  deep_think: '深度思考',
  research: '研究模式',
};

const PROVINCE_LABELS: Readonly<Record<string, string>> = {
  '110000': '北京市',
  '120000': '天津市',
  '130000': '河北省',
  '140000': '山西省',
  '150000': '内蒙古自治区',
  '210000': '辽宁省',
  '220000': '吉林省',
  '230000': '黑龙江省',
  '310000': '上海市',
  '320000': '江苏省',
  '330000': '浙江省',
  '340000': '安徽省',
  '350000': '福建省',
  '360000': '江西省',
  '370000': '山东省',
  '410000': '河南省',
  '420000': '湖北省',
  '430000': '湖南省',
  '440000': '广东省',
  '450000': '广西壮族自治区',
  '460000': '海南省',
  '500000': '重庆市',
  '510000': '四川省',
  '520000': '贵州省',
  '530000': '云南省',
  '540000': '西藏自治区',
  '610000': '陕西省',
  '620000': '甘肃省',
  '630000': '青海省',
  '640000': '宁夏回族自治区',
  '650000': '新疆维吾尔自治区',
};

type ReadState = { kind: 'loading' } | { kind: 'failed' } | { kind: 'ready'; data: CurrentConfig };

type QuestionLine = { group: string; text: string };
type TargetLine = {
  platform: string;
  collectionSurface: keyof typeof SURFACE_LABELS;
  productVariant: string | null;
  modes: string[];
};

type ParsedConfig = {
  schemaVersion: 'collection-config-v1-web-view' | 'collection-config-v2' | 'unknown';
  questions: QuestionLine[];
  questionSetRevision: string | null;
  targets: TargetLine[];
  regions: string[];
  legacyRegions: boolean;
  samplesPerCell: number | null;
  taskCount: number | null;
};

export function ReadonlyConfigSummary({
  session,
  projectPubId,
}: {
  session: SessionContext;
  projectPubId: string;
}) {
  const [state, setState] = useState<ReadState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: 'loading' });
    void servicesApi.currentConfig(session, projectPubId).then(
      (data) => {
        if (!cancelled) setState({ kind: 'ready', data });
      },
      () => {
        if (!cancelled) setState({ kind: 'failed' });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [session, projectPubId]);

  return (
    <section className="execution-card readonly-config-summary">
      <div className="section-title">
        <h2>本次评测配置</h2>
        <span>只读 · 以服务端冻结版本和哈希为准</span>
      </div>
      {state.kind === 'loading' ? (
        <p className="empty">正在读取生效配置…</p>
      ) : state.kind === 'failed' ? (
        <p className="empty">配置摘要暂不可用，请稍后重试。</p>
      ) : state.data.effective ? (
        <ConfigRevisionSummary
          config={state.data.effective}
          pending={state.data.next_pending}
          projectPubId={projectPubId}
        />
      ) : (
        <div className="readonly-config-empty">
          <p>该项目目前没有已生效的冻结配置。</p>
          {state.data.next_pending ? (
            <p>
              已冻结 v{state.data.next_pending.revision}，将在{' '}
              {formatTime(state.data.next_pending.effective_at)} 生效。
            </p>
          ) : null}
          <a href="/platform/operations/onboarding">前往开户向导</a>
        </div>
      )}
    </section>
  );
}

function ConfigRevisionSummary({
  config,
  pending,
  projectPubId,
}: {
  config: FrozenConfig;
  pending: FrozenConfig | null;
  projectPubId: string;
}) {
  const [hashCopyStatus, setHashCopyStatus] = useState('');
  const parsed = useMemo(
    () => parseConfig(config.snapshot, config.question_groups),
    [config.question_groups, config.snapshot],
  );
  const questions = usePageWindow(parsed.questions, `${projectPubId}:${config.pub_id}`);
  const platforms = [...new Set(parsed.targets.map((target) => target.platform))];
  const surfaces = [...new Set(parsed.targets.map((target) => target.collectionSurface))];
  const modes = [...new Set(parsed.targets.flatMap((target) => target.modes))];

  return (
    <>
      <dl className="readonly-config-meta">
        <div>
          <dt>配置版本</dt>
          <dd>v{config.revision}</dd>
        </div>
        <div>
          <dt>冻结时间</dt>
          <dd>{formatTime(config.frozen_at)}</dd>
        </div>
        <div>
          <dt>生效时间</dt>
          <dd>{formatTime(config.effective_at)}</dd>
        </div>
        <div>
          <dt>快照哈希</dt>
          <dd>
            <code>{config.snapshot_hash.slice(0, 12)}…</code>
            <button
              type="button"
              className="readonly-config-copy-hash"
              onClick={() => {
                const clipboard = globalThis.navigator?.clipboard;
                if (!clipboard) {
                  setHashCopyStatus('当前浏览器无法直接复制，请展开查看完整哈希。');
                  return;
                }
                void clipboard.writeText(config.snapshot_hash).then(
                  () => setHashCopyStatus('完整哈希已复制。'),
                  () => setHashCopyStatus('复制失败，请展开查看完整哈希。'),
                );
              }}
            >
              复制完整哈希
            </button>
            <details>
              <summary>查看完整哈希</summary>
              <code className="readonly-config-full-hash">{config.snapshot_hash}</code>
            </details>
            <span className="sr-only" role="status" aria-live="polite">
              {hashCopyStatus}
            </span>
          </dd>
        </div>
        <div>
          <dt>问题组 / 问题数</dt>
          <dd>
            {new Set(parsed.questions.map((question) => question.group)).size || '未提供'} /{' '}
            {parsed.questions.length || '未提供'}
          </dd>
        </div>
        <div>
          <dt>题库修订</dt>
          <dd>{parsed.questionSetRevision ?? '旧版快照未单独冻结'}</dd>
        </div>
        <div>
          <dt>采集来源</dt>
          <dd>{surfaces.map((surface) => SURFACE_LABELS[surface]).join(' · ') || '未冻结'}</dd>
        </div>
        <div>
          <dt>模式</dt>
          <dd>{modes.map((mode) => MODE_LABELS[mode] ?? mode).join(' · ') || '未冻结'}</dd>
        </div>
        <div>
          <dt>每格重复次数</dt>
          <dd>{parsed.samplesPerCell ?? '未冻结'}</dd>
        </div>
        <div>
          <dt>任务规模</dt>
          <dd>
            {parsed.taskCount === null
              ? '冻结信息不足，无法可靠计算'
              : `${parsed.taskCount} 个主采样位`}
          </dd>
        </div>
      </dl>

      <div className="readonly-config-block">
        <h3>平台</h3>
        <div className="readonly-config-tags" aria-label="冻结平台">
          {platforms.map((platform) => (
            <span key={platform} className="readonly-config-tag">
              <PlatformBadge platform={platform} />
            </span>
          ))}
        </div>
      </div>

      <div className="readonly-config-block">
        <h3>地域</h3>
        {parsed.legacyRegions ? (
          <p className="service-note">以下为旧版地域值，不代表已规范化为省级行政区。</p>
        ) : null}
        <div className="readonly-config-tags" aria-label="冻结地域">
          {parsed.regions.map((region) => (
            <span key={region} className="readonly-config-tag">
              {region}
              {parsed.legacyRegions ? ' · 旧版地域值' : ''}
            </span>
          ))}
          {parsed.regions.length === 0 ? <span>未冻结</span> : null}
        </div>
      </div>

      <div className="readonly-config-block">
        <h3>采集目标</h3>
        <ul className="readonly-config-targets">
          {parsed.targets.map((target) => (
            <li
              key={`${target.platform}:${target.collectionSurface}:${target.productVariant ?? 'legacy'}`}
            >
              {platformDisplayName(target.platform)} · {SURFACE_LABELS[target.collectionSurface]} ·{' '}
              {target.productVariant ?? '旧版产品身份未冻结'} ·{' '}
              {target.modes.map((mode) => MODE_LABELS[mode] ?? mode).join(' / ') || '模式未冻结'}
            </li>
          ))}
        </ul>
      </div>

      <div className="readonly-config-block">
        <h3>问题明细</h3>
        {questions.visibleItems.length > 0 ? (
          <ol className="readonly-config-questions" start={(questions.page - 1) * 4 + 1}>
            {questions.visibleItems.map((question, index) => (
              <li key={`${question.group}:${question.text}:${index}`}>
                <span>{question.group}</span>
                <p>{question.text}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="service-note">
            当前快照仅冻结题库修订身份，尚无可安全投影的问题文本；不会从其他版本猜取。
          </p>
        )}
        {parsed.questions.length > 0 ? (
          <Pagination
            page={questions.page}
            pageCount={questions.pageCount}
            totalItems={parsed.questions.length}
            onPageChange={questions.setPage}
            label="冻结问题分页"
          />
        ) : null}
      </div>

      {pending ? (
        <p className="service-note" role="status">
          下一待生效版本：v{pending.revision}，计划于 {formatTime(pending.effective_at)}{' '}
          生效；当前结果仍绑定 v{config.revision}。
        </p>
      ) : null}
      <p className="readonly-config-navigation">
        配置新增与变更不在本页执行。<a href="/platform/operations/onboarding">前往开户向导</a>
      </p>
    </>
  );
}

export function parseConfig(
  snapshot: Record<string, unknown>,
  questionGroups?: FrozenConfig['question_groups'],
): ParsedConfig {
  if (snapshot.schema_version === 'collection-config-v2') return parseV2(snapshot, questionGroups);
  if (isV1Snapshot(snapshot)) return parseV1(snapshot);
  return {
    schemaVersion: 'unknown',
    questions: [],
    questionSetRevision: null,
    targets: [],
    regions: [],
    legacyRegions: true,
    samplesPerCell: null,
    taskCount: null,
  };
}

function parseV1(snapshot: Record<string, unknown>): ParsedConfig {
  const questions = parseQuestions(snapshot.query_groups);
  const models = stringArray(snapshot.models);
  const modes = stringArray(snapshot.modes);
  const regions = stringArray(snapshot.regions);
  return {
    schemaVersion: 'collection-config-v1-web-view',
    questions,
    questionSetRevision: null,
    targets: models.map((platform) => ({
      platform,
      collectionSurface: 'consumer_web',
      productVariant: null,
      modes,
    })),
    regions,
    legacyRegions: true,
    samplesPerCell: null,
    taskCount: null,
  };
}

function parseV2(
  snapshot: Record<string, unknown>,
  questionGroups?: FrozenConfig['question_groups'],
): ParsedConfig {
  const questions = parseQuestions(questionGroups);
  const targets = Array.isArray(snapshot.collection_targets)
    ? snapshot.collection_targets.flatMap<TargetLine>((value) => {
        if (!isRecord(value) || typeof value.platform !== 'string') return [];
        if (!isSurface(value.collection_surface)) return [];
        return [
          {
            platform: value.platform,
            collectionSurface: value.collection_surface,
            productVariant:
              typeof value.product_variant === 'string' ? value.product_variant : null,
            modes: stringArray(value.interaction_modes),
          },
        ];
      })
    : [];
  const regions = stringArray(snapshot.province_codes).map(
    (code) => `${PROVINCE_LABELS[code] ?? '未知省级地域'}（GB${code}）`,
  );
  const samples =
    typeof snapshot.samples_per_cell === 'number' &&
    Number.isSafeInteger(snapshot.samples_per_cell) &&
    snapshot.samples_per_cell > 0
      ? snapshot.samples_per_cell
      : null;
  const modeLegCount = targets.reduce((sum, target) => sum + target.modes.length, 0);
  const taskCount =
    questions.length > 0 && regions.length > 0 && modeLegCount > 0 && samples !== null
      ? questions.length * regions.length * modeLegCount * samples
      : null;
  return {
    schemaVersion: 'collection-config-v2',
    questions,
    questionSetRevision:
      typeof snapshot.question_set_revision === 'string' ? snapshot.question_set_revision : null,
    targets,
    regions,
    legacyRegions: false,
    samplesPerCell: samples,
    taskCount,
  };
}

function parseQuestions(value: unknown): QuestionLine[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap<QuestionLine>((group) => {
    if (!isRecord(group) || !Array.isArray(group.items)) return [];
    const groupName = typeof group.name === 'string' ? group.name : '未命名问题组';
    return group.items.flatMap((item) => {
      if (typeof item === 'string' && item.trim()) return [{ group: groupName, text: item.trim() }];
      if (isRecord(item) && typeof item.text === 'string' && item.text.trim()) {
        return [{ group: groupName, text: item.text.trim() }];
      }
      return [];
    });
  });
}

function isV1Snapshot(snapshot: Record<string, unknown>): boolean {
  return (
    Array.isArray(snapshot.query_groups) &&
    Array.isArray(snapshot.models) &&
    Array.isArray(snapshot.modes) &&
    Array.isArray(snapshot.regions)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.length > 0)
    : [];
}

function isSurface(value: unknown): value is keyof typeof SURFACE_LABELS {
  return value === 'provider_api' || value === 'consumer_web' || value === 'consumer_app';
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}
