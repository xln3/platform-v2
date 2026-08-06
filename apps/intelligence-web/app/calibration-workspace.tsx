import { useEffect, useRef, useState } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Badge,
  containsClientSecret,
  createSafeExperienceScopeKey,
  createStructuredClientScopeKey,
  Dialog,
  FormField,
  MetricGrid,
  Pagination,
  ProjectionLimitNotice,
  StatePanel,
  Toast,
  useOptionalExperienceContext,
} from '@geo/design-system';
import {
  admitEvaluatedModel,
  approveEvaluationDataset,
  listEvaluationDatasets,
  listEvaluationRuns,
  listModelAdmissions,
  registerEvaluationDataset,
  runEvaluationDataset,
  type EvaluationDatasetCreate,
  type EvaluationDatasetPageProjection,
  type EvaluationDatasetSafeView,
  type EvaluationRunCreate,
  type EvaluationRunPageProjection,
  type EvaluationRunSafeView,
  type IdentitySessionHeaders,
  type ModelAdmissionPageProjection,
  type ModelAdmissionSafeView,
  type ProjectResourceResult,
} from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { useForm } from 'react-hook-form';
import { useSearchParams } from 'react-router';
import { z } from 'zod';
import { useIntelligenceMutationGuard } from './mutation-guard';

type IntelligenceCapabilities = {
  analyze: boolean;
  review: boolean;
};

const digestSchema = z.string().regex(/^[0-9a-f]{64}$/, '必须是 64 位小写 SHA-256');
const caseRowsSchema = z
  .array(
    z
      .object({
        case_digest: digestSchema,
        propagation_cluster_digest: digestSchema,
        actual_positive: z.boolean(),
      })
      .strict(),
  )
  .min(20, '至少需要 20 个案例摘要')
  .max(10_000, '最多允许 10,000 个案例摘要')
  .superRefine((rows, context) => {
    if (new Set(rows.map((item) => item.case_digest)).size !== rows.length) {
      context.addIssue({ code: 'custom', message: '案例摘要不能重复' });
    }
    if (new Set(rows.map((item) => item.propagation_cluster_digest)).size !== rows.length) {
      context.addIssue({ code: 'custom', message: '传播簇摘要不能重复' });
    }
    if (!rows.some((item) => item.actual_positive) || !rows.some((item) => !item.actual_positive)) {
      context.addIssue({ code: 'custom', message: '数据集必须同时包含正例和负例' });
    }
  });
const explanationFieldSchema = z.enum([
  'evidence_sufficiency',
  'independent_source_count',
  'uncertainty',
  'rule_version',
  'model_version',
  'human_verdict_state',
]);
const predictionRowsSchema = z
  .array(
    z
      .object({
        case_digest: digestSchema,
        probability: z.number().min(0).max(1),
        predicted_positive: z.boolean(),
        explanation_fields: z.array(explanationFieldSchema).max(50),
      })
      .strict(),
  )
  .min(20, '至少需要 20 条预测')
  .max(10_000, '最多允许 10,000 条预测')
  .superRefine((rows, context) => {
    if (new Set(rows.map((item) => item.case_digest)).size !== rows.length) {
      context.addIssue({ code: 'custom', message: '预测中的案例摘要不能重复' });
    }
  });
const trainingClusterRowsSchema = z
  .array(digestSchema)
  .max(50_000, '训练传播簇最多允许 50,000 条')
  .superRefine((rows, context) => {
    if (new Set(rows).size !== rows.length) {
      context.addIssue({ code: 'custom', message: '训练传播簇摘要不能重复' });
    }
  });

