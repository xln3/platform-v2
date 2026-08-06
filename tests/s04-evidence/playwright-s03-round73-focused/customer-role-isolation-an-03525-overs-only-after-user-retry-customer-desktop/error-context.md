# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-role-isolation.spec.ts >> an explicit session failure stays fail-closed and recovers only after user retry
- Location: tests/e2e/customer-role-isolation.spec.ts:268:5

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('button', { name: /重试恢复项目/ })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('button', { name: /重试恢复项目/ })

```

```yaml
- main:
  - alert:
    - strong: 加载失败
    - paragraph: 局部请求失败，其他区域仍可使用。
    - button "重试此区域"
```

# Test source

```ts
  233 |         page: { next_cursor: null, has_more: false },
  234 |       }),
  235 |     }),
  236 |   );
  237 |   await page.route('**/api/v2/analytics/**', (route) => {
  238 |     businessReads += 1;
  239 |     return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  240 |   });
  241 | 
  242 |   await page.goto('/platform/customer/');
  243 |   await expect(page.getByRole('alert')).toContainText('加载失败');
  244 |   await expect(page.getByText('不应显示的 bootstrap 项目')).toHaveCount(0);
  245 |   expect(businessReads).toBe(0);
  246 |   await expectAccessible(page);
  247 |   const surfaces = await page.evaluate(() =>
  248 |     JSON.stringify({
  249 |       dom: document.documentElement.outerHTML,
  250 |       localStorage,
  251 |       sessionStorage,
  252 |       href: location.href,
  253 |     }),
  254 |   );
  255 |   for (const canary of [
  256 |     'bootstrap-browser-permission-canary',
  257 |     'bootstrap-browser-session-canary',
  258 |     'bootstrap-cross-tenant-canary',
  259 |     'bootstrap-duplicate-canary',
  260 |     '/secret/profile',
  261 |   ]) {
  262 |     expect(surfaces).not.toContain(canary);
  263 |   }
  264 |   expect(consoleErrors).toEqual([]);
  265 |   expect(failedRequests).toEqual([]);
  266 | });
  267 | 
  268 | test('an explicit session failure stays fail-closed and recovers only after user retry', async ({
  269 |   page,
  270 | }) => {
  271 |   let successfulIdentityRequests = 0;
  272 |   await page.addInitScript(() => {
  273 |     localStorage.setItem('geo.session.tenant', 'tnt_retry_safe');
  274 |     localStorage.setItem('geo.session.actor', 'subject-retry-safe');
  275 |     localStorage.setItem('geo.session.role', 'customer');
  276 |   });
  277 |   await installSyntheticHttpResponses(page, [
  278 |     {
  279 |       id: 'customer-session-transient',
  280 |       path: '/api/v2/identity/session',
  281 |       status: 503,
  282 |       body: {
  283 |         detail: 'OTP 394820 at /var/browser/profile/customer-a',
  284 |       },
  285 |       remaining: 1,
  286 |     },
  287 |   ]);
  288 |   await page.route('**/api/v2/identity/session', (route) => {
  289 |     successfulIdentityRequests += 1;
  290 |     return route.fulfill({
  291 |       status: 200,
  292 |       contentType: 'application/json',
  293 |       body: JSON.stringify({
  294 |         tenant_pub_id: 'tnt_retry_safe',
  295 |         user_pub_id: 'usr_retry_safe',
  296 |         role: 'customer',
  297 |         permissions: ['project:read'],
  298 |       }),
  299 |     });
  300 |   });
  301 |   await page.route('**/api/v2/projects**', (route) =>
  302 |     route.fulfill({
  303 |       status: 200,
  304 |       contentType: 'application/json',
  305 |       body: JSON.stringify({
  306 |         data: [
  307 |           {
  308 |             pub_id: 'prj_retry_safe',
  309 |             tenant_pub_id: 'tnt_retry_safe',
  310 |             name: '重试恢复项目',
  311 |             state: 'active',
  312 |             created_at: '2026-07-24T00:00:00Z',
  313 |             updated_at: '2026-07-24T00:00:00Z',
  314 |           },
  315 |         ],
  316 |         page: { next_cursor: null, has_more: false },
  317 |       }),
  318 |     }),
  319 |   );
  320 |   await page.route('**/api/v2/health', (route) =>
  321 |     route.fulfill({
  322 |       status: 200,
  323 |       contentType: 'application/json',
  324 |       body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v1' }),
  325 |     }),
  326 |   );
  327 | 
  328 |   await page.goto('/platform/customer/');
  329 |   await expect(page.getByRole('alert')).toContainText('加载失败');
  330 |   await expect(page.getByRole('alert')).toHaveCount(1);
  331 |   await expect(page.getByText('品牌增长项目')).toHaveCount(0);
  332 |   await page.getByRole('button', { name: '重试此区域' }).click();
> 333 |   await expect(page.getByRole('button', { name: /重试恢复项目/ })).toBeVisible();
      |                                                              ^ Error: expect(locator).toBeVisible() failed
  334 |   expect(await syntheticHttpResponseCount(page, 'customer-session-transient')).toBe(1);
  335 |   expect(successfulIdentityRequests).toBe(1);
  336 |   const surfaces = await page.evaluate(() => ({
  337 |     dom: document.documentElement.outerHTML,
  338 |     url: location.href,
  339 |     localStorage: JSON.stringify(localStorage),
  340 |     sessionStorage: JSON.stringify(sessionStorage),
  341 |   }));
  342 |   expect(JSON.stringify(surfaces)).not.toContain('394820');
  343 |   expect(JSON.stringify(surfaces)).not.toContain('/profile/customer-a');
  344 | });
  345 | 
```