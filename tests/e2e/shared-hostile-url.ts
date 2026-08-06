import { expect, test } from './runtime-fixture';

export function verifyHostileUrlBoundary({
  product,
  path,
  role,
  heading,
  defaultSection,
}: {
  product: string;
  path: string;
  role: 'customer' | 'operator' | 'analyst' | 'reviewer';
  heading: string;
  defaultSection: string;
}) {
  test(`${product} removes hostile long URL values before rendering business content`, async ({
    page,
  }) => {
    await page.addInitScript(
      ({ productRole }) => {
        window.name = 'Bearer bootstrap-window-name-canary';
        localStorage.setItem('geo.session.tenant', 'tnt_hostile_url');
        localStorage.setItem('geo.session.actor', `${productRole}-hostile-url`);
        localStorage.setItem('geo.session.role', productRole);
        localStorage.setItem('geo.ａｃｃｅｓｓ＿ｔｏｋｅｎ', 'opaque-fullwidth-storage-key-canary');
        localStorage.setItem('geo.preference.theme', 'dark');
        sessionStorage.setItem('geo.to\u200bken', 'opaque-zero-width-storage-key-canary');
        sessionStorage.setItem(
          'geo.legacy.profile',
          String.raw`profile_dir=C:\Users\runner\Profile 1`,
        );
        sessionStorage.setItem('geo.preference.panel', 'expanded');
      },
      { productRole: role },
    );
    await page.route('**/api/v2/identity/session', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tenant_pub_id: 'tnt_hostile_url',
          user_pub_id: `usr_${role}_hostile_url`,
          role,
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
              pub_id: 'prj_hostile_url',
              tenant_pub_id: 'tnt_hostile_url',
              name: 'URL 边界项目',
              state: 'active',
              created_at: '2026-07-24T00:00:00Z',
              updated_at: '2026-07-24T00:00:00Z',
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
        body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v1' }),
      }),
    );
    await page.route('**/api/v2/analytics/overview**', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
    );
    for (const endpoint of ['**/api/v2/reports?**', '**/api/v2/intelligence/investigations?**']) {
      await page.route(endpoint, (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ data: [], page: { next_cursor: null, has_more: false } }),
        }),
      );
    }
    const hostileSection = `${'超长路由'.repeat(100)} Bearer dlp-shared-url-canary`;
    const hostileFragment = encodeURIComponent(
      encodeURIComponent('access_token=Bearer fragment-url-canary&otp=824911'),
    );
    const normalizedSecretKeyParameters = [
      `${encodeURIComponent('ａｃｃｅｓｓ＿ｔｏｋｅｎ')}=fullwidth-key-url-canary`,
      `${encodeURIComponent('to\u200bken')}=zero-width-key-url-canary`,
      `${encodeURIComponent(encodeURIComponent('profile_path'))}=encoded-key-url-canary`,
    ].join('&');
    const started = Date.now();
    await page.goto(
      `${path}?section=${encodeURIComponent(hostileSection)}&access_token=token-dlp-canary&${normalizedSecretKeyParameters}&safe_note=retained&long_safe=${'x'.repeat(2_000)}#${hostileFragment}`,
    );
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: defaultSection, exact: true })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect
      .poll(() => page.url())
      .not.toMatch(
        /dlp-shared-url-canary|access_token|token-dlp-canary|fragment-url-canary|824911|fullwidth-key-url-canary|zero-width-key-url-canary|encoded-key-url-canary|long_safe/,
      );
    expect(new URL(page.url()).hash).toBe('');
    expect(page.url()).toContain('safe_note=retained');
    expect(page.url().length).toBeLessThan(300);
    expect(Date.now() - started).toBeLessThan(10_000);
    const surfaces = await page.evaluate(() =>
      JSON.stringify({
        url: location.href,
        text: document.body.textContent,
        windowName: window.name,
        localStorage: { ...localStorage },
        sessionStorage: { ...sessionStorage },
      }),
    );
    expect(surfaces).not.toMatch(
      /dlp-shared-url-canary|token-dlp-canary|fragment-url-canary|824911|fullwidth-key-url-canary|zero-width-key-url-canary|encoded-key-url-canary|bootstrap-window-name-canary|fullwidth-storage-key-canary|zero-width-storage-key-canary|profile_dir|Profile 1|long_safe/,
    );
    expect(JSON.parse(surfaces).windowName).toBe('');
    expect(JSON.parse(surfaces).localStorage).toMatchObject({
      'geo.preference.theme': 'dark',
    });
    expect(JSON.parse(surfaces).sessionStorage).toMatchObject({
      'geo.preference.panel': 'expanded',
    });
    const immediateStorageSurfaces = await page.evaluate(() => {
      window.name = 'Cookie=session-window-name-write-canary';
      localStorage.setItem('geo.access_token', 'post-bootstrap-storage-key-canary');
      sessionStorage.setItem('geo.legacy.note', 'OTP 824911');
      localStorage.setItem('geo.preference.write-time', 'retained');
      return JSON.stringify({
        windowName: window.name,
        localStorage: { ...localStorage },
        sessionStorage: { ...sessionStorage },
      });
    });
    expect(immediateStorageSurfaces).not.toMatch(
      /access|token|window-name-write-canary|post-bootstrap-storage-key-canary|824911/i,
    );
    expect(JSON.parse(immediateStorageSurfaces).windowName).toBe('');
    expect(JSON.parse(immediateStorageSurfaces).localStorage).toMatchObject({
      'geo.preference.write-time': 'retained',
    });

    const immediateHistorySurfaces = await page.evaluate(
      ({ applicationPath, section }) => {
        const safeHistoryUrl = new URL(applicationPath, location.origin);
        safeHistoryUrl.searchParams.set('section', section);
        const hostileHistoryUrl = new URL(safeHistoryUrl);
        hostileHistoryUrl.pathname = `${safeHistoryUrl.pathname.replace(/\/?$/u, '/')}access%255Ftoken`;
        hostileHistoryUrl.searchParams.set('section', section);
        hostileHistoryUrl.searchParams.set('access_token', 'history-url-canary');
        hostileHistoryUrl.hash = '#profile%255Fpath';
        history.pushState(
          {
            navigationIndex: 10,
            nested: { safe: 'retained', note: 'OTP 824911' },
            ａｃｃｅｓｓ＿ｔｏｋｅｎ: 'history-state-key-canary',
            profilePath: '/secret/browser/profile/history-state-canary',
          },
          '',
          hostileHistoryUrl,
        );
        const immediateSurfaces = JSON.stringify({
          url: location.href,
          historyState: history.state,
        });
        history.pushState({ navigationIndex: 11 }, '', safeHistoryUrl);
        return immediateSurfaces;
      },
      { applicationPath: path, section: defaultSection },
    );
    expect(immediateHistorySurfaces).not.toMatch(
      /access|token|history-url-canary|824911|history-state-key-canary|profile|history-state-canary/i,
    );
    expect(JSON.parse(immediateHistorySurfaces).historyState).toEqual({
      navigationIndex: 10,
      nested: { safe: 'retained' },
    });
    await page.goBack();
    await expect
      .poll(() =>
        page.evaluate(() =>
          JSON.stringify({
            url: location.href,
            historyState: history.state,
          }),
        ),
      )
      .not.toMatch(
        /access|token|history-url-canary|824911|history-state-key-canary|profile|history-state-canary/i,
      );
    expect(new URL(page.url()).pathname).toBe(new URL(path, page.url()).pathname);
    expect(new URL(page.url()).hash).toBe('');
    expect(await page.evaluate(() => history.state)).toEqual({
      navigationIndex: 10,
      nested: { safe: 'retained' },
    });
  });
}
