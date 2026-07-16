# Evaluation Labels v1 Template

使用方法：

1. 打开本地报告 `knowledge/evaluations/EVAL_PREVIEW_V1_FAST.md`。
2. 对照下面每一题填写人工标注。
3. 不确定的证据先留空，不要为了当前答案硬填。
4. 标注完成后，再用这份表计算 Recall@K、MRR、Evidence Precision、Answer Accuracy 等指标。

评分建议：

- `answer_score`：0 错误 / 1 部分可用 / 2 可接受
- `faithfulness_score`：0 编造 / 1 部分支撑 / 2 证据充分支撑
- `citation_score`：0 错误或无引用 / 1 部分覆盖 / 2 覆盖关键结论

| ID | Type | Question | Expected Mode | Decision | Expected Evidence | Must Cover | Forbidden | Answer Score | Faithfulness | Citation | Notes |
|---|---|---|---|---|---|---|---|---:|---:|---:|---|
| Q001 | definition | Stateflow 是什么？ | rag |  |  | 有限状态机/状态图；用于事件驱动或反应式逻辑；引用 Stateflow 证据 | 无证据扩展到所有控制系统 |  |  |  |  |
| Q002 | definition | Simulink 中求解器是什么？ | rag |  |  | 数值求解；固定步长/可变步长；仿真场景 | 把求解器说成代码生成器 |  |  |  |  |
| Q003 | definition | ARXML 在 AUTOSAR 工作流中有什么作用？ | rag |  |  | AUTOSAR 描述文件；组件/接口/配置；导入导出或代码生成关系 | 把 ARXML 说成普通源代码 |  |  |  |  |
| Q004 | relation | Simulink 和 AUTOSAR 的关系是什么？ | rag |  |  | Simulink 建模；AUTOSAR 组件/接口映射；ARXML/代码生成 | 只讲 Simulink，不讲 AUTOSAR 映射 |  |  |  |  |
| Q005 | relation | Stateflow 和 Simulink 的关系是什么？ | rag |  |  | Stateflow chart 可作为 Simulink 模型中的控制逻辑；状态/事件逻辑 | 把 Stateflow 说成完全独立于 Simulink |  |  |  |  |
| Q006 | relation | AUTOSAR component 如何映射到 Simulink 模型？ | rag |  |  | 组件、端口、runnable/subsystem 或 code mappings | 混入 solver 作为主要证据 |  |  |  |  |
| Q007 | relation | ARXML 导入 Simulink 后通常会形成什么模型或组件？ | rag |  |  | component/composition/model；初始 Simulink 表示；后续配置 | 说成直接生成完整可运行 ECU |  |  |  |  |
| Q008 | comparison | fixed-step solver 和 variable-step solver 有什么区别？ | rag |  |  | 步长固定/可变；实时/代码生成 vs 离线精度；计算成本 | 混淆 fixed 和 variable |  |  |  |  |
| Q009 | comparison | Stateflow 和普通 Simulink blocks 在控制逻辑建模上有什么区别？ | rag |  |  | 状态/转移/事件驱动 vs block/signal/连续或模块化建模 | 把普通 blocks 说成不能做逻辑 |  |  |  |  |
| Q010 | comparison | AUTOSAR 代码生成和普通 C/C++ 代码生成有什么区别？ | rag |  |  | AUTOSAR 约束；ARXML；组件/接口映射；代码生成差异 | 无证据地泛谈普通 C/C++；夸大 SIL/PIL 强制性 |  |  |  |  |
| Q011 | procedure | 如何把 AUTOSAR XML 描述导入到 Simulink？ | rag |  |  | ARXML importer/createCompositionAsModel/createComponentAsModel；导入后形成模型；证据边界 | 脑补具体按钮、弹窗、保存步骤 |  |  |  |  |
| Q012 | procedure | Simulink 模型生成代码前通常需要检查哪些配置？ | rag |  |  | 求解器；代码生成目标；接口/映射；模型配置 | 列出未入库工具链细节 |  |  |  |  |
| Q013 | procedure | 如何选择 fixed-step solver 或 variable-step solver？ | rag |  |  | 实时性/代码生成；连续状态；精度与成本；离散控制 | 绝对化说某一种总是最好 |  |  |  |  |
| Q014 | citation | 请说明 fixed-step solver 的适用场景，并给出引用。 | rag |  |  | 实时仿真；代码生成；确定性执行/采样周期 | 无引用结论 |  |  |  |  |
| Q015 | citation | 请说明 AUTOSAR composition 导入 Simulink 的基本含义，并给出引用。 | rag |  |  | composition 导入；组件/架构模型；后续仿真/代码生成 | 混入无关 AUTOSAR 服务配置 |  |  |  |  |
| Q016 | citation | 请回答 Stateflow 中 state 和 transition 的关系，并给出引用。 | rag |  |  | state；transition；条件/事件导致状态变化；图表/状态机 | 非法引用格式；大段英文粘贴 |  |  |  |  |
| Q017 | multi-hop | 如果我要做一个车载控制器，Simulink、Stateflow、AUTOSAR 和代码生成大概如何串起来？ | rag |  |  | Simulink 算法建模；Stateflow 控制逻辑；AUTOSAR 映射；代码生成 | 装作给出完整量产流程 |  |  |  |  |
| Q018 | multi-hop | 固定步长求解器、实时仿真和代码生成之间有什么关系？ | rag |  |  | 固定步长；确定性；实时仿真；代码生成 | 把 variable-step 说成实时默认选择 |  |  |  |  |
| Q019 | multi-hop | AUTOSAR composition 导入后，Simulink 模型如何继续参与验证和代码生成？ | rag |  |  | 导入后模型；配置/仿真；SIL/PIL 或代码生成；ARXML/代码 | 脑补未入库验证工具细节 |  |  |  |  |
| Q020 | boundary | Simulink 可以直接生成 ROS 2 节点吗？ | refusal |  |  | 当前知识库缺 ROS 2 证据；建议导入相关文档 | 基于常识直接回答能/不能 |  |  |  |  |
| Q021 | boundary | 当前知识库是否包含 MathWorks R2027a 的 Stateflow 新特性？ | refusal |  |  | 当前资料版本不含 R2027a；拒绝编造新特性 | 编造 R2027a 新功能 |  |  |  |  |
| Q022 | boundary | 你能直接定位我私有 Simulink 模型里的 bug 原因吗？ | refusal |  |  | 需要模型文件/日志/上下文；当前不能直接定位 | 假装已看到用户私有模型 |  |  |  |  |
| Q023 | boundary | 如果没有导入 Simulink Test 文档，你能详细回答 Simulink Test 的所有 API 吗？ | refusal |  |  | 未导入资料则不能详细回答 API；建议导入文档 | 编造 API 清单 |  |  |  |  |
| Q024 | boundary | 当前知识库是否包含某个第三方 AUTOSAR 工具的专有配置？ | refusal |  |  | 当前知识库不能确认第三方专有配置；说明资料范围 | 使用 AUTOSAR 官方证据硬答第三方专有配置 |  |  |  |  |
| Q025 | robustness | 你好啊，你能做什么？ | direct |  |  | 直接说明助手能力；不检索技术证据 | 胡乱引用技术证据 |  |  |  |  |

## 人工失败样本

| Date | ID | Problem | Expected Behavior | Evidence | Suggested Fix |
|---|---|---|---|---|---|
|  |  |  |  |  |  |

