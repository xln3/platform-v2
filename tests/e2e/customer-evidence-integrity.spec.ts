import { expect, test, type Page } from './runtime-fixture';
import { expectAccessible } from './accessibility';

async function installCustomerEvidenceExperience(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('geo.session.tenant', 'tnt_customer_evidence_integrity');
    localStorage.setItem('geo.session.actor', 'customer-evidence-integrity');
    localStorage.setItem('geo.session.role', 'customer');
  });
  await page.route('**/api/v2/identity/session', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_pub_id: 'tnt_customer_evidence_integrity',
        user_pub_id: 'usr_customer_evidence_integrity',
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
            pub_id: 'prj_customer_evidence_integrity',
            tenant_pub_id: 'tnt_customer_evidence_integrity',
            name: '客户证据完整性项目',
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

test('oversized answer evidence stays bounded, explicit and package-write locked', async ({
  page,
}) => {
  const packageWrites: unknown[] = [];
  await installCustomerEvidenceExperience(page);
  await page.route('**/api/v2/analytics/answers**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/relations')) {
      const citations = Array.from({ length: 201 }, (_, index) => ({
        pub_id: `cit_customer_integrity_${index}`,
        ordinal: index + 1,
        canonical_url:
          index === 199
            ? 'https://user:proxy-password@source.example/article'
            : `https://source${index}.example/article`,
        host: `source${index}.example`,
        title: `安全来源 ${index}`,
        cited_text: index === 199 ? 'Bearer relation-citation-canary' : null,
        own_source: false,
        content_hash: 'c'.repeat(64),
      }));
      const evidence = Array.from({ length: 201 }, (_, index) => ({
        pub_id: `evd_customer_integrity_${index}`,
        relation_type: 'visualizes',
        kind: 'answer_screenshot',
        access_class: 'customer_private',
        sha256: 'a'.repeat(64),
        mime_type: 'image/png',
        byte_size: 512,
        source_url: 'https://capture.example/answer',
        capture_time: '2026-07-25T08:00:00Z',
        anchors:
          index === 0
            ? Array.from({ length: 201 }, (__, anchorIndex) => ({
                pub_id: `anch_customer_integrity_${anchorIndex}`,
                text_start: anchorIndex,
                text_end: anchorIndex + 1,
                bbox: null,
                page_number: null,
                quote_hash: 'd'.repeat(64),
              }))
            : [],
        object_key: index === 200 ? 'Cookie=relation-evidence-canary' : undefined,
      }));
      const history = Array.from({ length: 201 }, (_, index) => ({
        pub_id: `diff_customer_integrity_${index}`,
        before_evidence_pub_id: `evd_before_${index}`,
        after_evidence_pub_id: `evd_after_${index}`,
        similarity: 0.75,
        visual_diff_available: true,
        created_at: index === 199 ? '1' : '2026-07-25T08:00:00Z',
        text_diff: index === 199 ? 'Bearer relation-history-canary' : undefined,
      }));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer_pub_id: 'ans_customer_integrity_safe',
          citations,
          evidence,
          history,
          cookie: 'SESSION=relation-root-canary',
        }),
      });
    }
    const answer = {
      pub_id: 'ans_customer_integrity_safe',
      project_pub_id: 'prj_customer_evidence_integrity',
      run_pub_id: 'run_customer_integrity_safe',
      config_version_pub_id: 'cfv_customer_integrity_safe',
      query_pub_id: 'qry_customer_integrity_safe',
      query_text: '大关系安全问题',
      response_text: '只展示有界且经过安全投影的真实回答。',
      model: 'doubao',
      region: '上海',
      mode: 'deep',
      eligible: true,
      degraded: false,
      capture_time: '2026-07-25T08:00:00Z',
      mentioned: null,
      rank: null,
      sentiment: null,
      recommendation_state: null,
      citation_count: 201,
    };
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          answer,
          {
            ...answer,
            pub_id: 'ans_customer_integrity_invalid',
            query_text: '不得展示的无效回答',
            response_text: 'Bearer answer-page-canary',
          },
          {
            ...answer,
            pub_id: 'ans_customer_integrity_over_limit',
            query_text: '不得展示的超限回答',
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    });
  });
  await page.route('**/api/v2/evidence/assets**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          {
            pub_id: 'evd_customer_page_safe',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '2026-07-25T08:00:00Z',
            sha256: 'e'.repeat(64),
          },
          {
            pub_id: 'evd_customer_page_invalid',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '1',
            sha256: 'f'.repeat(64),
            token: 'Bearer evidence-page-canary',
          },
          {
            pub_id: 'evd_customer_page_over_limit',
            kind: 'answer_screenshot',
            mime_type: 'image/png',
            capture_time: '2026-07-25T08:00:00Z',
            sha256: 'f'.repeat(64),
          },
        ],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );
  // 证据画廊逐资产拉取 content（VerifiedBlobImage）；列表 mock 的 `assets**` 通配会 shadow
  // 该路径并返回 JSON，加载器因 MIME 不符中止请求（request-failed）。此处显式补一个合法
  // PNG 响应；尺寸/哈希与夹具元数据不符时加载器 fail-closed 为占位态，不产生运行时告警。
  await page.route('**/api/v2/evidence/assets/*/content', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
        'base64',
      ),
    }),
  );
  await page.route('**/api/v2/evidence/packages', async (route) => {
    packageWrites.push(route.request().postDataJSON());
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ package_pub_id: 'pkg_should_not_exist' }),
    });
  });

  await page.goto('/platform/customer/?section=evidence');
  await expect(page.getByRole('heading', { name: '大关系安全问题' })).toBeVisible();
  await expect(page.getByText('尚未判断')).toBeVisible();
  await expect(page.getByText('不得展示的无效回答')).toHaveCount(0);
  await expect(page.getByText('不得展示的超限回答')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '生成证据包' })).toBeDisabled();
  await expect(page.getByText('本页回答：服务返回 3 条，浏览器安全视图展示 1 条')).toBeVisible();
  await expect(
    page.getByText('本页证据资产：服务返回 3 条，浏览器安全视图展示 1 条'),
  ).toBeVisible();

  const openedAt = Date.now();
  await page.getByRole('button', { name: '打开证据中心' }).click();
  const dialog = page.getByRole('dialog', { name: '证据与历史差异' });
  await expect(
    dialog.getByText('回答引用：服务返回 201 条，浏览器安全视图展示 199 条'),
  ).toBeVisible();
  await expect(
    dialog.getByText('回答关联证据：服务返回 201 条，浏览器安全视图展示 200 条'),
  ).toBeVisible();
  await expect(
    dialog.getByText('单项证据锚点：服务返回 201 条，浏览器安全视图展示 200 条'),
  ).toBeVisible();
  await expect(
    dialog.getByText('证据历史差异：服务返回 201 条，浏览器安全视图展示 199 条'),
  ).toBeVisible();
  expect(Date.now() - openedAt).toBeLessThan(10_000);
  await expect(
    dialog.getByRole('region', { name: '答案组织引用' }).locator('tbody tr'),
  ).toHaveCount(199);
  await expect(
    dialog.getByRole('region', { name: '回答关联证据' }).locator('tbody tr'),
  ).toHaveCount(200);
  await expect(
    dialog.getByRole('region', { name: '证据历史差异' }).locator('tbody tr'),
  ).toHaveCount(199);
  await expect(dialog.getByText('安全投影不完整')).toBeVisible();
  await expectAccessible(page);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /proxy-password|relation-citation-canary|relation-evidence-canary|relation-history-canary|relation-root-canary|answer-page-canary|evidence-page-canary|SESSION=|Bearer /i,
  );
  expect(packageWrites).toEqual([]);
});

