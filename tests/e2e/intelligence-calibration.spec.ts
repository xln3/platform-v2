import { expect, test, type Page, type Route } from './runtime-fixture';
import { expectAccessible } from './accessibility';

const requiredExplanationFields = [
  'evidence_sufficiency',
  'independent_source_count',
  'uncertainty',
  'rule_version',
  'model_version',
  'human_verdict_state',
];
const dataset = {
  pub_id: 'dset_e2e_draft',
  version: 'external-candidate-v2',
  dataset_sha256: 'a'.repeat(64),
  state: 'draft',
  case_count: 20,
  positive_count: 10,
  labeler_count: 2,
  submitted_at: '2026-07-25T08:00:00Z',
  approved_at: null,
  cookie: 'SESSION=calibration-dataset-canary',
};
const approvedDataset = {
  ...dataset,
  pub_id: 'dset_e2e_approved',
  version: 'external-approved-v1',
  state: 'approved',
  approved_at: '2026-07-25T08:30:00Z',
};
const evaluationRun = {
  pub_id: 'eval_e2e_passed',
  dataset_pub_id: approvedDataset.pub_id,
  scorer_version: 'anti-geo-scorer-v3',
  decision_threshold: '0.5',
  calibration_bins: 10,
  training_cluster_manifest_sha256: 'b'.repeat(64),
  training_cluster_count: 4,
  sample_count: 20,
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
    precision: '0.90',
    recall: '0.90',
    false_positive_rate: '0.10',
    brier_score: '0.10',
    expected_calibration_error: '0.05',
    explanation_completeness_rate: '1',
    sample_count: 20,
    positive_count: 10,
    negative_count: 10,
    dataset_version: approvedDataset.version,
    scorer_version: 'anti-geo-scorer-v3',
    evaluation_sha256: 'c'.repeat(64),
  },
  required_explanation_fields: requiredExplanationFields,
  created_at: '2026-07-25T09:00:00Z',
  token: 'Bearer calibration-run-canary',
};

async function installExperience(page: Page, role: 'analyst' | 'reviewer' | 'admin') {
  await page.addInitScript((actorRole) => {
    localStorage.setItem('geo.session.tenant', 'tnt_calibration_e2e');
    localStorage.setItem('geo.session.actor', `${actorRole}-calibration-e2e`);
    localStorage.setItem('geo.session.role', actorRole);
  }, role);
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_calibration_e2e',
        user_pub_id: `usr_${role}_calibration_e2e`,
        role,
        permissions:
          role === 'analyst'
            ? ['intelligence:read', 'intelligence:write']
            : role === 'reviewer'
              ? ['intelligence:read', 'intelligence:review']
              : ['intelligence:read', 'intelligence:write', 'intelligence:review'],
      }),
    }),
  );
  await page.route('**/api/v2/projects**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'prj_calibration_e2e',
            tenant_pub_id: 'tnt_calibration_e2e',
            name: '模型治理项目',
            state: 'active',
            created_at: '2026-07-25T00:00:00Z',
            updated_at: '2026-07-25T00:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
    }),
  );
}

const emptyInvestigation = (route: Route) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      data: [],
      page: { next_cursor: null, has_more: false },
    }),
  });

