# Simulink_helper

面向 Simulink 资料的本地私有 LLM-Wiki。使用 FastAPI、Next.js、SQLite、Qdrant、Ollama 和 Docker Compose。

当前冻结版本：`v0.2.0-demo`。定版说明见 [`docs/DEMO_RELEASE_V0.2.0.md`](docs/DEMO_RELEASE_V0.2.0.md)。

- SQLite 保存文档记录、导入任务、会话、消息和低风险偏好记忆等业务数据。
- Qdrant 保存文档分块向量和检索元数据。
- Ollama 运行本地对话模型与 Embedding 模型。

已验证的默认参数：Embedding 为 1024 维，Qdrant 集合为 `simulink_documents`，对话默认关闭思考模式以降低延迟。

## 启动

### 一键启动（推荐）

在PowerShell中运行：

```powershell
.\scripts\start-demo.ps1
.\scripts\check-demo.ps1
```

需要同时加载并统计完整知识图谱时，运行 `.\scripts\check-demo.ps1 -Full`。

脚本默认使用GPU Compose，负责检查Docker、校验Compose、构建容器、等待API就绪并检查本地模型。仅在没有可用NVIDIA GPU时使用：

```powershell
.\scripts\start-demo.ps1 -Cpu
```

如镜像已经构建完成，可以使用 `-SkipBuild` 加快再次启动。

### 手动启动

如果本机有 NVIDIA GPU，推荐始终使用 GPU 叠加文件启动。否则 Ollama 可能会在 CPU 上运行，回答会从几秒变成一两分钟。

```powershell
docker compose config
docker compose build api
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

启动后确认 Ollama 是否真的使用 GPU：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml exec ollama ollama ps
```

其中 `PROCESSOR` 应显示 `100% GPU`。如果显示 `100% CPU`，通常表示启动时没有叠加 `compose.gpu.yaml`，请用上面的 GPU 命令重建 Ollama。

只有在没有 NVIDIA GPU 或 GPU Docker 支持暂时不可用时，才使用 CPU 模式：

```powershell
docker compose up -d
```

默认使用清华 PyPI 镜像下载 Python 依赖；如需切回官方源，可修改 `.env` 中的 `PIP_INDEX_URL`。

下载模型：

```powershell
docker exec simulink-assistant-ollama ollama pull qwen3.5:2b
docker exec simulink-assistant-ollama ollama pull qwen3-embedding:0.6b
```

验证：

```powershell
Invoke-RestMethod http://localhost:18080/health/live
Invoke-RestMethod http://localhost:18080/health/ready
```

API 文档：http://localhost:18080/docs

Web 工作台：http://localhost:13000

## Demo 流程

1. 在“导入”页上传 MD、TXT、DOCX 或 PDF。
2. Worker 保存原文件，执行 PyMuPDF/DOCX/Markdown 解析、结构切分、Embedding、BM25 与 Wiki 编译。
3. 在 Wiki 页审核草稿，在检索调试页查看 Dense/BM25/RRF，在问答页查看引用与 Judge 结果。

知识资产位于 `knowledge/`，其中 `raw` 永不由系统覆盖；`parsed`、`evidence`、`wiki`、`drafts`、`error-book` 均可重建。

当前Demo支持带文本层PDF；扫描PDF的Docling/OCR/VLM解析作为下一轮可选扩展接入。

## 当前Demo能力

- 三栏聊天工作台、持久会话、最近6轮上下文和资料范围筛选。
- 规则闲聊路由与本地RAG路由，支持流式回答、停止、重试和回答反馈。
- SQLite FTS5 BM25、Qdrant Dense向量、Wiki与图谱辅助召回、证据选择和引用校验。
- 文档、Wiki、PDF原页、证据引用和知识图谱联动。
- 图谱支持类型过滤、搜索、缩放、拖动、一跳邻居与快速布局。
- API重启或连接中断后自动收敛遗留生成状态，并保留已生成的部分答案。

完整交付检查见 [`docs/DEMO_ACCEPTANCE.md`](docs/DEMO_ACCEPTANCE.md)。

创建SQLite与Qdrant在线备份：

```powershell
.\scripts\backup-demo.ps1
```

跨文档Claim级知识连接已记录为进阶机制，见 [`docs/NEXT_STAGE_CLAIM_GRAPH.md`](docs/NEXT_STAGE_CLAIM_GRAPH.md)，不进入当前Demo。

## 入库规则 v1

- 管线版本：`structure-token-v1`
- 正文目标：估算 600 tokens；硬上限 800 tokens
- 正文重叠：同一标题路径、同一PDF页内约 100 tokens
- 标题或页码变化：强制切块，不跨边界重叠
- 表格：保持表头，按完整行切分，跨块重复上一行作为上下文
- 公式和图片说明：保持原子块
- Dense文本：`标题路径 + 正文`；BM25索引标题、标题路径和正文
- 每次导入在 `knowledge/evidence/<document_id>/manifest.json` 保存参数和切分结果

调整规则后通过 `POST /api/documents/{id}/reindex` 可从不可变raw自动重建，无需重新上传原文件。

停止服务时不要添加 `-v`，否则会删除模型和向量数据：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml down
```
