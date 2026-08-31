import {
  getCustomerSamplingProgress,
  type CustomerSamplingProgress,
  type CustomerSamplingProgressCell,
  type CustomerSamplingProgressColumn,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { Dialog, useOptionalExperienceContext } from '@geo/design-system';
import { useRef, useState } from 'react';
import './customer-sampling-progress.css';

type ProgressState =
  | { kind: 'loading' }
  | { kind: 'ready'; progress: CustomerSamplingProgress }
  | { kind: 'forbidden' }
  | { kind: 'failed' };

const modeLabels: Record<string, string> = {
  normal: '快速模式',
  deep_think: '深度思考',
};

const platformLabels: Record<string, string> = {
  doubao: '豆包',
  deepseek: 'DeepSeek',
  yuanbao: '腾讯元宝',
  qwen: '千问',
  wenxin: '文心一言',
  chatgpt: 'ChatGPT',
};

const fixtureProgress: CustomerSamplingProgress = {
  projectPubId: 'prj_fixture',
  configRevisionStart: 8,
  configRevisionEnd: 8,
  columns: [
    { key: 'doubao-beijing', model: 'doubao', region: '北京', mode: 'normal', modes: ['normal'] },
    {
      key: 'deepseek-shanghai',
      model: 'deepseek',
      region: '上海',
      mode: 'deep_think',
      modes: ['deep_think'],
    },
  ],
  rows: [
    {
      appendix: '附录二',
      group: 'G01',
      groupName: '品牌认知',
      expression: '原词',
      queryText: '当前品牌在行业中的优势是什么？',
      cells: [
        {
          columnKey: 'doubao-beijing',
          completedSamples: 2,
          latestCaptureTime: '2026-08-28T02:00:00Z',
          modeBreakdown: [
            { mode: 'normal', completedSamples: 2, latestCaptureTime: '2026-08-28T02:00:00Z' },
          ],
        },
      ],
    },
    {
      appendix: '附录二',
      group: 'G02',
      groupName: '服务选择',
      expression: '优化句',
      queryText: '选择该类服务时应该关注哪些能力？',
      cells: [],
    },
  ],
  observedCells: 1,
  totalCells: 4,
  answerCount: 2,
  latestCaptureTime: '2026-08-28T02:00:00Z',
  liveRuns: 0,
};

const platformLabel = (model: string): string => platformLabels[model] ?? model;

function revisionLabel(progress: CustomerSamplingProgress): string {
  if (progress.configRevisionStart === null || progress.configRevisionEnd === null) {
    return '尚无冻结配置';
  }
  return progress.configRevisionStart === progress.configRevisionEnd
    ? `配置 v${progress.configRevisionStart}`
    : `配置 v${progress.configRevisionStart}–v${progress.configRevisionEnd}`;
}

function formatCaptureTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function modeBreakdownLabel(
  column: CustomerSamplingProgressColumn,
  cell: CustomerSamplingProgressCell,
): string {
  const breakdown = cell.modeBreakdown.length
    ? cell.modeBreakdown
    : [{ mode: column.mode, completedSamples: cell.completedSamples }];
  return breakdown
    .map((item) => `${modeLabels[item.mode] ?? item.mode} ${item.completedSamples}遍`)
    .join(' · ');
}

function CustomerSamplingProgressMatrix({ progress }: { progress: CustomerSamplingProgress }) {
  return (
    <div
      className="customer-sampling-progress-scroll"
      tabIndex={0}
      aria-label="客户采样进度横竖滚动区域"
    >
      <table className="customer-sampling-progress-table" aria-label="客户采样进度全景表">
        <thead>
          <tr>
            <th>附录</th>
            <th>组</th>
            <th>表述</th>
            <th>问题</th>
            {progress.columns.map((column) => (
              <th key={column.key}>
                {platformLabel(column.model)}×{column.region}
                <small>{modeLabels[column.mode] ?? column.mode}</small>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {progress.rows.map((row) => {
            const cells = new Map(row.cells.map((cell) => [cell.columnKey, cell]));
            return (
              <tr key={`${row.group}:${row.expression}:${row.queryText}`}>
                <td>{row.appendix ?? '—'}</td>
                <td title={row.groupName}>{row.group}</td>
                <td>{row.expression}</td>
                <td>{row.queryText}</td>
                {progress.columns.map((column) => {
                  const cell = cells.get(column.key);
                  return (
                    <td key={column.key} className={cell ? 'is-observed' : 'is-empty'}>
                      {cell ? (
                        <>
                          <strong>{cell.completedSamples}遍</strong>
                          <time dateTime={cell.latestCaptureTime}>
                            {formatCaptureTime(cell.latestCaptureTime)}
                          </time>
                          <small>{modeBreakdownLabel(column, cell)}</small>
                        </>
                      ) : (
                        <span aria-label="尚无有效观测">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CustomerSamplingProgressDialog({
  state,
  onClose,
  onRetry,
}: {
  state: ProgressState;
  onClose: () => void;
  onRetry: () => void;
}) {
  return (
    <Dialog
      title="采样进度"
      eyebrow="完整问题 × 全部采样位"
      closeLabel="关闭采样进度"
      onClose={onClose}
      size="wide"
    >
      <div className="customer-sampling-progress-dialog">
        {state.kind === 'loading' ? (
          <p className="customer-sampling-progress-state">正在加载完整采样进度…</p>
        ) : state.kind === 'forbidden' ? (
          <p className="customer-sampling-progress-state">当前账号无权查看该项目的采样进度。</p>
        ) : state.kind === 'failed' ? (
          <p className="customer-sampling-progress-state">
            采样进度加载失败。
            <button className="button button-secondary" type="button" onClick={onRetry}>
              重试
            </button>
          </p>
        ) : state.progress.rows.length === 0 ? (
          <p className="customer-sampling-progress-state">该项目尚无可展示的冻结采样配置。</p>
        ) : (
          <>
            <div className="customer-sampling-progress-summary" aria-label="客户采样进度摘要">
              <span>{revisionLabel(state.progress)}</span>
              <span>{state.progress.rows.length} 问</span>
              <span>{state.progress.columns.length} 个采样位</span>
              <span>
                已观测 {state.progress.observedCells}/{state.progress.totalCells} 格
              </span>
              <span>共 {state.progress.answerCount} 条有效回答</span>
            </div>
            <p className="customer-sampling-progress-note">
              仅统计合格且非降级的有效回答。横向滚动查看采样位，纵向滚动查看全部问题。
            </p>
            <CustomerSamplingProgressMatrix progress={state.progress} />
          </>
        )}
      </div>
    </Dialog>
  );
}

export function CustomerSamplingProgressEntry() {
  const experience = useOptionalExperienceContext();
  const [state, setState] = useState<ProgressState | null>(null);
  const requestSerial = useRef(0);

  const open = async () => {
    const requestId = ++requestSerial.current;
    if (experience?.source !== 'live') {
      setState({ kind: 'ready', progress: fixtureProgress });
      return;
    }
    const headers = getValidatedIdentityHeaders();
    if (!headers || !experience.projectPubId) {
      setState({ kind: 'forbidden' });
      return;
    }
    setState({ kind: 'loading' });
    const result = await getCustomerSamplingProgress(headers, experience.projectPubId);
    if (requestId !== requestSerial.current) return;
    if (result.kind === 'ready') setState({ kind: 'ready', progress: result.data });
    else setState({ kind: result.kind === 'forbidden' ? 'forbidden' : 'failed' });
  };

  const close = () => {
    requestSerial.current += 1;
    setState(null);
  };

  return (
    <>
      <section className="panel geo-dashboard-panel customer-sampling-progress-entry">
        <div>
          <span>Sampling coverage</span>
          <h3>采样进度</h3>
          <p>查看完整问题集在各 AI 平台、地域和回答模式下的有效采样覆盖情况。</p>
        </div>
        <button className="button" type="button" onClick={() => void open()}>
          查看采样进度
        </button>
      </section>
      {state ? (
        <CustomerSamplingProgressDialog state={state} onClose={close} onRetry={() => void open()} />
      ) : null}
    </>
  );
}