test('reviewer governance writes stay single under synchronous duplicate submissions', async ({
  page,
}) => {
  const writes: { path: string; body: unknown; idempotency: string | null }[] = [];
  let datasetApproved = false;
  let modelAdmitted = false;
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() === 'POST') {
      writes.push({
        path,
        body: request.postDataJSON(),
        idempotency: request.headers()['idempotency-key'] ?? null,
      });
      if (path.endsWith('/approve')) {
        datasetApproved = true;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ...dataset,
            state: 'approved',
            approved_at: '2026-07-25T10:00:00Z',
          }),
        });
      }
      if (path.endsWith('/admit')) {
        modelAdmitted = true;
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            pub_id: 'madm_e2e_ready',
            evaluation_run_pub_id: evaluationRun.pub_id,
            scorer_version: evaluationRun.scorer_version,
            state: 'admitted',
            rationale: '阈值和训练 holdout 隔离已经独立复核',
            admitted_at: '2026-07-25T10:10:00Z',
            revoked_at: null,
            profile_path: '/secret/profile/admission-canary',
          }),
        });
      }
    }
    if (path.endsWith('/evaluation-datasets')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            datasetApproved
              ? { ...dataset, state: 'approved', approved_at: '2026-07-25T10:00:00Z' }
              : dataset,
          ],
          page: { next_cursor: null, has_more: false, token: 'Bearer page-canary' },
        }),
      });
    }
    if (path.endsWith('/evaluation-runs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            {
              ...evaluationRun,
              model_admission_state: modelAdmitted ? 'admitted' : null,
            },
          ],
          page: { next_cursor: null, has_more: false, cookie: 'SESSION=page-canary' },
        }),
      });
    }
    if (path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: modelAdmitted
            ? [
                {
                  pub_id: 'madm_e2e_ready',
                  evaluation_run_pub_id: evaluationRun.pub_id,
                  scorer_version: evaluationRun.scorer_version,
                  state: 'admitted',
                  rationale: '阈值和训练 holdout 隔离已经独立复核',
                  admitted_at: '2026-07-25T10:10:00Z',
                  revoked_at: null,
                },
              ]
            : [],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  await expect(page.getByRole('heading', { name: '模型校准与准入' })).toBeVisible();
  await expect(page.getByText('真实 intelligence API')).toBeVisible();
  await expect(page.getByRole('button', { name: '登记校准数据集' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '运行模型评估' })).toBeDisabled();
  await expect(page.locator('body')).not.toContainText(
    /calibration-dataset-canary|calibration-run-canary|Bearer|SESSION=|profile\/admission-canary/i,
  );

  await page.getByRole('button', { name: '独立审批' }).click();
  await page.getByLabel('独立复核理由').fill('外部标签策略和不可变来源证据已由不同审核者复核');
  await page.getByRole('button', { name: '确认审批' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText(/数据集已由独立审核者批准/)).toBeVisible();

  await page.getByRole('button', { name: '独立准入' }).click();
  await page.getByLabel('独立复核理由').fill('阈值和训练 holdout 隔离已经独立复核');
  await page.getByRole('button', { name: '确认准入' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText(/模型准入已记录/)).toBeVisible();
  await expect(page.getByText('admitted').first()).toBeVisible();

  expect(writes.map((item) => item.path)).toEqual([
    '/api/v2/intelligence/evaluation-datasets/dset_e2e_draft/approve',
    '/api/v2/intelligence/evaluation-runs/eval_e2e_passed/admit',
  ]);
  expect(writes[0]?.body).toEqual({
    rationale: '外部标签策略和不可变来源证据已由不同审核者复核',
  });
  expect(writes[0]?.idempotency).toBeNull();
  expect(writes[1]?.idempotency).toMatch(/^model-admission-/);
  expect(new URL(page.url()).searchParams.toString()).toBe('section=calibration');
  const persisted = await page.evaluate(() => JSON.stringify(localStorage));
  expect(persisted).not.toMatch(
    /calibration-dataset-canary|calibration-run-canary|Bearer|SESSION=/i,
  );
});

test('cross-target Anti-GEO write receipts fail closed without success claims or leakage', async ({
  page,
}) => {
  const writes: string[] = [];
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() === 'POST') {
      writes.push(path);
      if (path.endsWith('/approve')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ...dataset,
            pub_id: 'dset_wrong_receipt',
            state: 'approved',
            approved_at: '2026-07-25T10:00:00Z',
            cookie: 'SESSION=approval-receipt-canary',
          }),
        });
      }
      if (path.endsWith('/admit')) {
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            pub_id: 'madm_wrong_receipt',
            evaluation_run_pub_id: 'eval_wrong_receipt',
            scorer_version: evaluationRun.scorer_version,
            state: 'admitted',
            rationale: '阈值和训练 holdout 隔离已经独立复核',
            admitted_at: '2026-07-25T10:10:00Z',
            revoked_at: null,
            token: 'Bearer admission-receipt-canary',
          }),
        });
      }
    }
    if (path.endsWith('/evaluation-datasets')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [dataset],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/evaluation-runs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [evaluationRun],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  await page.getByRole('button', { name: '独立审批' }).click();
  await page.getByLabel('独立复核理由').fill('外部标签策略和不可变来源证据已由不同审核者复核');
  await page.getByRole('button', { name: '确认审批' }).click();
  await expect(page.getByText(/操作未完成/)).toBeVisible();
  await expect(page.getByText(/数据集已由独立审核者批准/)).toHaveCount(0);
  await page.getByRole('button', { name: '关闭独立复核' }).click();

  await page.getByRole('button', { name: '独立准入' }).click();
  await page.getByLabel('独立复核理由').fill('阈值和训练 holdout 隔离已经独立复核');
  await page.getByRole('button', { name: '确认准入' }).click();
  await expect(page.getByText(/操作未完成/)).toBeVisible();
  await expect(page.getByText(/模型准入已记录/)).toHaveCount(0);
  await page.getByRole('button', { name: '关闭独立复核' }).click();

  expect(writes).toEqual([
    '/api/v2/intelligence/evaluation-datasets/dset_e2e_draft/approve',
    '/api/v2/intelligence/evaluation-runs/eval_e2e_passed/admit',
  ]);
  await expectAccessible(page);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /wrong-receipt|approval-receipt-canary|admission-receipt-canary|SESSION=|Bearer /i,
  );
});

