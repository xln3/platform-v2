# 报价任务固定执行协议

版本：1.0

状态：Phase A 生效；正式出单仍需获批 V2
适用范围：`platform-v2` 内所有报价单、报价模板、GEO 报价、quotation、DOCX 生成与报价验收任务

本协议固定报价任务的入口记录、失败条件和验收门。它引用用户批准的模板资产，但不替代用户的最终审批，也不把 manifest、skill 或自动化测试声明为人类审批权威。

## 1. 每次任务的前置记录

执行任何读取客户数据、调用 LLM 或生成制品的动作前，必须先运行模板 preflight，并产生以下记录：

| 字段                 | 内容                                                         |
| -------------------- | ------------------------------------------------------------ |
| `canonical_template` | preflight 解析出的版本控制内模板绝对路径                     |
| `template_id`        | 模板标识                                                     |
| `template_sha256`    | 本次从模板字节重新计算的 SHA-256                             |
| `approval_status`    | 模板批准状态；不得由执行者自行改写                           |
| `approved_changes`   | 本次用户明确允许改变的区域和字段                             |
| `frozen_regions`     | 本次禁止改变的文字、结构、OOXML 与视觉区域                   |
| `artifact_kinds`     | `quote_table`、`query_appendix`、`complete` 中本次需要的制品 |
| `acceptance_gates`   | 本次必须通过的身份、结构、OOXML、PDF 视觉、业务和负面测试    |

该记录保存在本次任务的工作记录或交付合规报告中，不为此建立新的全项目治理 manifest。纯只读模板审计使用 `artifact_kinds: [none]`；不适用的门写成 `not_applicable` 并说明原因，不得静默省略。

Phase A 身份检查：

```bash
python3 tools/quotation_template_preflight.py
```

正式出单检查：

```bash
python3 tools/quotation_template_preflight.py --require-production
```

在五服务 V2 红线获得用户明确批准并冻结前，第二条命令必须以 `quotation_template_not_approved` 失败。不得绕过、吞掉或降级此错误。

## 2. 当前模板状态

- V1 资产：`api/geo_platform/quotations/assets/quotation-template-v1.docx`
- V1 manifest：`api/geo_platform/quotations/assets/quotation-template-v1.yaml`
- V1 角色：用户最终批准的四服务绝对真源和 V2 唯一派生起点。
- V1 限制：V1 的批准不等于五服务 V2 已获批准；当前正式生成仍被关闭。
- 拟议 V2：只有用户批准 `docs/QUOTATION_TEMPLATE_V1_TO_V2_REDLINE.md` 后才能创建并标记为批准。

## 3. 固定执行顺序

1. 运行 preflight，保存其 JSON 输出，并补全本次 `approved_changes`、`frozen_regions`、`artifact_kinds` 和 `acceptance_gates`。
2. 校验客户输入、服务选择、套餐展开、价格状态和 Query 工作簿；未知价格使用 `pending`。
3. 仅调用所选服务和制品真正需要的 LLM；不需要动态 Query 时不得调用 LLM。
4. 从同一获批模板体系派生制品；不得以无条件 `Document()` 或自创版式作为正式报价单根文档。
5. 对三类制品执行章节隔离检查，确保未选服务和不相关附录不出现。
6. 执行模板身份、结构、归一化 OOXML、PDF 视觉、业务和负面测试。
7. 交付 DOCX、PDF、制品 SHA、`template_id`、`template_version`、`template_sha256` 与合规报告。

## 4. 冻结区域与允许变量

冻结区域以获批 manifest 和结构合同为准，至少包括标题、五条商务条款、签署区、保密页眉、页码域、A4 版心、主表商业视觉语言、附录一/二/三的叙事顺序和样式体系。

允许变量仅包括经批准 manifest 声明的客户名称、报价日期、已选服务行、获批价格、客户 Query 及有证据支持的实测结果。任何新增章节、列、套餐副标题、执行顺序或商业口径都必须先进入红线并获得用户批准。

## 5. 三类制品边界

- `quote_table`：获批模板中的标题、客户与日期、报价主表、五条商务条款和签署区。
- `query_appendix`：获批模板中的 Query 方法论、原始 Query 与 A/B/C 变体，以及仅在所选服务需要时出现的新增优化 Query。
- `complete`：按获批模板顺序组合 `quote_table` 与适用的 Query 附录；不得重新设计封面或章节顺序。

## 6. 必须失败关闭的条件

- `quotation_template_missing`：manifest 或模板文件缺失。
- `quotation_template_hash_mismatch`：模板实际 SHA 与 manifest 不符。
- `quotation_template_version_unknown`：manifest schema 或模板版本未知。
- `quotation_template_not_approved`：模板、红线或正式使用状态未获批准。
- `quotation_template_manifest_invalid`：manifest 路径、必填字段或批准身份注册不合法。
- 商务条款漂移、附录顺序变化、未授权节点出现或视觉门失败时，不得产出正式客户报价。

当前 legacy renderer 产生的文档只能标为“非最终模板合规产物（仅供内部回归，禁止作为正式客户报价）”，不能通过本协议的正式出单门。

## 7. Repo skill 发现状态

唯一 skill 正文位于 `.agents/skills/geo-quotation/SKILL.md`，skill 内不保存模板。`quick_validate.py` 和独立前向测试已通过；Kimi 的既有入口使用符号链接指向同一正文。

创建本 skill 的当前 Codex 会话不能热加载新的 repo skill，因此当前会话的初始可发现列表不会更新。需要重启 Codex 或开启位于 `platform-v2` 的新会话，再验证自动发现和触发；在此之前不能宣称已完成热加载发现验收。
