# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-product-live.spec.ts >> customer product 404 fails closed without revealing whether analytics exist
- Location: tests/e2e/customer-product-live.spec.ts:898:5

# Error details

```
Error: expect(received).toBe(expected) // Object.is equality

Expected: 1
Received: 2
```

# Page snapshot

```yaml
- generic [ref=e2]:
  - link "跳到主要内容" [ref=e3] [cursor=pointer]:
    - /url: "#main-content"
  - complementary [ref=e4]:
    - generic "GEO Platform V2" [ref=e5]:
      - generic [ref=e6]: G
      - generic [ref=e7]:
        - text: GEO
        - text: Platform
    - generic [ref=e8]: Customer Web
    - navigation "Customer Web 主导航" [ref=e9]:
      - button "首页" [ref=e10] [cursor=pointer]:
        - generic [ref=e11]: 首页
      - button "资料" [ref=e12] [cursor=pointer]:
        - generic [ref=e13]: 资料
      - button "品牌产品" [ref=e14] [cursor=pointer]:
        - generic [ref=e15]: 品牌产品
      - button "问题目标" [ref=e16] [cursor=pointer]:
        - generic [ref=e17]: 问题目标
      - button "监测表现" [active] [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 监测表现
      - button "回答证据" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: 回答证据
      - button "报告" [ref=e22] [cursor=pointer]:
        - generic [ref=e23]: 报告
      - button "成员" [ref=e24] [cursor=pointer]:
        - generic [ref=e25]: 成员
      - button "平台账号" [ref=e26] [cursor=pointer]:
        - generic [ref=e27]: 平台账号
    - generic [ref=e28]: ok
  - generic [ref=e30]:
    - banner [ref=e31]:
      - button "租户 · bidden · 不可推断项目 ⌄" [ref=e32] [cursor=pointer]
      - generic [ref=e33]:
        - button "通知" [ref=e34] [cursor=pointer]: ◌
        - generic "用户 · bidden" [ref=e35]: 用
    - main [ref=e36]:
      - generic [ref=e37]:
        - generic [ref=e38]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e39]
          - paragraph [ref=e40]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e41]:
          - button "导出视图" [ref=e42] [cursor=pointer]
          - button "创建任务" [ref=e43] [cursor=pointer]
      - status [ref=e44]:
        - generic [ref=e46]:
          - strong [ref=e47]: 无权查看
          - paragraph [ref=e48]: 当前角色没有此资源权限，也不会披露资源是否存在。
```

# Test source

