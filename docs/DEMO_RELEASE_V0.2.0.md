# Simulink LLM-Wiki Demo v0.2.0

## 定版范围

- 单用户、本地部署、中文工作台。
- Next.js + FastAPI + SQLite + Qdrant + Ollama。
- 文档导入、结构切分、BM25/Dense/Wiki/Graph混合检索。
- 会话历史、最近6轮上下文、低风险偏好记忆和资料筛选。
- 流式回答、停止、重试、反馈、证据引用和PDF原页定位。
- Wiki阅读、证据引用、相邻Chunk导航和轻量知识图谱。

## 冻结检索结果

开发回归集：`docs/evaluation/EVAL_REGRESSION_V3_1.json`。

| 指标 | v0.2.0 |
|---|---:|
| Hit@10 | 0.833 |
| Hit@30 | 0.917 |
| Recall@30 | 0.688 |
| MRR@30 | 0.482 |
| nDCG@10 | 0.403 |
| Selected Hit | 0.625 |
| pass / partial / miss | 14 / 8 / 2 |
| 中位检索耗时 | 约580 ms |

v3.1 已用于诊断和优化，因此只能作为开发回归集；进阶版正式验收必须创建新的 v4 holdout。

## 已验证

- 后端测试：72 passed。
- Next.js生产构建：通过。
- API、Worker、Web、Ollama、Qdrant运行正常。
- 证据API可返回同文档上一块和下一块。
- Wiki图谱已生成确定性 `next_chunk` 边。
- 原始文档、SQLite和Qdrant均有持久化路径。

## 已知限制

- 扫描PDF、复杂表格、公式和图片语义尚未进入强制验收。
- 本地2B Judge只用于观察，不是生产级裁判。
- LLM Reranker默认关闭；只有显式开启且低置信度复合题才有资格调用。
- 不同文档Chunk主要通过实体关系间接关联，尚未实现Claim级审核图谱。
- v3.1剩余两道严格miss均为AUTOSAR题；存在相关替代证据，但没有修改冻结Gold。
- 不包含登录、RBAC、多人协作、公网部署、限流和审计账户。
- 2026-07-22的生产依赖审计报告含1项moderate和2项high的传递依赖告警（PostCSS、Sharp，经Next.js引入）；当前无可靠的兼容升级路径。由于Demo只绑定本机使用且不处理不可信前端图片/CSS输入，本次不阻塞定版，但公网部署前必须重新审计并消除告警。

## 运行与备份

```powershell
.\scripts\start-demo.ps1 -SkipBuild
.\scripts\check-demo.ps1 -Full
.\scripts\backup-demo.ps1
```

备份脚本在线备份SQLite并下载Qdrant集合快照到 `knowledge/backups/<时间>/`。`knowledge/raw` 仍在宿主机目录；如果需要防范硬盘损坏，应将整个 `knowledge/raw` 另行复制到其他磁盘。

严禁使用：

```powershell
docker compose down -v
```

## 下一阶段

跨文档Claim图谱见 `docs/NEXT_STAGE_CLAIM_GRAPH.md`。该机制不属于本次Demo定版范围。
