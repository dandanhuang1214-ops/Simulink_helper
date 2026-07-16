# Evaluation Metrics

本项目评测分为两条线：

1. **交互体验评测**：关注本地 Demo 是否快、稳、可追溯。
2. **完整 RAG 能力评测**：关注召回率、证据排序、答案准确性和拒答边界。

由于当前机器是 4GB 显存，本地交互模式会使用 fast profile 避免频繁切换 embedding/chat 模型；正式检索能力评测需要使用 full profile。

## Retrieval Profiles

| Profile | Dense | 目的 |
|---|---|---|
| `fast` | 明确术语问题可跳过 Dense | 本地 Demo 交互体验，减少 4GB 显存下模型换载 |
| `full` | 强制启用 Dense | 正式评估 BM25 + Dense + Wiki + Graph 的完整召回能力 |

运行示例：

```powershell
python /tmp/run_eval_preview_v1.py --profile fast
python /tmp/run_eval_preview_v1.py --profile full
```

## Retrieval Metrics

| 指标 | 含义 | 计算方式 |
|---|---|---|
| Recall@K | 正确证据是否进入 Top-K | Top-K 候选中命中人工标注证据则为 1 |
| MRR | 正确证据排名是否靠前 | 第一个正确证据排名的倒数 |
| Evidence Precision@K | Top-K 中相关证据比例 | 相关证据数 / K |
| Domain Accuracy | 是否命中正确领域 | 候选证据主领域是否符合问题领域 |
| Dense Contribution Rate | Dense 对召回的贡献 | 正确证据是否由 Dense 通道召回 |
| Wrong-domain Rate | 错误领域混入率 | 例如 AUTOSAR 问题混入 solver 证据 |

## Answer Metrics

| 指标 | 含义 | 计算方式 |
|---|---|---|
| Answer Accuracy | 答案是否正确 | 人工 0-5 分 |
| Faithfulness | 是否忠于证据 | 是否出现证据外断言 |
| Citation Coverage | 事实句是否都有引用 | 有引用事实句 / 全部事实句 |
| Completeness | 是否覆盖期望要点 | 命中 expected answer points 的比例 |
| Refusal Accuracy | 应拒答问题是否拒答 | 正确拒答数 / 应拒答数 |
| False Refusal Rate | 有答案问题是否被误拒答 | 误拒答数 / 可回答问题数 |
| Language Quality | 中文表达是否符合要求 | 中文问题是否主要中文回答、是否过长 |

## Performance Metrics

| 指标 | 含义 |
|---|---|
| Retrieval Latency | 检索阶段耗时 |
| Generation Latency | 生成阶段耗时 |
| Total Latency | 总耗时 |
| Dense Used | 是否触发 Dense |
| Model Switch Risk | 是否可能发生 embedding/chat 换载 |
| GPU Processor | Ollama 是否为 `100% GPU` |

## Recommended Evaluation Flow

### 1. Question Review

先人工审核 `EVAL_QUESTION_REVIEW_V1.md`：

- 问题是否清楚。
- 当前知识库是否应该能回答。
- 是否需要拆小。
- 是否需要加版本范围。

### 2. Manual Annotation

为保留的问题补充：

```yaml
id: Q004
type: relation
question: Simulink 和 AUTOSAR 的关系是什么？
expected_docs:
  - AUTOSAR Blockset User Guide R2024a
expected_evidence_keywords:
  - Code Mappings
  - AUTOSAR component
  - runnable
  - ARXML
expected_answer_points:
  - Simulink 可用于建模 AUTOSAR 组件行为
  - Code Mappings 将 Simulink 元素映射到 AUTOSAR 元素
  - 可生成代码和 ARXML 描述
refusal_expected: false
```

### 3. Fast vs Full Comparison

同一批问题分别跑：

```text
fast profile：评估本地交互体验
full profile：评估完整检索能力
```

报告中对比：

- Recall@K 是否下降。
- Answer Accuracy 是否下降。
- Latency 节省多少。
- 哪些问题必须依赖 Dense。

### 4. Ablation Study

后续可加入四种检索消融：

| 模式 | 说明 |
|---|---|
| `bm25_only` | 只看关键词召回 |
| `dense_only` | 只看语义召回 |
| `hybrid_fast` | 当前本地 Demo 交互模式 |
| `hybrid_full` | 完整 BM25 + Dense + Wiki + Graph |

这样可以回答两个关键问题：

1. Dense 是否真的提升召回率？
2. fast profile 是否以可接受的准确率损失换来了明显速度提升？
