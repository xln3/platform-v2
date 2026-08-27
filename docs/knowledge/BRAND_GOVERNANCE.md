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

| 类型            | 定义                          | 默认汇总                       |
| --------------- | ----------------------------- | ------------------------------ |
| `legal_entity`  | 法定登记主体                  | 不自动与集团或品牌等同         |
| `group`         | 控股或管理集团                | 只在集团视图汇总成员           |
| `company`       | 对外经营公司                  | 可拥有品牌、产品或业务线       |
| `brand`         | 稳定市场标识                  | 按品牌视图计数                 |
| `brand_family`  | 多个子品牌/产品共享的展示家族 | 仅按已审 relation 汇总         |
| `sub_brand`     | 有独立识别但受父品牌管理      | 保留原 mention，按策略 roll-up |
| `business_unit` | 公司内部或对外业务线          | 不等于独立法人                 |
| `product`       | 可购买或使用的产品/服务       | 不应仅因产品名像公司就建公司   |
| `tool`          | 工具或平台                    | 默认不进入公司榜               |
| `institution`   | 政府、研究、标准或公共机构    | 默认非商业竞品                 |

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

| 等级          | 例子                                     | 可支持的结论                   |
| ------------- | ---------------------------------------- | ------------------------------ |
| authoritative | 监管披露、交易所公告、法定登记、官方标准 | 法人、曾用名、受监管资质       |
| primary       | 公司官网、产品官网、官方公告             | 官方简称、品牌/产品/业务线关系 |
| secondary     | 高质量媒体、研究机构                     | 候选与交叉核验                 |
| unverified    | 聚合页、论坛、模型常识                   | 只能生成候选，不能批准公开事实 |

批准公开品牌对象至少需要一条 authoritative 或 primary 支持证据。反对证据必须和支持证据一起进入裁决，不得删除。

## 必测案例

| mention                        | 文本实际指向的对象                             | 与汇总对象的关系                         | 当前品牌家族榜 roll-up                                   | 竞品资格                                             |
| ------------------------------ | ---------------------------------------------- | ---------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| 腾讯云                         | `CYB-OBJ-TENCENT-CLOUD`，business unit         | `business_unit_of` 腾讯                  | `CYB-BR-TENCENT`，显示腾讯；同回答再出现“腾讯”不重复计权 | 云安全/网安 scope 可进入；身份不是腾讯法人           |
| 华为云                         | `CYB-OBJ-HUAWEI-CLOUD`，business unit          | `business_unit_of` 华为                  | `CYB-BR-HUAWEI`，显示华为；父/业务品牌同答一次           | 云安全/网安 scope 可进入；身份不是独立法人结论       |
| 绿盟、NSFOCUS                  | `CYB-BR-NSFOCUS`，绿盟科技 brand view          | `official_abbreviation` / `english_name` | 显示绿盟科技，同回答只计一次                             | 通用网安可进入                                       |
| 绿盟科技集团股份有限公司       | `CYB-OBJ-NSFOCUS-LEGAL`，legal entity          | `same_legal_entity` 到绿盟科技展示对象   | 显示绿盟科技                                             | 通用网安可进入                                       |
| BJCA、北京数字认证             | `CYB-BR-BJCA`，数字认证 brand view             | `trade_name` / `official_abbreviation`   | 显示数字认证，同回答只计一次                             | 电子认证、密码和网络安全公开业务足以进入通用网安比较 |
| 北京数字认证股份有限公司       | `CYB-OBJ-BJCA-LEGAL`，legal entity             | `same_legal_entity` 到数字认证展示对象   | 显示数字认证                                             | 同上；法人证据来自公司披露                           |
| 新大陆集团                     | `CYB-OBJ-NEWLAND-GROUP`，group                 | `brand_family_member` 到新大陆展示家族   | 显示新大陆，但保留集团身份                               | 通用网安不进入；CTID/数字身份可进入                  |
| 新大陆数字技术股份有限公司     | `CYB-OBJ-NEWLAND-DIGITAL`，legal entity        | `brand_family_member`                    | 显示新大陆                                               | 通用网安不进入；合适的数字身份 scope 才进入          |
| 新大陆（福建）公共服务有限公司 | `CYB-OBJ-NEWLAND-PUBLIC-SERVICE`，legal entity | `subsidiary_of`                          | 显示新大陆                                               | CTID/网络身份 scope 可进入，不能外推到所有网安采购   |
| ZoomEye                        | `CYB-OBJ-KNOWNSEC-ZOOMEYE`，product            | `product_of`                             | 在允许产品 roll-up 的榜显示知道创宇                      | 只按明确网安 scope；产品不能伪装成公司               |
| RayTAG                         | `CYB-OBJ-WEBRAY-RAYTAG`，product               | `product_of`                             | 在允许产品 roll-up 的榜显示盛邦安全                      | 只按明确 scope                                       |
| 卫士通                         | 当前视图映射到电科网安                         | `historical_name`                        | 显示当前品牌电科网安                                     | 通用网安可进入；曾用名不产生第二份权重               |

