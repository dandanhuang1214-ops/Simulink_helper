"use client";

import cytoscape, { Core, ElementDefinition } from "cytoscape";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import "./layout-fix.css";
import SourceFilter from "./source-filter";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:18080";

type Conversation = {
  id: number;
  title: string;
  pinned: boolean;
  source_filter?: { document_ids?: number[]; releases?: string[] };
};

type Message = {
  id: number;
  role: "user" | "assistant" | string;
  content: string;
  status: string;
  citations: any[];
  evaluation?: any;
  retrieval_trace?: any;
  latency_ms?: number | null;
};

type DocumentRow = {
  id: number;
  title: string;
  status: string;
  filename: string;
  release?: string;
  parse_mode?: string;
  media_type?: string;
  error?: string | null;
  created_at?: string;
};

type WikiSummary = {
  slug: string;
  title: string;
  status: string;
  type: string;
  updated_at: string;
};

type WikiDetail = {
  slug: string;
  title: string;
  content: string;
  status: string;
  links: string[];
};

type GraphNode = {
  id: string;
  type: "document" | "wiki_page" | "entity" | "concept" | "evidence" | string;
  label: string;
  status?: string;
  slug?: string;
  chunk_id?: number;
  document_id?: number;
  document_title?: string;
  page?: number;
  page_type?: string;
  entity_type?: string;
  wiki_count?: number;
  evidence_count?: number;
  confidence?: number;
};

type GraphEdge = {
  source: string;
  target: string;
  label: string;
  relation_label?: string;
  confidence?: number;
  weight?: number;
};

type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats?: Record<string, number>;
};

type WikiContext = {
  entities: GraphNode[];
  evidence: GraphNode[];
  relatedPages: GraphNode[];
  backlinks: GraphNode[];
  citations: number[];
};

function citationMarkdown(value: string) {
  return value.replace(/\[E:(\d+)\]/g, "[E:$1](evidence:$1)");
}

function stageText(stage?: string) {
  const map: Record<string, string> = {
    queued: "排队中",
    parsing: "解析原文件",
    chunking: "切分证据块",
    indexing: "写入 SQLite FTS 与 Qdrant",
    wiki: "编译 Wiki 草稿",
    completed: "完成",
    failed: "失败",
  };
  return stage ? map[stage] || stage : "";
}

