# 品牌分类与竞品治理规范

## 理论边界

品牌被消费者记起，不等于它属于同一法人，也不等于它在任意购买场景都是竞品。Hauser 与 Wernerfelt 对 consideration set 的研究把最终考虑集合与更大的可知集合分开；Nedungadi 进一步说明回忆与考虑集合会影响选择。因此 GEO 榜单必须把“被提及”“身份归属”“展示汇总”和“场景竞品资格”分开。

参考资料：

- [Hauser & Wernerfelt, An Evaluation Cost Model of Consideration Sets](https://academic.oup.com/jcr/article/16/4/393/1787720)
- [Nedungadi, Recall and Consumer Consideration Sets](https://academic.oup.com/jcr/article-abstract/17/3/263/1822527)
- [B2B Brand Architecture](https://journals.sagepub.com/doi/10.1525/cmr.2012.54.2.58)
- [Corporate Brand Architecture](https://journals.sagepub.com/doi/pdf/10.1177/1470593108100060)
- [WIPO Nice Classification](https://www.wipo.int/en/web/classification-nice/)
- [GS1 GTIN Management Standard](https://ref.gs1.org/standards/gtin-management/)

WIPO Nice class 是商标商品/服务分类，不是竞争关系真值。GS1 标识规则可帮助区分产品，但不能替代品牌家族或法人关系证据。跨市场关系研究也不能推出“同属一个行业就一定互为竞品”。

## 四个独立判断

1. `identity` 判断 mention 指向哪个稳定对象。
2. `relation` 判断 mention 与对象的名称或组织关系。
3. `roll_up` 判断本视图是否把对象展示到父品牌或品牌家族。
4. `comparison eligibility` 判断它在指定 domain、scope、受众、地域和购买阶段是否进入比较集合。

第五个正交维度是 `epistemic status`。同一判断可以来自 published、reviewed local、model inferred 或 unresolved。认识状态不能改变现实关系；它只说明我们凭什么相信该关系。

## 对象类型

| 类型 | 定义 | 默认汇总 |
| --- | --- | --- |
| `legal_entity` | 法定登记主体 | 不自动与集团或品牌等同 |
| `group` | 控股或管理集团 | 只在集团视图汇总成员 |
| `company` | 对外经营公司 | 可拥有品牌、产品或业务线 |
| `brand` | 稳定市场标识 | 按品牌视图计数 |
| `brand_family` | 多个子品牌/产品共享的展示家族 | 仅按已审 relation 汇总 |
| `sub_brand` | 有独立识别但受父品牌管理 | 保留原 mention，按策略 roll-up |
| `business_unit` | 公司内部或对外业务线 | 不等于独立法人 |
| `product` | 可购买或使用的产品/服务 | 不应仅因产品名像公司就建公司 |
| `tool` | 工具或平台 | 默认不进入公司榜 |
| `institution` | 政府、研究、标准或公共机构 | 默认非商业竞品 |

## 关系类型

`same_legal_entity` 只用于同一法人。`official_abbreviation`、`english_name`、`historical_name` 和 `trade_name` 是名称关系。`product_of`、`business_unit_of`、`subsidiary_of` 和 `brand_family_member` 是组织或品牌架构关系。它们不能被统一替换成“merge”。

## 决策树

1. 先确认 mention 是组织、品牌、产品、工具还是非商业机构。
2. 查找官方名称、官网、监管披露或公司公告。
3. 只有证据支持时才绑定现有 stable ID。
4. 记录精确 relation；相似拼写本身不是证据。
5. 根据当前视图决定 roll-up，保留 raw alias 和 relation。
6. 根据 comparison scope 判断 eligibility。
7. 证据不足时保持 unresolved 或提出新对象，不虚构 ID。
8. 模型判断可影响明确允许的当前请求，但必须进入候选队列并等待治理。

## 证据等级

| 等级 | 例子 | 可支持的结论 |
| --- | --- | --- |
| authoritative | 监管披露、交易所公告、法定登记、官方标准 | 法人、曾用名、受监管资质 |
| primary | 公司官网、产品官网、官方公告 | 官方简称、品牌/产品/业务线关系 |
| secondary | 高质量媒体、研究机构 | 候选与交叉核验 |
| unverified | 聚合页、论坛、模型常识 | 只能生成候选，不能批准公开事实 |

批准公开品牌对象至少需要一条 authoritative 或 primary 支持证据。反对证据必须和支持证据一起进入裁决，不得删除。

## 必测案例

| mention | identity | relation | roll-up | 通用网安资格 |
| --- | --- | --- | --- | --- |
| 腾讯云 | 腾讯 | `business_unit_of` | 品牌家族榜显示腾讯 | 可进入云安全/网安 scope |
| 华为云 | 华为 | `business_unit_of` | 品牌家族榜显示华为 | 可进入云安全/网安 scope |
| 绿盟、NSFOCUS | 绿盟科技 | abbreviation/English name | 同回答只计一次 | 是 |
| BJCA、北京数字认证 | 数字认证 | trade name/abbreviation | 同回答只计一次 | 是 |
| 新大陆 | 新大陆品牌家族 | self | 保留集团/成员关系 | 通用网安否；CTID/数字身份是 |
| ZoomEye | 知道创宇 | `product_of` | 品牌家族榜显示知道创宇 | 按网安 scope |
| RayTAG | 盛邦安全 | `product_of` | 品牌家族榜显示盛邦安全 | 按明确 scope |
| 卫士通 | 电科网安 | `historical_name` | 显示当前品牌 | 是 |

官方核查入口包括[腾讯云](https://cloud.tencent.com/about/)、[腾讯公司](https://www.tencent.com/index.php/zh-cn/about.html)、[华为 Trust Center](https://www.huawei.com/cn/trust-center/)、[华为云品牌说明](https://consumer.huawei.com/cn/support/content/zh-cn15955449/)、[绿盟科技](https://www.nsfocus.com.cn/html/7/)、[BJCA](https://www.bjca.cn/about/index.html)、[新大陆业务](https://www.newland.com.cn/business/A030002index_1.htm)和[新大陆 CTID 场景](https://dt.newland.com.cn/news/mtgz/2024/8/495f401fbb8348128277db56da342a00.htm)。

## 68 个网安实体迁移结论

候选 SiliconIndex release `2026-08-26.3` 含 68 个网安实体和 186 条 mention。审核不能把已有 `reviewed` 字段当成证据。确定性发布流程随后生成 `2026-08-27.1`；其内容 hash 与候选版同为 `sha256:a36b4523d10ce7c09b073604b5f4bea63badf67be25a441b05ac61646ea92b00`，因此记录为零数据变化的发布血缘，不重复创建治理对象。

当前迁移发布 40 个有公开证据且通过审核的实体。其余 28 个只保留 observation/candidate，不进入正式榜单。40 个发布对象的 `reviewed_without_evidence` 为 0。原 stable ID 和上游 release/hash 血缘被保留。

平台当前本地 release 是 `knowledge-2026-08-27.2`，父版本是 `.1`，内容 hash 为 `sha256:05eee1d75251efdc151e65afe9856d62f0c5ba21486a1ee23d3f09f1dac4c9d0`。`.2` 只更新上游 release lineage；数据库验证 40 个 reviewed object 的 stable ID 与内容均未改变，proposal/object 未重复写入。

## 版本和冲突

事实纠正必须生成新 proposal、adjudication、change set 和 release。旧 release 不修改。上游和本地相对同一 base 修改不同字段时自动合并；修改同一字段时产生冲突并阻止发布。紧急纠错可以绕过周度等待时间，但不能绕过证据、四眼审批和质量门。