test('calibration success waits for the exact governance projection refresh', async ({ page }) => {
  let releaseReconciliation!: () => void;
  const reconciliationGate = new Promise<void>((resolve) => {
    releaseReconciliation = resolve;
  });
  let approved = false;
  let reconciliationRequested = false;
  let datasetRequests = 0;
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() === 'POST' && path.endsWith('/approve')) {
      approved = true;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...dataset,
          state: 'approved',
          approved_at: '2026-07-25T11:30:00Z',
        }),
      });
    }
    if (path.endsWith('/evaluation-datasets')) {
      datasetRequests += 1;
      if (approved && datasetRequests > 1) {
        reconciliationRequested = true;
        await reconciliationGate;
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [
            approved
              ? { ...dataset, state: 'approved', approved_at: '2026-07-25T11:30:00Z' }
              : dataset,
          ],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/evaluation-runs') || path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  const datasetPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '校准数据集' }) });
  await expect(datasetPanel.getByText(dataset.version)).toBeVisible();
  await datasetPanel.getByRole('button', { name: '独立审批' }).click();
  await page.getByLabel('独立复核理由').fill('当前安全投影与外部标签策略已经独立复核');
  await page.getByRole('button', { name: '确认审批' }).click();

  await expect.poll(() => reconciliationRequested).toBe(true);
  await expect(page.getByRole('button', { name: '正在提交…' })).toBeVisible();
  await expect(datasetPanel.getByRole('button', { name: '独立审批' })).toBeDisabled();
  await expect(page.getByText(/数据集已由独立审核者批准/)).toHaveCount(0);
  await expect(datasetPanel.getByText('draft', { exact: true })).toBeVisible();

  releaseReconciliation();
  await expect(page.getByText(/数据集已由独立审核者批准/)).toBeVisible();
  await expect(page.getByRole('heading', { name: '独立审批数据集' })).toHaveCount(0);
  await expect(datasetPanel.getByText('approved', { exact: true })).toBeVisible();
  expect(datasetRequests).toBe(2);
});

