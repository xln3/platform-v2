import { useRef, useState } from 'react';
import {
  generateQuotation,
  type GeneratedQuotationDocument,
  type IdentitySessionHeaders,
} from '@geo/api-client';
import { StatePanel, VerifiedBlobDownload } from '@geo/design-system';
import './quotation-generator.css';

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const MAX_XLSX_BYTES = 10 * 1024 * 1024;

export type QuotationSession = {
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export function chinaDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  const pick = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? '';
  return `${pick('year')}-${pick('month')}-${pick('day')}`;
}

export function quotationDownloadName(brandName: string, quoteDate: string): string {
  const brand = brandName
    .normalize('NFC')
    .replace(/\s+/gu, ' ')
    .trim()
    .replace(/[<>:"/\\|?*\x00-\x1f]/gu, '_')
    .replace(/^[ ._]+|[ ._]+$/gu, '');
  return `报价单-${brand || '客户'}-${quoteDate.replaceAll('-', '')}.docx`;
}

export function quotationInputError(
  brandName: string,
  targetWords: File | null,
  quoteDate: string,
): string | null {
  const brand = brandName.normalize('NFC').replace(/\s+/gu, ' ').trim();
  if (brand.length < 2 || brand.length > 80) return '品牌名称需为 2—80 个字符。';
  if (
    !quoteDate ||
    !/^(?:20\d{2}|2100)-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/u.test(quoteDate)
  ) {
    return '请选择有效的报价日期。';
  }
  if (!targetWords) return '请选择品牌方提供的目标词 XLSX。';
  if (!targetWords.name.toLowerCase().endsWith('.xlsx')) return '目标词文件必须为 .xlsx。';
  if (targetWords.size <= 0) return '目标词文件为空，请重新选择。';
  if (targetWords.size > MAX_XLSX_BYTES) return '目标词文件不能超过 10 MB。';
  if (
    targetWords.type &&
    targetWords.type !== XLSX_MIME &&
    targetWords.type !== 'application/octet-stream'
  ) {
    return '浏览器识别到的文件类型不是 XLSX。';
  }
  return null;
}

const failureNotice = {
  forbidden: '当前账号没有生成报价单的权限，请使用运营或管理员账号。',
  invalid: '输入未通过校验，请检查品牌名称、报价日期和目标词工作簿内容。',
  disabled: '模型服务配置当前不可用，请联系管理员检查生产环境模型凭证。',
  failed: '品牌化内容生成失败，未输出不完整文档；请稍后重试。',
  unavailable: '报价单服务暂不可用，或返回文档未通过完整性校验，请稍后重试。',
} as const;

export function QuotationGenerator({ session }: { session: QuotationSession | undefined }) {
  const [brandName, setBrandName] = useState('');
  const [quoteDate, setQuoteDate] = useState(chinaDate);
  const [targetWords, setTargetWords] = useState<File | null>(null);
  const [notice, setNotice] = useState<{ tone: 'info' | 'success' | 'error'; text: string } | null>(
    null,
  );
  const [receipt, setReceipt] = useState<GeneratedQuotationDocument | null>(null);
  const [inputRevision, setInputRevision] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canGenerate = session?.role === 'operator' || session?.role === 'admin';
  const inputError = quotationInputError(brandName, targetWords, quoteDate);
  const downloadName = quotationDownloadName(brandName, quoteDate);
  const resourceKey = `quotation-${inputRevision}`;

  if (!session) {
    return (
      <main className="quotation-page">
        <StatePanel state="forbidden" />
      </main>
    );
  }

  const load = async () => {
    if (!canGenerate || inputError || !targetWords) {
      setNotice({
        tone: 'error',
        text: !canGenerate ? failureNotice.forbidden : (inputError ?? '请补全报价单生成信息。'),
      });
      return { kind: 'unavailable' as const };
    }
    setReceipt(null);
    setNotice({
      tone: 'info',
      text: '正在解析目标词、联网核对品牌业务并生成 DOCX，请勿关闭页面…',
    });
    const result = await generateQuotation({ brandName, targetWords, quoteDate }, session.headers);
    if (result.kind !== 'ready') {
      setNotice({ tone: 'error', text: failureNotice[result.kind] });
      return {
        kind: result.kind === 'forbidden' ? ('forbidden' as const) : ('unavailable' as const),
      };
    }
    setReceipt(result.data);
    setNotice({
      tone: 'success',
      text: `报价单已生成并下载：${result.data.fileName}`,
    });
    return { kind: 'ready' as const, blob: result.data.blob };
  };

  return (
    <main className="quotation-page">
      <section className="quotation-hero">
        <div>
          <span className="quotation-eyebrow">GEO 商务工具</span>
          <h1>报价单生成</h1>
          <p>输入品牌名称并上传优化目标词，一次生成保持既定版式与商务措辞的 DOCX 报价单。</p>
        </div>
        <div className="quotation-scope" aria-label="生成范围">
          <strong>固定模板 + 品牌化附录</strong>
          <span>18 个原词样本 · 16 个机会词 · A/B/C 三类变体</span>
        </div>
      </section>

      <section className="quotation-card" aria-labelledby="quotation-form-title">
        <div className="quotation-section-title">
          <div>
            <span>一步生成</span>
            <h2 id="quotation-form-title">填写报价信息</h2>
          </div>
          <span className="quotation-security">原始目标词不写入日志</span>
        </div>

        <div className="quotation-form-grid">
          <label>
            品牌名称
            <input
              value={brandName}
              maxLength={80}
              autoComplete="organization"
              placeholder="例如：盛邦安全"
              onChange={(event) => {
                setBrandName(event.target.value);
                setInputRevision((value) => value + 1);
                setReceipt(null);
                setNotice(null);
              }}
            />
          </label>
          <label>
            报价日期
            <input
              type="date"
              min="2020-01-01"
              max="2100-12-31"
              value={quoteDate}
              onChange={(event) => {
                setQuoteDate(event.target.value);
                setInputRevision((value) => value + 1);
                setReceipt(null);
                setNotice(null);
              }}
            />
          </label>
          <label className="quotation-file-field">
            优化目标词 XLSX
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => {
                setTargetWords(event.target.files?.[0] ?? null);
                setInputRevision((value) => value + 1);
                setReceipt(null);
                setNotice(null);
              }}
            />
            <small>
              支持示例中的分组目标词结构，也支持“分类 + 目标词”或单列目标词；最大 10 MB。
            </small>
          </label>
        </div>

        {inputError ? <p className="quotation-validation">{inputError}</p> : null}
        {!canGenerate ? <p className="quotation-validation">{failureNotice.forbidden}</p> : null}

        <div className="quotation-actions">
          {canGenerate && !inputError ? (
            <VerifiedBlobDownload
              load={load}
              fileName={downloadName}
              resourceKey={resourceKey}
              label="生成并下载 DOCX"
              loadingLabel="生成中，请稍候…"
              failureLabel="本次未生成可下载文档，请查看上方提示。"
              successLabel="报价单已通过完整性校验并下载"
            />
          ) : (
            <button type="button" className="button button-primary" disabled>
              生成并下载 DOCX
            </button>
          )}
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setBrandName('');
              setQuoteDate(chinaDate());
              setTargetWords(null);
              setInputRevision((value) => value + 1);
              setNotice(null);
              setReceipt(null);
              if (fileInputRef.current) fileInputRef.current.value = '';
            }}
          >
            清空
          </button>
        </div>

        {notice ? (
          <div
            className={`quotation-notice ${notice.tone}`}
            role={notice.tone === 'error' ? 'alert' : 'status'}
          >
            {notice.text}
          </div>
        ) : null}

        {receipt ? (
          <dl className="quotation-receipt" aria-label="生成结果">
            <div>
              <dt>读取目标词</dt>
              <dd>{receipt.targetQueryCount}</dd>
            </div>
            <div>
              <dt>附录二样本</dt>
              <dd>{receipt.selectedQueryCount}</dd>
            </div>
            <div>
              <dt>新增机会词</dt>
              <dd>{receipt.opportunityCount}</dd>
            </div>
            <div>
              <dt>文档校验</dt>
              <dd title={receipt.sha256}>SHA-256 {receipt.sha256.slice(0, 12)}…</dd>
            </div>
          </dl>
        ) : null}
      </section>

      <aside className="quotation-honesty">
        <strong>数据口径说明</strong>
        <p>
          仅凭品牌名和目标词无法推导真实投放效果，文档中的实测指标保持“待实测”，不会自动编造推荐次数或提升比例。
        </p>
      </aside>
    </main>
  );
}
