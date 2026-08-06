# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: customer-product-live.spec.ts >> oversized or unsafe identity governance lists stay explicit and governance-write locked
- Location: tests/e2e/customer-product-live.spec.ts:1270:5

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: getByRole('status')
Expected substring: "成员合同安全投影：服务返回 102 条，浏览器安全视图展示 98 条"
Error: strict mode violation: getByRole('status') resolved to 2 elements:
    1) <div role="status" aria-live="polite" class="sidebar-foot">…</div> aka getByText('ok')
    2) <div role="status" class="confirmation projection-limit-notice">…</div> aka getByText('受控展示上限成员合同安全投影：服务返回 102')

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for getByRole('status')

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
      - button "监测表现" [ref=e18] [cursor=pointer]:
        - generic [ref=e19]: 监测表现
      - button "回答证据" [ref=e20] [cursor=pointer]:
        - generic [ref=e21]: 回答证据
      - button "报告" [ref=e22] [cursor=pointer]:
        - generic [ref=e23]: 报告
      - button "成员" [ref=e24] [cursor=pointer]:
        - generic [ref=e25]: 成员
      - button "平台账号" [ref=e26] [cursor=pointer]:
        - generic [ref=e27]: 平台账号
    - status [ref=e28]: ok
  - generic [ref=e30]:
    - banner [ref=e31]:
      - button "租户 · ection · 成员安全投影项目 ⌄" [ref=e32] [cursor=pointer]
      - generic [ref=e33]:
        - button "通知" [ref=e34] [cursor=pointer]: ◌
        - generic "用户 · _admin" [ref=e35]: 用
    - main [ref=e36]:
      - generic [ref=e37]:
        - generic [ref=e38]:
          - text: Customer Web
          - heading "客户工作台" [level=1] [ref=e39]
          - paragraph [ref=e40]: 从项目资料到监测、证据、报告与平台账号授权的安全协作入口。
        - generic [ref=e41]:
          - button "导出视图" [ref=e42] [cursor=pointer]
          - button "创建任务" [ref=e43] [cursor=pointer]
      - generic [ref=e44]:
        - generic [ref=e45]:
          - heading "项目成员" [level=2] [ref=e46]
          - paragraph [ref=e47]: 客户管理员可以管理本租户成员；邮箱在列表和审计中保持掩码。
          - status [ref=e48]:
            - generic [ref=e49]: 受控展示上限
            - list [ref=e50]:
              - listitem [ref=e51]: 成员合同安全投影：服务返回 102 条，浏览器安全视图展示 98 条
              - listitem [ref=e52]: OIDC 绑定安全投影：服务返回 102 条，浏览器安全视图展示 98 条
            - generic [ref=e53]: 成员或绑定集合不完整时，邀请、角色、移除和 OIDC 写操作全部锁定；请先局部重试获取完整安全投影。
          - alert [ref=e54]:
            - generic [ref=e55]: 成员安全投影不完整
            - generic [ref=e56]: 治理写操作已锁定，当前安全子集不会被当作完整成员或绑定清单。
          - generic [ref=e57]:
            - article [ref=e58]:
              - generic [ref=e59]: 安
              - generic [ref=e60]:
                - strong [ref=e61]: 安全成员 0
                - generic [ref=e62]: m***@example.test
              - generic [ref=e63]: 客户管理员
              - button "管理 安全成员 0" [ref=e64] [cursor=pointer]: 管理
            - article [ref=e65]:
              - generic [ref=e66]: 安
              - generic [ref=e67]:
                - strong [ref=e68]: 安全成员 3
                - generic [ref=e69]: m***@example.test
              - generic [ref=e70]: 客户成员
              - button "管理 安全成员 3" [ref=e71] [cursor=pointer]: 管理
            - article [ref=e72]:
              - generic [ref=e73]: 安
              - generic [ref=e74]:
                - strong [ref=e75]: 安全成员 4
                - generic [ref=e76]: m***@example.test
              - generic [ref=e77]: 客户成员
              - button "管理 安全成员 4" [ref=e78] [cursor=pointer]: 管理
            - article [ref=e79]:
              - generic [ref=e80]: 安
              - generic [ref=e81]:
                - strong [ref=e82]: 安全成员 5
                - generic [ref=e83]: m***@example.test
              - generic [ref=e84]: 客户成员
              - button "管理 安全成员 5" [ref=e85] [cursor=pointer]: 管理
            - article [ref=e86]:
              - generic [ref=e87]: 安
              - generic [ref=e88]:
                - strong [ref=e89]: 安全成员 6
                - generic [ref=e90]: m***@example.test
              - generic [ref=e91]: 客户成员
              - button "管理 安全成员 6" [ref=e92] [cursor=pointer]: 管理
            - article [ref=e93]:
              - generic [ref=e94]: 安
              - generic [ref=e95]:
                - strong [ref=e96]: 安全成员 7
                - generic [ref=e97]: m***@example.test
              - generic [ref=e98]: 客户成员
              - button "管理 安全成员 7" [ref=e99] [cursor=pointer]: 管理
            - article [ref=e100]:
              - generic [ref=e101]: 安
              - generic [ref=e102]:
                - strong [ref=e103]: 安全成员 8
                - generic [ref=e104]: m***@example.test
              - generic [ref=e105]: 客户成员
              - button "管理 安全成员 8" [ref=e106] [cursor=pointer]: 管理
            - article [ref=e107]:
              - generic [ref=e108]: 安
              - generic [ref=e109]:
                - strong [ref=e110]: 安全成员 9
                - generic [ref=e111]: m***@example.test
              - generic [ref=e112]: 客户成员
              - button "管理 安全成员 9" [ref=e113] [cursor=pointer]: 管理
            - article [ref=e114]:
              - generic [ref=e115]: 安
              - generic [ref=e116]:
                - strong [ref=e117]: 安全成员 10
                - generic [ref=e118]: m***@example.test
              - generic [ref=e119]: 客户成员
              - button "管理 安全成员 10" [ref=e120] [cursor=pointer]: 管理
            - article [ref=e121]:
              - generic [ref=e122]: 安
              - generic [ref=e123]:
                - strong [ref=e124]: 安全成员 11
                - generic [ref=e125]: m***@example.test
              - generic [ref=e126]: 客户成员
              - button "管理 安全成员 11" [ref=e127] [cursor=pointer]: 管理
            - article [ref=e128]:
              - generic [ref=e129]: 安
              - generic [ref=e130]:
                - strong [ref=e131]: 安全成员 12
                - generic [ref=e132]: m***@example.test
              - generic [ref=e133]: 客户成员
              - button "管理 安全成员 12" [ref=e134] [cursor=pointer]: 管理
            - article [ref=e135]:
              - generic [ref=e136]: 安
              - generic [ref=e137]:
                - strong [ref=e138]: 安全成员 13
                - generic [ref=e139]: m***@example.test
              - generic [ref=e140]: 客户成员
              - button "管理 安全成员 13" [ref=e141] [cursor=pointer]: 管理
            - article [ref=e142]:
              - generic [ref=e143]: 安
              - generic [ref=e144]:
                - strong [ref=e145]: 安全成员 14
                - generic [ref=e146]: m***@example.test
              - generic [ref=e147]: 客户成员
              - button "管理 安全成员 14" [ref=e148] [cursor=pointer]: 管理
            - article [ref=e149]:
              - generic [ref=e150]: 安
              - generic [ref=e151]:
                - strong [ref=e152]: 安全成员 15
                - generic [ref=e153]: m***@example.test
              - generic [ref=e154]: 客户成员
              - button "管理 安全成员 15" [ref=e155] [cursor=pointer]: 管理
            - article [ref=e156]:
              - generic [ref=e157]: 安
              - generic [ref=e158]:
                - strong [ref=e159]: 安全成员 16
                - generic [ref=e160]: m***@example.test
              - generic [ref=e161]: 客户成员
              - button "管理 安全成员 16" [ref=e162] [cursor=pointer]: 管理
            - article [ref=e163]:
              - generic [ref=e164]: 安
              - generic [ref=e165]:
                - strong [ref=e166]: 安全成员 17
                - generic [ref=e167]: m***@example.test
              - generic [ref=e168]: 客户成员
              - button "管理 安全成员 17" [ref=e169] [cursor=pointer]: 管理
            - article [ref=e170]:
              - generic [ref=e171]: 安
              - generic [ref=e172]:
                - strong [ref=e173]: 安全成员 18
                - generic [ref=e174]: m***@example.test
              - generic [ref=e175]: 客户成员
              - button "管理 安全成员 18" [ref=e176] [cursor=pointer]: 管理
            - article [ref=e177]:
              - generic [ref=e178]: 安
              - generic [ref=e179]:
                - strong [ref=e180]: 安全成员 19
                - generic [ref=e181]: m***@example.test
              - generic [ref=e182]: 客户成员
              - button "管理 安全成员 19" [ref=e183] [cursor=pointer]: 管理
            - article [ref=e184]:
              - generic [ref=e185]: 安
              - generic [ref=e186]:
                - strong [ref=e187]: 安全成员 20
                - generic [ref=e188]: m***@example.test
              - generic [ref=e189]: 客户成员
              - button "管理 安全成员 20" [ref=e190] [cursor=pointer]: 管理
            - article [ref=e191]:
              - generic [ref=e192]: 安
              - generic [ref=e193]:
                - strong [ref=e194]: 安全成员 21
                - generic [ref=e195]: m***@example.test
              - generic [ref=e196]: 客户成员
              - button "管理 安全成员 21" [ref=e197] [cursor=pointer]: 管理
            - article [ref=e198]:
              - generic [ref=e199]: 安
              - generic [ref=e200]:
                - strong [ref=e201]: 安全成员 22
                - generic [ref=e202]: m***@example.test
              - generic [ref=e203]: 客户成员
              - button "管理 安全成员 22" [ref=e204] [cursor=pointer]: 管理
            - article [ref=e205]:
              - generic [ref=e206]: 安
              - generic [ref=e207]:
                - strong [ref=e208]: 安全成员 23
                - generic [ref=e209]: m***@example.test
              - generic [ref=e210]: 客户成员
              - button "管理 安全成员 23" [ref=e211] [cursor=pointer]: 管理
            - article [ref=e212]:
              - generic [ref=e213]: 安
              - generic [ref=e214]:
                - strong [ref=e215]: 安全成员 24
                - generic [ref=e216]: m***@example.test
              - generic [ref=e217]: 客户成员
              - button "管理 安全成员 24" [ref=e218] [cursor=pointer]: 管理
            - article [ref=e219]:
              - generic [ref=e220]: 安
              - generic [ref=e221]:
                - strong [ref=e222]: 安全成员 25
                - generic [ref=e223]: m***@example.test
              - generic [ref=e224]: 客户成员
              - button "管理 安全成员 25" [ref=e225] [cursor=pointer]: 管理
            - article [ref=e226]:
              - generic [ref=e227]: 安
              - generic [ref=e228]:
                - strong [ref=e229]: 安全成员 26
                - generic [ref=e230]: m***@example.test
              - generic [ref=e231]: 客户成员
              - button "管理 安全成员 26" [ref=e232] [cursor=pointer]: 管理
            - article [ref=e233]:
              - generic [ref=e234]: 安
              - generic [ref=e235]:
                - strong [ref=e236]: 安全成员 27
                - generic [ref=e237]: m***@example.test
              - generic [ref=e238]: 客户成员
              - button "管理 安全成员 27" [ref=e239] [cursor=pointer]: 管理
            - article [ref=e240]:
              - generic [ref=e241]: 安
              - generic [ref=e242]:
                - strong [ref=e243]: 安全成员 28
                - generic [ref=e244]: m***@example.test
              - generic [ref=e245]: 客户成员
              - button "管理 安全成员 28" [ref=e246] [cursor=pointer]: 管理
            - article [ref=e247]:
              - generic [ref=e248]: 安
              - generic [ref=e249]:
                - strong [ref=e250]: 安全成员 29
                - generic [ref=e251]: m***@example.test
              - generic [ref=e252]: 客户成员
              - button "管理 安全成员 29" [ref=e253] [cursor=pointer]: 管理
            - article [ref=e254]:
              - generic [ref=e255]: 安
              - generic [ref=e256]:
                - strong [ref=e257]: 安全成员 30
                - generic [ref=e258]: m***@example.test
              - generic [ref=e259]: 客户成员
              - button "管理 安全成员 30" [ref=e260] [cursor=pointer]: 管理
            - article [ref=e261]:
              - generic [ref=e262]: 安
              - generic [ref=e263]:
                - strong [ref=e264]: 安全成员 31
                - generic [ref=e265]: m***@example.test
              - generic [ref=e266]: 客户成员
              - button "管理 安全成员 31" [ref=e267] [cursor=pointer]: 管理
            - article [ref=e268]:
              - generic [ref=e269]: 安
              - generic [ref=e270]:
                - strong [ref=e271]: 安全成员 32
                - generic [ref=e272]: m***@example.test
              - generic [ref=e273]: 客户成员
              - button "管理 安全成员 32" [ref=e274] [cursor=pointer]: 管理
            - article [ref=e275]:
              - generic [ref=e276]: 安
              - generic [ref=e277]:
                - strong [ref=e278]: 安全成员 33
                - generic [ref=e279]: m***@example.test
              - generic [ref=e280]: 客户成员
              - button "管理 安全成员 33" [ref=e281] [cursor=pointer]: 管理
            - article [ref=e282]:
              - generic [ref=e283]: 安
              - generic [ref=e284]:
                - strong [ref=e285]: 安全成员 34
                - generic [ref=e286]: m***@example.test
              - generic [ref=e287]: 客户成员
              - button "管理 安全成员 34" [ref=e288] [cursor=pointer]: 管理
            - article [ref=e289]:
              - generic [ref=e290]: 安
              - generic [ref=e291]:
                - strong [ref=e292]: 安全成员 35
                - generic [ref=e293]: m***@example.test
              - generic [ref=e294]: 客户成员
              - button "管理 安全成员 35" [ref=e295] [cursor=pointer]: 管理
            - article [ref=e296]:
              - generic [ref=e297]: 安
              - generic [ref=e298]:
                - strong [ref=e299]: 安全成员 36
                - generic [ref=e300]: m***@example.test
              - generic [ref=e301]: 客户成员
              - button "管理 安全成员 36" [ref=e302] [cursor=pointer]: 管理
            - article [ref=e303]:
              - generic [ref=e304]: 安
              - generic [ref=e305]:
                - strong [ref=e306]: 安全成员 37
                - generic [ref=e307]: m***@example.test
              - generic [ref=e308]: 客户成员
              - button "管理 安全成员 37" [ref=e309] [cursor=pointer]: 管理
            - article [ref=e310]:
              - generic [ref=e311]: 安
              - generic [ref=e312]:
                - strong [ref=e313]: 安全成员 38
                - generic [ref=e314]: m***@example.test
              - generic [ref=e315]: 客户成员
              - button "管理 安全成员 38" [ref=e316] [cursor=pointer]: 管理
            - article [ref=e317]:
              - generic [ref=e318]: 安
              - generic [ref=e319]:
                - strong [ref=e320]: 安全成员 39
                - generic [ref=e321]: m***@example.test
              - generic [ref=e322]: 客户成员
              - button "管理 安全成员 39" [ref=e323] [cursor=pointer]: 管理
            - article [ref=e324]:
              - generic [ref=e325]: 安
              - generic [ref=e326]:
                - strong [ref=e327]: 安全成员 40
                - generic [ref=e328]: m***@example.test
              - generic [ref=e329]: 客户成员
              - button "管理 安全成员 40" [ref=e330] [cursor=pointer]: 管理
            - article [ref=e331]:
              - generic [ref=e332]: 安
              - generic [ref=e333]:
                - strong [ref=e334]: 安全成员 41
                - generic [ref=e335]: m***@example.test
              - generic [ref=e336]: 客户成员
              - button "管理 安全成员 41" [ref=e337] [cursor=pointer]: 管理
            - article [ref=e338]:
              - generic [ref=e339]: 安
              - generic [ref=e340]:
                - strong [ref=e341]: 安全成员 42
                - generic [ref=e342]: m***@example.test
              - generic [ref=e343]: 客户成员
              - button "管理 安全成员 42" [ref=e344] [cursor=pointer]: 管理
            - article [ref=e345]:
              - generic [ref=e346]: 安
              - generic [ref=e347]:
                - strong [ref=e348]: 安全成员 43
                - generic [ref=e349]: m***@example.test
              - generic [ref=e350]: 客户成员
              - button "管理 安全成员 43" [ref=e351] [cursor=pointer]: 管理
            - article [ref=e352]:
              - generic [ref=e353]: 安
              - generic [ref=e354]:
                - strong [ref=e355]: 安全成员 44
                - generic [ref=e356]: m***@example.test
              - generic [ref=e357]: 客户成员
              - button "管理 安全成员 44" [ref=e358] [cursor=pointer]: 管理
            - article [ref=e359]:
              - generic [ref=e360]: 安
              - generic [ref=e361]:
                - strong [ref=e362]: 安全成员 45
                - generic [ref=e363]: m***@example.test
              - generic [ref=e364]: 客户成员
              - button "管理 安全成员 45" [ref=e365] [cursor=pointer]: 管理
            - article [ref=e366]:
              - generic [ref=e367]: 安
              - generic [ref=e368]:
                - strong [ref=e369]: 安全成员 46
                - generic [ref=e370]: m***@example.test
              - generic [ref=e371]: 客户成员
              - button "管理 安全成员 46" [ref=e372] [cursor=pointer]: 管理
            - article [ref=e373]:
              - generic [ref=e374]: 安
              - generic [ref=e375]:
                - strong [ref=e376]: 安全成员 47
                - generic [ref=e377]: m***@example.test
              - generic [ref=e378]: 客户成员
              - button "管理 安全成员 47" [ref=e379] [cursor=pointer]: 管理
            - article [ref=e380]:
              - generic [ref=e381]: 安
              - generic [ref=e382]:
                - strong [ref=e383]: 安全成员 48
                - generic [ref=e384]: m***@example.test
              - generic [ref=e385]: 客户成员
              - button "管理 安全成员 48" [ref=e386] [cursor=pointer]: 管理
            - article [ref=e387]:
              - generic [ref=e388]: 安
              - generic [ref=e389]:
                - strong [ref=e390]: 安全成员 49
                - generic [ref=e391]: m***@example.test
              - generic [ref=e392]: 客户成员
              - button "管理 安全成员 49" [ref=e393] [cursor=pointer]: 管理
            - article [ref=e394]:
              - generic [ref=e395]: 安
              - generic [ref=e396]:
                - strong [ref=e397]: 安全成员 50
                - generic [ref=e398]: m***@example.test
              - generic [ref=e399]: 客户成员
              - button "管理 安全成员 50" [ref=e400] [cursor=pointer]: 管理
            - article [ref=e401]:
              - generic [ref=e402]: 安
              - generic [ref=e403]:
                - strong [ref=e404]: 安全成员 51
                - generic [ref=e405]: m***@example.test
              - generic [ref=e406]: 客户成员
              - button "管理 安全成员 51" [ref=e407] [cursor=pointer]: 管理
            - article [ref=e408]:
              - generic [ref=e409]: 安
              - generic [ref=e410]:
                - strong [ref=e411]: 安全成员 52
                - generic [ref=e412]: m***@example.test
              - generic [ref=e413]: 客户成员
              - button "管理 安全成员 52" [ref=e414] [cursor=pointer]: 管理
            - article [ref=e415]:
              - generic [ref=e416]: 安
              - generic [ref=e417]:
                - strong [ref=e418]: 安全成员 53
                - generic [ref=e419]: m***@example.test
              - generic [ref=e420]: 客户成员
              - button "管理 安全成员 53" [ref=e421] [cursor=pointer]: 管理
            - article [ref=e422]:
              - generic [ref=e423]: 安
              - generic [ref=e424]:
                - strong [ref=e425]: 安全成员 54
                - generic [ref=e426]: m***@example.test
              - generic [ref=e427]: 客户成员
              - button "管理 安全成员 54" [ref=e428] [cursor=pointer]: 管理
            - article [ref=e429]:
              - generic [ref=e430]: 安
              - generic [ref=e431]:
                - strong [ref=e432]: 安全成员 55
                - generic [ref=e433]: m***@example.test
              - generic [ref=e434]: 客户成员
              - button "管理 安全成员 55" [ref=e435] [cursor=pointer]: 管理
            - article [ref=e436]:
              - generic [ref=e437]: 安
              - generic [ref=e438]:
                - strong [ref=e439]: 安全成员 56
                - generic [ref=e440]: m***@example.test
              - generic [ref=e441]: 客户成员
              - button "管理 安全成员 56" [ref=e442] [cursor=pointer]: 管理
            - article [ref=e443]:
              - generic [ref=e444]: 安
              - generic [ref=e445]:
                - strong [ref=e446]: 安全成员 57
                - generic [ref=e447]: m***@example.test
              - generic [ref=e448]: 客户成员
              - button "管理 安全成员 57" [ref=e449] [cursor=pointer]: 管理
            - article [ref=e450]:
              - generic [ref=e451]: 安
              - generic [ref=e452]:
                - strong [ref=e453]: 安全成员 58
                - generic [ref=e454]: m***@example.test
              - generic [ref=e455]: 客户成员
              - button "管理 安全成员 58" [ref=e456] [cursor=pointer]: 管理
            - article [ref=e457]:
              - generic [ref=e458]: 安
              - generic [ref=e459]:
                - strong [ref=e460]: 安全成员 59
                - generic [ref=e461]: m***@example.test
              - generic [ref=e462]: 客户成员
              - button "管理 安全成员 59" [ref=e463] [cursor=pointer]: 管理
            - article [ref=e464]:
              - generic [ref=e465]: 安
              - generic [ref=e466]:
                - strong [ref=e467]: 安全成员 60
                - generic [ref=e468]: m***@example.test
              - generic [ref=e469]: 客户成员
              - button "管理 安全成员 60" [ref=e470] [cursor=pointer]: 管理
            - article [ref=e471]:
              - generic [ref=e472]: 安
              - generic [ref=e473]:
                - strong [ref=e474]: 安全成员 61
                - generic [ref=e475]: m***@example.test
              - generic [ref=e476]: 客户成员
              - button "管理 安全成员 61" [ref=e477] [cursor=pointer]: 管理
            - article [ref=e478]:
              - generic [ref=e479]: 安
              - generic [ref=e480]:
                - strong [ref=e481]: 安全成员 62
                - generic [ref=e482]: m***@example.test
              - generic [ref=e483]: 客户成员
              - button "管理 安全成员 62" [ref=e484] [cursor=pointer]: 管理
            - article [ref=e485]:
              - generic [ref=e486]: 安
              - generic [ref=e487]:
                - strong [ref=e488]: 安全成员 63
                - generic [ref=e489]: m***@example.test
              - generic [ref=e490]: 客户成员
              - button "管理 安全成员 63" [ref=e491] [cursor=pointer]: 管理
            - article [ref=e492]:
              - generic [ref=e493]: 安
              - generic [ref=e494]:
                - strong [ref=e495]: 安全成员 64
                - generic [ref=e496]: m***@example.test
              - generic [ref=e497]: 客户成员
              - button "管理 安全成员 64" [ref=e498] [cursor=pointer]: 管理
            - article [ref=e499]:
              - generic [ref=e500]: 安
              - generic [ref=e501]:
                - strong [ref=e502]: 安全成员 65
                - generic [ref=e503]: m***@example.test
              - generic [ref=e504]: 客户成员
              - button "管理 安全成员 65" [ref=e505] [cursor=pointer]: 管理
            - article [ref=e506]:
              - generic [ref=e507]: 安
              - generic [ref=e508]:
                - strong [ref=e509]: 安全成员 66
                - generic [ref=e510]: m***@example.test
              - generic [ref=e511]: 客户成员
              - button "管理 安全成员 66" [ref=e512] [cursor=pointer]: 管理
            - article [ref=e513]:
              - generic [ref=e514]: 安
              - generic [ref=e515]:
                - strong [ref=e516]: 安全成员 67
                - generic [ref=e517]: m***@example.test
              - generic [ref=e518]: 客户成员
              - button "管理 安全成员 67" [ref=e519] [cursor=pointer]: 管理
            - article [ref=e520]:
              - generic [ref=e521]: 安
              - generic [ref=e522]:
                - strong [ref=e523]: 安全成员 68
                - generic [ref=e524]: m***@example.test
              - generic [ref=e525]: 客户成员
              - button "管理 安全成员 68" [ref=e526] [cursor=pointer]: 管理
            - article [ref=e527]:
              - generic [ref=e528]: 安
              - generic [ref=e529]:
                - strong [ref=e530]: 安全成员 69
                - generic [ref=e531]: m***@example.test
              - generic [ref=e532]: 客户成员
              - button "管理 安全成员 69" [ref=e533] [cursor=pointer]: 管理
            - article [ref=e534]:
              - generic [ref=e535]: 安
              - generic [ref=e536]:
                - strong [ref=e537]: 安全成员 70
                - generic [ref=e538]: m***@example.test
              - generic [ref=e539]: 客户成员
              - button "管理 安全成员 70" [ref=e540] [cursor=pointer]: 管理
            - article [ref=e541]:
              - generic [ref=e542]: 安
              - generic [ref=e543]:
                - strong [ref=e544]: 安全成员 71
                - generic [ref=e545]: m***@example.test
              - generic [ref=e546]: 客户成员
              - button "管理 安全成员 71" [ref=e547] [cursor=pointer]: 管理
            - article [ref=e548]:
              - generic [ref=e549]: 安
              - generic [ref=e550]:
                - strong [ref=e551]: 安全成员 72
                - generic [ref=e552]: m***@example.test
              - generic [ref=e553]: 客户成员
              - button "管理 安全成员 72" [ref=e554] [cursor=pointer]: 管理
            - article [ref=e555]:
              - generic [ref=e556]: 安
              - generic [ref=e557]:
                - strong [ref=e558]: 安全成员 73
                - generic [ref=e559]: m***@example.test
              - generic [ref=e560]: 客户成员
              - button "管理 安全成员 73" [ref=e561] [cursor=pointer]: 管理
            - article [ref=e562]:
              - generic [ref=e563]: 安
              - generic [ref=e564]:
                - strong [ref=e565]: 安全成员 74
                - generic [ref=e566]: m***@example.test
              - generic [ref=e567]: 客户成员
              - button "管理 安全成员 74" [ref=e568] [cursor=pointer]: 管理
            - article [ref=e569]:
              - generic [ref=e570]: 安
              - generic [ref=e571]:
                - strong [ref=e572]: 安全成员 75
                - generic [ref=e573]: m***@example.test
              - generic [ref=e574]: 客户成员
              - button "管理 安全成员 75" [ref=e575] [cursor=pointer]: 管理
            - article [ref=e576]:
              - generic [ref=e577]: 安
              - generic [ref=e578]:
                - strong [ref=e579]: 安全成员 76
                - generic [ref=e580]: m***@example.test
              - generic [ref=e581]: 客户成员
              - button "管理 安全成员 76" [ref=e582] [cursor=pointer]: 管理
            - article [ref=e583]:
              - generic [ref=e584]: 安
              - generic [ref=e585]:
                - strong [ref=e586]: 安全成员 77
                - generic [ref=e587]: m***@example.test
              - generic [ref=e588]: 客户成员
              - button "管理 安全成员 77" [ref=e589] [cursor=pointer]: 管理
            - article [ref=e590]:
              - generic [ref=e591]: 安
              - generic [ref=e592]:
                - strong [ref=e593]: 安全成员 78
                - generic [ref=e594]: m***@example.test
              - generic [ref=e595]: 客户成员
              - button "管理 安全成员 78" [ref=e596] [cursor=pointer]: 管理
            - article [ref=e597]:
              - generic [ref=e598]: 安
              - generic [ref=e599]:
                - strong [ref=e600]: 安全成员 79
                - generic [ref=e601]: m***@example.test
              - generic [ref=e602]: 客户成员
              - button "管理 安全成员 79" [ref=e603] [cursor=pointer]: 管理
            - article [ref=e604]:
              - generic [ref=e605]: 安
              - generic [ref=e606]:
                - strong [ref=e607]: 安全成员 80
                - generic [ref=e608]: m***@example.test
              - generic [ref=e609]: 客户成员
              - button "管理 安全成员 80" [ref=e610] [cursor=pointer]: 管理
            - article [ref=e611]:
              - generic [ref=e612]: 安
              - generic [ref=e613]:
                - strong [ref=e614]: 安全成员 81
                - generic [ref=e615]: m***@example.test
              - generic [ref=e616]: 客户成员
              - button "管理 安全成员 81" [ref=e617] [cursor=pointer]: 管理
            - article [ref=e618]:
              - generic [ref=e619]: 安
              - generic [ref=e620]:
                - strong [ref=e621]: 安全成员 82
                - generic [ref=e622]: m***@example.test
              - generic [ref=e623]: 客户成员
              - button "管理 安全成员 82" [ref=e624] [cursor=pointer]: 管理
            - article [ref=e625]:
              - generic [ref=e626]: 安
              - generic [ref=e627]:
                - strong [ref=e628]: 安全成员 83
                - generic [ref=e629]: m***@example.test
              - generic [ref=e630]: 客户成员
              - button "管理 安全成员 83" [ref=e631] [cursor=pointer]: 管理
            - article [ref=e632]:
              - generic [ref=e633]: 安
              - generic [ref=e634]:
                - strong [ref=e635]: 安全成员 84
                - generic [ref=e636]: m***@example.test
              - generic [ref=e637]: 客户成员
              - button "管理 安全成员 84" [ref=e638] [cursor=pointer]: 管理
            - article [ref=e639]:
              - generic [ref=e640]: 安
              - generic [ref=e641]:
                - strong [ref=e642]: 安全成员 85
                - generic [ref=e643]: m***@example.test
              - generic [ref=e644]: 客户成员
              - button "管理 安全成员 85" [ref=e645] [cursor=pointer]: 管理
            - article [ref=e646]:
              - generic [ref=e647]: 安
              - generic [ref=e648]:
                - strong [ref=e649]: 安全成员 86
                - generic [ref=e650]: m***@example.test
              - generic [ref=e651]: 客户成员
              - button "管理 安全成员 86" [ref=e652] [cursor=pointer]: 管理
            - article [ref=e653]:
              - generic [ref=e654]: 安
              - generic [ref=e655]:
                - strong [ref=e656]: 安全成员 87
                - generic [ref=e657]: m***@example.test
              - generic [ref=e658]: 客户成员
              - button "管理 安全成员 87" [ref=e659] [cursor=pointer]: 管理
            - article [ref=e660]:
              - generic [ref=e661]: 安
              - generic [ref=e662]:
                - strong [ref=e663]: 安全成员 88
                - generic [ref=e664]: m***@example.test
              - generic [ref=e665]: 客户成员
              - button "管理 安全成员 88" [ref=e666] [cursor=pointer]: 管理
            - article [ref=e667]:
              - generic [ref=e668]: 安
              - generic [ref=e669]:
                - strong [ref=e670]: 安全成员 89
                - generic [ref=e671]: m***@example.test
              - generic [ref=e672]: 客户成员
              - button "管理 安全成员 89" [ref=e673] [cursor=pointer]: 管理
            - article [ref=e674]:
              - generic [ref=e675]: 安
              - generic [ref=e676]:
                - strong [ref=e677]: 安全成员 90
                - generic [ref=e678]: m***@example.test
              - generic [ref=e679]: 客户成员
              - button "管理 安全成员 90" [ref=e680] [cursor=pointer]: 管理
            - article [ref=e681]:
              - generic [ref=e682]: 安
              - generic [ref=e683]:
                - strong [ref=e684]: 安全成员 91
                - generic [ref=e685]: m***@example.test
              - generic [ref=e686]: 客户成员
              - button "管理 安全成员 91" [ref=e687] [cursor=pointer]: 管理
            - article [ref=e688]:
              - generic [ref=e689]: 安
              - generic [ref=e690]:
                - strong [ref=e691]: 安全成员 92
                - generic [ref=e692]: m***@example.test
              - generic [ref=e693]: 客户成员
              - button "管理 安全成员 92" [ref=e694] [cursor=pointer]: 管理
            - article [ref=e695]:
              - generic [ref=e696]: 安
              - generic [ref=e697]:
                - strong [ref=e698]: 安全成员 93
                - generic [ref=e699]: m***@example.test
              - generic [ref=e700]: 客户成员
              - button "管理 安全成员 93" [ref=e701] [cursor=pointer]: 管理
            - article [ref=e702]:
              - generic [ref=e703]: 安
              - generic [ref=e704]:
                - strong [ref=e705]: 安全成员 94
                - generic [ref=e706]: m***@example.test
              - generic [ref=e707]: 客户成员
              - button "管理 安全成员 94" [ref=e708] [cursor=pointer]: 管理
            - article [ref=e709]:
              - generic [ref=e710]: 安
              - generic [ref=e711]:
                - strong [ref=e712]: 安全成员 95
                - generic [ref=e713]: m***@example.test
              - generic [ref=e714]: 客户成员
              - button "管理 安全成员 95" [ref=e715] [cursor=pointer]: 管理
            - article [ref=e716]:
              - generic [ref=e717]: 安
              - generic [ref=e718]:
                - strong [ref=e719]: 安全成员 96
                - generic [ref=e720]: m***@example.test
              - generic [ref=e721]: 客户成员
              - button "管理 安全成员 96" [ref=e722] [cursor=pointer]: 管理
            - article [ref=e723]:
              - generic [ref=e724]: 安
              - generic [ref=e725]:
                - strong [ref=e726]: 安全成员 97
                - generic [ref=e727]: m***@example.test
              - generic [ref=e728]: 客户成员
              - button "管理 安全成员 97" [ref=e729] [cursor=pointer]: 管理
            - article [ref=e730]:
              - generic [ref=e731]: 安
              - generic [ref=e732]:
                - strong [ref=e733]: 安全成员 98
                - generic [ref=e734]: m***@example.test
              - generic [ref=e735]: 客户成员
              - button "管理 安全成员 98" [ref=e736] [cursor=pointer]: 管理
            - article [ref=e737]:
              - generic [ref=e738]: 安
              - generic [ref=e739]:
                - strong [ref=e740]: 安全成员 99
                - generic [ref=e741]: m***@example.test
              - generic [ref=e742]: 客户成员
              - button "管理 安全成员 99" [ref=e743] [cursor=pointer]: 管理
        - generic [ref=e744]:
          - heading "邀请成员" [level=2] [ref=e745]
          - generic [ref=e746]:
            - generic [ref=e747]: 姓名
            - textbox "姓名" [disabled] [ref=e748]
          - generic [ref=e749]:
            - generic [ref=e750]: 工作邮箱
            - textbox "工作邮箱" [disabled] [ref=e751]
          - generic [ref=e752]:
            - generic [ref=e753]: 项目角色
            - combobox "项目角色" [disabled] [ref=e754]:
              - option "客户成员" [selected]
              - option "客户管理员"
          - button "发送邀请" [disabled] [ref=e755]
