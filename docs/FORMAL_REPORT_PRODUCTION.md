# 正式报告生产链

## 目标

`/platform/operations/services/formal-reports` 从已落库证据冻结正式报告。一次生产请求可选
多项服务，但每项服务独立生成 DOCX、PDF 和审计 manifest；服务 1 还可交付
样本索引 XLSX 与证据 ZIP。生产链不把不同服务拼成一份 DOCX。

所有服务共用同一生产记录的时间窗、冻结事实和总体哈希，但各自保存服务编号、
稳定服务代码、报告版本、产物哈希和下载地址。

## 服务目录版本

新生产请求必须显式使用 `quotation_services_v2`：

| 服务 | 稳定代码                       | 独立报告   | 额外输入                |
| ---- | ------------------------------ | ---------- | ----------------------- |
| 1    | `ranking_test`                 | 测试       | 平台项目冻结样本        |
| 2    | `outbound_disparagement_audit` | 找拉踩帖   | 同租户、同品牌 SOP 项目 |
| 3    | `inbound_disparagement_audit`  | 找被拉踩帖 | 平台项目回答与公开信源  |
| 4    | `official_site_audit`          | 官网分析   | 平台项目官网与引用证据  |
| 5    | `content_publishing_pilot`     | 发帖提排名 | SOP 项目、发布前/后窗口 |

历史数据没有目录标记时按 `legacy_report_services_v1` 解释：旧服务 2 是内容生态风险，
旧服务 3 是官网引用能效，旧服务 4 是试点前后对比。读取、审批、重渲染和下载历史产物时
都使用该旧映射，不会把旧数字静默改成新含义。

## 新请求契约

```json
{
  "project_pub_id": "prj_...",
  "services": [1, 2, 3, 4, 5],
  "service_catalog_version": "quotation_services_v2",
  "sop_project_pub_id": "spr_...",
  "window_start": "2026-07-01",
  "window_end": "2026-08-20",
  "before_window": { "start": "2026-07-01", "end": "2026-07-15" },
  "after_window": { "start": "2026-08-01", "end": "2026-08-20" },
  "document_status": "internal_review",
  "candidate_group_strategy": "preregistered_scope_v1",
  "version": "V1.0",
  "prepared_by": "编制人",
  "prepared_date": "2026-08-20"
}
```

- 服务须去重、升序且位于当前目录允许集。
- 新服务 2 或 5 被选中时，`sop_project_pub_id` 必填；两者均未选时禁止携带该字段。
- SOP 项目必须属于同一租户，且标准品牌或别名必须与平台项目品牌匹配。
- 新服务 5 必须提供不重叠的发布前/后窗口；旧目录仍由旧服务 4 使用该窗口。
- 只有落在前测结束之后、后测开始之前的公开发布记录才能进入服务 5 干预证据。

## 生产与签发

```text
创建请求 → 冻结各服务事实 → 逐服务渲染 DOCX/PDF/manifest
         → 保存内部审核稿或交付候选稿 → 人工批准 → 重渲染签发版
```

每项服务在签发前单独执行证据门。证据不足时可以产生内部审核稿来披露缺口，但不能把
缺失值当作 0，也不能越过人工批准签发客户版。服务 5 只报告发布证据与同矩阵前后变化，
不把时间先后自动解释为因果，也不承诺排名一定提升。

服务 1 只按事实快照里实际留证的采集渠道出具结论。消费者网页结果不能改名为手机 App，
网页内部请求也不能冒充模型开放 API；未具备独立 API/App 采集矩阵时，报告不得声称已经
完成这两类渠道的差异验证。

## API 与产物

- `POST /api/v2/reports/formal-productions` 创建带目录版本的生产请求。
- `GET /api/v2/reports/formal-productions` 和单条查询返回 `service_catalog_version`。
- 每个 output 同时返回 `service_number` 与 `service_code`，调用方不应只靠数字猜语义。
- 下载仍使用 `/artifacts/{service_number}/{format_name}`，产物归属由生产记录的目录版本限定。
- 运营页面只为新请求发送 `quotation_services_v2`，但会按旧目录标签展示历史记录。
