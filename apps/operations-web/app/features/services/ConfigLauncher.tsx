import { useMemo, useState } from 'react';
import { executionApi } from '../execution/api';
import { DEFAULT_REGIONS, RegionMultiSelect } from './RegionMultiSelect';
import type { SessionContext } from './api';

export const LAUNCHER_PLATFORMS = [
  ['doubao', '豆包'],
  ['deepseek', 'DeepSeek'],
  ['yiyan', '文心一言'],
  ['tongyi', '通义千问'],
  ['yuanbao', '腾讯元宝'],
] as const;

const FREQUENCIES = [
  ['manual', '手动'],
  ['daily', '每日'],
  ['weekly', '每周'],
  ['monthly', '每月'],
] as const;

// 非 manual 频率在冻结成功后落地为真实调度（POST /api/v2/schedules），
// interval_minutes 与服务端 monitoring_schedule 口径一致。
const FREQUENCY_INTERVAL_MINUTES: Record<string, number> = {
  daily: 1440,
  weekly: 10080,
  monthly: 43200,
};

function frequencyLabel(value: string): string {
  return FREQUENCIES.find(([candidate]) => candidate === value)?.[1] ?? value;
}

// 平台 mode 词表（20260810 用户拍板）：deepseek 专家模式不支持搜索——GEO 评测
// 不测专家，测快速+搜索的「思考关/开」两种组合；元宝联网检索为平台自动行为
// （无开关），测 Hy3 的「思考关/开」两种；文心测「深度思考」chip 关/开两种
// （20260810 适配器解锁）；通义测 composer 菜单「快速/思考研究」两种
// （20260810 适配器解锁）；其余平台仅 normal。与服务端
// PLATFORM_MODE_CAPABILITIES（workflows/activities/collection.py）保持一致。
const MODES_BY_MODEL: Record<string, string[]> = {
  deepseek: ['normal', 'deep_think'],
  yuanbao: ['normal', 'deep_think'],
  yiyan: ['normal', 'deep_think'],
  tongyi: ['normal', 'deep_think'],
};

function modesForModel(slug: string): string[] {
  return MODES_BY_MODEL[slug] ?? ['normal'];
}

type LaunchResult = {
  revision: number;
  hashPrefix: string;
  startedRuns: number;
  schedule: { label: string; intervalMinutes: number; nextRunAt: string } | null;
};

type Props = {
  session: SessionContext;
  projectPubId: string;
  groupName: string;
  defaultModels?: string[];
  defaultRegions?: string[];
  queryPlaceholder?: string;
  onChanged?: () => void;
};

