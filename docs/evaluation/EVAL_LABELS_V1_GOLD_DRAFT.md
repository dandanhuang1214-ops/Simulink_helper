# Evaluation Labels v1 Gold Draft

这份表是“源文档驱动”的 gold draft，不从当前检索结果反推。

标注方法：

1. 先判断问题是否应该回答、拒答或改写。
2. 再写必须覆盖的事实点。
3. 再从 evidence DB 的原始证据块中选择可直接支撑这些事实点的 chunk id。
4. 当前答案评分暂不填写，避免把当前系统输出污染为 gold standard。

| ID | Type | Question | Expected Mode | Decision | Expected Evidence | Must Cover | Forbidden | Answer Score | Faithfulness | Citation | Notes |
|---|---|---|---|---|---|---|---|---:|---:|---:|---|
| Q001 | definition | Stateflow 是什么？ | rag | pass | 1070,1071,1072 | Stateflow 用于有限状态机/状态图；可连接 Simulink 模型；可仿真状态与转移 | 泛化成所有控制算法工具；无证据扩展 |  |  |  | gold evidence 来自 Stateflow FSM 章节 |
| Q002 | definition | Simulink 中求解器是什么？ | rag | pass | 1,3,4 | 求解器用于仿真数值求解；固定步长/可变步长；部署目标影响选择 | 把求解器说成代码生成器 |  |  |  | 当前有本地 solver md，足够做 demo gold |
| Q003 | definition | ARXML 在 AUTOSAR 工作流中有什么作用？ | rag | pass | 638,641,646,264,266 | ARXML 是 AUTOSAR XML 描述；可被导入；代码生成可导出 ARXML；关联组件/接口/配置 | 把 ARXML 说成普通 C/C++ 源代码 |  |  |  | 可接受多个 AUTOSAR 章节组合 |
| Q004 | relation | Simulink 和 AUTOSAR 的关系是什么？ | rag | pass | 146,155,205,638,274 | Simulink 可表示/开发 AUTOSAR 组件行为；AUTOSAR 组件通过端口/runnable 等映射；可生成 ARXML/C 代码 | 只讲 Simulink 或只讲 AUTOSAR 标准 |  |  |  | 关系题允许多证据组合 |
| Q005 | relation | Stateflow 和 Simulink 的关系是什么？ | rag | pass | 1070,1072,1373,2132 | Stateflow chart 可用于 Simulink 模型中的状态/事件逻辑；可连接 Simulink block；可参与仿真/代码生成 | 说 Stateflow 完全独立于 Simulink |  |  |  | 需要同时命中 Stateflow 与 Simulink 连接/代码生成证据 |
| Q006 | relation | AUTOSAR component 如何映射到 Simulink 模型？ | rag | pass | 146,155,202,205,274 | AUTOSAR component 封装算法并通过端口通信；Simulink 模型/子系统/函数表示 runnable 或行为；代码生成产生 runnable/IRV 等 | 用 solver 作为主证据；跳过组件映射 |  |  |  |  |
| Q007 | relation | ARXML 导入 Simulink 后通常会形成什么模型或组件？ | rag | pass | 264,266,267,271,272,783,864 | ARXML importer 可创建 component/composition 的 Simulink 表示；createComponentAsModel/createCompositionAsModel/importFromARXML；可生成/更新模型 | 说导入后直接得到完整量产 ECU |  |  |  |  |
| Q008 | comparison | fixed-step solver 和 variable-step solver 有什么区别？ | rag | pass | 1,3,4,1373 | fixed-step 固定基本步长；适合实时/代码生成/确定性；variable-step 适合连续系统离线分析和误差控制；采样时间约束 | 混淆 fixed 与 variable；绝对化结论 |  |  |  |  |
| Q009 | comparison | Stateflow 和普通 Simulink blocks 在控制逻辑建模上有什么区别？ | rag | pass | 1070,1072,1726,14,963 | Stateflow 适合状态/转移/事件驱动逻辑；Simulink block 适合系统布局、组件建模、信号流/模型化设计 | 说普通 blocks 不能做逻辑；说 Stateflow 不能连接 Simulink |  |  |  |  |
| Q010 | comparison | AUTOSAR 代码生成和普通 C/C++ 代码生成有什么区别？ | rag | pass | 638,641,646,665,666,667,205,382 | AUTOSAR 代码生成会生成符合 AUTOSAR 的 C/C++ 与 ARXML；涉及组件/runnable/接口映射；可用 SIL/PIL 验证生成代码；普通 C/C++ 不天然包含 AUTOSAR 描述与映射约束 | 无证据泛谈普通 C/C++；把 SIL/PIL 说成所有 AUTOSAR 项目的绝对强制步骤；夸大“普通 C/C++ 一定不能模块化” |  |  |  | gold 不要求同时命中全部证据，但必须覆盖 ARXML/映射/验证边界 |
| Q011 | procedure | 如何把 AUTOSAR XML 描述导入到 Simulink？ | rag | pass | 264,266,267,271,272,274,276,783,786,864 | 使用 ARXML importer；createComponentAsModel/createCompositionAsModel/importFromARXML/updateModel；可从 ARXML 创建 component/composition 的 Simulink 模型；导入后仍需开发/配置模型 | 脑补具体 UI 按钮、弹窗、保存流程；只说打开用户指南；把导入和导出混为一谈 |  |  |  | 这是重点 gold，必须优先引用 Import AUTOSAR XML/Component/Composition 章节 |
| Q012 | procedure | Simulink 模型生成代码前通常需要检查哪些配置？ | rag | pass | 4,155,202,205,638,641,646 | 检查 solver/采样时间/部署目标；AUTOSAR target 或 code generation 设置；接口/组件映射；生成 ARXML/C 代码配置 | 列出未入库工具链细节；跳过 solver 与接口配置 |  |  |  |  |
| Q013 | procedure | 如何选择 fixed-step solver 或 variable-step solver？ | rag | pass | 1,3,4,1373 | 实时代码生成/确定性优先 fixed-step；连续系统离线分析可 variable-step；检查采样时间、误差容限、目标硬件计算能力 | 绝对化说某一种总是最好 |  |  |  |  |
| Q014 | citation | 请说明 fixed-step solver 的适用场景，并给出引用。 | rag | pass | 1,3,4,1373 | fixed-step 适合实时仿真、代码生成、确定性执行、离散控制器采样周期对齐 | 无引用结论；引用非 solver 证据 |  |  |  |  |
| Q015 | citation | 请说明 AUTOSAR composition 导入 Simulink 的基本含义，并给出引用。 | rag | pass | 783,786,788,864,885 | 从 ARXML/software composition 创建 Simulink model；composition 聚合组件；可用于组合/仿真/后续代码或 ARXML 导出 | 混入无关服务配置作为主证据 |  |  |  |  |
| Q016 | citation | 请回答 Stateflow 中 state 和 transition 的关系，并给出引用。 | rag | pass | 1070,1072,1720,1726,1735 | state 表示系统模式/状态；transition 表示条件满足时状态间切换；Stateflow chart/transition table 可展示状态转移逻辑 | 非法引用格式；大段英文粘贴；把 state transition table 完全等同于全部 Stateflow |  |  |  | 当前证据对基本 state/transition 够用，但细节题需要更多 Stateflow 原始章节 |
| Q017 | multi-hop | 如果我要做一个车载控制器，Simulink、Stateflow、AUTOSAR 和代码生成大概如何串起来？ | rag | pass | 963,1070,146,155,205,274,382,638 | Simulink 做模型化设计和算法；Stateflow 做状态/事件控制逻辑；AUTOSAR component/runnable/ports 做软件架构映射；Simulink Coder/Embedded Coder 生成 C/ARXML | 装作给出完整量产流程；忽略证据边界和工具许可条件 |  |  |  | 多跳题允许使用 4-6 个核心证据，不要求一条证据覆盖全部 |
| Q018 | multi-hop | 固定步长求解器、实时仿真和代码生成之间有什么关系？ | rag | pass | 1,3,4,1373,202,155 | fixed-step 支持确定性执行周期；实时/代码生成通常需要固定采样/基本步长；目标硬件需在基本步长内完成计算 | 把 variable-step 说成实时部署默认选择；忽略目标硬件约束 |  |  |  |  |
| Q019 | multi-hop | AUTOSAR composition 导入后，Simulink 模型如何继续参与验证和代码生成？ | rag | pass | 783,786,788,864,885,378,382,665,666 | composition 可从 ARXML 导入成 Simulink/架构模型；后续开发/仿真组件；可生成 ARXML 和算法代码；可用 SIL/PIL 或 test harness 做验证 | 脑补未入库验证平台；说 composition-level model 一定可直接生成所有代码 |  |  |  |  |
| Q020 | boundary | Simulink 可以直接生成 ROS 2 节点吗？ | refusal | pass |  | 当前知识库缺 ROS 2 相关证据；应拒绝基于常识回答，并建议导入 ROS/Simulink ROS Toolbox 文档 | 基于常识直接回答能/不能；编造 ROS 2 工作流 |  |  |  | 边界题 expected_evidence 可为空 |
| Q021 | boundary | 当前知识库是否包含 MathWorks R2027a 的 Stateflow 新特性？ | refusal | pass |  | 当前资料不包含 R2027a Stateflow 新特性；应说明资料范围不足 | 编造 R2027a 新功能 |  |  |  |  |
| Q022 | boundary | 你能直接定位我私有 Simulink 模型里的 bug 原因吗？ | refusal | pass |  | 未提供私有模型、日志或错误信息；不能直接定位 bug；可建议上传模型/日志或描述现象 | 假装已看到用户私有模型 |  |  |  |  |
| Q023 | boundary | 如果没有导入 Simulink Test 文档，你能详细回答 Simulink Test 的所有 API 吗？ | refusal | pass |  | 未导入 Simulink Test API 文档时不能详细回答所有 API；可建议导入文档或只回答已覆盖内容 | 编造 API 清单 |  |  |  |  |
| Q024 | boundary | 当前知识库是否包含某个第三方 AUTOSAR 工具的专有配置？ | refusal | pass |  | 当前知识库无法确认第三方专有配置；应说明资料范围并建议导入对应手册 | 用 MathWorks AUTOSAR 官方证据硬答第三方专有配置 |  |  |  |  |
| Q025 | robustness | 你好啊，你能做什么？ | direct | pass |  | 直接说明助手能力；不启动 RAG；不引用技术证据 | 胡乱引用技术证据；长篇技术事实 |  |  |  |  |

## 下一步

1. 用这份 gold draft 对当前 fast 报告进行答案人工评分。
2. 对 Q010/Q011/Q016/Q017-Q019 优先打 `answer_score / faithfulness_score / citation_score`。
3. 后续脚本可用 `Expected Evidence` 计算 Recall@K、MRR 和 Evidence Precision。

