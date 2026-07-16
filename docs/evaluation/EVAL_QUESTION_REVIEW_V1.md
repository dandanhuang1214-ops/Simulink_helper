# Evaluation Question Review v1

这份问题集是“待人工审核”的评测草案，不直接作为训练集。目标是先让你检查问题质量：是否覆盖核心场景、是否太泛、是否需要拆分、是否能对应到当前知识库资料。

审核建议：

- 保留：问题清晰，当前知识库应该能回答。
- 修改：问题有价值，但表达太宽或需要限定版本/范围。
- 删除：当前阶段不适合测，或容易诱导模型编造。
- 补充期望证据：标记应该命中的文档、Wiki 页或证据块。

## 评测维度

| 类型 | 目标 |
|---|---|
| definition | 定义类问题，要求简洁准确 |
| relation | 关系/映射类问题，要求跨概念组织 |
| procedure | 流程/步骤类问题，要求按步骤回答 |
| comparison | 对比类问题，要求避免泛泛而谈 |
| boundary | 无答案/边界问题，要求拒答 |
| citation | 引用质量问题，要求证据覆盖 |

## 第一批建议问题

| ID | 类型 | 问题 | 期望行为 | 审核意见 |
|---|---|---|---|---|
| Q001 | definition | Stateflow 是什么？ | 用 2-3 句解释，引用 Stateflow 证据。 |  |
| Q002 | definition | Simulink 中求解器是什么？ | 解释求解器作用，引用 solver/Simulink 证据。 |  |
| Q003 | definition | ARXML 在 AUTOSAR 工作流中有什么作用？ | 解释 ARXML 与组件/接口/代码生成关系。 |  |
| Q004 | relation | Simulink 和 AUTOSAR 的关系是什么？ | 说明建模、映射、代码生成/ARXML 的关系。 |  |
| Q005 | relation | Stateflow 和 Simulink 的关系是什么？ | 说明 Stateflow chart 如何作为 Simulink 模型中的控制逻辑。 |  |
| Q006 | relation | AUTOSAR component 如何映射到 Simulink 模型？ | 命中 AUTOSAR mapping/code mappings 证据。 |  |
| Q007 | relation | ARXML 导入 Simulink 后通常会形成什么模型或组件？ | 回答软件组件/组成体模型，不混入 solver 证据。 |  |
| Q008 | comparison | fixed-step solver 和 variable-step solver 有什么区别？ | 对比适用场景、步长变化、实时/离线仿真。 |  |
| Q009 | comparison | Stateflow 和普通 Simulink blocks 在控制逻辑建模上有什么区别？ | 对比状态转移/事件驱动和连续/模块化信号建模。 |  |
| Q010 | comparison | AUTOSAR 代码生成和普通 C/C++ 代码生成有什么区别？ | 说明 ARXML、组件映射、AUTOSAR 约束。 |  |
| Q011 | procedure | 如何把 AUTOSAR XML 描述导入到 Simulink？ | 给出概念性步骤，引用 importer/导入章节。 |  |
| Q012 | procedure | Simulink 模型生成代码前通常需要检查哪些配置？ | 回答求解器、代码生成目标、接口/映射等。 |  |
| Q013 | procedure | 如何选择 fixed-step solver 或 variable-step solver？ | 按实时性、连续状态、代码生成、精度成本组织。 |  |
| Q014 | citation | 请说明 fixed-step solver 的适用场景，并给出引用。 | 必须有引用，不能只凭常识。 |  |
| Q015 | citation | 请说明 AUTOSAR composition 导入 Simulink 的基本含义，并给出引用。 | 命中 AUTOSAR composition/import 证据。 |  |
| Q016 | citation | 请回答 Stateflow 中 state 和 transition 的关系，并给出引用。 | 命中 Stateflow 状态/转换证据。 |  |
| Q017 | multi-hop | 如果我要做一个车载控制器，Simulink、Stateflow、AUTOSAR 和代码生成大概如何串起来？ | 综合回答，并说明证据边界。 |  |
| Q018 | multi-hop | 固定步长求解器、实时仿真和代码生成之间有什么关系？ | 解释确定性执行与代码生成关系。 |  |
| Q019 | multi-hop | AUTOSAR composition 导入后，Simulink 模型如何继续参与验证和代码生成？ | 跨导入、配置、验证/代码生成组织回答。 |  |
| Q020 | boundary | Simulink 可以直接生成 ROS 2 节点吗？ | 当前知识库缺 ROS2 证据时拒答。 |  |
| Q021 | boundary | 当前知识库是否包含 MathWorks R2027a 的 Stateflow 新特性？ | 若无 R2027a 资料，应拒答或说明未覆盖。 |  |
| Q022 | boundary | 你能直接定位我私有 Simulink 模型里的 bug 原因吗？ | 应说明需要模型文件/日志/知识库证据。 |  |
| Q023 | boundary | 如果没有导入 Simulink Test 文档，你能详细回答 Simulink Test 的所有 API 吗？ | 应拒绝详细 API 编造。 |  |
| Q024 | boundary | 当前知识库是否包含某个第三方 AUTOSAR 工具的专有配置？ | 应拒答或说明当前资料范围。 |  |
| Q025 | robustness | 你好啊，你能做什么？ | 不走 RAG 或轻量回答，不能胡乱引用技术证据。 |  |

## 需要你重点审核的问题

1. Q017-Q019 是否太大，是否要拆成更小的工程问题。
2. Q010 当前知识库是否有足够资料支撑“普通 C/C++ 代码生成”的对比。
3. Q016 当前 Stateflow 资料是否足够回答 state/transition 细节。
4. 是否要加入版本约束，例如 R2024a、R2026a。
5. 是否要加入英文提问测试，验证中英文资料能力。

## 失败样本记录模板

| 日期 | 问题 ID | 当前答案问题 | 期望行为 | 关联文档/证据 | 处理建议 |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
