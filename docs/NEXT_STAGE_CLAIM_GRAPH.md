# 下一阶段：跨文档 Claim 图谱

## 决策

当前 `v0.2.0-demo` 不继续扩展跨文档关系。Demo 保留三种可审计连接：

1. 同一文档相邻 Chunk 的 `previous_id / next_id` 顺序链；
2. Wiki `[E:n]` 到原始证据的引用链；
3. Wiki 实体关系及其 `wiki_refs / evidence_refs` 支撑链。

不同文档的 Chunk 可以通过共同实体或实体关系间接关联，但当前不把向量相似度直接持久化为知识关系。

## 进阶机制

后续以 Claim 为最小知识单元：

```text
Document A / Chunk A
  -> supports Claim A
  -> mentions Canonical Entity
  -> typed relation
  -> Canonical Entity
  <- supports Claim B
  <- Document B / Chunk B
```

每条跨文档关系至少保存：

- 标准化实体和别名；
- Claim 原文与规范化表述；
- `supports / extends / contradicts / requires / part_of` 等关系类型；
- 来源文档、页码、Chunk ID和坐标；
- 抽取方式、置信度、审核状态和版本；
- 建立、更新和失效时间。

## 建边规则

- 向量相似度只用于提出候选，不直接建立永久边。
- 实体规范化成功且 Claim 语义通过验证后才能建立候选边。
- `contradicts`、版本覆盖和数值结论必须人工审核。
- 低置信度边不得参与回答，只能出现在审核队列。
- 跨文档边必须能回溯到至少一个原始证据块；生产阶段可要求两个独立来源。

## 检索方式

1. BM25 + Dense 找到锚点 Chunk。
2. Selector 确定锚点证据。
3. 仅对关系题、多跳题或锚点证据不足的题执行一跳 Claim 扩展。
4. 扩展证据再次经过领域、版本、关系类型和证据充分性过滤。
5. 最终回答分别引用每个 Claim 的原始证据，不引用“图谱本身”。

## 进入条件

- Demo 冻结版本稳定。
- 新建独立 v4 holdout，避免继续使用 v3.1 调参。
- 完成人工审核的数据结构和冲突处理规则。
- 至少准备一组真实跨文档多跳问题。
