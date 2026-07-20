# 评测集 v3 正式验收总结

## 验收身份

- 评测集：`EVAL_REGRESSION_V3.json`
- SHA-256：`bded80c0c594d73c0d9b763397e31f7648d24b11a884fdbeb6a22e9b161f94e7`
- Gold：source-first 标注，76 个唯一证据块
- 运行方式：Fast 检索链路，关闭问题改写，保留 RRF、Wiki、Dense 策略、图谱和证据选择器
- 正式验收只运行一次；此后不得使用 v3 结果继续调参

## 基线与正式结果

| 指标 | 盲基线 | 正式验收 | 变化 |
|---|---:|---:|---:|
| Candidate Any-Gold Hit@5 | 0.333 | 0.208 | -0.125 |
| Candidate Any-Gold Hit@10 | 0.417 | 0.500 | +0.083 |
| Candidate Any-Gold Hit@20 | 0.542 | 0.542 | 0.000 |
| Candidate Any-Gold Hit@30 | 0.542 | 0.542 | 0.000 |
| Candidate Recall@10 | 0.217 | 0.245 | +0.028 |
| Candidate Recall@20 | 0.348 | 0.387 | +0.039 |
| Candidate Recall@30 | 0.348 | 0.403 | +0.055 |
| MRR@30 | 0.190 | 0.218 | +0.028 |
| nDCG@10 | 0.147 | 0.164 | +0.017 |
| Selected Any-Gold Hit | 0.292 | 0.208 | -0.084 |
| Selected Gold Recall | 0.127 | 0.106 | -0.021 |
| 中位检索耗时 | 406 ms | 378 ms | -28 ms |
| p95 检索耗时 | 14765 ms | 7382 ms | -7383 ms |

## 结论

1. 加权查询计划提高了 Top-10、总体 Gold recall、MRR 和 nDCG，且没有降低 Hit@30。
2. 多标签领域保留消除了 selector 中 AUTOSAR 对跨域问题的硬折叠；相应语言模式已有单元测试覆盖。
3. 最终证据选择没有同步改善，表明当前主要瓶颈已从“候选池是否包含证据”部分转移到“如何从候选池选出 4–6 个证据”。
4. Coverage Gate 通过率仍为 0.875，而正式验收中有 11 个 exact-Gold miss；该 Gate 只能作为生成前安全护栏，不能充当检索质量指标。
5. exact chunk Gold 很严格，相邻证据可能同样可回答，因此本报告用于比较链路，不等同最终答案准确率。

## 后续约束

- 不再根据 v3 的单题结果修改扩展词、阈值、selector 或提示词。
- selector、中文覆盖判定和边界意图的下一轮开发继续使用 v2 或新建开发集。
- 下一次正式验收必须创建 source-first 的 v4 holdout。
- 完整回答质量仍需单独评测引用正确性、事实一致性、证据覆盖、拒答准确率和人工可用性。

## 报告位置

- `knowledge/evaluations/RETRIEVAL_REGRESSION_V3_BLIND_BASELINE.md`
- `knowledge/evaluations/RETRIEVAL_REGRESSION_V3_FORMAL_ACCEPTANCE.md`
