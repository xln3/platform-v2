import { useCallback, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router';
import type { IdentitySessionHeaders, ProviderAccountStatus } from '@geo/api-client';
import { PostingComposer } from './PostingComposer';
import { ProviderAccountManager } from './ProviderAccountManager';
import { loadPostingHandoff } from './selection-handoff';
import '../media-prices/media-prices.css';

type Session = {
  tenantId: string;
  actorId: string;
  role: 'operator' | 'reviewer' | 'admin';
  headers: IdentitySessionHeaders;
};

export function PostingWorkspace({ session }: { session: Session }) {
  const location = useLocation();
  const [providerAccounts, setProviderAccounts] = useState<ProviderAccountStatus[]>([]);
  const onAccountsChange = useCallback((accounts: ProviderAccountStatus[]) => {
    setProviderAccounts(accounts);
  }, []);
  const handoff = useMemo(() => {
    const id = new URLSearchParams(location.search).get('handoff');
    if (!id) return { kind: 'missing' as const };
    return loadPostingHandoff({ id, tenantId: session.tenantId, actorId: session.actorId });
  }, [location.search, session.actorId, session.tenantId]);
  const selections = handoff.kind === 'ready' ? handoff.data.targets : [];

  return (
    <main className="media-prices posting-workspace" aria-label="发帖工作台">
      <div className="posting-channel-tabs" role="tablist" aria-label="发帖渠道">
        <button type="button" role="tab" aria-selected="true" className="active">
          媒体发稿
        </button>
        <button type="button" role="tab" aria-selected="false" disabled>
          抖音图文
          <small>即将接入</small>
        </button>
      </div>

      <section className="posting-handoff" aria-labelledby="posting-handoff-title">
        <div>
          <span className="eyebrow">verified catalog handoff</span>
          <h2 id="posting-handoff-title">本次投放目标</h2>
          <p>
            {selections.length > 0
              ? `已从比价台锁定 ${selections.length} 个媒体 / 采购平台组合。`
              : '尚未收到有效的比价台选单。'}
          </p>
        </div>
        <Link className="primary-link" to="/platform/operations/media-prices">
          {selections.length > 0 ? '返回比价台修改目标' : '前往比价台选择媒体'}
        </Link>
      </section>
      {handoff.kind === 'expired' ? (
        <div className="media-prices-notice warn" role="alert">
          选单已超过两小时，为避免使用旧报价，请回到比价台重新选择。
        </div>
      ) : null}
      {handoff.kind === 'forbidden' || handoff.kind === 'invalid' ? (
        <div className="media-prices-notice error" role="alert">
          选单无效或不属于当前账号，已阻止加载。
        </div>
      ) : null}

      <ProviderAccountManager session={session} onAccountsChange={onAccountsChange} />
      <PostingComposer
        session={session}
        selections={selections}
        providerAccounts={providerAccounts}
      />
      <footer className="security-note">
        比价页只负责选定目录目标；发帖页保存加密凭据并执行。创建批次时，服务端会再次核对数据快照、供应商媒体
        ID、名称和报价。
      </footer>
    </main>
  );
}
