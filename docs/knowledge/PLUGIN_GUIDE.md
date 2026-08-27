# 领域包与 Connector 开发指南

通用核心只认识 `RuntimeRequest`、`Decision`、`ObservationDraft`、release、merge、gateway 和 persistence 协议。领域语义必须留在 domain pack 中。

## Domain pack 接口

一个领域包注册唯一 `domain_id`，并固定 policy、prompt 和 tool version。它实现：

1. `release_ref`：选择并披露当前知识版本。
2. `deterministic_resolve`：规范化输入并执行确定性本体解析。
3. `build_model_prompt`：生成版本化结构化提示词和可用工具声明。
4. `validate_model_output`：拒绝非法结构、虚构 ID、非法 evidence ref 和越权输出。
5. `observations`：把未决或模型推理安全地投影为幂等观察。
6. `validate_release`：执行领域本体、证据和质量门。
7. `validate_release_impact`：按领域风险检查发布前回放、退化预算或明确的低风险豁免。
8. `project_release`：把通用对象与 assertion 编译成领域 read model。

normalizer/resolver 位于 `deterministic_resolve`。inference strategy 位于 prompt 与 output validator。evidence collector 通过 prompt-declared、deployment-registered tool 提供。review policy 由领域证据规则和通用四眼状态机共同构成。quality gate 在发布前强制调用。projector 只能生成确定性 artifact。

新领域包不能在 import 时自行注册。部署通过 `DomainRegistry.register` 显式注册，以便测试和多进程行为一致。

## 最小实现检查表

- domain id、schema 和状态含义有版本。
- 规范键能处理 Unicode 和大小写，但不依靠模糊字符串相似度决定现实身份。
- 确定性解析对未知项 abstain。
- 模型 schema 区分事实、假设和替代解释。
- existing ID 必须来自当前 release。
- evidence ref 必须来自请求允许集合。
- observation 不包含原始敏感上下文。
- release gate 能拒绝重复 ID、断裂引用和缺证据公开对象。
- 高影响领域的 impact gate 能拒绝没有评测集哈希、时间截点或超出新错误预算的发布。
- projector 的相同输入产生逐字节相同输出。
- 至少提供 deterministic、model failure、cache invalidation 和 release contract 测试。

`SourceTypeFixturePack` 是最小非品牌示例。它通过与品牌包相同的 observation、proposal、adjudication、change set 和 release 流程，发布后再从指定 release 读取结果，证明核心没有品牌或 SiliconIndex 假设。它的 impact gate 明确记录“不改变排名或实体归并，因此不要求品牌历史回放”，而不是在公共服务中写死品牌例外。

## 工具接口

领域 prompt 只能声明本任务需要的工具。部署必须按相同名称注册 callable。gateway 限制最大工具轮次、参数 JSON 形状、返回体大小和安全摘要。模型不能仅通过输出一个工具名就获得任意代码、文件或网络访问。

工具结果进入下一轮模型上下文。持久 trace 只保存工具名、状态和时延，不保存凭证或完整私有结果。

## Connector 接口

`KnowledgeConnector` 定义 `import_release`、`export_changes` 和 `reconcile`。connector 必须返回 adapter/version、operation、release lineage、cursor、结果和稳定错误码。HTTP 只负责把请求写入 `connector_run`；`run_knowledge_connector_queue.py` 在独立 systemd worker 中用行锁和 `skip_locked` 认领，避免请求进程执行长时间同步。

导入必须先验证 manifest、hash、重复 ID、引用和版本单调性。导出只能包含 approved、允许公开、已脱敏且有证据的对象。reconcile 必须使用 base/upstream/local 三方合并；冲突要进入 change set，不得任选一边。

`SiliconIndexAdapter` 是第一个实现。它属于 `domain/siliconindex`，不允许出现在通用核心中。静态站仓库仍保持无数据库、无运行 API。

## 发布新插件

先增加 domain pack 和 contract tests，再在应用 registry 显式注册。随后创建初始不可变 release，运行隔离 PostgreSQL 闭环，完成只读影子回放，最后才允许正式消费者选择该 domain。插件升级必须同时改变相应 policy/prompt/tool/schema version，使旧语义缓存自然失效。
