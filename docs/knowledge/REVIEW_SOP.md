# 知识审核 SOP

## 角色分工

- operator/analyst 聚合观察、创建提案并补充证据。
- reviewer 核对证据、批准/拒绝/延期提案并创建或批准变更集。
- admin 作为独立发布者激活或回滚 release，并运行 connector。
- 普通项目请求只能解析和提交观察，不能发布。

同一人不能完成提案与裁决，也不能创建并批准同一变更集。发布者还必须独立于该变更集的创建者和批准者。

## 每条候选的审核步骤

1. 核对 namespace、domain、聚合键、所有表面形式、观察次数和来源多样性。
2. 确认 observation 只包含脱敏摘要和不可逆引用。
3. 明确提案操作是 create、update、merge、split 还是 retire。
4. 分开填写 identity、relation、roll-up、eligibility 和 epistemic status。
5. 添加支持与反对证据，并记录 publisher、claim、content hash、获取时间和 trust tier。
6. 确认证据 URI 是公开 HTTPS；不要把登录态页面或客户材料标成 public。
7. 对照领域规范和当前 policy version 做裁决。
8. 将批准提案转换成相对 active release 的 change set。
9. 处理所有三方合并冲突；conflicts 非空时不能批准。
10. 由第二名 reviewer 批准 change set。
11. 由独立 admin 发布，检查 domain quality gate、manifest hash 和 parent release。
12. 激活后执行 deterministic smoke、报告读取和 connector 对账。

## 拒绝、延期和重开

`rejected` 表示证据支持不采纳。`deferred` 表示当前无法决定或等待外部事实。二者都不能在后续批次被自动重新提案。

重开必须给出至少十个字符的原因，并满足以下之一：出现新的 evidence version；policy version 改变；或 reviewer 明确批准人工重开。重开产生新的 audit event，旧 adjudication 保留。

## 质量抽样

每次发布至少抽查：所有高影响 merge/split；所有公开新对象；所有 `scope_required` eligibility；所有模型首次提出的 existing-ID 绑定；所有反对证据；以及腾讯云、华为云、NSFOCUS、BJCA、新大陆/CTID 金标回归。

若 identity 正确但 eligibility 不确定，应批准身份关系、延期竞品 assertion。不得为了让榜单“看起来完整”同时批准两个维度。

## 审核 SLA

指标接口披露 candidate backlog、review-ready 数量和 oldest candidate age。SLA 必须由业务负责人根据实际积压设定。当前实现只测量，不在无数据时拍脑袋写死阈值。