test('analyst registration and evaluation stay single under synchronous duplicate submissions', async ({
  page,
}) => {
  const writes: { path: string; body: Record<string, unknown>; idempotency: string | null }[] = [];
  await installExperience(page, 'analyst');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() === 'POST') {
      writes.push({
        path,
        body: request.postDataJSON() as Record<string, unknown>,
        idempotency: request.headers()['idempotency-key'] ?? null,
      });
      if (path.endsWith('/evaluation-datasets')) {
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            ...dataset,
            pub_id: 'dset_e2e_registered',
            version: 'external-import-v3',
          }),
        });
      }
      if (path.endsWith('/runs')) {
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            ...evaluationRun,
            pub_id: 'eval_e2e_created',
            scorer_version: 'anti-geo-scorer-v4',
            training_cluster_count: 2,
            metrics: {
              ...evaluationRun.metrics,
              scorer_version: 'anti-geo-scorer-v4',
            },
          }),
        });
      }
    }
    if (path.endsWith('/evaluation-datasets')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [dataset, approvedDataset],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/evaluation-runs') || path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  await expect(page.getByRole('heading', { name: '模型校准与准入' })).toBeVisible();
  await expect(page.getByRole('button', { name: '独立审批' })).toBeDisabled();

  const cases = Array.from({ length: 20 }, (_, index) => ({
    case_digest: index.toString(16).padStart(64, '0'),
    propagation_cluster_digest: (index + 100).toString(16).padStart(64, '0'),
    actual_positive: index < 10,
  }));
  await page.getByRole('button', { name: '登记校准数据集' }).click();
  await page.getByLabel('数据集版本').fill('external-import-v3');
  await page.getByLabel('来源证据资产 ID').fill('evd_calibration_e2e');
  await page.getByLabel('来源证据 SHA-256').fill('d'.repeat(64));
  await page.getByLabel('案例摘要 JSON').fill(JSON.stringify(cases));
  await page.getByRole('button', { name: '登记数据集' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText(/数据集已登记为 draft/)).toBeVisible();

  const predictions = cases.map((item) => ({
    case_digest: item.case_digest,
    probability: item.actual_positive ? 0.9 : 0.1,
    predicted_positive: item.actual_positive,
    explanation_fields: requiredExplanationFields,
  }));
  await page.getByRole('button', { name: '运行模型评估' }).click();
  await page.getByLabel('评分器版本').fill('anti-geo-scorer-v4');
  await page
    .getByLabel('训练传播簇摘要 JSON')
    .fill(JSON.stringify(['e'.repeat(64), 'f'.repeat(64)]));
  await page.getByLabel('预测摘要 JSON').fill(JSON.stringify(predictions));
  await page.getByRole('button', { name: '运行评估' }).evaluate((button) => {
    button.click();
    button.click();
  });
  await expect(page.getByText(/评估已完成并通过全部阈值/)).toBeVisible();

  expect(writes.map((item) => item.path)).toEqual([
    '/api/v2/intelligence/evaluation-datasets',
    '/api/v2/intelligence/evaluation-datasets/dset_e2e_approved/runs',
  ]);
  expect(writes[0]?.idempotency).toMatch(/^evaluation-dataset-registration-/);
  expect(writes[1]?.idempotency).toMatch(/^evaluation-run-/);
  expect(writes[0]?.body.cases).toHaveLength(20);
  expect(writes[1]?.body.training_propagation_cluster_digests).toEqual([
    'e'.repeat(64),
    'f'.repeat(64),
  ]);
  expect(JSON.stringify(writes)).not.toMatch(/Cookie=|Bearer |SESSION=|token=/i);
});

test('a malformed dataset projection retries locally without hiding neighboring governance data', async ({
  page,
}) => {
  const canary = 'SESSION=calibration-local-retry-canary';
  let datasetRequests = 0;
  let runRequests = 0;
  let admissionRequests = 0;
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (path.endsWith('/evaluation-datasets')) {
      datasetRequests += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          datasetRequests === 1
            ? {
                data: null,
                page: { next_cursor: null, has_more: false },
                cookie: canary,
              }
            : {
                data: [dataset],
                page: { next_cursor: null, has_more: false },
              },
        ),
      });
    }
    if (path.endsWith('/evaluation-runs')) {
      runRequests += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [evaluationRun],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    if (path.endsWith('/model-admissions')) {
      admissionRequests += 1;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [],
          page: { next_cursor: null, has_more: false },
        }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  await expect(page.getByRole('heading', { name: '模型校准与准入' })).toBeVisible();
  await expect(page.getByText('真实 API · 部分不可用')).toBeVisible();

  const datasetPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '校准数据集' }) });
  const runPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '评估运行' }) });
  const admissionPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '模型准入记录' }) });
  await expect(datasetPanel.getByText('加载失败')).toBeVisible();
  await expect(runPanel.getByText(evaluationRun.scorer_version)).toBeVisible();
  await expect(admissionPanel.getByText('暂无数据')).toBeVisible();
  await expect(page.locator('body')).not.toContainText(canary);

  await datasetPanel.getByRole('button', { name: '重试此区域' }).click();
  await expect(datasetPanel.getByText(dataset.version)).toBeVisible();
  await expect(page.getByText('真实 intelligence API')).toBeVisible();
  expect(datasetRequests).toBe(2);
  expect(runRequests).toBe(1);
  expect(admissionRequests).toBe(1);
  expect(new URL(page.url()).searchParams.toString()).toBe('section=calibration');

  const browserSurfaces = await page.evaluate(() =>
    JSON.stringify({
      localStorage,
      sessionStorage,
      url: location.href,
      body: document.body.textContent,
    }),
  );
  expect(browserSurfaces).not.toContain(canary);
});

