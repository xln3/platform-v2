// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { OutboundRiskWorkspace } from './features/services/service-outbound-risk/OutboundRiskWorkspace';
import { operationsNav } from './shell';

afterEach(cleanup);

describe('operations UVW information architecture', () => {
  it('places every collection lifecycle entry under collection and splits services 2 and 3', () => {
    const byId = new Map(operationsNav.map((item) => [item.id, item]));

    for (const id of ['execution', 'accounts', 'browsers', 'sessions', 'interventions', 'events']) {
      expect(byId.get(id)?.group).toBe('采集');
    }
    expect(byId.get('service-outbound-risk')?.group).toBe('五项服务生产');
    expect(byId.get('service-inbound-risk')?.group).toBe('五项服务生产');
    expect(byId.has('service-risk')).toBe(false);
    expect(operationsNav.some((item) => item.group === '系统运营')).toBe(false);
  });

  it('keeps service 2 scoped to owned content and directs external U risk to service 3', () => {
    render(
      <OutboundRiskWorkspace
        project={{
          pub_id: 'prj_fixture',
          name: 'Fixture Project',
          state: 'active',
          updated_at: '2026-08-20T00:00:00Z',
        }}
      />,
    );

    expect(screen.getAllByText(/己方归属证据/).length).toBeGreaterThan(0);
    expect(screen.getByText(/本页不读取互联网 U 页面作为“己方内容”/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '打开信源 SOP' })).toBeTruthy();
  });
});