export function ConfigLauncher({
  session,
  projectPubId,
  groupName,
  defaultModels,
  defaultRegions,
  queryPlaceholder,
  onChanged,
}: Props) {
  const [questions, setQuestions] = useState('');
  const [models, setModels] = useState<string[]>(defaultModels ?? ['doubao', 'deepseek', 'yiyan']);
  const [regions, setRegions] = useState<string[]>(defaultRegions ?? DEFAULT_REGIONS);
  const [samples, setSamples] = useState(2);
  const [frequency, setFrequency] = useState<string>('manual');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [result, setResult] = useState<LaunchResult | null>(null);

  const questionItems = useMemo(
    () =>
      questions
        .split('\n')
        .map((item) => item.trim())
        .filter(Boolean),
    [questions],
  );
  const modes = useMemo(() => [...new Set(models.flatMap(modesForModel))], [models]);
  const platformModeCount = models.reduce((sum, slug) => sum + modesForModel(slug).length, 0);
  const perRound = questionItems.length * platformModeCount * regions.length;
  const boundedSamples = Number.isSafeInteger(samples) ? Math.min(Math.max(samples, 1), 5) : 2;
  const total = perRound * boundedSamples;
  const canAct = !busy && questionItems.length > 0 && models.length > 0 && regions.length > 0;

  async function freeze() {
    const frozen = await executionApi.freezeConfig(session, projectPubId, {
      queryGroups: [
        {
          name: groupName,
          items: questionItems.map((text, index) => ({ text, priority: index + 1 })),
        },
      ],
      regions,
      models,
      modes,
      frequency,
      effectiveAt: new Date().toISOString(),
    });
    return frozen;
  }

  async function run(action: 'freeze' | 'launch') {
    if (!canAct) return;
    setBusy(true);
    setError(null);
    setScheduleError(null);
    setResult(null);
    try {
      const frozen = await freeze();
      let startedRuns = 0;
      if (action === 'launch') {
        for (let index = 0; index < boundedSamples; index += 1) {
          await executionApi.startRun(session, projectPubId, frozen.pub_id);
          startedRuns += 1;
        }
      }
      // 非手动频率落真实调度：冻结/启动已成功时建调度失败只记部分成功，不吞错。
      let schedule: LaunchResult['schedule'] = null;
      const intervalMinutes = FREQUENCY_INTERVAL_MINUTES[frequency];
      if (intervalMinutes !== undefined) {
        try {
          const created = await executionApi.createSchedule(session, {
            projectId: projectPubId,
            configVersionId: frozen.pub_id,
            intervalMinutes,
            nextRunAt: new Date(Date.now() + intervalMinutes * 60_000).toISOString(),
            responsiblePubId: session.actorId,
          });
          schedule = {
            label: frequencyLabel(frequency),
            intervalMinutes,
            nextRunAt: created.next_run_at,
          };
        } catch (cause) {
          setScheduleError(cause instanceof Error ? cause.message : '未知错误');
        }
      }
      setResult({
        revision: frozen.revision,
        hashPrefix: frozen.snapshot_hash.slice(0, 8),
        startedRuns,
        schedule,
      });
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="execution-card config-launcher">
      <div className="section-title">
        <h2>配置与启动</h2>
        <span>{groupName} · 冻结后不可改，启动按采样次数逐轮下发</span>
      </div>
      <div className="launcher-grid">
        <label>
          1. 监测问题（每行一条）
          <textarea
            rows={6}
            value={questions}
            onChange={(event) => setQuestions(event.target.value)}
            placeholder={queryPlaceholder ?? '品牌在 AI 搜索中的口碑如何？'}
          />
        </label>
        <fieldset>
          <legend>2. 采集平台</legend>
          <div className="platform-checks">
            {LAUNCHER_PLATFORMS.map(([slug, label]) => (
              <label key={slug}>
                <input
                  type="checkbox"
                  checked={models.includes(slug)}
                  onChange={(event) =>
                    setModels((current) =>
                      event.target.checked
                        ? [...current, slug]
                        : current.filter((item) => item !== slug),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>3. 地域（多选）</legend>
          <RegionMultiSelect value={regions} onChange={setRegions} />
        </fieldset>
        <div className="inline-fields">
          <label>
            4. 采样次数（每题重复 N 次）
            <input
              type="number"
              min={1}
              max={5}
              value={samples}
              onChange={(event) => setSamples(Number(event.target.value))}
            />
          </label>
          <label>
            5. 频率
            <select value={frequency} onChange={(event) => setFrequency(event.target.value)}>
              {FREQUENCIES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <p className="setup-summary" aria-live="polite">
        {questionItems.length} 题 × {platformModeCount} 平台×模式 × {regions.length} 地域 = 每轮{' '}
        {perRound} 任务，采样 {boundedSamples} 轮共 {total} 任务
      </p>
      <div className="actions">
        <button type="button" disabled={!canAct} onClick={() => void run('freeze')}>
          仅冻结配置
        </button>
        <button type="button" disabled={!canAct} onClick={() => void run('launch')}>
          冻结并启动采样
        </button>
      </div>
      {error ? (
        <p className="launcher-error" role="alert">
          操作失败：{error}
        </p>
      ) : null}
      {result ? (
        <>
          <p className="receipt">
            配置 v{result.revision} 已冻结（{result.hashPrefix}）
            {result.startedRuns > 0 ? `；已启动 ${result.startedRuns} 个采样 run` : ''}
          </p>
          {result.schedule ? (
            <p className="receipt">
              {`已创建调度：${result.schedule.label}（每 ${result.schedule.intervalMinutes} 分钟），下次运行 ${new Date(result.schedule.nextRunAt).toLocaleString('zh-CN', { hour12: false })}。 `}
              <a href="/platform/operations/execution">前往「执行与账号」查看调度</a>
            </p>
          ) : null}
          {scheduleError ? (
            <p className="launcher-error" role="alert">
              配置已冻结，但自动调度创建失败：{scheduleError}
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