test('oversized or unsafe calibration pages stay explicit and governance-write locked', async ({
  page,
}) => {
  const writes: string[] = [];
  await installExperience(page, 'admin');
  const datasetRows = Array.from({ length: 21 }, (_, index) => ({
    ...dataset,
    pub_id: `dset_projection_${index}`,
    version: index === 1 ? 'Bearer calibration-page-secret' : `external-projection-v${index}`,
    cookie: 'SESSION=calibration-page-extension-secret',
  }));
  const runRows = Array.from({ length: 21 }, (_, index) => ({
    ...evaluationRun,
    pub_id: `eval_projection_${index}`,
    dataset_pub_id: `dset_projection_${index}`,
    scorer_version: index === 1 ? 'Cookie=calibration-run-secret' : `anti-geo-v${index}`,
    metrics: {
      ...evaluationRun.metrics,
      dataset_version: `external-projection-v${index}`,
      scorer_version: index === 1 ? 'Cookie=calibration-run-secret' : `anti-geo-v${index}`,
    },
    profile_path: '/secret/profile/calibration-run-extension-secret',
  }));
  const admissionRows = Array.from({ length: 21 }, (_, index) => ({
    pub_id: `madm_projection_${index}`,
    evaluation_run_pub_id: `eval_projection_${index}`,
    scorer_version: `anti-geo-v${index}`,
    state: 'admitted',
    rationale: index === 1 ? 'Bearer calibration-admission-secret' : `独立复核 ${index}`,
    admitted_at: '2026-07-25T10:10:00Z',
    revoked_at: null,
    otp: 824911,
  }));
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() !== 'GET') {
      writes.push(`${request.method()} ${path}`);
      return route.fulfill({ status: 409, contentType: 'application/json', body: '{}' });
    }
    const data = path.endsWith('/evaluation-datasets')
      ? datasetRows
      : path.endsWith('/evaluation-runs')
        ? runRows
        : admissionRows;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data,
        page: { next_cursor: null, has_more: false },
        token: 'Bearer calibration-page-root-secret',
      }),
    });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  await expect(page.getByRole('heading', { name: '模型校准与准入' })).toBeVisible();
  await expect(page.getByText('真实 API · 部分不可用')).toBeVisible();
  await expect(
    page.getByText(/校准数据集：服务返回 21 条，浏览器安全视图展示 19 条/),
  ).toBeVisible();
  await expect(page.getByText(/评估运行：服务返回 21 条，浏览器安全视图展示 19 条/)).toBeVisible();
  await expect(
    page.getByText(/模型准入记录：服务返回 21 条，浏览器安全视图展示 19 条/),
  ).toBeVisible();
  await expect(page.getByText('安全投影不完整', { exact: true })).toHaveCount(3);
  await expect(page.getByRole('button', { name: '登记校准数据集' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '运行模型评估' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '独立审批' }).first()).toBeDisabled();
  await expect(page.getByRole('button', { name: '独立准入' }).first()).toBeDisabled();
  await expectAccessible(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /calibration-(?:page|run|admission)-(?:secret|extension-secret)|Bearer |Cookie=|SESSION=|824911|profile/i,
  );
  expect(writes).toEqual([]);
});

test('calibration cursor pagination restores browser history and rejects secret cursors', async ({
  page,
}) => {
  const requestCursors: Array<string | null> = [];
  const requestCanary = 'calibration-pagination-request-canary';
  const responseCanary = 'calibration-pagination-response-canary';
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const requestUrl = new URL(route.request().url());
    const path = requestUrl.pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (path.endsWith('/evaluation-datasets')) {
      const cursor = requestUrl.searchParams.get('cursor');
      requestCursors.push(cursor);
      const secondPage = cursor === 'dset_cursor_safe_02';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [secondPage ? approvedDataset : dataset],
          page: {
            next_cursor: secondPage ? null : 'dset_cursor_safe_02',
            has_more: !secondPage,
            cookie: `SESSION=${responseCanary}`,
          },
          token: `Bearer ${responseCanary}`,
        }),
      });
    }
    if (path.endsWith('/evaluation-runs')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [evaluationRun],
          page: {
            next_cursor: `eval_Bearer ${responseCanary}`,
            has_more: true,
          },
        }),
      });
    }
    if (path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto(
    `/platform/intelligence/?section=calibration&cal_dataset_page=2&cal_dataset_cursor=${encodeURIComponent(`dset_Bearer ${requestCanary}`)}`,
  );
  await expect(page.getByRole('heading', { name: '模型校准与准入' })).toBeVisible();
  await expect(page).not.toHaveURL(/cal_dataset_(?:page|cursor)=/);

  const datasetPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '校准数据集' }) });
  const runPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '评估运行' }) });
  await expect(datasetPanel.getByText(dataset.version)).toBeVisible();
  await expect(runPanel.getByRole('button', { name: '下一页' })).toBeDisabled();

  await datasetPanel.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/cal_dataset_page=2/);
  await expect(page).toHaveURL(/cal_dataset_cursor=dset_cursor_safe_02/);
  await expect(datasetPanel.getByText(approvedDataset.version)).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(/cal_dataset_cursor=dset_cursor_safe_02/);
  await expect(datasetPanel.getByText(approvedDataset.version)).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/cal_dataset_(?:page|cursor)=/);
  await expect(datasetPanel.getByText(dataset.version)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/cal_dataset_cursor=dset_cursor_safe_02/);
  await expect(datasetPanel.getByText(approvedDataset.version)).toBeVisible();

  expect(requestCursors).toEqual([null, 'dset_cursor_safe_02', 'dset_cursor_safe_02', null]);
  const browserSurfaces = await page.evaluate(() =>
    JSON.stringify({
      localStorage,
      sessionStorage,
      url: location.href,
      body: document.body.textContent,
    }),
  );
  expect(browserSurfaces).not.toMatch(
    new RegExp(`${requestCanary}|${responseCanary}|SESSION=|Bearer`, 'i'),
  );
});