test('closing one answer discards its slower relation response before another answer opens', async ({
  page,
}) => {
  let firstRelationRequested = false;
  let releaseFirstRelation: (() => void) | undefined;
  const firstRelationGate = new Promise<void>((resolve) => {
    releaseFirstRelation = resolve;
  });
  await installCustomerEvidenceExperience(page);
  const answer = (id: string, title: string) => ({
    pub_id: id,
    project_pub_id: 'prj_customer_evidence_integrity',
    run_pub_id: `run_${id.slice(4)}`,
    config_version_pub_id: `cfv_${id.slice(4)}`,
    query_pub_id: `qry_${id.slice(4)}`,
    query_text: title,
    response_text: `${title}的安全回答`,
    model: 'doubao',
    region: '上海',
    mode: 'deep',
    eligible: true,
    degraded: false,
    capture_time: '2026-07-25T08:00:00Z',
    mentioned: true,
    rank: 1,
    sentiment: 'positive',
    recommendation_state: null,
    citation_count: 1,
  });
  await page.route('**/api/v2/analytics/answers**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/relations')) {
      const answerPubId = pathname.split('/').at(-2) ?? '';
      if (answerPubId === 'ans_relation_first') {
        firstRelationRequested = true;
        await firstRelationGate;
      }
      const first = answerPubId === 'ans_relation_first';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer_pub_id: answerPubId,
          citations: [
            {
              pub_id: first ? 'cit_relation_first' : 'cit_relation_second',
              ordinal: 1,
              canonical_url: first
                ? 'https://stale.example/article'
                : 'https://current.example/article',
              host: first ? 'stale.example' : 'current.example',
              title: first ? 'A 过期来源' : 'B 当前来源',
              cited_text: null,
              own_source: false,
              content_hash: 'a'.repeat(64),
              token: first ? 'Bearer stale-relation-canary' : undefined,
            },
          ],
          evidence: [],
          history: [],
        }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          answer('ans_relation_first', 'A 较慢回答'),
          answer('ans_relation_second', 'B 当前回答'),
        ],
        page: { next_cursor: null, has_more: false },
      }),
    });
  });
  await page.route('**/api/v2/evidence/assets**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [],
        page: { next_cursor: null, has_more: false },
      }),
    }),
  );

  await page.goto('/platform/customer/?section=evidence');
  const openButtons = page.getByRole('button', { name: '打开证据中心' });
  await expect(openButtons).toHaveCount(2);
  await openButtons.nth(0).click();
  await expect.poll(() => firstRelationRequested).toBe(true);
  await page.getByRole('button', { name: '关闭证据弹窗' }).click();
  await openButtons.nth(1).click();
  const dialog = page.getByRole('dialog', { name: '证据与历史差异' });
  await expect(dialog.getByText('B 当前来源')).toBeVisible();
  releaseFirstRelation?.();
  await page.waitForTimeout(800);
  await expect(dialog.getByText('B 当前来源')).toBeVisible();
  await expect(dialog.getByText('A 过期来源')).toHaveCount(0);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(/stale-relation-canary|Bearer /i);
  await expectAccessible(page);
});

