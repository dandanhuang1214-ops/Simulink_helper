# Scripts

## Demo 启动与检查

```text
start-demo.ps1                       一键启动GPU/CPU Demo并等待服务就绪
check-demo.ps1                       检查Web、依赖服务、知识资产数量和GPU Compose状态；-Full统计完整图谱
```

脚本分为三类：验收、烟测、调试。

## 验收脚本

```text
run_answer_quality_acceptance.py      回答质量验收：引用、中文回答、拒答、速度
run_eval_preview_v1.py                25题正式预评测：fast/full profile 对照
evaluate_gold_metrics.py              用 gold labels 计算检索/引用指标
summarize_manual_eval_labels.py       汇总人工标注表：通过率、平均分、待修复题目
validate_eval_regression_v2.py        校验 v2 固定回归集数量、领域分布和 Gold 隔离规则
evaluate_retrieval_regression_v2.py   在冻结 v2 Gold 上评测运行时检索链路并输出 MD/JSON
evaluate_structural_relevance_v2.py   用不可变 chunk 顺序和标题层级审计精确 Gold 假阴性
evaluate_answer_regression_v2.py      从冻结 v2 生成答案、引用与人工审核清单（不把本地 Judge 当真值）
run_round3_retrieval_acceptance.py    检索链路验收：BM25/Dense/Wiki/Graph/Selector
run_round3_generation_spotcheck.py    生成链路抽样验收
run_round3_acceptance.py              旧版综合验收脚本，保留作参考
```

## 烟测脚本

```text
smoke_coverage_gate.py                覆盖门槛/拒答 smoke test
smoke_evidence_selector.py            证据选择器 smoke test
smoke_graph_retrieval.py              图谱扩展 smoke test
smoke_relationship_selector.py        关系类问题选择 smoke test
smoke_prompt_compaction.py            Prompt 压缩 smoke test
smoke_generation_fast.py              单题生成速度 smoke test
smoke_generation_cn.py                中文三题生成 smoke test
smoke_generation_solver.py            求解器单题速度 smoke test
```

## 调试脚本

```text
inspect_question_retrieval.py         打印单个问题的候选证据、选择器结果和 trace
```

## 运行示例

脚本通常在 API 容器中运行：

```powershell
docker cp scripts\run_answer_quality_acceptance.py simulink-assistant-api:/tmp/run_answer_quality_acceptance.py
docker compose -f compose.yaml -f compose.gpu.yaml exec -T api env PYTHONPATH=/app python /tmp/run_answer_quality_acceptance.py
```

运行生成的报告默认写入 `knowledge/evaluations/`，该目录属于本地运行资产，不提交到 Git。