const parseJson = <T,>(value: string, schema: z.ZodType<T>): T | null => {
  if (containsClientSecret(value)) return null;
  try {
    const result = schema.safeParse(JSON.parse(value) as unknown);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
};

const safeText = (minimum: number, maximum: number, label: string) =>
  z
    .string()
    .trim()
    .min(minimum, `${label}至少需要 ${minimum} 个字符`)
    .max(maximum, `${label}不能超过 ${maximum} 个字符`)
    .refine((value) => !containsClientSecret(value), `${label}不能包含秘密或敏感凭据`);

const datasetFormSchema = z.object({
  version: safeText(1, 100, '数据集版本'),
  sourceArtifactPubId: safeText(1, 100, '来源证据 ID').refine(
    (value) => /^evd_[A-Za-z0-9_-]+$/.test(value),
    '来源证据 ID 必须以 evd_ 开头',
  ),
  sourceArtifactSha256: digestSchema,
  labelPolicyVersion: safeText(1, 100, '标注策略版本'),
  labelerCount: z.number().int().min(2, '至少需要 2 名标注者').max(100),
  casesJson: z
    .string()
    .min(1, '请导入案例摘要 JSON')
    .refine((value) => parseJson(value, caseRowsSchema) !== null, '案例 JSON 格式或治理约束不合法'),
});
type DatasetFormFields = z.infer<typeof datasetFormSchema>;

const runFormSchema = z.object({
  datasetPubId: safeText(1, 120, '数据集 ID').refine(
    (value) => /^dset_[A-Za-z0-9_-]+$/.test(value),
    '数据集 ID 必须以 dset_ 开头',
  ),
  scorerVersion: safeText(1, 100, '评分器版本'),
  decisionThreshold: z.number().gt(0, '阈值必须大于 0').lt(1, '阈值必须小于 1'),
  calibrationBins: z.number().int().min(2).max(100),
  trainingClustersJson: z
    .string()
    .min(1, '请提供训练传播簇 JSON；没有时使用 []')
    .refine(
      (value) => parseJson(value, trainingClusterRowsSchema) !== null,
      '训练传播簇 JSON 格式或治理约束不合法',
    ),
  predictionsJson: z
    .string()
    .min(1, '请导入预测 JSON')
    .refine(
      (value) => parseJson(value, predictionRowsSchema) !== null,
      '预测 JSON 格式或治理约束不合法',
    ),
});
type RunFormFields = z.infer<typeof runFormSchema>;

const reviewFormSchema = z.object({
  rationale: safeText(5, 2_000, '独立复核理由'),
});
type ReviewFormFields = z.infer<typeof reviewFormSchema>;

const HASH_A = 'a'.repeat(64);
const HASH_B = 'b'.repeat(64);
type CalibrationFixturePage<T> = {
  data: T[];
  page: { next_cursor: string | null; has_more: boolean };
};
const fixtureDatasetPage: CalibrationFixturePage<EvaluationDatasetSafeView> = {
  data: [
    {
      pub_id: 'dset_contract_approved',
      version: 'external-approved-v1',
      dataset_sha256: HASH_A,
      state: 'approved',
      case_count: 120,
      positive_count: 54,
      labeler_count: 3,
      submitted_at: '2026-07-25T08:00:00Z',
      approved_at: '2026-07-25T09:10:00Z',
    },
    {
      pub_id: 'dset_contract_draft',
      version: 'external-candidate-v2',
      dataset_sha256: HASH_B,
      state: 'draft',
      case_count: 80,
      positive_count: 36,
      labeler_count: 2,
      submitted_at: '2026-07-25T10:20:00Z',
      approved_at: null,
    },
  ],
  page: { next_cursor: null, has_more: false },
};
const fixtureRunPage: CalibrationFixturePage<EvaluationRunSafeView> = {
  data: [
    {
      pub_id: 'eval_contract_ready',
      dataset_pub_id: 'dset_contract_approved',
      scorer_version: 'anti-geo-scorer-v3',
      decision_threshold: '0.5',
      calibration_bins: 10,
      training_cluster_manifest_sha256: HASH_A,
      training_cluster_count: 0,
      sample_count: 120,
      admission_policy_version: 'anti-geo-admission-v1',
      admission_checks: {
        precision: true,
        recall: true,
        false_positive_rate: true,
        brier_score: true,
        expected_calibration_error: true,
        explanation_completeness: true,
      },
      admission_passed: true,
      model_admission_state: null,
      metrics: {
        precision: '0.91',
        recall: '0.89',
        false_positive_rate: '0.06',
        brier_score: '0.11',
        expected_calibration_error: '0.05',
        explanation_completeness_rate: '1',
        sample_count: 120,
        positive_count: 54,
        negative_count: 66,
        dataset_version: 'external-approved-v1',
        scorer_version: 'anti-geo-scorer-v3',
        evaluation_sha256: HASH_B,
      },
      required_explanation_fields: [
        'evidence_sufficiency',
        'independent_source_count',
        'uncertainty',
        'rule_version',
        'model_version',
        'human_verdict_state',
      ],
      created_at: '2026-07-25T11:00:00Z',
    },
  ],
  page: { next_cursor: null, has_more: false },
};
const fixtureAdmissionPage: CalibrationFixturePage<ModelAdmissionSafeView> = {
  data: [],
  page: { next_cursor: null, has_more: false },
};

const issueKey = (prefix: string): string => {
  const randomId =
    typeof globalThis.crypto?.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : '00000000-0000-4000-8000-000000000000';
  return `${prefix}-${randomId}`;
};
const createFixtureId = (prefix: string): string =>
  `${prefix}_contract_${issueKey('receipt').replaceAll('-', '').slice(-20)}`;

const unwrap = <T,>(result: ProjectResourceResult<T>): T => {
  if (result.kind === 'ready') return result.data;
  throw new Error(result.kind === 'forbidden' ? 'forbidden' : 'unavailable');
};

const stateTone = (state: string): 'positive' | 'warning' | 'danger' | 'neutral' =>
  state === 'approved' || state === 'admitted'
    ? 'positive'
    : state === 'draft'
      ? 'warning'
      : state === 'revoked'
        ? 'danger'
        : 'neutral';
const formatTimestamp = (value: string): string =>
  value.replace('T', ' ').replace('Z', '').slice(0, 16);
const formatRatio = (value: string | null): string =>
  value === null ? '—' : `${(Number(value) * 100).toFixed(1)}%`;

type CalibrationPaginationKey = 'dataset' | 'run' | 'admission';
type CalibrationPagination = {
  page: number;
  cursor: string;
  pageParam: string;
  cursorParam: string;
};
const calibrationCursorPrefixes: Record<CalibrationPaginationKey, string> = {
  dataset: 'dset_',
  run: 'eval_',
  admission: 'madm_',
};
const safeCalibrationCursor = (value: unknown, key: CalibrationPaginationKey): string => {
  const prefix = calibrationCursorPrefixes[key];
  return typeof value === 'string' &&
    value.startsWith(prefix) &&
    value.length <= 120 &&
    new RegExp(`^${prefix}[A-Za-z0-9_-]{1,${120 - prefix.length}}$`).test(value) &&
    !containsClientSecret(value)
    ? value
    : '';
};

type CalibrationPageProjection = {
  total: number;
  shown: number;
  invalid: boolean;
};
type CalibrationProjectedPage =
  | EvaluationDatasetPageProjection
  | EvaluationRunPageProjection
  | ModelAdmissionPageProjection;
const readCalibrationProjection = (
  page:
    | CalibrationFixturePage<EvaluationDatasetSafeView>
    | CalibrationFixturePage<EvaluationRunSafeView>
    | CalibrationFixturePage<ModelAdmissionSafeView>
    | CalibrationProjectedPage
    | undefined,
): CalibrationPageProjection | null => (page && 'projection' in page ? page.projection : null);

function CalibrationProjectionNotice({
  label,
  projection,
}: {
  label: string;
  projection: CalibrationPageProjection | null;
}) {
  if (!projection) return null;
  return (
    <>
      {projection.total !== projection.shown ? (
        <ProjectionLimitNotice
          items={[
            {
              key: label,
              label,
              total: projection.total,
              shown: projection.shown,
            },
          ]}
        />
      ) : null}
      {projection.invalid ? (
        <div className="confirmation projection-limit-notice" role="alert">
          <Badge tone="warning">安全投影不完整</Badge>
          <span>{label}含未通过安全校验或分页契约的数据；相关治理写操作已锁定。</span>
        </div>
      ) : null}
    </>
  );
}
const readCalibrationPagination = (
  searchParams: URLSearchParams,
  key: CalibrationPaginationKey,
  live: boolean,
): CalibrationPagination => {
  const pageParam = `cal_${key}_page`;
  const cursorParam = `cal_${key}_cursor`;
  const rawPage = searchParams.get(pageParam) ?? '';
  const rawCursor = searchParams.get(cursorParam) ?? '';
  const parsedPage = /^[1-9]\d{0,2}$/.test(rawPage) ? Number(rawPage) : 1;
  const cursor = live ? safeCalibrationCursor(rawCursor, key) : '';
  return {
    page: live && parsedPage > 1 && !cursor ? 1 : live ? parsedPage : 1,
    cursor,
    pageParam,
    cursorParam,
  };
};

function DatasetDialog({
  open,
  onClose,
  onSubmit,
  saving,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (body: EvaluationDatasetCreate) => Promise<void>;
  saving: boolean;
}) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isValid },
  } = useForm<DatasetFormFields>({
    resolver: zodResolver(datasetFormSchema),
    defaultValues: {
      version: '',
      sourceArtifactPubId: '',
      sourceArtifactSha256: '',
      labelPolicyVersion: 'anti-geo-human-label-v1',
      labelerCount: 2,
      casesJson: '',
    },
    mode: 'onChange',
  });
  if (!open) return null;
  const submit = handleSubmit(async (values) => {
    const cases = parseJson(values.casesJson, caseRowsSchema);
    if (!cases) return;
    try {
      await onSubmit({
        version: values.version,
        source_artifact_pub_id: values.sourceArtifactPubId,
        source_artifact_sha256: values.sourceArtifactSha256,
        label_policy_version: values.labelPolicyVersion,
        labeler_count: values.labelerCount,
        cases,
      });
      reset();
    } catch {
      // React Query owns the generic, secret-free failure state and retry surface.
    }
  });
  return (
    <Dialog
      title="登记外部校准数据集"
      eyebrow="Governed dataset"
      closeLabel="关闭数据集登记"
      onClose={onClose}
    >
      <p className="panel-subtitle">
        只接收已授权证据资产 ID、SHA-256 和不可逆案例摘要；不要粘贴正文、账号凭据或个人资料。
      </p>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <div className="form-grid">
          <FormField id="dataset-version" label="数据集版本" error={errors.version}>
            <input
              id="dataset-version"
              {...register('version')}
              aria-invalid={Boolean(errors.version)}
            />
          </FormField>
          <FormField
            id="dataset-label-policy"
            label="标注策略版本"
            error={errors.labelPolicyVersion}
          >
            <input
              id="dataset-label-policy"
              {...register('labelPolicyVersion')}
              aria-invalid={Boolean(errors.labelPolicyVersion)}
            />
          </FormField>
          <FormField
            id="dataset-source-id"
            label="来源证据资产 ID"
            error={errors.sourceArtifactPubId}
          >
            <input
              id="dataset-source-id"
              {...register('sourceArtifactPubId')}
              aria-invalid={Boolean(errors.sourceArtifactPubId)}
              autoComplete="off"
            />
          </FormField>
          <FormField
            id="dataset-source-hash"
            label="来源证据 SHA-256"
            error={errors.sourceArtifactSha256}
          >
            <input
              id="dataset-source-hash"
              {...register('sourceArtifactSha256')}
              aria-invalid={Boolean(errors.sourceArtifactSha256)}
              autoComplete="off"
            />
          </FormField>
          <FormField id="dataset-labelers" label="独立标注者数量" error={errors.labelerCount}>
            <input
              id="dataset-labelers"
              type="number"
              min={2}
              max={100}
              {...register('labelerCount', { valueAsNumber: true })}
              aria-invalid={Boolean(errors.labelerCount)}
            />
          </FormField>
        </div>
        <FormField
          id="dataset-cases"
          label="案例摘要 JSON"
          error={errors.casesJson}
          hint="数组字段：case_digest、propagation_cluster_digest、actual_positive；20–10,000 条。"
        >
          <textarea
            id="dataset-cases"
            rows={8}
            spellCheck={false}
            {...register('casesJson')}
            aria-invalid={Boolean(errors.casesJson)}
          />
        </FormField>
        <div className="form-actions">
          <span>幂等键由浏览器生成并仅放入请求头，不写 URL 或本地存储。</span>
          <button className="button" type="submit" disabled={!isValid || saving}>
            {saving ? '正在登记…' : '登记数据集'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function RunDialog({
  open,
  datasets,
  onClose,
  onSubmit,
  saving,
}: {
  open: boolean;
  datasets: EvaluationDatasetSafeView[];
  onClose: () => void;
  onSubmit: (datasetPubId: string, body: EvaluationRunCreate) => Promise<void>;
  saving: boolean;
}) {
  const approved = datasets.filter((item) => item.state === 'approved');
  const {
    register,
    handleSubmit,
    getValues,
    setValue,
    setError,
    reset,
    formState: { errors, isValid },
  } = useForm<RunFormFields>({
    resolver: zodResolver(runFormSchema),
    defaultValues: {
      datasetPubId: approved[0]?.pub_id ?? '',
      scorerVersion: '',
      decisionThreshold: 0.5,
      calibrationBins: 10,
      trainingClustersJson: '[]',
      predictionsJson: '',
    },
    mode: 'onChange',
  });
  const defaultDatasetPubId = approved[0]?.pub_id ?? '';
  useEffect(() => {
    if (!open || !defaultDatasetPubId) return;
    const current = getValues('datasetPubId');
    if (approved.some((item) => item.pub_id === current)) return;
    setValue('datasetPubId', defaultDatasetPubId, { shouldValidate: true });
  }, [approved, defaultDatasetPubId, getValues, open, setValue]);
  if (!open) return null;
  const submit = handleSubmit(async (values) => {
    const predictions = parseJson(values.predictionsJson, predictionRowsSchema);
    const trainingClusters = parseJson(values.trainingClustersJson, trainingClusterRowsSchema);
    if (!predictions || !trainingClusters) return;
    if (
      predictions.some(
        (item) => item.predicted_positive !== item.probability >= values.decisionThreshold,
      )
    ) {
      setError('predictionsJson', {
        message: '预测标签必须与当前决策阈值一致',
      });
      return;
    }
    try {
      await onSubmit(values.datasetPubId, {
        scorer_version: values.scorerVersion,
        decision_threshold: values.decisionThreshold,
        calibration_bins: values.calibrationBins,
        training_propagation_cluster_digests: trainingClusters,
        predictions,
      });
      reset();
    } catch {
      // React Query owns the generic, secret-free failure state and retry surface.
    }
  });
  return (
    <Dialog
      title="运行准入评估"
      eyebrow="Model evaluation"
      closeLabel="关闭模型评估"
      onClose={onClose}
    >
      <p className="panel-subtitle">
        评估只接收案例摘要、概率、阈值标签和六类解释字段存在性，不接收案例正文。
      </p>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <div className="form-grid">
          <FormField id="run-dataset" label="已批准数据集" error={errors.datasetPubId}>
            <select
              id="run-dataset"
              {...register('datasetPubId')}
              aria-invalid={Boolean(errors.datasetPubId)}
            >
              <option value="">请选择</option>
              {approved.map((item) => (
                <option key={item.pub_id} value={item.pub_id}>
                  {item.version} · {item.case_count} 条
                </option>
              ))}
            </select>
          </FormField>
          <FormField id="run-scorer" label="评分器版本" error={errors.scorerVersion}>
            <input
              id="run-scorer"
              {...register('scorerVersion')}
              aria-invalid={Boolean(errors.scorerVersion)}
            />
          </FormField>
          <FormField id="run-threshold" label="决策阈值" error={errors.decisionThreshold}>
            <input
              id="run-threshold"
              type="number"
              min="0.000001"
              max="0.999999"
              step="0.01"
              {...register('decisionThreshold', { valueAsNumber: true })}
              aria-invalid={Boolean(errors.decisionThreshold)}
            />
          </FormField>
          <FormField id="run-bins" label="校准分箱" error={errors.calibrationBins}>
            <input
              id="run-bins"
              type="number"
              min={2}
              max={100}
              {...register('calibrationBins', { valueAsNumber: true })}
              aria-invalid={Boolean(errors.calibrationBins)}
            />
          </FormField>
        </div>
        <FormField
          id="run-training-clusters"
          label="训练传播簇摘要 JSON"
          error={errors.trainingClustersJson}
          hint="数组仅含 64 位 SHA-256；用于服务端证明训练集与校准 holdout 无传播簇重叠。没有训练清单时使用 []。"
        >
          <textarea
            id="run-training-clusters"
            rows={4}
            spellCheck={false}
            {...register('trainingClustersJson')}
            aria-invalid={Boolean(errors.trainingClustersJson)}
          />
        </FormField>
        <FormField
          id="run-predictions"
          label="预测摘要 JSON"
          error={errors.predictionsJson}
          hint="必须覆盖数据集全部案例；只允许合同规定的六类 explanation_fields。"
        >
          <textarea
            id="run-predictions"
            rows={8}
            spellCheck={false}
            {...register('predictionsJson')}
            aria-invalid={Boolean(errors.predictionsJson)}
          />
        </FormField>
        <div className="form-actions">
          <span>运行结果会逐项显示阈值检查；未通过时不能进入模型准入。</span>
          <button
            className="button"
            type="submit"
            disabled={!isValid || saving || approved.length === 0}
          >
            {saving ? '正在评估…' : '运行评估'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function ReviewDialog({
  target,
  onClose,
  onSubmit,
  saving,
}: {
  target: { kind: 'dataset' | 'run'; pubId: string } | null;
  onClose: () => void;
  onSubmit: (rationale: string) => Promise<void>;
  saving: boolean;
}) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isValid },
  } = useForm<ReviewFormFields>({
    resolver: zodResolver(reviewFormSchema),
    defaultValues: { rationale: '' },
    mode: 'onChange',
  });
  if (!target) return null;
  const submit = handleSubmit(async ({ rationale }) => {
    try {
      await onSubmit(rationale);
      reset();
    } catch {
      // React Query owns the generic, secret-free failure state and retry surface.
    }
  });
  return (
    <Dialog
      title={target.kind === 'dataset' ? '独立审批数据集' : '准入已评估模型'}
      eyebrow="Four-eyes review"
      closeLabel="关闭独立复核"
      onClose={onClose}
    >
      <p className="panel-subtitle">
        当前操作必须由不同于提交人或评估运行人的审核者完成；服务端会再次强制校验。
      </p>
      <form onSubmit={(event) => void submit(event)} noValidate>
        <FormField
          id="review-rationale"
          label="独立复核理由"
          error={errors.rationale}
          hint={`目标：${target.pubId}`}
        >
          <textarea
            id="review-rationale"
            rows={5}
            {...register('rationale')}
            aria-invalid={Boolean(errors.rationale)}
          />
        </FormField>
        <div className="form-actions">
          <span>该理由进入审计投影；请勿包含账号凭据、个人资料或秘密。</span>
          <button className="button" type="submit" disabled={!isValid || saving}>
            {saving ? '正在提交…' : target.kind === 'dataset' ? '确认审批' : '确认准入'}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function CalibrationWorkspace({
  live,
  capabilities,
}: {
  live: boolean;
  capabilities: IntelligenceCapabilities;
}) {
  const queryClient = useQueryClient();
  const experience = useOptionalExperienceContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const headers: IdentitySessionHeaders | null = live ? getValidatedIdentityHeaders() : null;
  const identityScope =
    live && experience?.source === 'live'
      ? createSafeExperienceScopeKey(experience)
      : 'contract-fixture';
  const datasetPagination = readCalibrationPagination(searchParams, 'dataset', live);
  const runPagination = readCalibrationPagination(searchParams, 'run', live);
  const admissionPagination = readCalibrationPagination(searchParams, 'admission', live);
  const datasetPage = datasetPagination.page;
  const runPage = runPagination.page;
  const admissionPage = admissionPagination.page;
  const datasetCursor = datasetPagination.cursor;
  const runCursor = runPagination.cursor;
  const admissionCursor = admissionPagination.cursor;
  const calibrationViewScope = createStructuredClientScopeKey([
    'calibration',
    identityScope,
    String(datasetPage),
    datasetCursor,
    String(runPage),
    runCursor,
    String(admissionPage),
    admissionCursor,
  ]);
  const datasetCursors = useRef(new Map<number, string>([[1, '']]));
  const runCursors = useRef(new Map<number, string>([[1, '']]));
  const admissionCursors = useRef(new Map<number, string>([[1, '']]));
  const [fixtureDatasets, setFixtureDatasets] = useState(fixtureDatasetPage);
  const [fixtureRuns, setFixtureRuns] = useState(fixtureRunPage);
  const [fixtureAdmissions, setFixtureAdmissions] = useState(fixtureAdmissionPage);
  const [datasetDialogScope, setDatasetDialogScope] = useState('');
  const [runDialogScope, setRunDialogScope] = useState('');
  const [reviewTarget, setReviewTarget] = useState<{
    kind: 'dataset' | 'run';
    pubId: string;
    scope: string;
  } | null>(null);
  const [notice, setNotice] = useState<{ scope: string; message: string } | null>(null);
  const [localMutationFailureScope, setLocalMutationFailureScope] = useState('');
  const [mutationScope, setMutationScope] = useState('');
  const [reconciliationScope, setReconciliationScope] = useState('');
  const currentViewScopeRef = useRef(calibrationViewScope);
  currentViewScopeRef.current = calibrationViewScope;
  const governanceWrite = useIntelligenceMutationGuard(calibrationViewScope);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    for (const pagination of [datasetPagination, runPagination, admissionPagination]) {
      if (pagination.page > 1) next.set(pagination.pageParam, String(pagination.page));
      else next.delete(pagination.pageParam);
      if (pagination.cursor) next.set(pagination.cursorParam, pagination.cursor);
      else next.delete(pagination.cursorParam);
    }
    if (next.toString() !== searchParams.toString()) {
      void setSearchParams(next, { replace: true });
    }
  }, [admissionPagination, datasetPagination, runPagination, searchParams, setSearchParams]);
  useEffect(() => {
    setDatasetDialogScope((scope) => (scope === calibrationViewScope ? scope : ''));
    setRunDialogScope((scope) => (scope === calibrationViewScope ? scope : ''));
    setReviewTarget((target) => (target?.scope === calibrationViewScope ? target : null));
    setNotice((current) => (current?.scope === calibrationViewScope ? current : null));
    setLocalMutationFailureScope((scope) => (scope === calibrationViewScope ? scope : ''));
    setMutationScope((scope) => (scope === calibrationViewScope ? scope : ''));
    setReconciliationScope((scope) => (scope === calibrationViewScope ? scope : ''));
  }, [calibrationViewScope]);

  const datasetsQuery = useQuery({
    queryKey: ['intelligence', 'evaluation-datasets', identityScope, datasetPage, datasetCursor],
    enabled: live && Boolean(headers),
    queryFn: async () =>
      unwrap(
        await listEvaluationDatasets(headers!, {
          ...(datasetCursor ? { cursor: datasetCursor } : {}),
          limit: 20,
        }),
      ),
    retry: false,
  });
  const runsQuery = useQuery({
    queryKey: ['intelligence', 'evaluation-runs', identityScope, runPage, runCursor],
    enabled: live && Boolean(headers),
    queryFn: async () =>
      unwrap(
        await listEvaluationRuns(headers!, {
          ...(runCursor ? { cursor: runCursor } : {}),
          limit: 20,
        }),
      ),
    retry: false,
  });
  const admissionsQuery = useQuery({
    queryKey: ['intelligence', 'model-admissions', identityScope, admissionPage, admissionCursor],
    enabled: live && Boolean(headers),
    queryFn: async () =>
      unwrap(
        await listModelAdmissions(headers!, {
          ...(admissionCursor ? { cursor: admissionCursor } : {}),
          limit: 20,
        }),
      ),
    retry: false,
  });

  const datasets = live ? datasetsQuery.data : fixtureDatasets;
  const runs = live ? runsQuery.data : fixtureRuns;
  const admissions = live ? admissionsQuery.data : fixtureAdmissions;
  const visibleDatasets = datasets ?? { data: [], page: { next_cursor: null, has_more: false } };
  const visibleRuns = runs ?? { data: [], page: { next_cursor: null, has_more: false } };
  const visibleAdmissions = admissions ?? {
    data: [],
    page: { next_cursor: null, has_more: false },
  };
  const datasetProjection = live ? readCalibrationProjection(datasets) : null;
  const runProjection = live ? readCalibrationProjection(runs) : null;
  const admissionProjection = live ? readCalibrationProjection(admissions) : null;
  const projectionIsIncomplete = (projection: CalibrationPageProjection | null) =>
    Boolean(projection && (projection.invalid || projection.total !== projection.shown));
  const datasetProjectionIncomplete = projectionIsIncomplete(datasetProjection);
  const runProjectionIncomplete = projectionIsIncomplete(runProjection);
  const admissionProjectionIncomplete = projectionIsIncomplete(admissionProjection);
  const datasetDialogOpen =
    datasetDialogScope === calibrationViewScope &&
    capabilities.analyze &&
    Boolean(datasets) &&
    !datasetProjectionIncomplete;
  const runDialogOpen =
    runDialogScope === calibrationViewScope &&
    capabilities.analyze &&
    Boolean(datasets) &&
    !datasetProjectionIncomplete;
  const effectiveReviewTarget =
    reviewTarget?.scope === calibrationViewScope &&
    (reviewTarget.kind === 'dataset'
      ? !datasetProjectionIncomplete &&
        visibleDatasets.data.some(
          (item) => item.pub_id === reviewTarget.pubId && item.state === 'draft',
        )
      : !runProjectionIncomplete &&
        visibleRuns.data.some(
          (item) =>
            item.pub_id === reviewTarget.pubId &&
            item.admission_passed &&
            !item.model_admission_state,
        ))
      ? { kind: reviewTarget.kind, pubId: reviewTarget.pubId }
      : null;
  const refreshGovernance = async () => {
    if (!live) return;
    await queryClient.invalidateQueries({ queryKey: ['intelligence'] });
  };
  const reconcileGovernance = async (scope: string) => {
    setReconciliationScope(scope);
    try {
      await refreshGovernance();
    } catch {
      if (currentViewScopeRef.current === scope) setLocalMutationFailureScope(scope);
      return false;
    } finally {
      setReconciliationScope((current) => (current === scope ? '' : current));
    }
    return currentViewScopeRef.current === scope;
  };

  const registerMutation = useMutation({
    mutationFn: async ({
      body,
      requestHeaders,
    }: {
      body: EvaluationDatasetCreate;
      requestHeaders: IdentitySessionHeaders | null;
    }): Promise<EvaluationDatasetSafeView> => {
      if (!live) {
        return {
          pub_id: createFixtureId('dset'),
          version: body.version,
          dataset_sha256: HASH_A,
          state: 'draft',
          case_count: body.cases.length,
          positive_count: body.cases.filter((item) => item.actual_positive).length,
          labeler_count: body.labeler_count,
          submitted_at: '2026-07-25T12:00:00Z',
          approved_at: null,
        };
      }
      if (!requestHeaders || datasetProjectionIncomplete) throw new Error('unavailable');
      return unwrap(
        await registerEvaluationDataset(
          body,
          issueKey('evaluation-dataset-registration'),
          requestHeaders,
        ),
      );
    },
  });
  const runMutation = useMutation({
    mutationFn: async ({
      datasetPubId,
      body,
      requestHeaders,
    }: {
      datasetPubId: string;
      body: EvaluationRunCreate;
      requestHeaders: IdentitySessionHeaders | null;
    }): Promise<EvaluationRunSafeView> => {
      if (!live) {
        return {
          ...fixtureRunPage.data[0]!,
          pub_id: createFixtureId('eval'),
          dataset_pub_id: datasetPubId,
          scorer_version: body.scorer_version,
          decision_threshold: String(body.decision_threshold),
          calibration_bins: body.calibration_bins,
          training_cluster_manifest_sha256: HASH_A,
          training_cluster_count: body.training_propagation_cluster_digests?.length ?? 0,
          sample_count: body.predictions.length,
          metrics: {
            ...fixtureRunPage.data[0]!.metrics,
            sample_count: body.predictions.length,
            positive_count: Math.floor(body.predictions.length / 2),
            negative_count: Math.ceil(body.predictions.length / 2),
            scorer_version: body.scorer_version,
          },
        };
      }
      if (!requestHeaders || datasetProjectionIncomplete) throw new Error('unavailable');
      return unwrap(
        await runEvaluationDataset(datasetPubId, body, issueKey('evaluation-run'), requestHeaders),
      );
    },
  });
  const approveMutation = useMutation({
    mutationFn: async ({
      datasetPubId,
      rationale,
      requestHeaders,
    }: {
      datasetPubId: string;
      rationale: string;
      requestHeaders: IdentitySessionHeaders | null;
    }): Promise<EvaluationDatasetSafeView> => {
      if (!live) {
        const target = fixtureDatasets.data.find((item) => item.pub_id === datasetPubId);
        if (!target) throw new Error('unavailable');
        return { ...target, state: 'approved', approved_at: '2026-07-25T12:20:00Z' };
      }
      if (!requestHeaders || datasetProjectionIncomplete) throw new Error('unavailable');
      return unwrap(await approveEvaluationDataset(datasetPubId, { rationale }, requestHeaders));
    },
  });
  const admissionMutation = useMutation({
    mutationFn: async ({
      evaluationRunPubId,
      rationale,
      requestHeaders,
    }: {
      evaluationRunPubId: string;
      rationale: string;
      requestHeaders: IdentitySessionHeaders | null;
    }): Promise<ModelAdmissionSafeView> => {
      if (!live) {
        const target = fixtureRuns.data.find((item) => item.pub_id === evaluationRunPubId);
        if (!target) throw new Error('unavailable');
        return {
          pub_id: createFixtureId('madm'),
          evaluation_run_pub_id: evaluationRunPubId,
          scorer_version: target.scorer_version,
          state: 'admitted',
          rationale,
          admitted_at: '2026-07-25T12:30:00Z',
          revoked_at: null,
        };
      }
      if (!requestHeaders || runProjectionIncomplete) throw new Error('unavailable');
      return unwrap(
        await admitEvaluatedModel(
          evaluationRunPubId,
          { rationale },
          issueKey('model-admission'),
          requestHeaders,
        ),
      );
    },
  });

  const beginGovernanceWrite = () => {
    const requestHeaders = live ? getValidatedIdentityHeaders() : null;
    if (live && !requestHeaders) {
      setLocalMutationFailureScope(calibrationViewScope);
      return null;
    }
    const ticket = requestHeaders
      ? governanceWrite.begin(requestHeaders)
      : governanceWrite.beginFixture();
    if (!ticket) return null;
    setLocalMutationFailureScope('');
    setMutationScope(calibrationViewScope);
    return { requestHeaders, ticket };
  };
  const submitDataset = async (body: EvaluationDatasetCreate) => {
    const started = beginGovernanceWrite();
    if (!started) throw new Error('mutation_locked');
    try {
      const created = await registerMutation.mutateAsync({
        body,
        requestHeaders: started.requestHeaders,
      });
      if (!governanceWrite.finish(started.ticket)) throw new Error('mutation_superseded');
      if (!live) {
        setFixtureDatasets((current) => ({ ...current, data: [created, ...current.data] }));
      }
      const writeScope = calibrationViewScope;
      if (!(await reconcileGovernance(writeScope))) return;
      setNotice({
        scope: writeScope,
        message: '数据集已登记为 draft，等待另一名独立审核者。',
      });
      setDatasetDialogScope('');
    } catch (error) {
      governanceWrite.finish(started.ticket);
      throw error;
    }
  };
  const submitRun = async (datasetPubId: string, body: EvaluationRunCreate) => {
    const started = beginGovernanceWrite();
    if (!started) throw new Error('mutation_locked');
    try {
      const created = await runMutation.mutateAsync({
        datasetPubId,
        body,
        requestHeaders: started.requestHeaders,
      });
      if (!governanceWrite.finish(started.ticket)) throw new Error('mutation_superseded');
      if (!live) setFixtureRuns((current) => ({ ...current, data: [created, ...current.data] }));
      const writeScope = calibrationViewScope;
      if (!(await reconcileGovernance(writeScope))) return;
      setNotice({
        scope: writeScope,
        message: created.admission_passed
          ? '评估已完成并通过全部阈值，等待独立审核者准入。'
          : '评估已完成，但至少一项准入阈值未通过。',
      });
      setRunDialogScope('');
    } catch (error) {
      governanceWrite.finish(started.ticket);
      throw error;
    }
  };
  const submitDatasetApproval = async (datasetPubId: string, rationale: string) => {
    const started = beginGovernanceWrite();
    if (!started) throw new Error('mutation_locked');
    try {
      const approved = await approveMutation.mutateAsync({
        datasetPubId,
        rationale,
        requestHeaders: started.requestHeaders,
      });
      if (!governanceWrite.finish(started.ticket)) throw new Error('mutation_superseded');
      if (!live) {
        setFixtureDatasets((current) => ({
          ...current,
          data: current.data.map((item) => (item.pub_id === approved.pub_id ? approved : item)),
        }));
      }
      const writeScope = calibrationViewScope;
      if (!(await reconcileGovernance(writeScope))) return;
      setNotice({
        scope: writeScope,
        message: '数据集已由独立审核者批准，可用于模型评估。',
      });
      setReviewTarget(null);
    } catch (error) {
      governanceWrite.finish(started.ticket);
      throw error;
    }
  };
  const submitAdmission = async (evaluationRunPubId: string, rationale: string) => {
    const started = beginGovernanceWrite();
    if (!started) throw new Error('mutation_locked');
    try {
      const admission = await admissionMutation.mutateAsync({
        evaluationRunPubId,
        rationale,
        requestHeaders: started.requestHeaders,
      });
      if (!governanceWrite.finish(started.ticket)) throw new Error('mutation_superseded');
      if (!live) {
        setFixtureAdmissions((current) => ({
          ...current,
          data: [admission, ...current.data],
        }));
        setFixtureRuns((current) => ({
          ...current,
          data: current.data.map((item) =>
            item.pub_id === admission.evaluation_run_pub_id
              ? { ...item, model_admission_state: admission.state }
              : item,
          ),
        }));
      }
      const writeScope = calibrationViewScope;
      if (!(await reconcileGovernance(writeScope))) return;
      setNotice({
        scope: writeScope,
        message: '模型准入已记录；评估摘要和独立审核链保持可追溯。',
      });
      setReviewTarget(null);
    } catch (error) {
      governanceWrite.finish(started.ticket);
      throw error;
    }
  };
  const governanceMutationPending =
    registerMutation.isPending ||
    runMutation.isPending ||
    approveMutation.isPending ||
    admissionMutation.isPending ||
    reconciliationScope === calibrationViewScope;
  const mutationOwnsCurrentView = mutationScope === calibrationViewScope;
  const anyMutationFailed =
    localMutationFailureScope === calibrationViewScope ||
    (mutationOwnsCurrentView &&
      (registerMutation.isError ||
        runMutation.isError ||
        approveMutation.isError ||
        admissionMutation.isError));
  const queryFallback = (
    pending: boolean,
    error: Error | null,
    hasData: boolean,
    retry: () => void,
  ) => {
    if (!live) return null;
    if (!headers) return <StatePanel state="failed" />;
    if (pending) return <StatePanel state="loading" />;
    if (error?.message === 'forbidden') return <StatePanel state="forbidden" />;
    if (error || !hasData) return <StatePanel state="failed" onRetry={retry} />;
    return null;
  };
  const datasetFallback = queryFallback(
    datasetsQuery.isPending,
    datasetsQuery.error,
    Boolean(datasets),
    () => void datasetsQuery.refetch(),
  );
  const runFallback = queryFallback(
    runsQuery.isPending,
    runsQuery.error,
    Boolean(runs),
    () => void runsQuery.refetch(),
  );
  const admissionFallback = queryFallback(
    admissionsQuery.isPending,
    admissionsQuery.error,
    Boolean(admissions),
    () => void admissionsQuery.refetch(),
  );
  const governanceReady =
    Boolean(datasets && runs && admissions) &&
    !datasetProjectionIncomplete &&
    !runProjectionIncomplete &&
    !admissionProjectionIncomplete;
  const datasetNextCursor = safeCalibrationCursor(visibleDatasets.page.next_cursor, 'dataset');
  const runNextCursor = safeCalibrationCursor(visibleRuns.page.next_cursor, 'run');
  const admissionNextCursor = safeCalibrationCursor(
    visibleAdmissions.page.next_cursor,
    'admission',
  );
  const changeCursorPage = (
    key: CalibrationPaginationKey,
    nextPage: number,
    currentPage: number,
    nextCursor: string,
    cursors: Map<number, string>,
  ) => {
    if (nextPage === currentPage + 1 && nextCursor) {
      cursors.set(nextPage, nextCursor);
    }
    const cursor = cursors.get(nextPage);
    if (cursor === undefined) return;
    const pageParam = `cal_${key}_page`;
    const cursorParam = `cal_${key}_cursor`;
    const next = new URLSearchParams(searchParams);
    if (nextPage > 1 && cursor) {
      next.set(pageParam, String(nextPage));
      next.set(cursorParam, cursor);
    } else {
      next.delete(pageParam);
      next.delete(cursorParam);
    }
    void setSearchParams(next);
  };

  return (
    <div className="calibration-workspace">
      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Anti-GEO admission</span>
            <h2>模型校准与准入</h2>
            <p className="panel-subtitle">
              外部授权数据集、独立审批、阈值评估和四眼准入形成一条不可跳过的治理链。
            </p>
          </div>
          <Badge tone={live && governanceReady ? 'positive' : 'warning'}>
            {!live
              ? 'contract fixture'
              : governanceReady
                ? '真实 intelligence API'
                : '真实 API · 部分不可用'}
          </Badge>
        </div>
        <MetricGrid
          metrics={[
            {
              label: '本页数据集',
              value: datasets ? String(datasets.data.length) : '—',
              detail: datasets ? '只展示摘要与治理状态' : '等待安全投影',
            },
            {
              label: '本页评估',
              value: runs ? String(runs.data.length) : '—',
              detail: runs
                ? `${runs.data.filter((item) => item.admission_passed).length} 个通过阈值`
                : '等待安全投影',
            },
            {
              label: '本页准入',
              value: admissions ? String(admissions.data.length) : '—',
              detail: admissions
                ? `${admissions.data.filter((item) => item.state === 'admitted').length} 个有效`
                : '等待安全投影',
            },
            {
              label: '准入策略',
              value: 'v1',
              detail: 'precision/recall/calibration 六项门槛',
            },
          ]}
        />
        <div className="button-row">
          <button
            className="button"
            disabled={
              governanceMutationPending ||
              !capabilities.analyze ||
              !datasets ||
              datasetProjectionIncomplete
            }
            onClick={() => setDatasetDialogScope(calibrationViewScope)}
          >
            登记校准数据集
          </button>
          <button
            className="button button-secondary"
            disabled={
              !capabilities.analyze ||
              governanceMutationPending ||
              !datasets ||
              datasetProjectionIncomplete ||
              !datasets.data.some((item) => item.state === 'approved')
            }
            onClick={() => setRunDialogScope(calibrationViewScope)}
          >
            运行模型评估
          </button>
        </div>
        {!capabilities.analyze ? (
          <span className="field-hint">登记和评估仅允许分析师；审核者不能代为提交。</span>
        ) : null}
        {notice?.scope === calibrationViewScope ? <Toast>{notice.message}</Toast> : null}
        {anyMutationFailed ? (
          <Toast tone="negative">
            操作未完成。请核对角色分离、数据状态和幂等约束后重试；响应内容未写入错误报告。
          </Toast>
        ) : null}
      </section>

      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Evaluation datasets</span>
            <h2>校准数据集</h2>
          </div>
          <Badge tone="info">最少 20 个独立传播簇</Badge>
        </div>
        <CalibrationProjectionNotice label="校准数据集" projection={datasetProjection} />
        {datasetFallback ? (
          datasetFallback
        ) : visibleDatasets.data.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <>
            <div
              className="table-scroll"
              role="region"
              aria-label="可横向滚动的校准数据集表格"
              tabIndex={0}
            >
              <table className="data-table">
                <caption className="sr-only">校准数据集治理状态</caption>
                <thead>
                  <tr>
                    <th scope="col">版本 / 摘要</th>
                    <th scope="col">样本</th>
                    <th scope="col">标注者</th>
                    <th scope="col">状态</th>
                    <th scope="col">提交时间</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleDatasets.data.map((item) => (
                    <tr key={item.pub_id}>
                      <td>
                        <strong>{item.version}</strong>
                        <span className="calibration-id">{item.dataset_sha256.slice(0, 12)}…</span>
                      </td>
                      <td>
                        {item.case_count} 条 · 正例 {item.positive_count}
                      </td>
                      <td>{item.labeler_count}</td>
                      <td>
                        <Badge tone={stateTone(item.state)}>{item.state}</Badge>
                      </td>
                      <td>
                        <time dateTime={item.submitted_at}>
                          {formatTimestamp(item.submitted_at)}
                        </time>
                      </td>
                      <td>
                        {item.state === 'draft' ? (
                          <button
                            className="button button-secondary"
                            disabled={
                              governanceMutationPending ||
                              !capabilities.review ||
                              datasetProjectionIncomplete
                            }
                            onClick={() =>
                              setReviewTarget({
                                kind: 'dataset',
                                pubId: item.pub_id,
                                scope: calibrationViewScope,
                              })
                            }
                          >
                            独立审批
                          </button>
                        ) : (
                          <span className="field-hint">
                            {item.approved_at ? `已批准 ${formatTimestamp(item.approved_at)}` : '—'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              label="校准数据集分页"
              page={datasetPage}
              pageCount={datasetPage + (visibleDatasets.page.has_more && datasetNextCursor ? 1 : 0)}
              onPageChange={(next) =>
                changeCursorPage(
                  'dataset',
                  next,
                  datasetPage,
                  datasetNextCursor,
                  datasetCursors.current,
                )
              }
            />
          </>
        )}
      </section>

      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Threshold evidence</span>
            <h2>评估运行</h2>
          </div>
          <Badge tone="neutral">策略：precision ≥ 80% · FPR ≤ 10%</Badge>
        </div>
        <CalibrationProjectionNotice label="评估运行" projection={runProjection} />
        {runFallback ? (
          runFallback
        ) : visibleRuns.data.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <>
            <div
              className="table-scroll"
              role="region"
              aria-label="可横向滚动的评估运行表格"
              tabIndex={0}
            >
              <table className="data-table">
                <caption className="sr-only">模型评估指标与准入状态</caption>
                <thead>
                  <tr>
                    <th scope="col">评分器 / 数据集</th>
                    <th scope="col">Precision / Recall</th>
                    <th scope="col">FPR / Brier / ECE</th>
                    <th scope="col">解释完整度</th>
                    <th scope="col">阈值结论</th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRuns.data.map((item) => (
                    <tr key={item.pub_id}>
                      <td>
                        <strong>{item.scorer_version}</strong>
                        <span className="calibration-id">
                          {item.metrics.dataset_version} · 训练簇 {item.training_cluster_count} ·{' '}
                          {item.training_cluster_manifest_sha256.slice(0, 10)}…
                        </span>
                      </td>
                      <td>
                        {formatRatio(item.metrics.precision)} / {formatRatio(item.metrics.recall)}
                      </td>
                      <td>
                        {formatRatio(item.metrics.false_positive_rate)} /{' '}
                        {formatRatio(item.metrics.brier_score)} /{' '}
                        {formatRatio(item.metrics.expected_calibration_error)}
                      </td>
                      <td>{formatRatio(item.metrics.explanation_completeness_rate)}</td>
                      <td>
                        <Badge tone={item.admission_passed ? 'positive' : 'danger'}>
                          {item.admission_passed ? '全部通过' : '未通过'}
                        </Badge>
                      </td>
                      <td>
                        {item.model_admission_state ? (
                          <Badge tone={stateTone(item.model_admission_state)}>
                            {item.model_admission_state}
                          </Badge>
                        ) : (
                          <button
                            className="button button-secondary"
                            disabled={
                              governanceMutationPending ||
                              !capabilities.review ||
                              !item.admission_passed ||
                              runProjectionIncomplete
                            }
                            onClick={() =>
                              setReviewTarget({
                                kind: 'run',
                                pubId: item.pub_id,
                                scope: calibrationViewScope,
                              })
                            }
                          >
                            独立准入
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              label="评估运行分页"
              page={runPage}
              pageCount={runPage + (visibleRuns.page.has_more && runNextCursor ? 1 : 0)}
              onPageChange={(next) =>
                changeCursorPage('run', next, runPage, runNextCursor, runCursors.current)
              }
            />
          </>
        )}
      </section>

      <section className="panel">
        <div className="account-head">
          <div>
            <span className="overline">Independent admissions</span>
            <h2>模型准入记录</h2>
          </div>
          <Badge tone="warning">同一评分器仅一个有效准入</Badge>
        </div>
        <CalibrationProjectionNotice label="模型准入记录" projection={admissionProjection} />
        {admissionFallback ? (
          admissionFallback
        ) : visibleAdmissions.data.length === 0 ? (
          <StatePanel state="empty" />
        ) : (
          <>
            <div
              className="table-scroll"
              role="region"
              aria-label="可横向滚动的模型准入记录表格"
              tabIndex={0}
            >
              <table className="data-table">
                <caption className="sr-only">独立模型准入记录</caption>
                <thead>
                  <tr>
                    <th scope="col">评分器</th>
                    <th scope="col">评估运行</th>
                    <th scope="col">状态</th>
                    <th scope="col">复核理由</th>
                    <th scope="col">准入时间</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleAdmissions.data.map((item) => (
                    <tr key={item.pub_id}>
                      <td>
                        <strong>{item.scorer_version}</strong>
                      </td>
                      <td className="calibration-id">{item.evaluation_run_pub_id}</td>
                      <td>
                        <Badge tone={stateTone(item.state)}>{item.state}</Badge>
                      </td>
                      <td>{item.rationale}</td>
                      <td>
                        <time dateTime={item.admitted_at}>{formatTimestamp(item.admitted_at)}</time>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              label="模型准入记录分页"
              page={admissionPage}
              pageCount={
                admissionPage + (visibleAdmissions.page.has_more && admissionNextCursor ? 1 : 0)
              }
              onPageChange={(next) =>
                changeCursorPage(
                  'admission',
                  next,
                  admissionPage,
                  admissionNextCursor,
                  admissionCursors.current,
                )
              }
            />
          </>
        )}
      </section>

      <DatasetDialog
        open={datasetDialogOpen}
        saving={governanceMutationPending}
        onClose={() => setDatasetDialogScope('')}
        onSubmit={submitDataset}
      />
      <RunDialog
        open={runDialogOpen}
        datasets={visibleDatasets.data}
        saving={governanceMutationPending}
        onClose={() => setRunDialogScope('')}
        onSubmit={submitRun}
      />
      <ReviewDialog
        target={effectiveReviewTarget}
        saving={governanceMutationPending}
        onClose={() => setReviewTarget(null)}
        onSubmit={async (rationale) => {
          const target = effectiveReviewTarget;
          if (!target) return;
          if (target.kind === 'dataset') {
            await submitDatasetApproval(target.pubId, rationale);
          } else {
            await submitAdmission(target.pubId, rationale);
          }
        }}
      />
    </div>
  );
}