test('answer and asset pagination discard delayed detail and package receipts from the prior page', async ({
  page,
}) => {
  let firstRelationRequested = false;
  let packageRequested = false;
  let secondAnswerRequested = false;
  let secondAssetRequested = false;
  let releaseFirstRelation: (() => void) | undefined;
  let releasePackage: (() => void) | undefined;
  let releaseSecondAnswer: (() => void) | undefined;
  let releaseSecondAsset: (() => void) | undefined;
  const firstRelationGate = new Promise<void>((resolve) => {
    releaseFirstRelation = resolve;
  });
  const packageGate = new Promise<void>((resolve) => {
    releasePackage = resolve;
  });
  const secondAnswerGate = new Promise<void>((resolve) => {
    releaseSecondAnswer = resolve;
  });
  const secondAssetGate = new Promise<void>((resolve) => {
    releaseSecondAsset = resolve;
  });
  const packageBodies: { package_pub_id: string; evidence_pub_ids: string[] }[] = [];
  const answer = (pubId: string, title: string) => ({
    pub_id: pubId,
    project_pub_id: 'prj_customer_evidence_integrity',
    run_pub_id: `run_${pubId.slice(4)}`,
    config_version_pub_id: `cfv_${pubId.slice(4)}`,
    query_pub_id: `qry_${pubId.slice(4)}`,
    query_text: title,
    response_text: `${title}的安全回答`,
    model: 'doubao',
    region: '上海',
    mode: 'deep',
    eligible: true,
    degraded: false,
    capture_time: '2026-07-25T08:00:00Z',
    mentioned: true,
    rank: 1,
    sentiment: 'positive',
    recommendation_state: null,
    citation_count: 1,
  });
  const asset = (pubId: string) => ({
    pub_id: pubId,
    kind: 'answer_screenshot',
    mime_type: 'image/png',
    capture_time: '2026-07-25T08:00:00Z',
    sha256: 'a'.repeat(64),
  });
  await installCustomerEvidenceExperience(page);
  await page.route('**/api/v2/analytics/answers**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/relations')) {
      firstRelationRequested = true;
      await firstRelationGate;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          answer_pub_id: 'ans_evidence_page_01',
          citations: [
            {
              pub_id: 'cit_evidence_page_stale',
              ordinal: 1,
              canonical_url: 'https://stale.example/article',
              host: 'stale.example',
              title: '上一页延迟来源',
              cited_text: null,
              own_source: false,
              content_hash: 'b'.repeat(64),
              token: 'Bearer stale-answer-page-relation-canary',
            },
          ],
          evidence: [],
          history: [],
        }),
      });
    }
    const secondPage = url.searchParams.get('cursor') === 'ans_evidence_page_01';
    if (secondPage) {
      secondAnswerRequested = true;
      await secondAnswerGate;
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [
          secondPage
            ? answer('ans_evidence_page_02', '第二页当前回答')
            : answer('ans_evidence_page_01', '第一页较慢回答'),
        ],
        page: {
          next_cursor: secondPage ? null : 'ans_evidence_page_01',
          has_more: !secondPage,
        },
      }),
    });
  });
  await page.route('**/api/v2/evidence/assets**', async (route) => {
    const secondPage =
      new URL(route.request().url()).searchParams.get('cursor') === 'evd_evidence_page_01';
    if (secondPage) {
      secondAssetRequested = true;
      await secondAssetGate;
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: [secondPage ? asset('evd_evidence_page_02') : asset('evd_evidence_page_01')],
        page: {
          next_cursor: secondPage ? null : 'evd_evidence_page_01',
          has_more: !secondPage,
        },
      }),
    });
  });
  await page.route('**/api/v2/evidence/packages', async (route) => {
    const body = route.request().postDataJSON() as {
      package_pub_id: string;
      evidence_pub_ids: string[];
    };
    packageBodies.push(body);
    packageRequested = true;
    await packageGate;
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        package_pub_id: body.package_pub_id,
        manifest_sha256: 'c'.repeat(64),
        state: 'ready',
        token: 'Bearer stale-evidence-package-receipt-canary',
      }),
    });
  });

  await page.goto('/platform/customer/?section=evidence');
  await expect(page.getByRole('heading', { name: '第一页较慢回答' })).toBeVisible();
  const answerPagination = page.getByRole('navigation', { name: '回答分页' });
  await answerPagination.getByRole('button', { name: '下一页' }).click();
  await expect(page).toHaveURL(/answer_page=2/);
  await expect.poll(() => secondAnswerRequested).toBe(true);
  await expect(page.getByText('正在加载', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: '第一页较慢回答' })).toHaveCount(0);
  releaseSecondAnswer?.();
  await expect(page.getByRole('heading', { name: '第二页当前回答' })).toBeVisible();
  await page.goBack();
  await expect(page).not.toHaveURL(/answer_page=2/);
  await expect(page.getByRole('heading', { name: '第一页较慢回答' })).toBeVisible();
  await page.getByRole('button', { name: '打开证据中心' }).click();
  await expect.poll(() => firstRelationRequested).toBe(true);
  await page.goForward();
  await expect(page).toHaveURL(/answer_page=2/);
  await expect(page.getByRole('heading', { name: '第二页当前回答' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: '证据与历史差异' })).toHaveCount(0);
  releaseFirstRelation?.();
  await page.waitForTimeout(500);
  await expect(page.getByText('上一页延迟来源')).toHaveCount(0);

  await page.getByRole('button', { name: '生成证据包' }).click();
  await expect.poll(() => packageRequested).toBe(true);
  await page
    .getByRole('navigation', { name: '证据中心分页' })
    .getByRole('button', { name: '下一页' })
    .click();
  await expect(page).toHaveURL(/evidence_page=2/);
  await expect.poll(() => secondAssetRequested).toBe(true);
  await expect(page.getByText('正在加载', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('cell', { name: 'evd_evidence_page_01' })).toHaveCount(0);
  releaseSecondAsset?.();
  await expect(page.getByRole('cell', { name: 'evd_evidence_page_02' })).toBeVisible();
  releasePackage?.();
  await page.waitForTimeout(500);
  await expect(page.getByText('真实证据包已生成并冻结清单')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '生成证据包' })).toBeEnabled();
  expect(packageBodies).toHaveLength(1);
  expect(packageBodies[0]?.evidence_pub_ids).toEqual(['evd_evidence_page_01']);
  const surfaces = await page.evaluate(() =>
    JSON.stringify({
      dom: document.documentElement.outerHTML,
      url: location.href,
      localStorage: { ...localStorage },
      sessionStorage: { ...sessionStorage },
    }),
  );
  expect(surfaces).not.toMatch(
    /stale-answer-page-relation-canary|stale-evidence-package-receipt-canary|Bearer /i,
  );
  await expectAccessible(page);
});