function formatMs(value?: number) {
  if (value === undefined || value === null) return "";
  return value > 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

function evaluationText(evaluation?: any) {
  if (!evaluation || typeof evaluation.passed !== "boolean") return "等待证据检查";
  return evaluation.passed ? "已通过证据校验" : "需要人工复核";
}

function channelLabel(channel: string) {
  const map: Record<string, string> = { dense: "向量", bm25: "BM25" };
  return map[channel] || channel;
}

function scoreText(value?: number) {
  if (typeof value !== "number") return "";
  return value.toFixed(4);
}

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [wikiPages, setWikiPages] = useState<WikiSummary[]>([]);
  const [wikiPage, setWikiPage] = useState<WikiDetail | null>(null);
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [stageKind, setStageKind] = useState<"idle" | "connecting" | "retrieval" | "generation" | "judge" | "done" | "error">("idle");
  const [evidence, setEvidence] = useState<any[]>([]);
  const [chosen, setChosen] = useState<any>(null);
  const [view, setView] = useState("chat");
  const abort = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  async function refresh() {
    const [conversationRows, documentRows, wikiRows, graphRows] = await Promise.all([
      fetch(`${API}/api/conversations`).then((r) => r.json()),
      fetch(`${API}/api/documents`).then((r) => r.json()),
      fetch(`${API}/api/wiki/pages`).then((r) => r.json()),
      fetch(`${API}/api/wiki/graph`).then((r) => r.json()),
    ]);
    setConversations(conversationRows);
    setDocuments(documentRows);
    setWikiPages(wikiRows);
    setGraphData(graphRows);
    if (!conversationId && conversationRows[0]) setConversationId(conversationRows[0].id);
  }

  async function loadConversation(id: number) {
    const data = await fetch(`${API}/api/conversations/${id}`).then((r) => r.json());
    setMessages(data.messages || []);
  }

  async function loadWikiPage(slug: string) {
    const data = await fetch(`${API}/api/wiki/pages/${slug}`).then((r) => r.json());
    setWikiPage(data);
    setChosen(null);
  }

  async function openEvidence(chunkId: number, fallback?: any) {
    const cached = evidence.find((item) => item.chunk_id === chunkId);
    if (cached) {
      setChosen(cached);
      return;
    }
    const response = await fetch(`${API}/api/evidence/${chunkId}`);
    if (response.ok) {
      const data = await response.json();
      setChosen(data);
      setEvidence((rows) => (rows.some((item) => item.chunk_id === data.chunk_id) ? rows : [data, ...rows]));
      return;
    }
    if (fallback) setChosen(fallback);
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (conversationId) loadConversation(conversationId);
  }, [conversationId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, messages[messages.length - 1]?.content, stage]);

  async function createConversation() {
    const row = await fetch(`${API}/api/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "新对话" }),
    }).then((r) => r.json());
    setConversations((rows) => [row, ...rows]);
    setConversationId(row.id);
    setMessages([]);
    setView("chat");
  }

  async function saveFilter(source_filter: { document_ids?: number[]; releases?: string[] }) {
    if (!conversationId) return;
    const row = await fetch(`${API}/api/conversations/${conversationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_filter }),
    }).then((r) => r.json());
    setConversations((rows) => rows.map((item) => (item.id === conversationId ? row : item)));
  }

  async function pollEvaluation(messageId: number) {
    for (let i = 0; i < 20; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const row = await fetch(`${API}/api/messages/${messageId}`).then((r) => (r.ok ? r.json() : null));
      if (!row?.evaluation || typeof row.evaluation.passed !== "boolean") continue;
      setMessages((rows) =>
        rows.map((message) => (message.id === messageId ? { ...message, evaluation: row.evaluation } : message)),
      );
      setStage("后台评估完成");
      return;
    }
  }

  async function send() {
    if (!question.trim() || busy) return;

    let id = conversationId;
    if (!id) {
      const row = await fetch(`${API}/api/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "新对话" }),
      }).then((r) => r.json());
      id = row.id;
      setConversationId(id);
      setConversations((rows) => [row, ...rows]);
    }

    const text = question.trim();
    const tempUserId = -Date.now();
    const tempAssistantId = tempUserId - 1;
    let liveAssistantId = tempAssistantId;

    setQuestion("");
    setBusy(true);
    setStage("正在连接本地 API…");
    setStageKind("connecting");
    setStage("正在连接本地 API…");
    setMessages((rows) => [
      ...rows,
      { id: tempUserId, role: "user", content: text, status: "completed", citations: [] },
      { id: tempAssistantId, role: "assistant", content: "", status: "generating", citations: [] },
    ]);

    abort.current = new AbortController();
    try {
      const response = await fetch(`${API}/api/conversations/${id}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
        signal: abort.current.signal,
      });
      if (!response.body) throw Error("SSE unavailable");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";

        for (const eventText of events) {
          const event = eventText.match(/^event: (.+)$/m)?.[1];
          const dataLine = eventText.split("\n").find((line) => line.startsWith("data: "));
          if (!event || !dataLine) continue;
          const data = JSON.parse(dataLine.slice(6));

          if (event === "message.created") {
            liveAssistantId = data.assistant_message_id;
            setMessages((rows) =>
              rows.map((message) => {
                if (message.id === tempUserId) return { ...message, id: data.user_message_id, role: "user" };
                if (message.id === tempAssistantId) {
                  return { ...message, id: data.assistant_message_id, role: "assistant" };
                }
                return message;
              }),
            );
          }
          if (event === "stage.started") {
            if (data.stage === "retrieval") setStageKind("retrieval");
            if (data.stage === "generation") setStageKind("generation");
            if (data.stage === "judge") setStageKind("judge");
            setStage(data.label || "正在处理…");
          }
          if (event === "stage.completed") {
            if (data.stage === "retrieval") {
              setStage(`检索完成：${data.candidate_count ?? 0} 个候选 · ${formatMs(data.elapsed_ms)}`);
            } else if (data.stage === "generation") {
              setStage(`答案已生成 · ${formatMs(data.elapsed_ms)}`);
            } else if (data.label) {
              setStage(data.label);
            }
          }
          if (event === "answer.delta") {
            setMessages((rows) =>
              rows.map((message) =>
                message.id === liveAssistantId
                  ? { ...message, role: "assistant", content: message.content + data.delta }
                  : message,
              ),
            );
          }
          if (event === "answer.completed") {
            setEvidence(data.evidence || []);
            setMessages((rows) =>
              rows.map((message) =>
                message.id === liveAssistantId
                  ? {
                      ...message,
                      role: "assistant",
                      status: "completed",
                      citations: data.citations || [],
                      evaluation: data.evaluation || message.evaluation,
                      retrieval_trace: data.trace || message.retrieval_trace,
                    }
                  : message,
              ),
            );
            setBusy(false);
            if (!data.evaluation) pollEvaluation(liveAssistantId);
            setStageKind(data.evaluation ? "done" : "judge");
            setStage(data.evaluation ? "已直接回答" : "答案已生成，后台正在做证据检查…");
          }
          if (event === "judge.completed") {
            setStageKind("done");
            setMessages((rows) =>
              rows.map((message) =>
                message.id === liveAssistantId ? { ...message, role: "assistant", evaluation: data.evaluation } : message,
              ),
            );
            setStage("后台评估完成");
          }
          if (event === "error") {
            setBusy(false);
            setStageKind("error");
            setStage(data.message);
          }
        }
      }
      await refresh();
    } catch (error: any) {
      setStageKind(error.name === "AbortError" ? "idle" : "error");
      setStage(error.name === "AbortError" ? "已停止" : error.message);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  function CitationLink({ href, children, citations = [] }: { href?: string; children: any; citations?: any[] }) {
    const text = Array.isArray(children) ? children.join("") : String(children ?? "");
    const childCitation = text.replace(/\s+/g, "").match(/^E:(\d+)$/)?.[1];
    const evidenceHref = href?.startsWith("evidence:") ? href : childCitation ? `evidence:${childCitation}` : "";
    if (!evidenceHref) {
      if (!href) return <span>{children}</span>;
      return <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer">{children}</a>;
    }
    const id = Number(evidenceHref.split(":")[1]);
    return (
      <button
        className="cite"
        type="button"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          openEvidence(id, citations.find((item) => item.chunk_id === id));
        }}
      >
        {children}
      </button>
    );
  }

  const current = conversations.find((item) => item.id === conversationId);
  const latestAssistant = [...messages].reverse().find((message) => message.role !== "user");
  const currentTrace = latestAssistant?.retrieval_trace || {};

  return (
    <main className={`app ${view === "graph" ? "graph-mode" : ""}`}>
      <aside className="left">
        <div className="brand">
          <b>SL</b>
          <span>
            Simulink Wiki<small>LOCAL KNOWLEDGE OS</small>
          </span>
        </div>
        <button className="primary" onClick={createConversation}>
          ＋ 新建对话
        </button>
        <nav>
          {[
            ["chat", "对话"],
            ["documents", "文档"],
            ["knowledge", "Wiki"],
            ["graph", "图谱"],
            ["review", "审核"],
            ["evaluations", "评测"],
            ["settings", "设置"],
          ].map(([key, label]) => (
            <button className={view === key ? "on" : ""} onClick={() => setView(key)} key={key}>
              {label}
            </button>
          ))}
        </nav>
        <h4>历史对话</h4>
        <div className="history">
          {conversations.map((item) => (
            <button
              className={conversationId === item.id ? "on" : ""}
              onClick={() => {
                setConversationId(item.id);
                setView("chat");
              }}
              key={item.id}
            >
              {item.pinned ? "◆ " : ""}
              {item.title}
            </button>
          ))}
        </div>
        <footer>
          <i />
          本地模型在线
        </footer>
      </aside>

      <section className="center">
        {view === "chat" ? (
          <>
            <header>
              <div>
                <small>当前会话</small>
                <h1>{current?.title || "新对话"}</h1>
              </div>
              <SourceFilter current={current} documents={documents} onChange={saveFilter} />
            </header>
            <div className="messages">
              {stage && (
                <div className={`chat-status ${stageKind}`}>
                  <span />
                  <b>{stage}</b>
                </div>
              )}
              {!messages.length && (
                <div className="welcome">
                  <em>TRACEABLE RAG</em>
                  <h2>从你的 Simulink 资料中找到可验证的答案</h2>
                  <p>结论可追溯到原始文档、页码和证据块。</p>
                </div>
              )}
              {messages.map((message) => {
                const role = message.role === "user" ? "user" : "assistant";
                return (
                  <article className={role} key={message.id}>
                    <i>{role === "user" ? "你" : "AI"}</i>
                    <div>
                      <ReactMarkdown
                        urlTransform={(url) => url}
                        components={{
                          a: ({ href, children }) => (
                            <CitationLink href={href} citations={message.citations}>
                              {children}
                            </CitationLink>
                          ),
                        }}
                      >
                        {citationMarkdown(message.content || (role === "assistant" ? "正在生成…" : ""))}
                      </ReactMarkdown>
                      {role === "assistant" && typeof message.evaluation?.passed === "boolean" && (
                        <small className={message.evaluation.passed ? "pass" : "fail"}>
                          {message.evaluation.passed ? "已通过证据检查" : "需要复核"}
                        </small>
                      )}
                    </div>
                  </article>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
            <div className="compose">
              <small>{stage}</small>
              <div>
                <textarea
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder="询问 Simulink 知识…"
                />
                <button onClick={busy ? () => abort.current?.abort() : send}>{busy ? "■" : "↑"}</button>
              </div>
            </div>
          </>
        ) : (
          <Workspace
            view={view}
            documents={documents}
            wikiPages={wikiPages}
            wikiPage={wikiPage}
            graphData={graphData}
            onRefresh={refresh}
            onOpenWiki={loadWikiPage}
            onShowGraph={() => setView("graph")}
            onOpenWikiFromGraph={(slug: string) => {
              loadWikiPage(slug);
              setView("knowledge");
            }}
            onOpenEvidence={openEvidence}
            CitationLink={CitationLink}
          />
        )}
      </section>

      <aside className="right">
        <header>
          <div>
            <small>EVIDENCE</small>
            <h3>证据与原页</h3>
          </div>
          <b>{evidence.length}</b>
        </header>
        {currentTrace?.candidate_count ? (
          <details className="trace-card">
            <summary>
              <span>检索摘要</span>
              <b>{currentTrace.candidate_count} 个候选</b>
            </summary>
            <dl>
              <div><dt>查询</dt><dd>{(currentTrace.queries || []).join(" / ") || "原问题"}</dd></div>
              <div><dt>改写</dt><dd>{formatMs(currentTrace.rewrite_ms) || "0ms"}</dd></div>
              <div><dt>召回</dt><dd>{formatMs(currentTrace.retrieval_ms) || "-"}</dd></div>
              <div><dt>精排</dt><dd>{currentTrace.rerank_used === false ? "已跳过" : formatMs(currentTrace.rerank_ms)}</dd></div>
              {currentTrace.rerank_reason && <div><dt>策略</dt><dd>{currentTrace.rerank_reason}</dd></div>}
            </dl>
          </details>
        ) : (
          <div className="trace-card muted">
            <b>本轮暂无检索轨迹</b>
            <small>问候、功能说明等直接回答不会启动 RAG。</small>
          </div>
        )}
        {chosen ? (
          <>
            <article className="evidence-detail">
              <b>
                [E:{chosen.chunk_id}] {chosen.title}
              </b>
              <div className="evidence-meta">
                {chosen.page && <span>p.{chosen.page}</span>}
                {chosen.channels?.map((channel: string) => <span key={channel}>{channelLabel(channel)}</span>)}
                {scoreText(chosen.rrf_score) && <span>RRF {scoreText(chosen.rrf_score)}</span>}
              </div>
              {chosen.heading_path && <small>{chosen.heading_path}</small>}
              <p>{chosen.content}</p>
            </article>
            {chosen.page && <img src={`${API}/api/sources/${chosen.document_id}/pages/${chosen.page}`} alt="source page" />}
          </>
        ) : evidence.length ? (
          <div className="evidence-list">
            {evidence.map((item) => (
              <button
                className={chosen?.chunk_id === item.chunk_id ? "selected" : ""}
                key={item.chunk_id}
                onClick={() => openEvidence(item.chunk_id, item)}
              >
                <b>E:{item.chunk_id}</b>
                <span>{item.title}</span>
                <small>{item.page ? `p.${item.page}` : "无页码"}</small>
                <em>{item.heading_path || "未识别标题路径"}</em>
                <i>{(item.channels || []).map(channelLabel).join(" · ") || "证据"}</i>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty">
            ⌁
            <p>聊天或 Wiki 中点击引用后，证据和 PDF 原页会出现在这里。</p>
          </div>
        )}
      </aside>
    </main>
  );
}

function Workspace({
  view,
  documents,
  wikiPages,
  wikiPage,
  graphData,
  onRefresh,
  onOpenWiki,
  onShowGraph,
  onOpenWikiFromGraph,
  onOpenEvidence,
  CitationLink,
}: {
  view: string;
  documents: DocumentRow[];
  wikiPages: WikiSummary[];
  wikiPage: WikiDetail | null;
  graphData: GraphData;
  onRefresh: () => Promise<void>;
  onOpenWiki: (slug: string) => Promise<void>;
  onShowGraph: () => void;
  onOpenWikiFromGraph: (slug: string) => void;
  onOpenEvidence: (chunkId: number, fallback?: any) => Promise<void>;
  CitationLink: any;
}) {
  const titles: Record<string, string> = {
    documents: "文档与导入",
    knowledge: "Wiki 知识页",
    graph: "知识图谱",
    review: "草稿审核",
    evaluations: "评测与链路",
    settings: "本地设置与记忆",
  };

  if (view === "documents") {
    return (
      <div className="utility">
        <em>SIMULINK WORKSPACE</em>
        <h1>文档与导入</h1>
        <Upload onDone={onRefresh} />
        <div className="cards">
          {documents.map((document) => (
            <DocumentCard key={document.id} document={document} onDone={onRefresh} />
          ))}
        </div>
      </div>
    );
  }

  if (view === "knowledge") {
    return (
      <WikiWorkspace
        wikiPages={wikiPages}
        wikiPage={wikiPage}
        graphData={graphData}
        onOpenWiki={onOpenWiki}
        onOpenEvidence={onOpenEvidence}
        onShowGraph={onShowGraph}
        CitationLink={CitationLink}
      />
    );
  }

  if (view === "__legacy_knowledge") {
    return (
      <div className="utility wiki-utility">
        <em>LLM-WIKI</em>
        <h1>Wiki 知识页</h1>
        <div className="wiki-shell">
          <div className="wiki-list">
            {wikiPages.map((page) => (
              <button
                className={wikiPage?.slug === page.slug ? "on" : ""}
                key={page.slug}
                onClick={() => onOpenWiki(page.slug)}
              >
                <b>{page.title}</b>
                <span>{page.type} · {page.status}</span>
              </button>
            ))}
          </div>
          <article className="wiki-reader">
            {wikiPage ? (
              <>
                <small>{wikiPage.status} · {wikiPage.slug}</small>
                <ReactMarkdown
                  urlTransform={(url) => url}
                  components={{
                    a: ({ href, children }) => <CitationLink href={href}>{children}</CitationLink>,
                  }}
                >
                  {citationMarkdown(wikiPage.content)}
                </ReactMarkdown>
              </>
            ) : (
              <div className="wiki-empty">
                <h2>选择一篇 Wiki 草稿</h2>
                <p>阅读章节页，并点击 `[E:n]` 查看右侧原始证据和 PDF 原页。</p>
              </div>
            )}
          </article>
        </div>
      </div>
    );
  }

  if (view === "graph") {
    return (
      <GraphWorkspace
        graphData={graphData}
        onOpenWiki={onOpenWikiFromGraph}
        onOpenEvidence={onOpenEvidence}
        onRefresh={onRefresh}
      />
    );
  }

  return (
    <div className="utility">
      <em>SIMULINK WORKSPACE</em>
      <h1>{titles[view]}</h1>
      <p>该模块会在后续里程碑中升级为独立工作台。当前 Demo 优先完成 Wiki 浏览、引用联动和证据问答闭环。</p>
    </div>
  );
}

function wikiTitleParts(title = "") {
  return title.split("/").map((part) => part.trim()).filter(Boolean);
}

function shortWikiTitle(title = "") {
  const parts = wikiTitleParts(title);
  return parts.length ? parts[parts.length - 1] : title;
}

function wikiSourceTitle(title = "") {
  const parts = wikiTitleParts(title);
  return parts.length > 1 ? parts[0] : "Local Wiki";
}

function citationIds(content = "") {
  return Array.from(new Set(Array.from(content.matchAll(/\[E:(\d+)\]/g)).map((match) => Number(match[1]))));
}

function buildWikiContext(wikiPage: WikiDetail | null, graphData: GraphData): WikiContext {
  if (!wikiPage) return { entities: [], evidence: [], relatedPages: [], backlinks: [], citations: [] };
  const nodes = graphData.nodes || [];
  const edges = graphData.edges || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const wikiNodeId = `wiki:${wikiPage.slug}`;
  const citations = citationIds(wikiPage.content);
  const citationSet = new Set(citations);
  const content = `${wikiPage.title}\n${wikiPage.content}`.toLowerCase();
  const entities = nodes
    .filter((node) => node.type === "entity" && node.label && content.includes(node.label.toLowerCase()))
    .sort((a, b) => (b.wiki_count || 0) - (a.wiki_count || 0))
    .slice(0, 12);
  const evidence = nodes
    .filter((node) => node.type === "evidence" && typeof node.chunk_id === "number" && citationSet.has(node.chunk_id))
    .slice(0, 18);
  const relatedPages = edges
    .filter((edge) => edge.source === wikiNodeId && edge.target.startsWith("wiki:"))
    .map((edge) => byId.get(edge.target))
    .filter(Boolean)
    .slice(0, 12) as GraphNode[];
  const backlinks = edges
    .filter((edge) => edge.target === wikiNodeId && edge.source.startsWith("wiki:"))
    .map((edge) => byId.get(edge.source))
    .filter(Boolean)
    .slice(0, 12) as GraphNode[];
  return { entities, evidence, relatedPages, backlinks, citations };
}

function WikiWorkspace({
  wikiPages,
  wikiPage,
  graphData,
  onOpenWiki,
  onOpenEvidence,
  onShowGraph,
  CitationLink,
}: {
  wikiPages: WikiSummary[];
  wikiPage: WikiDetail | null;
  graphData: GraphData;
  onOpenWiki: (slug: string) => Promise<void>;
  onOpenEvidence: (chunkId: number, fallback?: any) => Promise<void>;
  onShowGraph: () => void;
  CitationLink: any;
}) {
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const context = useMemo(() => buildWikiContext(wikiPage, graphData), [wikiPage, graphData]);
  const filteredPages = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return wikiPages.filter((page) => {
      if (typeFilter !== "all" && page.type !== typeFilter) return false;
      if (statusFilter !== "all" && page.status !== statusFilter) return false;
      if (!needle) return true;
      return `${page.title} ${page.slug} ${page.type} ${page.status}`.toLowerCase().includes(needle);
    });
  }, [wikiPages, query, typeFilter, statusFilter]);

  const sourceTitle = wikiPage ? wikiSourceTitle(wikiPage.title) : "";
  const displayTitle = wikiPage ? shortWikiTitle(wikiPage.title) : "";
  const headings = wikiPage
    ? Array.from(wikiPage.content.matchAll(/^#{2,3}\s+(.+)$/gm)).map((match) => match[1].replace(/\[E:\d+\]/g, "").trim()).slice(0, 8)
    : [];

  return (
    <div className="utility wiki-utility wiki-v2">
      <div className="wiki-topbar">
        <div>
          <em>LLM-WIKI READER</em>
          <h1>Wiki 知识页面</h1>
          <p>阅读结构化知识页，沿引用回到原始证据，并从实体关系继续探索。</p>
        </div>
        <button onClick={onShowGraph}>打开知识图谱</button>
      </div>

      <div className="wiki-shell wiki-shell-v2">
        <aside className="wiki-nav">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Wiki 页面..." />
          <div className="wiki-filter-row">
            <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
              <option value="all">全部类型</option>
              <option value="source">Source</option>
              <option value="source_section">Section</option>
            </select>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">全部状态</option>
              <option value="draft">Draft</option>
              <option value="published">Published</option>
            </select>
          </div>
          <div className="wiki-list wiki-list-v2">
            {filteredPages.map((page) => (
              <button
                className={wikiPage?.slug === page.slug ? "on" : ""}
                key={page.slug}
                onClick={() => onOpenWiki(page.slug)}
              >
                <b>{shortWikiTitle(page.title)}</b>
                <span>{wikiSourceTitle(page.title)}</span>
                <small>{page.type} · {page.status}</small>
              </button>
            ))}
          </div>
        </aside>

        <article className="wiki-reader wiki-reader-v2">
          {wikiPage ? (
            <>
              <header className="wiki-reader-head">
                <small>{wikiPage.status} · {wikiPage.slug}</small>
                <h2>{displayTitle}</h2>
                <p>{sourceTitle}</p>
                <div className="wiki-actions">
                  <span>{wikiPage.status}</span>
                  <span>{context.citations.length} citations</span>
                  <span>{context.entities.length} entities</span>
                  <button onClick={onShowGraph}>在图谱中查看</button>
                  <button>用此页提问</button>
                </div>
              </header>

              <section className="wiki-overview-card">
                <div>
                  <small>PAGE BRIEF</small>
                  <b>{displayTitle}</b>
                  <p>本页由原始证据编译生成，所有关键结论应能回溯到右侧引用证据。</p>
                </div>
                <div>
                  <small>TOP ENTITIES</small>
                  <div className="entity-pills">
                    {context.entities.slice(0, 6).map((entity) => (
                      <button key={entity.id}>{entity.label}</button>
                    ))}
                    {!context.entities.length && <span>暂无实体</span>}
                  </div>
                </div>
              </section>

              <ReactMarkdown
                urlTransform={(url) => url}
                components={{
                  a: ({ href, children }) => <CitationLink href={href}>{children}</CitationLink>,
                }}
              >
                {citationMarkdown(wikiPage.content)}
              </ReactMarkdown>
            </>
          ) : (
            <div className="wiki-empty">
              <h2>选择一篇 Wiki 页面</h2>
              <p>阅读知识页，点击引用查看证据，或从实体关系进入图谱探索。</p>
            </div>
          )}
        </article>

        <aside className="wiki-context">
          <header>
            <small>PAGE CONTEXT</small>
            <h3>本页上下文</h3>
          </header>

          <section>
            <h4>Key Entities</h4>
            <div className="context-list">
              {context.entities.slice(0, 10).map((entity) => (
                <button key={entity.id} onClick={onShowGraph}>
                  <b>{entity.label}</b>
                  <small>{entity.entity_type || "entity"} · wiki {entity.wiki_count || 0}</small>
                </button>
              ))}
              {!context.entities.length && <p>暂无实体匹配。</p>}
            </div>
          </section>

          <section>
            <h4>Cited Evidence</h4>
            <div className="context-list">
              {context.evidence.map((item) => (
                <button key={item.id} onClick={() => item.chunk_id && onOpenEvidence(item.chunk_id, item)}>
                  <b>E:{item.chunk_id}</b>
                  <small>{item.document_title || "source"}{item.page ? ` · p.${item.page}` : ""}</small>
                </button>
              ))}
              {!context.evidence.length && <p>正文中暂无可定位证据。</p>}
            </div>
          </section>

          <section>
            <h4>Outline</h4>
            <div className="context-tags">
              {headings.map((heading) => <span key={heading}>{heading}</span>)}
              {!headings.length && <p>暂无标题层级。</p>}
            </div>
          </section>

          <section>
            <h4>Related Pages</h4>
            <div className="context-list">
              {[...context.relatedPages, ...context.backlinks].slice(0, 10).map((page) => (
                <button key={page.id} onClick={() => page.slug && onOpenWiki(page.slug)}>
                  <b>{page.label}</b>
                  <small>{page.page_type || "wiki"}</small>
                </button>
              ))}
              {!context.relatedPages.length && !context.backlinks.length && <p>暂无显式页面链接。</p>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

function GraphWorkspace({
  graphData,
  onOpenWiki,
  onOpenEvidence,
  onRefresh,
}: {
  graphData: GraphData;
  onOpenWiki: (slug: string) => void;
  onOpenEvidence: (chunkId: number, fallback?: any) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [query, setQuery] = useState("");
  const [maxNodes, setMaxNodes] = useState(220);
  const [enabledTypes, setEnabledTypes] = useState<Record<string, boolean>>({
    document: true,
    wiki_page: true,
    entity: true,
    evidence: false,
    concept: true,
  });
  const cyRef = useRef<Core | null>(null);
  const graphRef = useRef<HTMLDivElement | null>(null);
  const nodes = graphData.nodes || [];
  const edges = graphData.edges || [];
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const degree = useMemo(() => {
    const value = new Map<string, number>();
    edges.forEach((edge) => {
      value.set(edge.source, (value.get(edge.source) || 0) + 1);
      value.set(edge.target, (value.get(edge.target) || 0) + 1);
    });
    return value;
  }, [edges]);
  const selectedEdges = useMemo(
    () => (selected ? edges.filter((edge) => edge.source === selected.id || edge.target === selected.id).slice(0, 80) : []),
    [edges, selected],
  );
  const visibleNodes = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = nodes.filter((node) => {
      if (!enabledTypes[node.type]) return false;
      if (!needle) return true;
      return [node.label, node.id, node.entity_type, node.document_title]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle));
    });
    const base = filtered.sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0)).slice(0, maxNodes);
    if (!needle) return base;
    const keep = new Map(base.map((node) => [node.id, node]));
    const ids = new Set(keep.keys());
    edges.forEach((edge) => {
      if (ids.has(edge.source) && !keep.has(edge.target)) {
        const node = byId.get(edge.target);
        if (node && enabledTypes[node.type]) keep.set(node.id, node);
      }
      if (ids.has(edge.target) && !keep.has(edge.source)) {
        const node = byId.get(edge.source);
        if (node && enabledTypes[node.type]) keep.set(node.id, node);
      }
    });
    return Array.from(keep.values()).slice(0, maxNodes + 80);
  }, [nodes, edges, byId, degree, enabledTypes, query, maxNodes]);
  const visibleIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)).slice(0, 900),
    [edges, visibleIds],
  );

  useEffect(() => {
    if (!graphRef.current) return;
    const elements: ElementDefinition[] = [
      ...visibleNodes.map((node) => ({
        data: {
          id: node.id,
          label: node.label,
          type: node.type,
          entityType: node.entity_type || node.page_type || node.type,
          size: Math.min(62, 22 + Math.sqrt((degree.get(node.id) || 1) * 18)),
        },
        classes: `node-${node.type}`,
      })),
      ...visibleEdges.map((edge, index) => ({
        data: {
          id: `edge:${index}:${edge.source}:${edge.target}:${edge.label}`,
          source: edge.source,
          target: edge.target,
          label: edge.relation_label || edge.label,
          type: edge.label,
          weight: edge.weight || 1,
        },
      })),
    ];

    cyRef.current?.destroy();
    const cy = cytoscape({
      container: graphRef.current,
      elements,
      minZoom: 0.25,
      maxZoom: 3,
      wheelSensitivity: 0.18,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#60a5fa",
            "border-color": "#ffffff",
            "border-width": 2,
            color: "#172033",
            "font-size": 10,
            label: "data(label)",
            "min-zoomed-font-size": 8,
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.82,
            "text-background-padding": "3px",
            "text-max-width": 120,
            "text-valign": "bottom",
            "text-wrap": "ellipsis",
            height: "data(size)",
            width: "data(size)",
          },
        },
        { selector: ".node-document", style: { "background-color": "#fb923c", shape: "round-rectangle" } },
        { selector: ".node-wiki_page", style: { "background-color": "#64748b", shape: "hexagon" } },
        { selector: ".node-entity", style: { "background-color": "#60a5fa" } },
        { selector: ".node-concept", style: { "background-color": "#c084fc" } },
        { selector: ".node-evidence", style: { "background-color": "#22c55e", shape: "diamond" } },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "line-color": "#c6d7ee",
            opacity: 0.42,
            "target-arrow-color": "#c6d7ee",
            "target-arrow-shape": "triangle",
            width: 1,
          },
        },
        { selector: ".faded", style: { opacity: 0.08, "text-opacity": 0.04 } },
        { selector: ".highlighted", style: { opacity: 1, "line-color": "#2563eb", "target-arrow-color": "#2563eb", "border-color": "#0f172a", "border-width": 3 } },
        { selector: ".selected", style: { "border-color": "#0f172a", "border-width": 4, "background-blacken": -0.08 } },
      ] as any,
      layout: {
        name: "cose",
        animate: false,
        fit: true,
        padding: 42,
        nodeRepulsion: 9000,
        idealEdgeLength: 120,
        edgeElasticity: 90,
        nestingFactor: 1.1,
        numIter: 900,
      } as any,
    });

    cy.on("tap", "node", (event) => {
      const node = byId.get(event.target.id());
      if (!node) return;
      clickNode(node);
    });
    cy.on("tap", (event) => {
      if (event.target === cy) {
        setSelected(null);
        cy.elements().removeClass("faded highlighted selected");
      }
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, [visibleNodes, visibleEdges, byId, degree, onOpenEvidence]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded highlighted selected");
    if (!selected) return;
    const node = cy.getElementById(selected.id);
    if (!node.length) return;
    const neighborhood = node.closedNeighborhood();
    cy.elements().not(neighborhood).addClass("faded");
    neighborhood.addClass("highlighted");
    node.addClass("selected");
    cy.animate({ fit: { eles: neighborhood, padding: 80 }, duration: 280 });
  }, [selected]);

  function clickNode(node: GraphNode) {
    setSelected(node);
    if (node.type === "evidence" && node.chunk_id) onOpenEvidence(node.chunk_id, node);
  }

  function fitGraph() {
    const cy = cyRef.current;
    if (cy) cy.animate({ fit: { eles: cy.elements(), padding: 50 }, duration: 250 });
  }

  function runLayout() {
    cyRef.current?.layout({
      name: "cose",
      animate: true,
      animationDuration: 450,
      fit: true,
      padding: 50,
      nodeRepulsion: 9000,
      idealEdgeLength: 120,
      edgeElasticity: 90,
      numIter: 700,
    } as any).run();
  }

  async function compileGraph() {
    setCompiling(true);
    try {
      await fetch(`${API}/api/wiki/graph/compile?use_llm=true&limit_pages=80`, { method: "POST" });
      await onRefresh();
    } finally {
      setCompiling(false);
    }
  }

  return (
    <div className="utility graph-utility">
      <div className="graph-topbar">
        <div>
          <em>TRACEABLE KNOWLEDGE GRAPH</em>
          <h1>知识图谱</h1>
          <p className="graph-intro">
            图谱 v3：支持搜索、过滤、缩放、拖拽和一跳邻居高亮；从 Wiki 与证据派生，不改原始文档。
          </p>
        </div>
        <button onClick={compileGraph} disabled={compiling}>
          {compiling ? "编译中..." : "编译图谱"}
        </button>
      </div>

      <div className="graph-stats">
        {Object.entries(graphData.stats || {}).map(([key, value]) => (
          <span key={key}>
            <b>{value}</b>
            {key}
          </span>
        ))}
      </div>

      <div className="graph-toolbar">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索实体、Wiki、文档，例如 AUTOSAR / Simulink / ARXML"
        />
        <select value={maxNodes} onChange={(event) => setMaxNodes(Number(event.target.value))}>
          <option value={120}>Top 120</option>
          <option value={220}>Top 220</option>
          <option value={360}>Top 360</option>
        </select>
        <button onClick={fitGraph}>适配视图</button>
        <button onClick={runLayout}>重新布局</button>
      </div>

      <div className="graph-filters">
        {[
          ["document", "Source"],
          ["wiki_page", "Wiki"],
          ["entity", "Entity"],
          ["evidence", "Evidence"],
          ["concept", "Concept"],
        ].map(([key, label]) => (
          <label key={key} className={enabledTypes[key] ? "on" : ""}>
            <input
              checked={!!enabledTypes[key]}
              onChange={(event) => setEnabledTypes((value) => ({ ...value, [key]: event.target.checked }))}
              type="checkbox"
            />
            <i className={`dot-${key}`} />
            {label}
          </label>
        ))}
        <small>
          当前显示 {visibleNodes.length} 节点 / {visibleEdges.length} 关系；点击节点会高亮一跳邻居。
        </small>
      </div>

      <div className="graph-shell-lite graph-canvas-shell">
        <section className="graph-canvas">
          <div className="cy-canvas" ref={graphRef} />
          <div className="graph-legend">
            {[
              ["document", "Source"],
              ["wiki_page", "Wiki"],
              ["entity", "Entity"],
              ["evidence", "Evidence"],
              ["concept", "Concept"],
            ].map(([key, label]) => (
              <span key={key}>
                <i className={`dot-${key}`} />
                {label}
              </span>
            ))}
          </div>
        </section>

        <aside className="graph-side">
          <div className="graph-detail">
            <small>SELECTED NODE</small>
            {selected ? (
              <>
                <h3>{selected.label}</h3>
                <p>{selected.id}</p>
                {selected.type === "wiki_page" && selected.slug && (
                  <button className="graph-open" onClick={() => onOpenWiki(selected.slug!)}>
                    打开 Wiki 页面
                  </button>
                )}
                {selected.type === "evidence" && selected.chunk_id && (
                  <button className="graph-open" onClick={() => onOpenEvidence(selected.chunk_id!, selected)}>
                    查看证据原文
                  </button>
                )}
                <dl>
                  <dt>type</dt>
                  <dd>{selected.type}</dd>
                  {selected.entity_type && (
                    <>
                      <dt>entity</dt>
                      <dd>{selected.entity_type}</dd>
                    </>
                  )}
                  {selected.status && (
                    <>
                      <dt>status</dt>
                      <dd>{selected.status}</dd>
                    </>
                  )}
                  {selected.document_title && (
                    <>
                      <dt>source</dt>
                      <dd>{selected.document_title}</dd>
                    </>
                  )}
                  {typeof selected.wiki_count === "number" && (
                    <>
                      <dt>wiki</dt>
                      <dd>{selected.wiki_count}</dd>
                    </>
                  )}
                  {typeof selected.evidence_count === "number" && (
                    <>
                      <dt>evidence</dt>
                      <dd>{selected.evidence_count}</dd>
                    </>
                  )}
                </dl>
              </>
            ) : (
              <p>点击一个节点查看实体、Wiki、文档和证据之间的一跳关系。</p>
            )}
          </div>

          <div className="graph-edges">
            <h3>{selected ? "Neighbor Relations" : "Top Relations"}</h3>
            {(selected ? selectedEdges : visibleEdges.slice(0, 120)).map((edge, index) => (
              <button
                key={`${edge.source}-${edge.target}-${edge.label}-${index}`}
                onClick={() => {
                  const next = selected?.id === edge.source ? byId.get(edge.target) : byId.get(edge.source);
                  if (next) clickNode(next);
                }}
              >
                <span>{byId.get(edge.source)?.label || edge.source}</span>
                <b>{edge.relation_label || edge.label}</b>
                <span>{byId.get(edge.target)?.label || edge.target}</span>
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

function GraphWorkspaceOld({
  graphData,
  onOpenWiki,
  onOpenEvidence,
  onRefresh,
}: {
  graphData: GraphData;
  onOpenWiki: (slug: string) => void;
  onOpenEvidence: (chunkId: number, fallback?: any) => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [compiling, setCompiling] = useState(false);
  const nodes = graphData.nodes || [];
  const edges = graphData.edges || [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const degree = new Map<string, number>();
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  });
  const visibleNodes = [...nodes]
    .sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))
    .slice(0, 180);
  const visible = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)).slice(0, 360);
  const positions = layoutGraph(visibleNodes, degree);

  function clickNode(node: GraphNode) {
    setSelected(node);
    if (node.type === "evidence" && node.chunk_id) onOpenEvidence(node.chunk_id, node);
  }

  async function compileGraph() {
    setCompiling(true);
    try {
      await fetch(`${API}/api/wiki/graph/compile?use_llm=true&limit_pages=80`, { method: "POST" });
      await onRefresh();
    } finally {
      setCompiling(false);
    }
  }

  return (
    <div className="utility graph-utility">
      <div className="graph-topbar">
        <div>
          <em>TRACEABLE KNOWLEDGE GRAPH</em>
          <h1>知识图谱</h1>
          <p className="graph-intro">
            当前是图谱 v2：从 Wiki 和证据派生实体/关系，原始文档不被修改，可重复编译。
          </p>
        </div>
        <button onClick={compileGraph} disabled={compiling}>
          {compiling ? "编译中..." : "编译图谱"}
        </button>
      </div>

      <div className="graph-stats">
        {Object.entries(graphData.stats || {}).map(([key, value]) => (
          <span key={key}>
            <b>{value}</b>
            {key}
          </span>
        ))}
      </div>

      <div className="graph-shell-lite graph-canvas-shell">
        <section className="graph-canvas">
          <svg viewBox="0 0 1200 760" role="img" aria-label="Knowledge graph">
            <g className="graph-lines">
              {visibleEdges.map((edge, index) => {
                const source = positions.get(edge.source);
                const target = positions.get(edge.target);
                if (!source || !target) return null;
                const active = selected && (edge.source === selected.id || edge.target === selected.id);
                return (
                  <line
                    key={`${edge.source}-${edge.target}-${edge.label}-${index}`}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    className={active ? "active" : ""}
                  />
                );
              })}
            </g>
            <g>
              {visibleNodes.map((node) => {
                const position = positions.get(node.id);
                if (!position) return null;
                const size = Math.min(30, 8 + Math.sqrt((degree.get(node.id) || 1) * 18));
                return (
                  <g
                    key={node.id}
                    className={`graph-svg-node node-${node.type} ${selected?.id === node.id ? "on" : ""}`}
                    transform={`translate(${position.x} ${position.y})`}
                    onClick={() => clickNode(node)}
                  >
                    <circle r={size} />
                    <text x={size + 6} y="4">
                      {node.label.length > 32 ? `${node.label.slice(0, 32)}...` : node.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
          <div className="graph-legend">
            {[
              ["document", "Source"],
              ["wiki_page", "Wiki"],
              ["entity", "Entity"],
              ["evidence", "Evidence"],
              ["concept", "Concept"],
            ].map(([key, label]) => (
              <span key={key}>
                <i className={`dot-${key}`} />
                {label}
              </span>
            ))}
          </div>
        </section>

        <aside className="graph-side">
          <div className="graph-detail">
            <small>SELECTED NODE</small>
            {selected ? (
              <>
                <h3>{selected.label}</h3>
                <p>{selected.id}</p>
                {selected.type === "wiki_page" && selected.slug && (
                  <button className="graph-open" onClick={() => onOpenWiki(selected.slug!)}>
                    打开 Wiki 页面
                  </button>
                )}
                <dl>
                  <dt>type</dt>
                  <dd>{selected.type}</dd>
                  {selected.entity_type && (
                    <>
                      <dt>entity</dt>
                      <dd>{selected.entity_type}</dd>
                    </>
                  )}
                  {selected.status && (
                    <>
                      <dt>status</dt>
                      <dd>{selected.status}</dd>
                    </>
                  )}
                  {selected.document_title && (
                    <>
                      <dt>source</dt>
                      <dd>{selected.document_title}</dd>
                    </>
                  )}
                  {typeof selected.wiki_count === "number" && (
                    <>
                      <dt>wiki</dt>
                      <dd>{selected.wiki_count}</dd>
                    </>
                  )}
                  {typeof selected.evidence_count === "number" && (
                    <>
                      <dt>evidence</dt>
                      <dd>{selected.evidence_count}</dd>
                    </>
                  )}
                </dl>
              </>
            ) : (
              <p>点击一个节点查看它和文档、Wiki、实体、证据之间的关系。</p>
            )}
          </div>

          <div className="graph-edges">
            <h3>Relations</h3>
            {edges.slice(0, 120).map((edge, index) => (
              <button
                key={`${edge.source}-${edge.target}-${edge.label}-${index}`}
                onClick={() => setSelected(byId.get(edge.source) || byId.get(edge.target) || null)}
              >
                <span>{byId.get(edge.source)?.label || edge.source}</span>
                <b>{edge.label}</b>
                <span>{byId.get(edge.target)?.label || edge.target}</span>
              </button>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

function layoutGraph(nodes: GraphNode[], degree: Map<string, number>) {
  const result = new Map<string, { x: number; y: number }>();
  const groups: Record<string, GraphNode[]> = {
    document: [],
    wiki_page: [],
    entity: [],
    evidence: [],
    concept: [],
    other: [],
  };
  nodes.forEach((node) => {
    (groups[node.type] || groups.other).push(node);
  });

  function ring(items: GraphNode[], cx: number, cy: number, rx: number, ry: number, start = 0) {
    const sorted = [...items].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0));
    sorted.forEach((node, index) => {
      const angle = start + (Math.PI * 2 * index) / Math.max(sorted.length, 1);
      result.set(node.id, { x: cx + Math.cos(angle) * rx, y: cy + Math.sin(angle) * ry });
    });
  }

  ring(groups.wiki_page, 590, 380, 150, 105, -Math.PI / 2);
  ring(groups.entity, 610, 380, 345, 245, -Math.PI / 6);
  ring(groups.concept, 610, 380, 420, 285, Math.PI / 5);
  ring(groups.document, 170, 380, 70, 260, -Math.PI / 2);
  ring(groups.evidence, 1035, 380, 75, 270, -Math.PI / 2);
  ring(groups.other, 610, 380, 480, 330, 0);
  return result;
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    ready: "READY",
    failed: "FAILED",
    processing: "PROCESSING",
    queued: "QUEUED",
  };
  return map[status] || status.toUpperCase();
}

function DocumentCard({ document, onDone }: { document: DocumentRow; onDone: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const isActive = ["queued", "processing"].includes(document.status);
  const isFailed = document.status === "failed";

  async function pollJob(jobId: number) {
    for (let i = 0; i < 120; i++) {
      const job = await fetch(`${API}/api/jobs/${jobId}`).then((r) => r.json());
      setNote(`${stageText(job.stage)} · ${job.progress}%`);
      await onDone();
      if (job.status === "completed") {
        setNote("重建完成");
        await onDone();
        return;
      }
      if (job.status === "failed") {
        setNote(`失败：${job.error || "未知错误"}`);
        await onDone();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
  }

  async function reindex() {
    setNote("正在创建重建任务…");
    const response = await fetch(`${API}/api/documents/${document.id}/reindex`, { method: "POST" });
    const body = await response.json();
    if (!response.ok) {
      setNote(body.detail || "重建任务创建失败");
      return;
    }
    await onDone();
    pollJob(body.id);
  }

  return (
    <article className={`doc-card doc-${document.status}`}>
      <div className="doc-card-head">
        <b>{document.title}</b>
        <span>{statusLabel(document.status)}</span>
      </div>
      <p>{document.filename}</p>
      <small>
        {document.media_type || "unknown"} · {document.parse_mode || "auto"}
        {document.release ? ` · ${document.release}` : ""}
      </small>
      {document.error && <pre>{document.error}</pre>}
      {note && <small className="doc-note">{note}</small>}
      <div className="doc-actions">
        <button onClick={onDone}>刷新</button>
        {(isFailed || document.status === "ready") && <button onClick={reindex}>{isFailed ? "重试" : "重建"}</button>}
        {isActive && <button onClick={onDone}>更新状态</button>}
      </div>
    </article>
  );
}

function Upload({ onDone }: { onDone: () => Promise<void> }) {
  const [note, setNote] = useState("");

  async function pollJob(jobId: number) {
    for (let i = 0; i < 120; i++) {
      const job = await fetch(`${API}/api/jobs/${jobId}`).then((r) => r.json());
      setNote(`导入任务 #${jobId}：${stageText(job.stage)} · ${job.progress}%`);
      if (job.status === "completed") {
        setNote(`导入任务 #${jobId}：完成。文档已入库，Wiki 草稿已生成。`);
        await onDone();
        return;
      }
      if (job.status === "failed") {
        setNote(`导入任务 #${jobId}：失败。${job.error || ""}`);
        await onDone();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setNote("正在上传并创建导入任务…");
    const response = await fetch(`${API}/api/documents`, { method: "POST", body: data });
    const body = await response.json();
    if (!response.ok) {
      setNote(body.detail || "上传失败");
      return;
    }
    form.reset();
    await onDone();
    pollJob(body.job.id);
  }

  return (
    <form className="upload-box" onSubmit={submit}>
      <input name="file" type="file" accept=".md,.markdown,.txt,.docx,.pdf" required />
      <select name="parse_mode" defaultValue="auto">
        <option value="auto">自动判断</option>
        <option value="text">文本层 PDF</option>
        <option value="ocr">OCR</option>
        <option value="vlm">VLM</option>
      </select>
      <input name="release" placeholder="版本，例如 R2025a" />
      <button>上传并编译</button>
      {note && <small>{note}</small>}
    </form>
  );
}
