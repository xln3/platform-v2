// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest';
import {
  createPostingHandoff,
  loadPostingHandoff,
  selectedPostingTarget,
  type ComparisonPostingSelection,
} from './selection-handoff';

const selection: ComparisonPostingSelection = {
  key: 'news\u0000\u0000人民网',
  catalogType: 'news',
  catalogSha256: 'a'.repeat(64),
  mediaName: '人民网',
  mediaPlatform: '',
  provider: 'toumeiw',
  options: {
    prfabu: { providerMediaId: 'pr_1001', quotedPrice: 100 },
    toumeiw: { providerMediaId: 'tm_2001', quotedPrice: 80 },
  },
};

afterEach(() => window.sessionStorage.clear());

describe('posting selection handoff', () => {
  it('freezes the chosen supplier media ID and reloads it for the same actor', () => {
    const target = selectedPostingTarget(selection);
    expect(target).toMatchObject({
      provider: 'toumeiw',
      providerMediaId: 'tm_2001',
      quotedPrice: 80,
      catalogSha256: 'a'.repeat(64),
    });

    const created = createPostingHandoff({
      tenantId: 'tnt_alpha',
      actorId: 'usr_operator',
      selections: [selection],
      now: 1_000_000,
    });
    expect(created?.href).toMatch(/^\/platform\/operations\/posting\?handoff=/u);
    const loaded = loadPostingHandoff({
      id: created!.id,
      tenantId: 'tnt_alpha',
      actorId: 'usr_operator',
      now: 1_000_100,
    });
    expect(loaded.kind).toBe('ready');
    if (loaded.kind === 'ready') {
      expect(loaded.data.targets).toEqual([target]);
    }
  });

  it('binds an otherwise valid handoff to its tenant and actor', () => {
    const created = createPostingHandoff({
      tenantId: 'tnt_alpha',
      actorId: 'usr_owner',
      selections: [selection],
    })!;

    expect(
      loadPostingHandoff({
        id: created.id,
        tenantId: 'tnt_beta',
        actorId: 'usr_owner',
      }).kind,
    ).toBe('forbidden');
    expect(
      loadPostingHandoff({
        id: created.id,
        tenantId: 'tnt_alpha',
        actorId: 'usr_other',
      }).kind,
    ).toBe('forbidden');
  });

  it('expires old selections and keeps independent drafts in separate records', () => {
    const first = createPostingHandoff({
      tenantId: 'tnt_alpha',
      actorId: 'usr_owner',
      selections: [selection],
      now: 1_000,
    })!;
    const second = createPostingHandoff({
      tenantId: 'tnt_alpha',
      actorId: 'usr_owner',
      selections: [{ ...selection, provider: 'prfabu' }],
      now: 1_001,
    })!;
    expect(first.id).not.toBe(second.id);
    expect(
      loadPostingHandoff({
        id: first.id,
        tenantId: 'tnt_alpha',
        actorId: 'usr_owner',
        now: 1_000 + 2 * 60 * 60 * 1_000 + 1,
      }).kind,
    ).toBe('expired');
    const secondRead = loadPostingHandoff({
      id: second.id,
      tenantId: 'tnt_alpha',
      actorId: 'usr_owner',
      now: 1_002,
    });
    expect(secondRead.kind).toBe('ready');
  });

  it('rejects a handoff whose exact media identity is tampered in browser storage', () => {
    const created = createPostingHandoff({
      tenantId: 'tnt_alpha',
      actorId: 'usr_owner',
      selections: [selection],
    })!;
    const key = window.sessionStorage.key(0)!;
    const record = JSON.parse(window.sessionStorage.getItem(key)!);
    record.targets[0].providerMediaId = '../../wrong-target';
    window.sessionStorage.setItem(key, JSON.stringify(record));

    expect(
      loadPostingHandoff({
        id: created.id,
        tenantId: 'tnt_alpha',
        actorId: 'usr_owner',
      }).kind,
    ).toBe('invalid');
  });
});