官方核查入口包括[腾讯云](https://cloud.tencent.com/about)、[腾讯产品页](https://www.tencent.com/zh-cn/products/tencent-cloud/)、[华为云品牌说明](https://consumer.huawei.com/cn/support/content/zh-cn15955449/)、[绿盟科技](https://www.nsfocus.com.cn/html/7/)、[BJCA](https://www.bjca.cn/about/index.html)、[数字认证年度报告](https://disc.static.szse.cn/download/disc/disk03/finalpage/2026-03-31/3179d1d6-b086-4d6c-8278-46846186df37.PDF)、[新大陆官网](https://www.newland.com.cn/)和[新大陆公开披露](https://static.cninfo.com.cn/finalpage/2025-08-26/1224570931.PDF)。官网能证明品牌/业务关系，交易所披露更适合证明法人和子公司；二者不能混用。

## 容易误判的边界

- 同名异企：名称相同或近似时，先比较法人登记、官网域名、所在地和产品上下文；没有同一主体证据就保留两个 stable ID，并把 mention 设为 `context_required` 或 unresolved。
- 产品似公司：名称带“云”“平台”“中心”或英文大写不构成公司证据。产品只建 `product`/`tool` 对象，通过 `product_of` 连接拥有者；当前榜单是否 roll-up 由项目策略决定。
- 集团和子公司：集团、上市公司和具体子公司各有 identity object。`subsidiary_of` 与 `brand_family_member` 不等于 `same_legal_entity`。
- 曾用名：必须有工商、交易所公告或官方历史页面，并保存有效时间。曾用名可解析到当前展示对象，但历史文本中的法人状态仍按当时有效期解释。
- 错误别名：已有 `reviewed` 标签而没有能支持具体 claim 的来源时降为 pending。后续模型再次提出同一别名只增加观察，不恢复 reviewed。
- 跨语言名称：官方英文名可用 `english_name`；机器翻译、域名缩写或第三方译名只能形成候选。大小写变体可以做规范化，但不能凭规范化跨公司合并。

## 榜单计权规则

解析先返回 identity，再单独返回 roll-up。计权键是当前项目政策选定的 roll-up stable ID，不是原字符串。一个回答里“腾讯云”和“腾讯”可以保留两个 raw mention 和两条 identity 解释，但品牌家族榜只给腾讯一个 answer-level hit。若项目要求业务品牌榜，则可展示腾讯云，但仍不能同时给父品牌和业务品牌各加一份同口径权重。模型只能在请求显式允许 `adopt_model_inferred` 时改变当次结果；状态仍为 `model_inferred`，不会进入已发布计权字典。

## 68 个网安实体迁移结论

候选 SiliconIndex release `2026-08-26.3` 含 68 个网安实体和 186 条 mention。重新审计没有把已有 `reviewed` 字段当成证据。稳定 ID 被保留，但缺少能支持具体关系的来源时，关系必须降级而不是靠旧标签延续。

第一轮关系复核候选为 `2026-08-27.4`：网安域仍有 68 个品牌展示对象，其中 40 reviewed、28 pending；186 条 mention 中 111 reviewed、75 pending。36 条此前缺乏 claim-specific 证据的名称关系被降级，避免 pending 关系从 resolver、search index、graph、bundle 或前端泄漏到正式结果。

`.5` 先补入 ZoomEye 和 RayTAG 产品对象。1309 条真实历史答案的第一次影子回放随后暴露出 `.5` 的误伤：统一降级公司/集团名称会让 121 次 mention 失去身份解析，并让目标品牌少 1 个 answer-level hit。这一候选没有进入生产。

最终静态发布是 `2026-08-27.6`，schema `1.2.0`，content hash `sha256:d5e15f1dc9f6b3b2d1addf6c41500e3eb3a5514b295a3a1134d4c636c07dabd2`。它直接继承最后一个公开版本 `.2`；本地 `.3`、`.4`、`.5` 候选没有进入公开 release 索引，因此版本号有意跳号。`.6` 没有恢复旧 `reviewed` 标签，而是为能由官网或公开披露证明的关系新增 29 个稳定 identity object，并把 34 条 mention 绑定到具体对象和对象级证据。网安域现在有 39 个 identity object：29 个法人、4 个集团、3 个业务单元、2 个产品和 1 个机构；186 条 mention 中 145 reviewed、41 pending。腾讯集团/腾讯云/腾讯安全、华为法人/华为云、绿盟法人、数字认证法人、新大陆集团/上市公司/公共服务子公司、白帽汇研究院、ZoomEye/RayTAG 产品等身份与榜单汇总分别表达。`360集团` 仍因指向范围不明确保持 pending；`360数字安全集团` 有官方对象证据，可解析到具体法人后再汇总。

扩展后的时间冻结回放集有 22 条请求。当前本机 `.3` 在 15 条身份、层级、关系或场景判断上不符合新金标，`.6` 为 0 条，新增错误预算为 0。真实项目的 1309 条答案回放中，`.6` 相对 `.3` 只把 5 次歧义 `360集团` 从正式计权改为 unresolved；目标品牌 741 次 answer-level hit、8 次同回答别名折叠和 1158 个受治理投影影响的答案数均不变。这个结果是发布门禁和工程回归证据，不是论文结论。

本机数据库的初次导入保存 40 个发布汇总对象；具体法人、集团、业务、产品和机构以带独立 stable ID、类型、关系和证据的 identity object 保存在其版本化属性中，并由运行时单独返回。后继导入不会复制完整对象集：只有内容变化的 stable ID 才创建新 observation、candidate、proposal、supporting evidence、adjudication、change set 和 `knowledge_object.version+1`；无变化对象继续引用旧版本。对象级来源会落成 claim-specific Evidence 行。artifact 先生成但不激活，数据库提交成功后才原子推进 `CURRENT`。同一 `.6` 重跑必须保持幂等。

## 版本和冲突

事实纠正必须生成新 proposal、adjudication、change set 和 release。旧 release 和旧对象/assertion 行不能修改。上游和本地相对同一共同 base 修改不同字段时自动合并；修改同一字段时产生 state=`conflict` 的 change set 并阻止发布。冲突解决后旧冲突标为 `superseded`，不再计入未处理积压但仍可审计。

品牌发布还必须通过历史回放 impact gate：报告包含截止时间、评测集内容 hash、基线/候选错误、修复数、新错误数和允许预算。紧急纠错可以绕过周度等待时间，但不能绕过证据、四眼审批、回放和质量门。若回放没有比强基线好，只能缩小变更或报告负结果。
