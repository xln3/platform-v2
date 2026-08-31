import { expect, test } from './runtime-fixture';

const projectPubId = 'prj_customer_metric_trace';

const neutralRecommendationMetrics = [
  'ai_recommendation_organic_mention_rate_v2',
  'ai_recommendation_organic_recommendation_rate_v2',
  'ai_recommendation_rankable_response_rate_v2',
  'ai_recommendation_organic_top1_visibility_rate_v2',
  'ai_recommendation_organic_top3_visibility_rate_v2',
  'ai_recommendation_organic_top5_visibility_rate_v2',
  'ai_recommendation_organic_top1_given_rankable_rate_v2',
  'ai_recommendation_organic_top3_given_rankable_rate_v2',
  'ai_recommendation_organic_top5_given_rankable_rate_v2',
  'ai_recommendation_mean_rank_given_target_ranked_v2',
  'ai_recommendation_entity_share_v2',
];

test('customer V2 trace corrects one fact without legacy or model reads', async ({ page }) => {
  const dashboardRequests: URL[] = [];
  const forbiddenReads: string[] = [];
  const correctionRequests: Array<{ url: URL; body: unknown }> = [];
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_metric_trace');
    localStorage.setItem('geo.session.actor', 'customer-metric-trace');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_metric_trace',
        user_pub_id: 'usr_customer_metric_trace',
        role: 'customer',
        permissions: ['project:read'],
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
            pub_id: projectPubId,
            tenant_pub_id: 'tnt_customer_metric_trace',
            name: '客户 V2 指标追溯项目',
            state: 'active',
            created_at: '2026-08-01T00:00:00Z',
            updated_at: '2026-08-17T08:00:00Z',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  await page.route('**/api/v2/metrics/operations/semantic-decisions/*/overrides', async (route) => {
    correctionRequests.push({
      url: new URL(route.request().url()),
      body: route.request().postDataJSON(),
    });
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        schema_version: 'semantic-decision-override-v2',
        decision_pub_id: 'sdr_customer_trace_corrected',
        supersedes_pub_id: 'sdr_customer_trace_01',
        decision_hash: '7'.repeat(64),
        recompute_job_pub_id: 'mrj_customer_trace_corrected',
      }),
    });
  });
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (url.pathname.includes('/api/v2/customer-dashboard/')) dashboardRequests.push(url);
    const expectedCorrection =
      request.method() === 'POST' &&
      url.pathname.endsWith(
        '/api/v2/metrics/operations/semantic-decisions/sdr_customer_trace_01/overrides',
      );
    if (
      (url.pathname.includes('/api/v2/customer-dashboard/') && request.method() !== 'GET') ||
      (url.pathname.includes('/semantic-decisions') && !expectedCorrection) ||
      url.pathname.includes('/decision-jobs') ||
      url.pathname.includes('/recompute')
    ) {
      forbiddenReads.push(`${request.method()} ${url.pathname}`);
    }
  });
  await page.goto('/platform/customer/?section=monitoring');

  await expect(page.getByRole('heading', { name: '云岫智能 · AI 推荐' })).toBeVisible();
  await expect(page.getByText('mss_customer_dashboard_fixture').first()).toBeVisible();
  await expect(page.getByRole('region', { name: 'Top3 完整指标组' })).toBeVisible();
  await expect(page.getByText('Top3 率（仅可排序回答）')).toBeVisible();

  const initialDashboardRequest = dashboardRequests.find(
    (url) =>
      url.pathname.endsWith('/dashboard-v2') &&
      url.searchParams.get('business_view') === 'ai_recommendation',
  );
  expect(initialDashboardRequest).toBeTruthy();
  expect(initialDashboardRequest?.searchParams.get('exposure_role')).toBe('brand_neutral');
  expect(initialDashboardRequest?.searchParams.getAll('metric_name')).toEqual(
    neutralRecommendationMetrics,
  );
  expect(initialDashboardRequest?.searchParams.getAll('metric_name')).not.toContain('mention_rate');

  await page.getByRole('button', { name: '查看计算明细' }).first().click();
  const dialog = page.getByRole('dialog', { name: /ai_recommendation_/ });
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole('cell', { name: 'accepted_recommendation_relation', exact: true }),
  ).toBeVisible();
  await expect(dialog.getByText('query 0.250000')).toBeVisible();
  await expect(
    dialog.getByText(/design 1\.000000 × repeat 1\.000000 = final 0\.250000/),
  ).toBeVisible();
  await expect(dialog.getByText('recommendation_relation_v2@2.0.0')).toBeVisible();
  await expect(dialog.getByText(/model · accepted · 模型自动判定 · 置信度 96\.0%/)).toBeVisible();
  await expect(dialog.getByText('回答明确把焦点品牌作为正向推荐候选。')).toBeVisible();
  await expect(dialog.getByText('云岫智能值得优先考虑。')).toBeVisible();
  await expect(dialog.getByText('mss_customer_dashboard_fixture')).toBeVisible();
  await expect(dialog.getByText('a'.repeat(64))).toBeVisible();
  await expect(dialog.getByText('e'.repeat(64))).toBeVisible();
  await expect(dialog.getByRole('button', { name: '关闭计算明细' })).toBeFocused();
  await dialog.getByRole('button', { name: '纠错' }).click();
  const correctionForm = dialog.getByRole('form', {
    name: '纠正 recommendation_relation_v2 判定',
  });
  await expect(correctionForm).toBeVisible();
  await expect(correctionForm.getByText(/只修正这一条具体事实/u)).toBeVisible();
  await expect(correctionForm.getByLabel('修正后的结构化判定')).toHaveValue(
    JSON.stringify({ relation: 'recommended', stance: 'positive' }, null, 2),
  );
  await correctionForm
    .getByLabel('修正后的结构化判定')
    .fill(JSON.stringify({ relation: 'mentioned', stance: 'neutral' }, null, 2));
  await correctionForm.getByLabel('纠错理由').fill('原文只是中性提及，不构成推荐。');
  await correctionForm.getByRole('button', { name: '提交纠错并重算' }).click();
  await expect(correctionForm.getByRole('status')).toContainText(
    '纠错已提交，受影响指标正在自动重算',
  );
  expect(correctionRequests).toHaveLength(1);
  expect(correctionRequests[0]?.url.pathname).toBe(
    '/api/v2/metrics/operations/semantic-decisions/sdr_customer_trace_01/overrides',
  );
  expect(correctionRequests[0]?.body).toEqual({
    project_pub_id: projectPubId,
    result: { relation: 'mentioned', stance: 'neutral' },
    rationale_summary: '原文只是中性提及，不构成推荐。',
    reason_codes: ['customer_correction'],
    expected_decision_hash: '6'.repeat(64),
  });
  await correctionForm.getByRole('button', { name: '完成' }).click();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);

  await page.getByRole('button', { name: 'AI 印象', exact: true }).click();
  await expect(page.getByRole('heading', { name: '云岫智能 · AI 印象' })).toBeVisible();
  await expect
    .poll(() =>
      dashboardRequests
        .filter((url) => url.pathname.endsWith('/dashboard-v2'))
        .at(-1)
        ?.searchParams.getAll('metric_name'),
    )
    .toEqual(['ai_impression_neutral_spontaneous_association_rate_v2']);

  await page.getByRole('button', { name: 'AI 推荐', exact: true }).click();
  await expect(page.getByRole('heading', { name: '云岫智能 · AI 推荐' })).toBeVisible();
  await page.getByRole('button', { name: '焦点品牌点名' }).click();
  await expect
    .poll(() => {
      const latest = dashboardRequests
        .filter((url) => url.pathname.endsWith('/dashboard-v2'))
        .at(-1);
      return {
        exposureRole: latest?.searchParams.get('exposure_role'),
        metricNames: latest?.searchParams.getAll('metric_name'),
      };
    })
    .toEqual({
      exposureRole: 'focal_named_only',
      metricNames: [
        'prompted_recommendation_positive_rate_v2',
        'prompted_recommendation_conditional_rate_v2',
        'prompted_recommendation_negative_rate_v2',
        'prompted_recommendation_neutral_rate_v2',
      ],
    });

  const legacyRequests = dashboardRequests.filter((url) => {
    const path = url.pathname;
    return (
      path.endsWith(`/projects/${projectPubId}`) ||
      path.endsWith('/metrics/catalog') ||
      path.endsWith(`/projects/${projectPubId}/answers`)
    );
  });
  expect(legacyRequests).toEqual([]);
  expect(forbiddenReads).toEqual([]);
});