test('calibration history closes prior-page review and suppresses its delayed receipt', async ({
  page,
}) => {
  let releaseApproval!: () => void;
  const approvalGate = new Promise<void>((resolve) => {
    releaseApproval = resolve;
  });
  const writes: string[] = [];
  const pageOneDataset = {
    ...dataset,
    pub_id: 'dset_scope_page_01',
    version: 'scope-page-one',
  };
  const pageTwoDataset = {
    ...dataset,
    pub_id: 'dset_scope_page_02',
    version: 'scope-page-two',
  };
  await installExperience(page, 'reviewer');
  await page.route('**/api/v2/intelligence/**', async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    if (path.endsWith('/investigations')) return emptyInvestigation(route);
    if (request.method() === 'POST' && path.endsWith('/dset_scope_page_02/approve')) {
      writes.push(path);
      await approvalGate;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...pageTwoDataset,
          state: 'approved',
          approved_at: '2026-07-25T12:40:00Z',
        }),
      });
    }
    if (path.endsWith('/evaluation-datasets')) {
      const secondPage = requestUrl.searchParams.get('cursor') === 'dset_scope_cursor_02';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          data: [secondPage ? pageTwoDataset : pageOneDataset],
          page: {
            next_cursor: secondPage ? null : 'dset_scope_cursor_02',
            has_more: !secondPage,
          },
        }),
      });
    }
    if (path.endsWith('/evaluation-runs') || path.endsWith('/model-admissions')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
      });
    }
    return route.fulfill({ status: 404, body: '' });
  });

  await page.goto('/platform/intelligence/?section=calibration');
  const datasetPanel = page
    .locator('section.panel')
    .filter({ has: page.getByRole('heading', { name: '校准数据集' }) });
  await expect(datasetPanel.getByText(pageOneDataset.version)).toBeVisible();
  await datasetPanel.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/cal_dataset_page=2/);
  await expect(datasetPanel.getByText(pageTwoDataset.version)).toBeVisible();

  await datasetPanel.getByRole('button', { name: '独立审批' }).click();
  await page.getByLabel('独立复核理由').fill('当前页外部标签策略已完成独立复核');
  await page.getByRole('button', { name: '确认审批' }).click();
  await expect(page.getByRole('button', { name: '正在提交…' })).toBeVisible();

  await page.goBack();
  await expect(page).not.toHaveURL(/cal_dataset_(?:page|cursor)=/);
  await expect(page.getByRole('heading', { name: '独立审批数据集' })).toHaveCount(0);
  await expect(datasetPanel.getByText(pageOneDataset.version)).toBeVisible();
  await expect(page.getByText(/数据集已由独立审核者批准/)).toHaveCount(0);
  await expect(page.getByText(/操作未完成/)).toHaveCount(0);

  releaseApproval();
  await expect
    .poll(() => writes)
    .toEqual(['/api/v2/intelligence/evaluation-datasets/dset_scope_page_02/approve']);
  await expect(datasetPanel.getByRole('button', { name: '独立审批' })).toBeEnabled();
  await expect(page.getByText(/数据集已由独立审核者批准/)).toHaveCount(0);
  await expect(page.getByText(/操作未完成/)).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(/SESSION=/i);
});
