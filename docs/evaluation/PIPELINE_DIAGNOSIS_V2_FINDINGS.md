# v2 链路诊断结论

## 诊断边界

- 只使用 v2 开发集，未再次运行或调整 v3。
- 未修改 Top-K、chunk、selector、模型、提示词或阈值。
- 结果同时参考 exact Gold、同章节/邻居、预声明章节和人工证据摘要；exact Gold 不视为穷举相关文档。

## 第一轮结果

| 初始机器归因 | 数量 | 人工复核后的解释 |
|---|---:|---|
| D：exact Gold 进入最终证据 | 9 | 检索链路基本就绪，后续需单独审查生成答案 |
| C：Gold 到 Top-10 但未被选择 | 3 | AUT-02 基本可用；STF-01、STF-03 的替代证据只部分支持问题 |
| B：Gold 仅在 Top-10 之后 | 1 | COV-03 已选择可支持“收集+报告”的替代章节，主要是 exact Gold 非穷举 |
| A：exact Gold 未进入候选 | 4 | AUT-03 命中更直接的有效替代证据 E:483；AUT-01、STF-02、TST-01 仍存在真实召回或覆盖缺口 |

综合人工判断：约 11 题证据基本就绪，4 题部分覆盖，2 题明显不足。该结论是证据就绪度，不等同最终答案准确率。

## 已确认问题

### 1. Coverage Gate 不能代表中文问题的证据充足性

- 17/17 题全部通过 Coverage Gate。
- 其中仍有 4 个 exact Gold 召回失败，人工复核后至少 2 题证据明显不足。
- 多个中文问题的原因是 `no_specific_terms`；当前关键术语提取忽略长度小于 3 的 token，而中文分词主要产生双字 token。
- 该 Gate 当前只能作为很弱的安全护栏，不能驱动 Dense fallback，也不能作为检索质量指标。

### 2. Dense Fast Path 对概念/关系问题过于激进，但 Dense 不是单独解法

四个 A 类问题全部跳过 Dense：

- AUT-01：`skip:dense_fast_path_autosar`
- AUT-03：`skip:dense_fast_path_autosar`
- STF-02：`skip:dense_fast_path_stateflow`
- TST-01：`skip:dense_fast_path_simulink`

强制 Full/Dense 后：

- STF-02、TST-01 从 exact miss 恢复到候选 Hit@30。
- AUT-01、AUT-03 的 exact Gold 仍未恢复。
- 四题最终 exact Gold 均未进入最终证据。

结论：Dense fallback 有价值，但必须与问题方面覆盖和 selector 联动；直接“所有问题都开 Dense”不能解决最终证据选择。

### 3. 问题角色存在误分类

- AUT-01 是“组件建模、接口映射、代码生成之间的关系”，但因为出现“流程”被标成 `procedure`。
- STF-02 询问 Stateflow 与 Simulink 的交互关系，但因为出现“如何”被标成 `procedure`。
- 当前角色判断采用单一 cue 优先级，尚不能稳定表示 `relationship + procedure` 等复合意图。

角色误分类会影响候选深度、流程阶段补齐和 selector 的证据角色偏好。

### 4. Selector 的问题是“方面覆盖”，不只是排序

- STF-01 的 Gold 排名第 4，仍被同章节但不够直接的块替换。
- STF-03 的 Gold 排名第 1，却因文档配额/阶段选择被替换为部分相关证据。
- AUT-02 选择的 E:265、E:269 可支持导入工作流，但对 component/composition 的覆盖不如完整 Gold 集。

因此 selector 需要覆盖问题的不同方面，例如定义、接口、操作、限制，而不是仅凭总分、文档配额和固定流程阶段挑选。

### 5. chunk 偏小是真问题，但不是当前首要召回根因

- 全库 chunk 中位数为 228 tokens，1584/3606 小于 200 tokens。
- 17 题中只有 3 题满足严格的 fragmentation-risk 条件：SIM-03、STF-01、STF-02。
- 真实召回失败中的 AUT-01、AUT-03、TST-01，其 Gold chunk 中位数并不小。

父级/邻居上下文适合改善答案完整度以及 STF-01、STF-02，但不能替代基础召回。

### 6. Top-K 过大不是当前主因

- 只有 COV-03 一题属于 Gold 排名 10 以后。
- 该题最终已经选到可支持收集和报告的替代章节。
- 直接缩小 Top-K 可能丢失流程题后半段证据。

当前应先修角色、fallback 触发和方面覆盖，再测试自适应 Top-K。

### 7. 本地模型换载与 rerank 是延迟长尾来源

- AUT-02 使用本地 rerank，约 12.7 秒。
- COV-01 使用本地 rerank，约 11 秒。
- TST-03 需要 Dense，约 7 秒。
- 大多数纯 BM25/Wiki 快路径为 200–550 ms。

这说明中位速度已经可用，P95 主要受 Embedding/对话模型换载及本地 rerank 影响。

## 建议的单一下一步实验

先不重建 chunk，也不直接缩小 Top-K。使用 v2 开发集实现并测试“问题方面覆盖诊断”，但暂不改变最终选择结果：

1. 将问题解析为一个或多个角色：definition、relationship、procedure、comparison、constraint。
2. 为每个角色提取 2–4 个方面，例如 AUT-01 的 component modeling、interface mapping、code generation。
3. 统计候选和最终证据分别覆盖了哪些方面。
4. 仅生成诊断分数和缺失方面，不参与排序。
5. 人工确认方面识别可靠后，再让 selector 使用该信号。

这样可以同时解释 Coverage Gate 偏乐观、复合意图误分类和 selector 漏掉问题一部分的现象，而且不会为了某一题追加特例。

## 相关报告

- `knowledge/evaluations/PIPELINE_DIAGNOSIS_V2.md`
- `knowledge/evaluations/PIPELINE_DIAGNOSIS_V2_STRUCTURAL.md`
- `knowledge/evaluations/PIPELINE_DIAGNOSIS_V2_A_FAILURES_FULL.md`
