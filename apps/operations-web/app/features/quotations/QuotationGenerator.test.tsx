// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { generateQuotation } from '@geo/api-client';
import {
  QuotationGenerator,
  chinaDate,
  quotationDownloadName,
  quotationInputError,
  yuanInputToCents,
} from './QuotationGenerator';

vi.mock('@geo/api-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@geo/api-client')>();
  return { ...original, generateQuotation: vi.fn() };
});

const session = {
  role: 'operator' as const,
  headers: {
    'X-Tenant-Id': 'tnt_test',
    'X-Actor-Id': 'usr_test',
    'X-Actor-Role': 'operator',
  },
};

const xlsx = () =>
  new File(['xlsx'], '硅基守望-盛邦安全目标词.xlsx', {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    lastModified: 1_786_454_400_000,
  });

const effectQuantities = {
  ranking_test: 1,
  outbound_disparagement_audit: 1,
  inbound_disparagement_audit: 1,
  official_site_audit: 1,
} as const;

const effectPrices = {
  ranking_test: '20000',
  outbound_disparagement_audit: '8000',
  inbound_disparagement_audit: '12000',
  official_site_audit: '10000',
} as const;

beforeEach(() => {
  const NativeURL = URL;
  class MockURL extends NativeURL {
    static createObjectURL = vi.fn(() => 'blob:quotation');
    static revokeObjectURL = vi.fn();
  }
  vi.stubGlobal('URL', MockURL);
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('quotation generator', () => {
  it('derives date, filename, integer cents and validates a priced package', () => {
    expect(chinaDate(new Date('2026-08-11T16:30:00Z'))).toBe('2026-08-12');
    expect(quotationDownloadName(' 盛邦/安全 ', '2026-08-12', 'geo_effect_assessment')).toBe(
      '非最终模板合规产物-报价单-盛邦_安全-GEO效果评测-20260812.docx',
    );
    expect(
      quotationDownloadName('盛邦安全', '2026-08-12', 'minimum_validation', 'query_appendix'),
    ).toBe('非最终模板合规产物-报价单-盛邦安全-GEO最小验证-查询附件-20260812.docx');
    expect(
      quotationDownloadName('盛邦安全', '2026-08-12', 'geo_effect_assessment', 'quote_table'),
    ).toBe('非最终模板合规产物-报价单-盛邦安全-GEO效果评测-报价单表格-20260812.docx');
    expect(yuanInputToCents('20000.50')).toBe(2_000_050);
    expect(yuanInputToCents('20.999')).toBeNull();
    expect(
      quotationInputError({
        brandName: '盛邦安全',
        websiteUrl: 'https://www.webray.com.cn',
        quoteDate: '2026-08-12',
        packageCode: 'geo_effect_assessment',
        artifactKind: 'complete',
        officialSiteInCitations: true,
        officialSiteCitationUrl: '',
        pricingStatus: 'priced',
        quantities: effectQuantities,
        prices: effectPrices,
        targetWords: xlsx(),
      }),
    ).toBeNull();
    expect(
      quotationInputError({
        brandName: '盛邦安全',
        websiteUrl: 'https://www.webray.com.cn',
        quoteDate: '2026-08-12',
        packageCode: 'custom',
        artifactKind: 'complete',
        officialSiteInCitations: true,
        officialSiteCitationUrl: 'https://www.webray.com.cn/cited-page',
        pricingStatus: 'priced',
        quantities: { ranking_test: 1 },
        prices: { ranking_test: '20000' },
        targetWords: null,
      }),
    ).toBe('官网引用证据只适用于最小验证套餐的“官网已命中”状态。');
    expect(
      quotationInputError({
        brandName: '盛邦安全',
        websiteUrl: '',
        quoteDate: '2026-08-12',
        packageCode: 'custom',
        artifactKind: 'query_appendix',
        officialSiteInCitations: false,
        officialSiteCitationUrl: '',
        pricingStatus: 'pending',
        quantities: { inbound_disparagement_audit: 1 },
        prices: {},
        targetWords: xlsx(),
      }),
    ).toBe('查询附件要求服务组合至少包含服务 1（测试）或服务 5（发帖提排名）。');
  });

  it('calculates each line and generates a base quotation without forcing XLSX', async () => {
    const blob = new Blob(['PK\u0003\u0004docx'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    vi.mocked(generateQuotation).mockResolvedValue({
      kind: 'ready',
      data: {
        blob,
        fileName: '非最终模板合规产物-报价单-盛邦安全-GEO效果评测-20260812.docx',
        sha256: 'a'.repeat(64),
        targetQueryCount: 0,
        selectedQueryCount: 0,
        opportunityCount: 0,
        packageCode: 'geo_effect_assessment',
        artifactKind: 'complete',
        serviceCount: 4,
        pricingStatus: 'priced',
        totalPriceCents: 5_000_000,
        maximumTotalPriceCents: 5_000_000,
        queryAppendixIncluded: false,
        templateCompliance: 'non-final-template',
      },
    });
    render(<QuotationGenerator session={session} />);

    fireEvent.change(screen.getByLabelText('客户/品牌名称'), { target: { value: '盛邦安全' } });
    fireEvent.change(screen.getByLabelText('报价日期'), { target: { value: '2026-08-12' } });
    fireEvent.change(screen.getByLabelText(/客户官网/u), {
      target: { value: 'https://www.webray.com.cn' },
    });
    fireEvent.change(screen.getByLabelText('测试单价（元/轮）'), {
      target: { value: '20000' },
    });
    fireEvent.change(screen.getByLabelText('找拉踩帖单价（元/项）'), {
      target: { value: '8000' },
    });
    fireEvent.change(screen.getByLabelText('找被拉踩帖单价（元/项）'), {
      target: { value: '12000' },
    });
    fireEvent.change(screen.getByLabelText('官网分析单价（元/项）'), {
      target: { value: '10000' },
    });

    expect(screen.getAllByText('¥50,000.00').length).toBeGreaterThan(0);
    expect(screen.getByRole('alert', { name: '模板合规警示' }).textContent).toContain(
      '非最终模板合规产物（仅供内部回归，禁止作为正式客户报价）',
    );
    fireEvent.click(screen.getByRole('button', { name: '生成并下载内部回归 DOCX' }));

    await waitFor(() => expect(generateQuotation).toHaveBeenCalledTimes(1));
    expect(generateQuotation).toHaveBeenCalledWith(
      expect.objectContaining({
        brandName: '盛邦安全',
        quoteDate: '2026-08-12',
        packageCode: 'geo_effect_assessment',
        serviceQuotes: [
          { serviceCode: 'ranking_test', quantity: 1, unitPriceCents: 2_000_000 },
          {
            serviceCode: 'outbound_disparagement_audit',
            quantity: 1,
            unitPriceCents: 800_000,
          },
          {
            serviceCode: 'inbound_disparagement_audit',
            quantity: 1,
            unitPriceCents: 1_200_000,
          },
          { serviceCode: 'official_site_audit', quantity: 1, unitPriceCents: 1_000_000 },
        ],
      }),
      session.headers,
    );
    expect(vi.mocked(generateQuotation).mock.calls[0]?.[0]).not.toHaveProperty('targetWords');
    await screen.findByText(/非最终模板合规产物已生成并下载/u);
    expect(screen.getByText('非最终模板合规产物·禁止发送客户')).toBeTruthy();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });

  it('expands 13451 as baseline and retest and makes service 4 conditional', () => {
    render(<QuotationGenerator session={session} />);
    fireEvent.click(screen.getByRole('button', { name: /未开展 GEO · 最小化验证/u }));
    fireEvent.change(screen.getByLabelText('客户/品牌名称'), {
      target: { value: '盛邦安全' },
    });
    fireEvent.change(screen.getByLabelText(/客户官网/u), {
      target: { value: 'https://www.webray.com.cn' },
    });
    fireEvent.change(screen.getByLabelText('测试单价（元/轮）'), {
      target: { value: '20000' },
    });
    fireEvent.change(screen.getByLabelText('找被拉踩帖单价（元/项）'), {
      target: { value: '12000' },
    });
    fireEvent.change(screen.getByLabelText('官网分析单价（元/项）'), {
      target: { value: '10000' },
    });
    fireEvent.change(screen.getByLabelText('发帖提排名单价（元/项）'), {
      target: { value: '30000' },
    });
    expect((screen.getByLabelText('待首轮测试确认（条件报价）') as HTMLInputElement).checked).toBe(
      true,
    );
    expect(screen.getByText('1（基线）→ 3 → 4（命中后）→ 5 → 1（复测）')).toBeTruthy();
    expect(screen.getAllByText('¥82,000.00').length).toBeGreaterThan(0);
    expect(screen.getByText(/官网命中后最高总价 ¥92,000.00/u)).toBeTruthy();

    fireEvent.click(screen.getByLabelText('是，计入官网分析'));
    fireEvent.change(screen.getByLabelText('官网引用证据 URL'), {
      target: { value: 'https://www.webray.com.cn/cited-page' },
    });
    expect((screen.getByLabelText('测试数量（轮）') as HTMLInputElement).value).toBe('2');
    expect(screen.getByText('2 轮分别用于发帖前基线和发帖后同口径复测。')).toBeTruthy();
    expect(screen.getByText('1（基线）→ 3 → 4 → 5 → 1（复测）')).toBeTruthy();
    expect(screen.getAllByText('¥92,000.00').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText('否，本次不计入'));
    expect(screen.queryByLabelText('官网分析单价（元/项）')).toBeNull();
    expect(screen.getByText('1（基线）→ 3 → 5 → 1（复测）')).toBeTruthy();
    expect(screen.getAllByText('¥82,000.00').length).toBeGreaterThan(0);
  });

  it('renders only preset services and keeps explicit custom add/remove controls', () => {
    render(<QuotationGenerator session={session} />);

    expect(screen.getByLabelText('测试单价（元/轮）')).toBeTruthy();
    expect(screen.getByLabelText('找拉踩帖单价（元/项）')).toBeTruthy();
    expect(screen.queryByLabelText('发帖提排名单价（元/项）')).toBeNull();
    expect(screen.queryByRole('group', { name: '自定义服务选择' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /未开展 GEO · 最小化验证/u }));
    expect(screen.queryByLabelText('找拉踩帖单价（元/项）')).toBeNull();
    expect(screen.getByLabelText('发帖提排名单价（元/项）')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /自定义组合/u }));
    expect(screen.getByRole('group', { name: '自定义服务选择' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '移除服务 3：找被拉踩帖' }));
    expect(screen.queryByLabelText('找被拉踩帖单价（元/项）')).toBeNull();
    expect(screen.getByRole('button', { name: '添加服务 3：找被拉踩帖' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '添加服务 2：找拉踩帖' }));
    expect(screen.getByLabelText('找拉踩帖单价（元/项）')).toBeTruthy();
    expect(screen.getByRole('button', { name: '移除服务 2：找拉踩帖' })).toBeTruthy();
  });

  it('selects all three DOCX artifacts and fails closed for a query appendix without XLSX', async () => {
    const blob = new Blob(['PK\u0003\u0004query'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    vi.mocked(generateQuotation).mockResolvedValue({
      kind: 'ready',
      data: {
        blob,
        fileName: '非最终模板合规产物-报价单-盛邦安全-GEO效果评测-查询附件-20260812.docx',
        sha256: 'c'.repeat(64),
        targetQueryCount: 1,
        selectedQueryCount: 1,
        opportunityCount: 0,
        packageCode: 'geo_effect_assessment',
        artifactKind: 'query_appendix',
        serviceCount: 4,
        pricingStatus: 'pending',
        totalPriceCents: null,
        maximumTotalPriceCents: null,
        queryAppendixIncluded: true,
        templateCompliance: 'non-final-template',
      },
    });
    render(<QuotationGenerator session={session} />);
    expect((screen.getByLabelText('完整报价单') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByLabelText('报价单表格')).toBeTruthy();
    expect(screen.getByLabelText('查询附件')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('报价单表格'));
    expect((screen.getByLabelText('报价单表格') as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText(/目标词 XLSX/u)).toBeNull();
    expect(screen.getByText('报价单表格不读取 XLSX')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('客户/品牌名称'), { target: { value: '盛邦安全' } });
    fireEvent.change(screen.getByLabelText(/客户官网/u), {
      target: { value: 'https://www.webray.com.cn' },
    });
    fireEvent.click(screen.getByLabelText('价格待确认样稿（不构成正式价格承诺）'));
    fireEvent.click(screen.getByLabelText('查询附件'));
    expect(
      screen.getByText('查询附件必须上传包含有效目标词的 XLSX；未上传时不会生成空附件。'),
    ).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '生成并下载内部回归 DOCX' }).hasAttribute('disabled'),
    ).toBe(true);

    fireEvent.change(screen.getByLabelText('目标词 XLSX（查询附件必填）'), {
      target: { files: [xlsx()] },
    });
    expect(
      screen.queryByText('查询附件必须上传包含有效目标词的 XLSX；未上传时不会生成空附件。'),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '生成并下载内部回归 DOCX' }));
    await waitFor(() => expect(generateQuotation).toHaveBeenCalledTimes(1));
    expect(generateQuotation).toHaveBeenCalledWith(
      expect.objectContaining({ artifactKind: 'query_appendix', targetWords: expect.any(File) }),
      session.headers,
    );
  });

  it('generates a price-pending sample without invented amounts', async () => {
    const blob = new Blob(['PK\u0003\u0004docx'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    vi.mocked(generateQuotation).mockResolvedValue({
      kind: 'ready',
      data: {
        blob,
        fileName: '非最终模板合规产物-报价单-盛邦安全-GEO效果评测-20260812.docx',
        sha256: 'b'.repeat(64),
        targetQueryCount: 0,
        selectedQueryCount: 0,
        opportunityCount: 0,
        packageCode: 'geo_effect_assessment',
        serviceCount: 4,
        pricingStatus: 'pending',
        totalPriceCents: null,
        maximumTotalPriceCents: null,
        queryAppendixIncluded: false,
        templateCompliance: 'non-final-template',
      },
    });
    render(<QuotationGenerator session={session} />);
    fireEvent.change(screen.getByLabelText('客户/品牌名称'), { target: { value: '盛邦安全' } });
    fireEvent.change(screen.getByLabelText(/客户官网/u), {
      target: { value: 'https://www.webray.com.cn' },
    });
    fireEvent.click(screen.getByLabelText('价格待确认样稿（不构成正式价格承诺）'));
    fireEvent.click(screen.getByRole('button', { name: '生成并下载内部回归 DOCX' }));
    await waitFor(() => expect(generateQuotation).toHaveBeenCalledTimes(1));
    expect(generateQuotation).toHaveBeenCalledWith(
      expect.objectContaining({
        pricingStatus: 'pending',
        serviceQuotes: expect.arrayContaining([
          expect.objectContaining({ serviceCode: 'ranking_test', unitPriceCents: null }),
        ]),
      }),
      session.headers,
    );
  });

  it('keeps invalid input and reviewer accounts fail-closed', () => {
    render(<QuotationGenerator session={{ ...session, role: 'reviewer' }} />);
    expect(screen.getByText('客户/品牌名称需为 2—80 个字符。')).toBeTruthy();
    expect(screen.getByText('当前账号没有生成报价单的权限，请使用运营或管理员账号。')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: '生成并下载内部回归 DOCX' }).hasAttribute('disabled'),
    ).toBe(true);
  });
});