```ts
  875  |       contact_role: '品牌负责人',
  876  |       audience: '需要可验证企业知识服务的采购团队',
  877  |       public_statement: '真实客户企业提供可公开核验的知识服务。',
  878  |       truth_confirmed: true,
  879  |     },
  880  |   ]);
  881  |   expect(assetConfirmationBodies).toEqual([
  882  |     {
  883  |       brand_name: '确认品牌',
  884  |       website: 'https://confirmed.example',
  885  |       product_name: '确认产品',
  886  |       competitor_name: '确认竞品',
  887  |       prohibited_claim: '未经证实的行业第一',
  888  |       truth_confirmed: true,
  889  |     },
  890  |   ]);
  891  |   expect(profileCursors.filter(Boolean)).toEqual(['2']);
  892  |   expect(assetConfirmationCursors.filter(Boolean)).toEqual(['2']);
  893  |   expect(evidenceCursors).toEqual([null]);
  894  |   expect(consoleErrors).toEqual([]);
  895  |   expect(failedRequests).toEqual([]);
  896  | });
  897  | 
  898  | test('customer product 404 fails closed without revealing whether analytics exist', async ({
  899  |   page,
  900  | }) => {
  901  |   await page.addInitScript(() => {
  902  |     localStorage.setItem('geo.session.tenant', 'tnt_customer_forbidden');
  903  |     localStorage.setItem('geo.session.actor', 'customer-forbidden');
  904  |     localStorage.setItem('geo.session.role', 'customer');
  905  |   });
  906  |   await installSyntheticHttpResponses(page, [
  907  |     {
  908  |       id: 'customer-overview-forbidden',
  909  |       path: '/api/v2/analytics/overview',
  910  |       status: 404,
  911  |       body: {
  912  |         error: {
  913  |           code: 'not_found',
  914  |           message: 'Cookie=forbidden-customer-canary',
  915  |           request_id: 'req_safe',
  916  |         },
  917  |       },
  918  |     },
  919  |     {
  920  |       id: 'customer-delta-forbidden',
  921  |       path: '/api/v2/analytics/delta',
  922  |       status: 404,
  923  |     },
  924  |     {
  925  |       id: 'customer-competitors-forbidden',
  926  |       path: '/api/v2/analytics/competitors',
  927  |       status: 404,
  928  |     },
  929  |     {
  930  |       id: 'customer-breakdown-forbidden',
  931  |       path: '/api/v2/analytics/breakdown',
  932  |       status: 404,
  933  |     },
  934  |     {
  935  |       id: 'customer-answers-forbidden',
  936  |       path: '/api/v2/analytics/answers',
  937  |       status: 404,
  938  |     },
  939  |   ]);
  940  |   await page.route('**/api/v2/identity/session', (route) =>
  941  |     route.fulfill({
  942  |       status: 200,
  943  |       contentType: 'application/json',
  944  |       body: JSON.stringify({
  945  |         tenant_pub_id: 'tnt_customer_forbidden',
  946  |         user_pub_id: 'usr_customer_forbidden',
  947  |         role: 'customer',
  948  |         permissions: ['project:read'],
  949  |       }),
  950  |     }),
  951  |   );
  952  |   await page.route('**/api/v2/projects**', (route) =>
  953  |     route.fulfill({
  954  |       status: 200,
  955  |       contentType: 'application/json',
  956  |       body: JSON.stringify({
  957  |         data: [
  958  |           {
  959  |             pub_id: 'prj_customer_hidden',
  960  |             tenant_pub_id: 'tnt_customer_forbidden',
  961  |             name: '不可推断项目',
  962  |             state: 'active',
  963  |             created_at: '2026-07-25T00:00:00Z',
  964  |             updated_at: '2026-07-25T00:00:00Z',
  965  |           },
  966  |         ],
  967  |         page: { next_cursor: null, has_more: false },
  968  |       }),
  969  |     }),
  970  |   );
  971  |   await page.goto('/platform/customer/');
  972  |   await page.getByRole('button', { name: '监测表现' }).click();
  973  |   await expect(page.getByText('无权查看')).toBeVisible();
  974  |   await expect(page.getByText('Cookie=forbidden-customer-canary')).toHaveCount(0);
> 975  |   expect(await syntheticHttpResponseCount(page, 'customer-overview-forbidden')).toBe(1);
       |                                                                                 ^ Error: expect(received).toBe(expected) // Object.is equality
  976  |   expect(await syntheticHttpResponseCount(page, 'customer-answers-forbidden')).toBe(0);
  977  | });
  978  | 
  979  | test('validated tenant admin manages masked customer members through generated identity paths', async ({
  980  |   page,
  981  | }) => {
  982  |   const writes: Array<{ url: string; body: unknown }> = [];
  983  |   const consoleErrors: string[] = [];
  984  |   const failedRequests: string[] = [];
  985  |   page.on('console', (message) => {
  986  |     if (message.type() === 'error') consoleErrors.push('console-error');
  987  |   });
  988  |   page.on('requestfailed', (request) => failedRequests.push('request-failed'));
  989  |   await page.addInitScript(() => {
  990  |     localStorage.setItem('geo.session.tenant', 'tnt_member_live');
  991  |     localStorage.setItem('geo.session.actor', 'tenant-admin-live');
  992  |     localStorage.setItem('geo.session.role', 'admin');
  993  |   });
  994  |   await page.route('**/api/v2/identity/session', (route) =>
  995  |     route.fulfill({
  996  |       status: 200,
  997  |       contentType: 'application/json',
  998  |       body: JSON.stringify({
  999  |         tenant_pub_id: 'tnt_member_live',
  1000 |         user_pub_id: 'usr_admin_live',
  1001 |         role: 'admin',
  1002 |         permissions: ['*'],
  1003 |       }),
  1004 |     }),
  1005 |   );
  1006 |   await page.route('**/api/v2/projects**', (route) =>
  1007 |     route.fulfill({
  1008 |       status: 200,
  1009 |       contentType: 'application/json',
  1010 |       body: JSON.stringify({
  1011 |         data: [
  1012 |           {
  1013 |             pub_id: 'prj_member_live',
  1014 |             tenant_pub_id: 'tnt_member_live',
  1015 |             name: '成员联调项目',
  1016 |             state: 'active',
  1017 |             created_at: '2026-07-25T00:00:00Z',
  1018 |             updated_at: '2026-07-25T00:00:00Z',
  1019 |           },
  1020 |         ],
  1021 |         page: { next_cursor: null, has_more: false },
  1022 |       }),
  1023 |     }),
  1024 |   );
  1025 |   await page.route('**/api/v2/health', (route) =>
  1026 |     route.fulfill({
  1027 |       status: 200,
  1028 |       contentType: 'application/json',
  1029 |       body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
  1030 |     }),
  1031 |   );
  1032 |   await page.route('**/api/v2/identity/oidc-bindings', (route) =>
  1033 |     route.fulfill({
  1034 |       status: 200,
  1035 |       contentType: 'application/json',
  1036 |       body: JSON.stringify([
  1037 |         {
  1038 |           user_pub_id: 'usr_member_not_in_safe_projection',
  1039 |           active: true,
  1040 |           created_at: '2026-07-25T00:00:00Z',
  1041 |           revoked_at: null,
  1042 |           token: 'Bearer oidc-binding-extension-canary',
  1043 |         },
  1044 |       ]),
  1045 |     }),
  1046 |   );
  1047 |   await page.route('**/api/v2/identity/members**', async (route) => {
  1048 |     const request = route.request();
  1049 |     if (request.method() === 'GET') {
  1050 |       return route.fulfill({
  1051 |         status: 200,
  1052 |         contentType: 'application/json',
  1053 |         body: JSON.stringify([
  1054 |           {
  1055 |             pub_id: 'mbr_admin_live',
  1056 |             user_pub_id: 'usr_admin_live',
  1057 |             subject: 'admin@example.test',
  1058 |             display_name: '租户管理员',
  1059 |             role: 'admin',
  1060 |             state: 'active',
  1061 |             service_account: false,
  1062 |             cookie: 'SESSION=member-list-canary',
  1063 |             profile_path: '/secret/profile/member-list-canary',
  1064 |           },
  1065 |           {
  1066 |             pub_id: 'mbr_worker_hidden',
  1067 |             user_pub_id: 'usr_worker_hidden',
  1068 |             subject: 'service:worker',
  1069 |             display_name: '不应展示的服务账号',
  1070 |             role: 'worker',
  1071 |             state: 'active',
  1072 |             service_account: true,
  1073 |             token: 'Bearer member-worker-canary',
  1074 |           },
  1075 |         ]),
```