```

# Test source

```ts
  1262 |       localStorage,
  1263 |       sessionStorage,
  1264 |       href: location.href,
  1265 |     }),
  1266 |   );
  1267 |   expect(surface).not.toMatch(/member-input-mismatch-canary|\/secret\/profile|Bearer|错误成员/);
  1268 | });
  1269 | 
  1270 | test('oversized or unsafe identity governance lists stay explicit and governance-write locked', async ({
  1271 |   page,
  1272 | }) => {
  1273 |   let governanceWrites = 0;
  1274 |   await page.addInitScript(() => {
  1275 |     localStorage.setItem('geo.session.tenant', 'tnt_member_projection');
  1276 |     localStorage.setItem('geo.session.actor', 'tenant-admin-projection');
  1277 |     localStorage.setItem('geo.session.role', 'admin');
  1278 |   });
  1279 |   await page.route('**/api/v2/identity/session', (route) =>
  1280 |     route.fulfill({
  1281 |       status: 200,
  1282 |       contentType: 'application/json',
  1283 |       body: JSON.stringify({
  1284 |         tenant_pub_id: 'tnt_member_projection',
  1285 |         user_pub_id: 'usr_member_projection_admin',
  1286 |         role: 'admin',
  1287 |         permissions: ['*'],
  1288 |       }),
  1289 |     }),
  1290 |   );
  1291 |   await page.route('**/api/v2/projects**', (route) =>
  1292 |     route.fulfill({
  1293 |       status: 200,
  1294 |       contentType: 'application/json',
  1295 |       body: JSON.stringify({
  1296 |         data: [
  1297 |           {
  1298 |             pub_id: 'prj_member_projection',
  1299 |             tenant_pub_id: 'tnt_member_projection',
  1300 |             name: '成员安全投影项目',
  1301 |             state: 'active',
  1302 |             created_at: '2026-07-25T00:00:00Z',
  1303 |             updated_at: '2026-07-25T00:00:00Z',
  1304 |           },
  1305 |         ],
  1306 |         page: { next_cursor: null, has_more: false },
  1307 |       }),
  1308 |     }),
  1309 |   );
  1310 |   await page.route('**/api/v2/health', (route) =>
  1311 |     route.fulfill({
  1312 |       status: 200,
  1313 |       contentType: 'application/json',
  1314 |       body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
  1315 |     }),
  1316 |   );
  1317 |   await page.route('**/api/v2/identity/oidc-bindings', (route) =>
  1318 |     route.fulfill({
  1319 |       status: 200,
  1320 |       contentType: 'application/json',
  1321 |       body: JSON.stringify(
  1322 |         Array.from({ length: 102 }, (_, index) => ({
  1323 |           user_pub_id:
  1324 |             index === 1
  1325 |               ? 'usr_member_boundary_0'
  1326 |               : index === 2
  1327 |                 ? 'Cookie=oidc-visible-row-canary'
  1328 |                 : `usr_member_boundary_${index}`,
  1329 |           active: true,
  1330 |           created_at: '2026-07-25T00:00:00Z',
  1331 |           revoked_at: null,
  1332 |           profile_path: '/secret/profile/oidc-list-extension-canary',
  1333 |         })),
  1334 |       ),
  1335 |     }),
  1336 |   );
  1337 |   await page.route('**/api/v2/identity/members**', (route) => {
  1338 |     if (route.request().method() !== 'GET') {
  1339 |       governanceWrites += 1;
  1340 |       return route.fulfill({ status: 500, body: '{}' });
  1341 |     }
  1342 |     return route.fulfill({
  1343 |       status: 200,
  1344 |       contentType: 'application/json',
  1345 |       body: JSON.stringify(
  1346 |         Array.from({ length: 102 }, (_, index) => ({
  1347 |           pub_id: index === 1 ? 'mbr_boundary_0' : `mbr_boundary_${index}`,
  1348 |           user_pub_id: `usr_member_boundary_${index}`,
  1349 |           subject: `member${index}@example.test`,
  1350 |           display_name: index === 2 ? 'Bearer member-visible-row-canary' : `安全成员 ${index}`,
  1351 |           role: index === 0 ? 'admin' : 'customer',
  1352 |           state: 'active',
  1353 |           service_account: false,
  1354 |           cookie: 'SESSION=member-list-extension-canary',
  1355 |         })),
  1356 |       ),
  1357 |     });
  1358 |   });
  1359 | 
  1360 |   await page.goto('/platform/customer/?section=members');
  1361 |   await expect(page.getByRole('alert')).toContainText('成员安全投影不完整');
> 1362 |   await expect(page.getByRole('status')).toContainText(
       |                                          ^ Error: expect(locator).toContainText(expected) failed
  1363 |     '成员合同安全投影：服务返回 102 条，浏览器安全视图展示 98 条',
  1364 |   );
  1365 |   await expect(page.getByRole('status')).toContainText(
  1366 |     'OIDC 绑定安全投影：服务返回 102 条，浏览器安全视图展示 98 条',
  1367 |   );
  1368 |   await expect(page.locator('.member-list article')).toHaveCount(98);
  1369 |   await expect(page.getByRole('button', { name: '发送邀请' })).toBeDisabled();
  1370 | 
  1371 |   await page.getByRole('button', { name: '管理 安全成员 0' }).click();
  1372 |   await expect(page.getByRole('button', { name: '改为客户成员' })).toBeDisabled();
  1373 |   await expect(page.getByRole('button', { name: '移出项目' })).toBeDisabled();
  1374 |   await expect(page.getByLabel('IdP opaque subject')).toBeDisabled();
  1375 |   await expect(page.getByRole('button', { name: '撤销 OIDC 绑定' })).toBeDisabled();
  1376 | 
  1377 |   await expectAccessible(page);
  1378 |   await expect
  1379 |     .poll(() =>
  1380 |       page.evaluate(
  1381 |         () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
  1382 |       ),
  1383 |     )
  1384 |     .toBe(true);
  1385 |   const surface = await page.evaluate(() =>
  1386 |     JSON.stringify({
  1387 |       dom: document.documentElement.outerHTML,
  1388 |       localStorage,
  1389 |       sessionStorage,
  1390 |       href: location.href,
  1391 |     }),
  1392 |   );
  1393 |   for (const canary of [
  1394 |     'member-visible-row-canary',
  1395 |     'oidc-visible-row-canary',
  1396 |     'member-list-extension-canary',
  1397 |     'oidc-list-extension-canary',
  1398 |     '/secret/profile',
  1399 |   ]) {
  1400 |     expect(surface).not.toContain(canary);
  1401 |   }
  1402 |   expect(governanceWrites).toBe(0);
  1403 | });
  1404 | 
  1405 | test('customer role cannot infer tenant member existence', async ({ page }) => {
  1406 |   let memberWrites = 0;
  1407 |   await page.addInitScript(() => {
  1408 |     localStorage.setItem('geo.session.tenant', 'tnt_member_forbidden');
  1409 |     localStorage.setItem('geo.session.actor', 'customer-member-forbidden');
  1410 |     localStorage.setItem('geo.session.role', 'customer');
  1411 |   });
  1412 |   await page.route('**/api/v2/identity/session', (route) =>
  1413 |     route.fulfill({
  1414 |       status: 200,
  1415 |       contentType: 'application/json',
  1416 |       body: JSON.stringify({
  1417 |         tenant_pub_id: 'tnt_member_forbidden',
  1418 |         user_pub_id: 'usr_member_forbidden',
  1419 |         role: 'customer',
  1420 |         permissions: ['project:read'],
  1421 |       }),
  1422 |     }),
  1423 |   );
  1424 |   await page.route('**/api/v2/projects**', (route) =>
  1425 |     route.fulfill({
  1426 |       status: 200,
  1427 |       contentType: 'application/json',
  1428 |       body: JSON.stringify({
  1429 |         data: [
  1430 |           {
  1431 |             pub_id: 'prj_member_forbidden',
  1432 |             tenant_pub_id: 'tnt_member_forbidden',
  1433 |             name: '成员不可推断项目',
  1434 |             state: 'active',
  1435 |             created_at: '2026-07-25T00:00:00Z',
  1436 |             updated_at: '2026-07-25T00:00:00Z',
  1437 |           },
  1438 |         ],
  1439 |         page: { next_cursor: null, has_more: false },
  1440 |       }),
  1441 |     }),
  1442 |   );
  1443 |   await page.route('**/api/v2/health', (route) =>
  1444 |     route.fulfill({
  1445 |       status: 200,
  1446 |       contentType: 'application/json',
  1447 |       body: JSON.stringify({ status: 'ok', service: 'geo-platform-v2', version: 'contract-v2' }),
  1448 |     }),
  1449 |   );
  1450 |   await page.route('**/api/v2/identity/oidc-bindings', (route) =>
  1451 |     route.fulfill({
  1452 |       status: 403,
  1453 |       contentType: 'application/json',
  1454 |       body: JSON.stringify({ detail: { code: 'admin_required' } }),
  1455 |     }),
  1456 |   );
  1457 |   await page.route('**/api/v2/identity/members**', (route) => {
  1458 |     if (route.request().method() !== 'GET') memberWrites += 1;
  1459 |     return route.fulfill({
  1460 |       status: 403,
  1461 |       contentType: 'application/json',
  1462 |       body: JSON.stringify({
```