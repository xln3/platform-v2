---
name: geo-quotation
description: 报价单、报价模板、GEO报价、quotation、DOCX生成、报价验收、V1模板审计：在 platform-v2 内预检唯一批准模板，配置五项服务与两个套餐，从同一模板体系派生报价表、查询附件或完整报价单，并执行模板身份、结构、OOXML、PDF 视觉与业务门禁。凡创建、修改、下载、验收、审计或排查 GEO 报价制品时使用。
---

# GEO 报价模板工作流

## 开始前必须 preflight

在仓库根目录 `platform-v2` 执行：

```bash
python3 tools/quotation_template_preflight.py
```

读取 JSON 输出后，按 `docs/QUOTATION_TASK_PROTOCOL.md` 记录：

- `canonical_template`
- `template_id`
- `template_sha256`
- `approval_status`
- `approved_changes`
- `frozen_regions`
- `artifact_kinds`
- `acceptance_gates`

在完成该记录前，不读取客户 Query、不调用 LLM、不生成制品。需要正式客户报价时，额外执行：

```bash
python3 tools/quotation_template_preflight.py --require-production
```

命令失败就停止正式出单。不得绕过、吞掉或降级错误。

## 唯一模板真源

- V1 资产：`api/geo_platform/quotations/assets/quotation-template-v1.docx`
- V1 manifest：`api/geo_platform/quotations/assets/quotation-template-v1.yaml`
- V1 结构合同：`api/geo_platform/quotations/assets/quotation-template-v1.structure.json`
- V1 原始来源：`../client-sbaq/报价单-盛邦-final(2).docx`
- V1 SHA-256：`90ae5beb10ab3bacea3b706a2068945f828e275784e99da6b72dc44f8b0d9913`

V1 是用户批准的四服务原始模板和 V2 唯一派生起点。V1 的 `status=approved` 不表示五服务正式生成已获批准；以 manifest 的 `production_use.status` 为准。

V2 只有在用户明确批准 `docs/QUOTATION_TEMPLATE_V1_TO_V2_REDLINE.md` 后才可从 V1 产生并冻结。不得从空白 Word 重建 V2，也不得把红线预览标记为批准模板。

禁止使用以下内容定义模板：

- `client-sbaq/报价单.docx`
- 两份文件名含 `20260820` 的旧样稿
- legacy `renderer.py` 通过 `Document()` 自创的版式
- skill 内的模板副本；本 skill 不保存 DOCX

## 模板状态与失败条件

正式生成必须同时满足：模板文件存在、SHA 与 manifest 一致、版本已知、`approval_status=approved` 且 `production_use.status=approved`。否则按下列代码失败关闭：

- `quotation_template_missing`
- `quotation_template_hash_mismatch`
- `quotation_template_version_unknown`
- `quotation_template_not_approved`
- `quotation_template_manifest_invalid`

当前 Phase A 中，V1 身份 preflight 应通过，`--require-production` 必须返回 `quotation_template_not_approved`。这是正确状态，不得改成通过。

只读 V1 审计可在普通 preflight 通过后继续；任务记录使用 `artifact_kinds: [none]`，不适用的门必须显式写成 `not_applicable`，不能省略。正式生成不得使用这一例外。

## 业务配置边界

五项原子服务为测试、找拉踩帖、找被拉踩帖、官网分析、发帖提排名。每项单独定价；套餐只是快捷组合：

- 已做过 GEO 的效果评测：1、2、3、4。
- 未做 GEO 的最小验证：1（基线）→3→4（官网命中条件项）→5→1（复测）。

未选服务不得出现在页面或报价单中。最小验证中的服务 1 可按两轮计价；服务 4 保留条件价语义。没有获批价格时必须使用 `pending`。不得编造 API/App、排名、拉踩、官网引用、发帖或优化效果。

套餐名称、套餐副标题、执行顺序和六列表格都不是已批准的模板区域；除非红线明确获批，不得加入客户制品。

## 三类制品

三类制品必须来自同一获批模板体系，并保留适用章节的原顺序和样式：

- `quote_table`：标题、客户与日期、批准主表、五条商务条款和签署区。
- `query_appendix`：附录一方法论、附录二原始 Query 与 A/B/C 变体；只有所选服务需要时才包含附录三新增优化 Query。
- `complete`：先 `quote_table`，再按批准顺序组合适用附录。

不得新增独立封面或“服务输入、执行与交付说明”附录。不得让未选服务通过摘要、前置条件、交接说明或附录泄漏。

## 生成纪律

1. 以结构化配置展开原子服务、数量、轮次、条件价和获批价格。
2. renderer 必须加载获批模板或由该模板派生的批准制品模板；正式文档根节点禁止无条件 `Document()`。
3. 模板缺失或不合规时不得回退到自创版式。
4. 只在所选制品需要动态 Query 时调用 LLM；纯报价表不调用 LLM。
5. 输出记录 `template_id`、`template_version` 和 `template_sha256`。
6. 生成 DOCX 后转换 PDF 进行视觉验收；“能打开”或字节 SHA 完整不等于模板合规。

## 验收命令

先运行模板身份门：

```bash
python3 tools/quotation_template_preflight.py --require-production
```

Phase A 红线预览可用以下命令复现；它只生成带红色未批准标识的审阅产物：

```bash
.venv/bin/python tools/build_quotation_v2_redline_preview.py
```

V2 获批并实现后，执行报价相关测试和前端契约检查：

```bash
.venv/bin/pytest -q tests/unit/test_quotation_generation.py tests/unit/test_generate_quotation_cli.py
pnpm --filter @geo/api-client test -- src/index.test.ts
pnpm --filter @geo/api-client typecheck
pnpm --filter @geo/operations-web test -- QuotationGenerator.test.tsx
pnpm --filter @geo/operations-web typecheck
```

还必须执行获批实现提供的归一化 OOXML golden test 与 PDF 视觉回归，检查 A4、字体字号、表格边界、页眉页脚、分页、截断、重叠、孤标题和空白页。任一门失败都停止交付。

最终交付同时报告制品路径与 SHA、模板 ID/版本/SHA、用户批准的变化、冻结区域、三类制品派生证据、自动化测试和逐页视觉结果。生产端没有完成带授权的真实下载前，不得宣称端到端正式出单已验收。
