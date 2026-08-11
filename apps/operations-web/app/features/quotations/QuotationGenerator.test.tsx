// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { generateQuotation } from '@geo/api-client';
import {
  QuotationGenerator,
  chinaDate,
  quotationDownloadName,
  quotationInputError,
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

beforeEach(() => {
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: vi.fn(() => 'blob:quotation'),
    revokeObjectURL: vi.fn(),
  });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('quotation generator', () => {
  it('derives the Shanghai date and safe delivery filename', () => {
    expect(chinaDate(new Date('2026-08-11T16:30:00Z'))).toBe('2026-08-12');
    expect(quotationDownloadName(' 盛邦/安全 ', '2026-08-12')).toBe(
      '报价单-盛邦_安全-20260812.docx',
    );
    expect(quotationInputError('盛邦安全', xlsx(), '2026-08-12')).toBeNull();
  });

  it('generates, verifies and downloads the quotation in one action', async () => {
    const blob = new Blob(['PK\u0003\u0004docx'], {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    vi.mocked(generateQuotation).mockResolvedValue({
      kind: 'ready',
      data: {
        blob,
        fileName: '报价单-盛邦安全-20260812.docx',
        sha256: 'a'.repeat(64),
        targetQueryCount: 64,
        selectedQueryCount: 18,
        opportunityCount: 16,
      },
    });
    render(<QuotationGenerator session={session} />);

    fireEvent.change(screen.getByLabelText('品牌名称'), { target: { value: '盛邦安全' } });
    fireEvent.change(screen.getByLabelText('报价日期'), { target: { value: '2026-08-12' } });
    fireEvent.change(screen.getByLabelText(/优化目标词 XLSX/u), {
      target: { files: [xlsx()] },
    });
    fireEvent.click(screen.getByRole('button', { name: '生成并下载 DOCX' }));

    await waitFor(() => expect(generateQuotation).toHaveBeenCalledTimes(1));
    expect(generateQuotation).toHaveBeenCalledWith(
      expect.objectContaining({
        brandName: '盛邦安全',
        quoteDate: '2026-08-12',
        targetWords: expect.any(File),
      }),
      session.headers,
    );
    await screen.findByText('报价单已生成并下载：报价单-盛邦安全-20260812.docx');
    expect(screen.getByText('64')).toBeTruthy();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });

  it('keeps invalid input and reviewer accounts fail-closed', () => {
    render(<QuotationGenerator session={{ ...session, role: 'reviewer' }} />);
    expect(screen.getByText('品牌名称需为 2—80 个字符。')).toBeTruthy();
    expect(screen.getByText('当前账号没有生成报价单的权限，请使用运营或管理员账号。')).toBeTruthy();
    expect(screen.getByRole('button', { name: '生成并下载 DOCX' }).hasAttribute('disabled')).toBe(
      true,
    );
  });
});
