# Project Structure

本项目当前按“本地 LLM-Wiki RAG 工作台”组织，运行数据和可提交源码分层管理。

## 可提交源码与配置

```text
app/                    FastAPI 后端、RAG 链路、入库 Worker、SQLite/Qdrant/Ollama 服务封装
frontend/               Next.js 前端工作台
scripts/                验收、烟测、调试脚本
tests/                  后端单元/集成测试
samples/                小样例文件
docs/                   项目说明、评测设计、人工审核材料
compose.yaml            基础 Docker Compose
compose.gpu.yaml        NVIDIA GPU 叠加配置
Dockerfile              API/Worker 镜像
README.md               启动与 Demo 流程说明
```

## 本地运行数据

```text
knowledge/raw/          原始资料，系统不覆盖
knowledge/parsed/       解析结果，可重建
knowledge/evidence/     证据块、manifest，可重建
knowledge/wiki/         Wiki 草稿/页面，可重建
knowledge/drafts/       待审核派生知识
knowledge/error-book/   入库错误记录
knowledge/evaluations/  本地验收报告
data/                   SQLite 等运行时数据
```

`knowledge/*` 和 `data/*` 默认不提交。需要版本化的评测设计放在 `docs/evaluation/`。

## 当前核心链路

```text
Upload/Raw
  -> Parse & Clean
  -> Chunk/Evidence
  -> SQLite FTS5 + Qdrant Dense + Wiki + Graph
  -> Evidence Selector
  -> Coverage Gate
  -> Compact Prompt
  -> Ollama Answer
  -> Citations / Quality Report
```

## GPU 启动约定

有 NVIDIA GPU 时始终使用：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up -d
```

并用下面命令确认：

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml exec ollama ollama ps
```

`PROCESSOR` 应显示 `100% GPU`。
