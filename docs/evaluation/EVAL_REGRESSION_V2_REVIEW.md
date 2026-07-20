# Evaluation Regression v2 - Frozen Review

2026-07-17 用户确认全部保留。问题在运行评测前设计，17 道 RAG 题按原文完成来源标注，共绑定 57 个唯一证据块；标注过程未使用当前 Top-K 结果反推。

## 分布

| Domain | Count | Cases |
|---|---:|---|
| Simulink | 4 | definition, comparison, procedure, boundary |
| AUTOSAR | 4 | relationship, procedure, definition+procedure, boundary |
| Stateflow | 4 | definition, relationship, comparison, boundary |
| Simulink Test | 4 | definition+procedure, procedure, comparison, multi-hop |
| Simulink Coverage | 4 | definition+procedure, comparison, procedure, comparison |

## 已冻结问题

| ID | Question | Mode | Gold Evidence | Review |
|---|---|---|---|---|
| SIM-01 | Simulink 模型中的 block、signal 和 subsystem 分别起什么作用？ | rag | 998, 1018, 1020 | keep |
| SIM-02 | fixed-step solver 和 variable-step solver 有什么区别，分别适合什么场景？ | rag | 1, 2, 3, 4 | keep |
| SIM-03 | 从空白模型开始，创建一个简单 Simulink 模型并运行仿真的基本流程是什么？ | rag | 1003, 1005, 1007, 1009 | keep |
| SIM-04 | R2027a 的 Simulink 新增了哪些求解器功能？ | refusal | — | keep |
| AUT-01 | Simulink 与 AUTOSAR 在组件建模、接口映射和代码生成流程中是什么关系？ | rag | 146, 193, 205, 638 | keep |
| AUT-02 | 如何把 AUTOSAR ARXML 中的软件组件或 composition 导入为 Simulink 模型？ | rag | 264, 266, 267, 783 | keep |
| AUT-03 | AUTOSAR runnable 是什么，在 Simulink 中通常如何映射和配置？ | rag | 199, 193, 205, 220 | keep |
| AUT-04 | 当前知识库能否给出 Vector DaVinci Developer 某个私有项目的完整 AUTOSAR 配置步骤？ | refusal | — | keep |
| STF-01 | Stateflow chart 中的 state、transition、event 和 action 分别表示什么？ | rag | 1070, 1073, 1075, 1079 | keep |
| STF-02 | Stateflow chart 如何与 Simulink 模型中的信号和仿真执行相互作用？ | rag | 1076, 1077, 1078 | keep |
| STF-03 | Stateflow 状态机与普通 Simulink block 信号流在控制逻辑建模上有什么区别？ | rag | 1070, 998, 1020 | keep |
| STF-04 | 没有提供模型文件和运行日志时，能否直接判断私有 Stateflow chart 为什么发生错误转移？ | refusal | — | keep |
| TST-01 | Simulink Test harness 是什么，它与被测模型之间如何同步和管理？ | rag | 2466, 2470, 2517, 2518 | keep |
| TST-02 | 如何把 Simulink Test 的测试用例或 Test Sequence 步骤链接到需求？ | rag | 2453, 2454, 2456 | keep |
| TST-03 | baseline test 和 equivalence test 的目的与比较对象有什么区别？ | rag | 2901, 2826, 2891, 2882 | keep |
| TST-04 | 如何使用 Test harness 对同一组件执行 Normal、SIL 或 PIL 测试，并比较结果？ | rag | 2483, 2675, 2694, 2684, 2685, 2686 | keep |
| COV-01 | MCDC 覆盖是什么，Simulink Coverage 如何记录和分析它？ | rag | 3154, 3288, 3285, 3287 | keep |
| COV-02 | decision coverage、condition coverage 和 MCDC 分别检查什么？ | rag | 3153, 3154 | keep |
| COV-03 | 如何为 Simulink 模型收集覆盖率数据并生成覆盖率报告？ | rag | 3285, 3286, 3287, 3457 | keep |
| COV-04 | Coverage Filter 中 Excluded 和 Justified 有什么区别，什么时候需要重新仿真？ | rag | 3509, 3513, 3314, 3528, 3530 | keep |

## 冻结规则

- 后续调参与代码修改不得改变问题、`must_cover`、`forbidden` 或 Gold 来迎合结果。
- 精确 chunk 指标是严格指标；相邻 chunk 或同章节的有效证据需要单独报告，不能偷偷改进 Gold。
- 先评检索，再评证据选择、回答事实与引用，最后评拒答边界。
