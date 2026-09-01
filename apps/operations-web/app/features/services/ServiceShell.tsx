import { useEffect, useState, type ReactNode } from 'react';
import { getHealth } from '@geo/api-client';
import { getValidatedIdentityHeaders } from '@geo/auth';
import { ProductShell, StatePanel, useOptionalExperienceContext } from '@geo/design-system';
import { liveOperationsRouteNav, operationsRouteNav } from '../../shell';
import { servicesApi, type Project, type SessionContext } from './api';
import './services.css';

type Props = {
  navId: string;
  title: string;
  description: string;
  blurb: string;
  allowAnalyst?: boolean;
  children: (session: SessionContext, project: Project) => ReactNode;
};

function readProjectParam(): string {
  if (typeof window === 'undefined') return '';
  return new URL(window.location.href).searchParams.get('project') ?? '';
}

export function ServiceShell({
  navId,
  title,
  description,
  blurb,
  allowAnalyst = false,
  children,
}: Props) {
  const experience = useOptionalExperienceContext();
  const headers = getValidatedIdentityHeaders();
  const role = experience?.roles.find(
    (candidate): candidate is 'operator' | 'analyst' | 'reviewer' | 'admin' =>
      candidate === 'operator' ||
      (allowAnalyst && candidate === 'analyst') ||
      candidate === 'reviewer' ||
      candidate === 'admin',
  );
  const session: SessionContext | null =
    experience && headers && role
      ? { tenantId: experience.tenantPubId, actorId: experience.userPubId, role, headers }
      : null;
  return (
    <ProductShell
      product="Operations Web"
      title={title}
      description={description}
      nav={experience?.source === 'live' ? liveOperationsRouteNav : operationsRouteNav}
      currentNavId={navId}
      probe={getHealth}
    >
      {() =>
        !session ? (
          <main className="services-page">
            <StatePanel state="forbidden" />
          </main>
        ) : (
          <ServiceWorkspace session={session} title={title} blurb={blurb}>
            {children}
          </ServiceWorkspace>
        )
      }
    </ProductShell>
  );
}

function ServiceWorkspace({
  session,
  title,
  blurb,
  children,
}: {
  session: SessionContext;
  title: string;
  blurb: string;
  children: (session: SessionContext, project: Project) => ReactNode;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(readProjectParam);
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>('loading');

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    servicesApi
      .projects(session)
      .then((page) => {
        if (cancelled) return;
        const items = page.data;
        setProjects(items);
        setProjectId((current) =>
          current && items.some((item) => item.pub_id === current)
            ? current
            : (items[0]?.pub_id ?? ''),
        );
        setState('ready');
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setState(
          error instanceof Error && error.message === 'permission_denied' ? 'forbidden' : 'failed',
        );
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  const project = projects.find((item) => item.pub_id === projectId) ?? null;

  return (
    <main className="services-page">
      <header className="service-heading">
        <div>
          <h1>{title}</h1>
          <p>{blurb}</p>
        </div>
        <label className="service-project-picker">
          项目
          <select
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            disabled={projects.length === 0}
          >
            {projects.length === 0 ? <option value="">暂无项目</option> : null}
            {projects.map((item) => (
              <option key={item.pub_id} value={item.pub_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </header>
      {state === 'loading' ? (
        <StatePanel state="loading" />
      ) : state === 'failed' || state === 'forbidden' ? (
        <StatePanel state={state} />
      ) : !project ? (
        <p className="empty">尚无项目。请先在开户向导中创建客户项目。</p>
      ) : (
        children(session, project)
      )}
    </main>
  );
}
