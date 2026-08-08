// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';

vi.mock('react-konva', () => ({
  Stage: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="konva-stage">{children}</div>
  ),
  Layer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Rect: () => <span />,
  Text: () => <span />,
}));
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: () => ({ promise: new Promise(() => undefined), destroy: async () => undefined }),
}));

import Shell, { projectLiveReportTarget, reportProjectionLimits } from './shell';
const renderShell = () =>
  render(
    <MemoryRouter>
      <Shell />
    </MemoryRouter>,
  );

describe('Report Studio', () => {
  beforeEach(() => {
    history.replaceState(null, '', '/platform/reports/');
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              status: 'ok',
              service: 'geo-platform-v2',
              version: 'contract-v1',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders the collapsible AI operations dock with a per-operation model drawer', async () => {
    const user = userEvent.setup();
    localStorage.removeItem('geo.ai.dock.expanded');
    localStorage.removeItem('geo.ai.model.report-draft');
    renderShell();
    // 默认展开：列出 AI 操作与当前模型徽章；fixture 环境下模型下拉禁用
    expect(screen.getByLabelText('AI 操作面板')).toBeTruthy();
    expect(screen.getByText('AI 操作')).toBeTruthy();
    expect(screen.getByText('默认模型')).toBeTruthy();
    const select = screen.getByLabelText('AI 起草报告章节模型选择') as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    // 折叠后清单消失、状态记忆到 localStorage；再展开恢复
    await user.click(screen.getByRole('button', { name: '收起 AI 面板' }));
    expect(screen.queryByText('AI 操作')).toBeNull();
    expect(localStorage.getItem('geo.ai.dock.expanded')).toBe('0');
    await user.click(screen.getByRole('button', { name: '展开 AI 面板' }));
    expect(screen.getByText('AI 操作')).toBeTruthy();
  });

  it('freezes facts, traces a KPI and distinguishes AI from human edits', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '冻结事实并创建 v0.8' }));
    expect(screen.getAllByText('事实已冻结').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'KPI Trace' }));
    await user.click(screen.getByRole('button', { name: /Top 3 占比/ }));
    expect(screen.getByText('ans_03 · rank 1')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '章节编辑' }));
    await user.click(screen.getByRole('button', { name: /模型差异分析/ }));
    expect(screen.getByText('AI 生成 · 未确认')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '接受草稿并标记人工确认' }));
    expect(screen.getByText('人工内容')).toBeTruthy();
  });

  it('binds accessible Konva evidence and pages through the report preview', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '证据编排' }));
    expect(screen.getByRole('img', { name: /品牌提及锚点/ })).toBeTruthy();
    expect(screen.getByTestId('konva-stage')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '绑定到“执行摘要”' }));
    expect(screen.getByText('绑定成功')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: 'PDF 预览' }));
    expect(screen.getByLabelText('报告预览第 1 页')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByLabelText('报告预览第 2 页')).toBeTruthy();
  });

  it('enforces review gates before publication and records outcomes', async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole('button', { name: '冻结事实并创建 v0.8' }));
    await user.click(screen.getByRole('button', { name: /审核发布/ }));
    await user.click(screen.getByRole('button', { name: '提交审核' }));
    expect((screen.getByRole('button', { name: '批准发布' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    await user.click(screen.getByRole('button', { name: '纳入本次审核' }));
    await user.click(screen.getByRole('button', { name: '批准发布' }));
    await user.click(screen.getByRole('button', { name: '发布 v1.0' }));
    expect(screen.getByText('在线版已生成；客户可见性以独立 delivery 记录为准。')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '效果复盘' }));
    await user.click(screen.getByRole('button', { name: '开始执行' }));
    await user.click(screen.getByRole('button', { name: '记录复测效果' }));
    expect(screen.getByText('+6.2pp')).toBeTruthy();
  });

  it('bounds live report detail, accepts real rptv ids and fails closed on invalid governance data', () => {
    const currentVersion = {
      pub_id: 'rptv_unit_003',
      version_number: 3,
      status: 'review',
      frozen_facts: Array.from({ length: 501 }, (_, index) => ({
        pub_id: `rptf_unit_003_${index}`,
        report_version_pub_id: 'rptv_unit_003',
        ordinal: index,
        payload: { metric: `metric_${index}`, value: index },
        payload_hash: 'a'.repeat(64),
        created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
      })),
      components: Array.from({ length: 101 }, (_, index) => ({
        pub_id: `rptc_unit_003_${index}`,
        report_version_pub_id: 'rptv_unit_003',
        component_type: 'section',
        ordinal: index,
        source: index % 2 ? 'human' : 'ai',
        payload: {
          title: `章节 ${index}`,
          body: `正文 ${index}`,
          evidence_pub_ids: Array.from(
            { length: 101 },
            (__, evidenceIndex) => `evd_${index}_${evidenceIndex}`,
          ),
        },
        created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
      })),
      artifacts: [
        {
          pub_id: 'rpta_unit_003_html',
          report_version_pub_id: 'rptv_unit_003',
          format: 'html',
          evidence_pub_id: 'evd_unit_003_html',
          mime_type: 'text/html',
          byte_size: 20,
          sha256: 'c'.repeat(64),
          created_at: '2026-07-25T00:20:00Z',
        },
        {
          pub_id: 'rpta_unit_003_pdf',
          report_version_pub_id: 'rptv_unit_003',
          format: 'pdf',
          evidence_pub_id: 'evd_unit_003_pdf',
          mime_type: 'application/pdf',
          byte_size: 30,
          sha256: 'd'.repeat(64),
          created_at: '2026-07-25T00:21:00Z',
        },
      ],
      evidence_bindings: Array.from({ length: 501 }, (_, index) => ({
        pub_id: `rptev_unit_003_${index}`,
        report_version_pub_id: 'rptv_unit_003',
        evidence_pub_id: `evd_binding_${index}`,
        byte_size: index,
        anchor_count: index,
        kind: 'answer_screenshot',
        purpose: 'frozen_fact_or_component',
        access_class: 'customer_private',
        mime_type: 'image/png',
        sha256: 'b'.repeat(64),
        capture_time: new Date(Date.UTC(2026, 6, 24, 0, 0, index)).toISOString(),
        created_at: new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
      })),
      comments: Array.from({ length: 501 }, (_, index) => ({
        pub_id: `cmt_${index}`,
        report_version_pub_id: 'rptv_unit_003',
        parent_pub_id: null,
        author_pub_id: 'usr_unit_reviewer',
        body: index === 500 ? 'Cookie=report-comment-canary' : `评论 ${index}`,
        resolved_at: null,
        created_at:
          index === 499 ? '1' : new Date(Date.UTC(2026, 6, 25, 0, 0, index)).toISOString(),
      })),
    };
    const projected = projectLiveReportTarget({
      pub_id: 'rpt_unit_safe',
      state: 'approved',
      versions: [
        {
          pub_id: 'rptv_unit_001',
          version_number: 1,
          status: 'review',
          components: [],
        },
        {
          pub_id: 'rptv_unit_002',
          version_number: 2,
          status: 'review',
          components: [],
        },
        currentVersion,
      ],
      optimization_actions: Array.from({ length: 201 }, (_, index) => ({
        pub_id: `act_${index}`,
        description: `优化行动 ${index}`,
        owner_pub_id: null,
        state: 'done',
        baseline: { version_number: 2 },
        outcome: { completed: true },
        created_at: new Date(Date.UTC(2026, 6, 1, 0, 0, index)).toISOString(),
        updated_at: new Date(Date.UTC(2026, 6, 2, 0, 0, index)).toISOString(),
        effect_retests:
          index === 200
            ? Array.from({ length: 201 }, (__, retestIndex) => ({
                pub_id: `rts_200_${retestIndex}`,
                action_pub_id: 'act_200',
                measured_at: new Date(Date.UTC(2026, 7, 1, 0, 0, retestIndex)).toISOString(),
                result: { delta: retestIndex / 10 },
                recorded_by_pub_id: 'usr_unit_reviewer',
                created_at: new Date(Date.UTC(2026, 7, 2, 0, 0, retestIndex)).toISOString(),
              }))
            : [],
      })),
    } as never);

    expect(projected?.versionPubId).toBe('rptv_unit_003');
    expect(projected?.status).toBe('approved');
    expect(projected?.versions).toHaveLength(reportProjectionLimits.versions);
    expect(projected?.facts).toHaveLength(reportProjectionLimits.facts);
    expect(projected?.sections).toHaveLength(reportProjectionLimits.sections);
    expect(projected?.evidenceBindings).toHaveLength(reportProjectionLimits.evidenceBindings);
    expect(projected?.comments).toHaveLength(reportProjectionLimits.comments - 2);
    expect(projected?.actions).toHaveLength(reportProjectionLimits.actions);
    expect(projected?.actions.at(-1)?.retests).toHaveLength(reportProjectionLimits.effectRetests);
    expect(projected?.projectionNotices.versions?.total).toBe(3);
    expect(projected?.projectionNotices.sections?.total).toBe(101);
    expect(projected?.projectionNotices.actions?.total).toBe(201);
    expect(projected?.invalidProjection).toContain('comments');
    expect(JSON.stringify(projected)).not.toContain('report-comment-canary');

    const danglingSectionEvidence = projectLiveReportTarget({
      pub_id: 'rpt_unit_section_evidence',
      state: 'review',
      versions: [
        {
          pub_id: 'rptv_unit_section_evidence',
          version_number: 1,
          status: 'review',
          components: [
            {
              pub_id: 'rptc_unit_section_evidence',
              report_version_pub_id: 'rptv_unit_section_evidence',
              component_type: 'section',
              ordinal: 0,
              source: 'human',
              payload: {
                title: '严格证据绑定章节',
                body: '仅保留当前版本 evidence binding 能证明的证据引用。',
                evidence_pub_ids: ['evd_unit_linked_safe', 'evd_unit_dangling_canary'],
              },
              created_at: '2026-07-25T00:00:00Z',
            },
          ],
          evidence_bindings: [
            {
              pub_id: 'rptev_unit_linked_safe',
              report_version_pub_id: 'rptv_unit_section_evidence',
              evidence_pub_id: 'evd_unit_linked_safe',
              byte_size: 64,
              anchor_count: 1,
              kind: 'answer_screenshot',
              purpose: 'frozen_fact_or_component',
              access_class: 'customer_private',
              mime_type: 'image/png',
              sha256: 'a'.repeat(64),
              capture_time: '2026-07-25T00:00:00Z',
              created_at: '2026-07-25T00:01:00Z',
            },
          ],
        },
      ],
      optimization_actions: [],
    } as never);
    expect(danglingSectionEvidence?.sections[0]?.evidencePubIds).toEqual(['evd_unit_linked_safe']);
    expect(danglingSectionEvidence?.versions[0]?.sections[0]?.evidencePubIds).toEqual([
      'evd_unit_linked_safe',
    ]);
    expect(danglingSectionEvidence?.invalidProjection).toContain('sectionEvidenceIds');
    expect(JSON.stringify(danglingSectionEvidence)).not.toContain('evd_unit_dangling_canary');

    const crossVersionArtifact = projectLiveReportTarget({
      pub_id: 'rpt_unit_cross_artifact',
      state: 'approved',
      versions: [
        {
          ...currentVersion,
          components: [],
          artifacts: [
            {
              ...currentVersion.artifacts[0],
              report_version_pub_id: 'rptv_unit_other',
            },
          ],
          evidence_bindings: [
            {
              ...currentVersion.evidence_bindings[0],
              report_version_pub_id: 'rptv_unit_other',
            },
          ],
          comments: [
            {
              ...currentVersion.comments[0],
              report_version_pub_id: 'rptv_unit_other',
            },
          ],
          frozen_facts: [
            {
              ...currentVersion.frozen_facts[0],
              report_version_pub_id: 'rptv_unit_other',
            },
          ],
          reviews: [
            {
              pub_id: 'rvw_unit_cross_version',
              report_version_pub_id: 'rptv_unit_other',
              reviewer_pub_id: 'usr_unit_reviewer',
              decision: 'approved',
              rationale: 'Bearer unit-review-canary',
              created_at: '2026-07-25T00:32:00Z',
            },
          ],
          events: [
            {
              pub_id: 'evt_unit_cross_version',
              report_version_pub_id: 'rptv_unit_other',
              event_type: 'published',
              actor_pub_id: 'usr_unit_reviewer',
              data: { token: 'Bearer unit-event-canary' },
              created_at: '2026-07-25T00:33:00Z',
            },
          ],
        },
      ],
      optimization_actions: [],
    } as never);
    expect(crossVersionArtifact?.artifacts).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('artifacts');
    expect(crossVersionArtifact?.evidenceBindings).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('evidenceBindings');
    expect(crossVersionArtifact?.comments).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('comments');
    expect(crossVersionArtifact?.facts).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('facts');
    expect(crossVersionArtifact?.reviews).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('reviews');
    expect(crossVersionArtifact?.events).toEqual([]);
    expect(crossVersionArtifact?.invalidProjection).toContain('events');
    expect(JSON.stringify(crossVersionArtifact)).not.toMatch(
      /unit-(?:review|event)-canary|Bearer /i,
    );

    const crossActionRetest = projectLiveReportTarget({
      pub_id: 'rpt_unit_cross_action',
      state: 'approved',
      versions: [{ ...currentVersion, components: [] }],
      optimization_actions: [
        {
          pub_id: 'act_unit_parent',
          description: '父行动',
          owner_pub_id: null,
          state: 'done',
          baseline: { version_number: 3 },
          outcome: { delta: 1 },
          created_at: '2026-07-25T00:00:00Z',
          updated_at: '2026-07-25T01:00:00Z',
          effect_retests: [
            {
              pub_id: 'rts_unit_cross_action',
              action_pub_id: 'act_unit_other',
              measured_at: '2026-07-25T00:30:00Z',
              result: { delta: 1, token: 'Bearer unit-retest-canary' },
              recorded_by_pub_id: 'usr_unit_reviewer',
              created_at: '2026-07-25T00:31:00Z',
            },
          ],
        },
      ],
    } as never);
    expect(crossActionRetest?.actions[0]?.retests).toEqual([]);
    expect(crossActionRetest?.invalidProjection).toContain('effectRetests');
    expect(JSON.stringify(crossActionRetest)).not.toContain('unit-retest-canary');

    const outOfOrderVersions = projectLiveReportTarget({
      pub_id: 'rpt_unit_version_order',
      state: 'review',
      versions: [
        {
          pub_id: 'rptv_unit_order_002',
          version_number: 2,
          status: 'review',
          components: [],
        },
        {
          pub_id: 'rptv_unit_order_001',
          version_number: 1,
          status: 'review',
          components: [],
        },
      ],
      optimization_actions: [],
    } as never);
    expect(outOfOrderVersions?.invalidProjection).toContain('versions');
  });

  it('binds a live report detail to the requested report and current project', () => {
    const detail = {
      pub_id: 'rpt_unit_bound',
      project_pub_id: 'prj_unit_bound',
      title: '严格绑定报告',
      state: 'review',
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T01:00:00Z',
      versions: [
        {
          pub_id: 'rptv_unit_bound',
          version_number: 1,
          status: 'review',
          components: [],
        },
      ],
      optimization_actions: [],
    };
    expect(
      projectLiveReportTarget(detail as never, 'rpt_unit_bound', 'prj_unit_bound'),
    ).toMatchObject({
      reportPubId: 'rpt_unit_bound',
      versionPubId: 'rptv_unit_bound',
    });
    expect(projectLiveReportTarget(detail as never, 'rpt_other', 'prj_unit_bound')).toBeNull();
    expect(projectLiveReportTarget(detail as never, 'rpt_unit_bound', 'prj_other')).toBeNull();
    expect(
      projectLiveReportTarget(
        { ...detail, updated_at: '2026-07-24T23:59:59Z' } as never,
        'rpt_unit_bound',
        'prj_unit_bound',
      ),
    ).toBeNull();
  });
});
