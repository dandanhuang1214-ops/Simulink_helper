"use client";

import cytoscape, { Core, ElementDefinition } from "cytoscape";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import "./layout-fix.css";
import SourceFilter from "./source-filter";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:18080";

type Conversation = {
  id: number;
  title: string;
  pinned: boolean;
  updated_at?: string;
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
  error?: string | null;
  feedback?: string | null;
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

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, string[]> = {
    chat: ["M4 4.5h12v8.5H9l-4 3v-3H4z"],
    documents: ["M5 3.5h7l3 3v10H5z", "M12 3.5v3h3"],
    knowledge: ["M3.5 4.5h5.2c1 0 1.3.6 1.3 1.2v10c0-.8-.6-1.2-1.4-1.2H3.5z", "M16.5 4.5h-5.2c-1 0-1.3.6-1.3 1.2v10c0-.8.6-1.2 1.4-1.2h5.1z"],
    graph: ["M5 6.5 10 4l5 2.5v6L10 16l-5-3.5z", "M5 6.5l5 3 5-3", "M10 9.5V16"],
    review: ["M4.5 4.5h11v11h-11z", "m7 11 2 2 4-5"],
    evaluations: ["M4.5 15.5v-4", "M9 15.5v-7", "M13.5 15.5v-10", "M3 15.5h13.5"],
    settings: ["M10 6.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8", "M10 3v2M10 15v2M3 10h2M15 10h2M5 5l1.4 1.4M13.6 13.6 15 15M15 5l-1.4 1.4M6.4 13.6 5 15"],
  };
  return (
    <svg className="nav-icon" viewBox="0 0 20 20" aria-hidden="true">
      {(paths[name] || paths.chat).map((path, index) => <path d={path} key={index} />)}
    </svg>
  );
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
  const [conversationQuery, setConversationQuery] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [conversationMenu, setConversationMenu] = useState<{ id: number; top: number; left: number } | null>(null);
  const [editingConversationId, setEditingConversationId] = useState<number | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [deleteCandidate, setDeleteCandidate] = useState<Conversation | null>(null);
  const [sidebarNotice, setSidebarNotice] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [composerHeight, setComposerHeight] = useState(122);
  const [stage, setStage] = useState("");
  const [stageKind, setStageKind] = useState<"idle" | "connecting" | "retrieval" | "generation" | "judge" | "done" | "error">("idle");
  const [evidence, setEvidence] = useState<any[]>([]);
  const [chosen, setChosen] = useState<any>(null);
  const [view, setView] = useState("chat");
  const abort = useRef<AbortController | null>(null);
  const activeRequestRef = useRef(0);
  const sendLockRef = useRef(false);
  const conversationIdRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const autoFollowRef = useRef(true);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

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
    if (!conversationId && conversationRows[0]) {
      conversationIdRef.current = conversationRows[0].id;
      setConversationId(conversationRows[0].id);
    }
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
    setSidebarCollapsed(window.localStorage.getItem("simulink-wiki:sidebar-collapsed") === "1");
  }, []);

  useEffect(() => {
    if (!conversationMenu) return;
    const closeMenu = () => setConversationMenu(null);
    document.addEventListener("pointerdown", closeMenu);
    window.addEventListener("resize", closeMenu);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("resize", closeMenu);
    };
  }, [conversationMenu]);

  useEffect(() => {
    autoFollowRef.current = true;
    setShowScrollToBottom(false);
    if (conversationId) loadConversation(conversationId);
  }, [conversationId]);

  useEffect(() => {
    if (!autoFollowRef.current) return;
    const frame = requestAnimationFrame(() => {
      const container = messagesRef.current;
      if (!container) return;
      container.scrollTo({ top: container.scrollHeight, behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages.length, messages[messages.length - 1]?.content, composerHeight]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    const nextHeight = Math.min(textarea.scrollHeight, 144);
    textarea.style.height = `${Math.max(nextHeight, 44)}px`;
    textarea.style.overflowY = textarea.scrollHeight > 144 ? "auto" : "hidden";
  }, [question]);

  useEffect(() => {
    const composer = composerRef.current;
    if (!composer || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setComposerHeight(Math.ceil(entry.contentRect.height));
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, [view]);

  function handleMessagesScroll() {
    const container = messagesRef.current;
    if (!container) return;
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const nearBottom = distanceFromBottom <= 96;
    autoFollowRef.current = nearBottom;
    setShowScrollToBottom(!nearBottom && distanceFromBottom > 140);
  }

  function scrollToLatest() {
    const container = messagesRef.current;
    if (!container) return;
    autoFollowRef.current = true;
    setShowScrollToBottom(false);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }

  function interruptActiveRequest(label = "生成已中断") {
    if (!abort.current && !sendLockRef.current) return;
    activeRequestRef.current += 1;
    abort.current?.abort();
    abort.current = null;
    sendLockRef.current = false;
    setBusy(false);
    setStopping(false);
    setMessages((rows) => rows.map((message) =>
      message.status === "generating" ? { ...message, status: "cancelled" } : message,
    ));
    setStageKind("idle");
    setStage(label);
  }

  function selectConversation(id: number) {
    if (id === conversationIdRef.current) {
      setView("chat");
      return;
    }
    interruptActiveRequest("已停止上一会话的生成");
    setEvidence([]);
    setChosen(null);
    setStage("");
    setStageKind("idle");
    conversationIdRef.current = id;
    setConversationId(id);
    setView("chat");
  }

  function selectView(nextView: string) {
    if (nextView !== "chat") interruptActiveRequest("生成已中断");
    setView(nextView);
  }

  function toggleSidebar() {
    const next = !sidebarCollapsed;
    setSidebarCollapsed(next);
    setConversationMenu(null);
    window.localStorage.setItem("simulink-wiki:sidebar-collapsed", next ? "1" : "0");
  }

  async function updateConversation(id: number, payload: { title?: string; pinned?: boolean }) {
    const response = await fetch(`${API}/api/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`更新会话失败（${response.status}）`);
    const updated = await response.json();
    setConversations((rows) => {
      const next = rows.map((item) => (item.id === id ? updated : item));
      return next.sort((a, b) => {
        if (a.pinned !== b.pinned) return Number(b.pinned) - Number(a.pinned);
        return Date.parse(b.updated_at || "") - Date.parse(a.updated_at || "");
      });
    });
    setSidebarNotice("");
    return updated as Conversation;
  }

  async function toggleConversationPin(item: Conversation) {
    try {
      await updateConversation(item.id, { pinned: !item.pinned });
      setConversationMenu(null);
    } catch (error: any) {
      setSidebarNotice(error.message);
    }
  }

  function beginConversationRename(item: Conversation) {
    setEditingConversationId(item.id);
    setEditingTitle(item.title);
    setConversationMenu(null);
    setSidebarNotice("");
  }

  async function saveConversationRename(event: FormEvent) {
    event.preventDefault();
    if (!editingConversationId) return;
    const title = editingTitle.trim();
    if (!title) {
      setSidebarNotice("会话名称不能为空");
      return;
    }
    try {
      await updateConversation(editingConversationId, { title });
      setEditingConversationId(null);
      setEditingTitle("");
    } catch (error: any) {
      setSidebarNotice(error.message);
    }
  }

  function askDeleteConversation(item: Conversation) {
    if (busy && item.id === conversationId) {
      setSidebarNotice("请先停止当前回答，再删除这个会话");
      setConversationMenu(null);
      return;
    }
    setDeleteCandidate(item);
    setConversationMenu(null);
  }

  async function confirmDeleteConversation() {
    if (!deleteCandidate) return;
    const deletingId = deleteCandidate.id;
    try {
      const response = await fetch(`${API}/api/conversations/${deletingId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`删除会话失败（${response.status}）`);
      const remaining = conversations.filter((item) => item.id !== deletingId);
      setConversations(remaining);
      if (conversationId === deletingId) {
        setConversationId(remaining[0]?.id ?? null);
        setMessages([]);
        setEvidence([]);
        setChosen(null);
      }
      setDeleteCandidate(null);
      setSidebarNotice("");
    } catch (error: any) {
      setSidebarNotice(error.message);
      setDeleteCandidate(null);
    }
  }

  async function createConversation() {
    interruptActiveRequest("已停止上一会话的生成");
    const row = await fetch(`${API}/api/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "新对话" }),
    }).then((r) => r.json());
    setConversations((rows) => [row, ...rows]);
    conversationIdRef.current = row.id;
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

  async function pollEvaluation(messageId: number, originConversationId: number, requestId: number) {
    for (let i = 0; i < 20; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const row = await fetch(`${API}/api/messages/${messageId}`).then((r) => (r.ok ? r.json() : null));
      if (!row?.evaluation || typeof row.evaluation.passed !== "boolean") continue;
      setMessages((rows) =>
        rows.map((message) => (message.id === messageId ? { ...message, evaluation: row.evaluation } : message)),
      );
      if (activeRequestRef.current === requestId && conversationIdRef.current === originConversationId) {
        setStageKind("done");
        setStage("后台评估完成");
      }
      return;
    }
  }

  async function send(overrideText?: string) {
    const requestedText = (overrideText ?? question).trim();
    if (!requestedText || sendLockRef.current) return;
    sendLockRef.current = true;
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;

    let id = conversationId;
    if (!id) {
      const row = await fetch(`${API}/api/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "新对话" }),
      }).then((r) => r.json());
      id = row.id;
      conversationIdRef.current = id;
      setConversationId(id);
      setConversations((rows) => [row, ...rows]);
    }
    const activeConversationId = id as number;

    const text = requestedText;
    const tempUserId = -Date.now();
    const tempAssistantId = tempUserId - 1;
    let liveAssistantId = tempAssistantId;

    setQuestion("");
    setBusy(true);
    setStopping(false);
    autoFollowRef.current = true;
    setShowScrollToBottom(false);
    setStage("正在连接本地 API…");
    setStageKind("connecting");
    setStage("正在连接本地 API…");
    setMessages((rows) => [
      ...rows,
      { id: tempUserId, role: "user", content: text, status: "completed", citations: [] },
      { id: tempAssistantId, role: "assistant", content: "", status: "generating", citations: [] },
    ]);

    const controller = new AbortController();
    abort.current = controller;
    try {
      const response = await fetch(`${API}/api/conversations/${activeConversationId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
        signal: controller.signal,
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
          if (activeRequestRef.current !== requestId) continue;
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
            sendLockRef.current = false;
            if (!data.evaluation) pollEvaluation(liveAssistantId, activeConversationId, requestId);
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
            sendLockRef.current = false;
            setMessages((rows) => rows.map((message) =>
              message.id === liveAssistantId ? { ...message, status: "failed", error: data.message } : message,
            ));
            setStageKind("error");
            setStage(data.message);
          }
        }
      }
      if (activeRequestRef.current === requestId) await refresh();
    } catch (error: any) {
      if (activeRequestRef.current === requestId) {
        const interrupted = error.name === "AbortError";
        setMessages((rows) => rows.map((message) =>
          message.id === liveAssistantId || message.id === tempAssistantId
            ? { ...message, status: interrupted ? "cancelled" : "failed", error: interrupted ? null : error.message }
            : message,
        ));
        setStageKind(interrupted ? "idle" : "error");
        setStage(interrupted ? "生成已中断，可重新生成" : error.message);
        if (interrupted) {
          await new Promise((resolve) => setTimeout(resolve, 120));
          if (conversationIdRef.current === activeConversationId) await loadConversation(activeConversationId);
        }
      }
    } finally {
      if (activeRequestRef.current === requestId) {
        setBusy(false);
        setStopping(false);
        sendLockRef.current = false;
        if (abort.current === controller) abort.current = null;
      }
    }
  }

  function stopGeneration() {
    if (!busy || stopping) return;
    setStopping(true);
    setStage("正在停止生成…");
    abort.current?.abort();
  }

  async function copyMessage(message: Message) {
    await navigator.clipboard.writeText(message.content);
    setCopiedMessageId(message.id);
    window.setTimeout(() => setCopiedMessageId((id) => (id === message.id ? null : id)), 1400);
  }

  async function regenerateMessage(message: Message) {
    if (sendLockRef.current) return;
    const response = await fetch(`${API}/api/messages/${message.id}/regenerate`, { method: "POST" });
    if (!response.ok) {
      setStageKind("error");
      setStage("无法找到这条回答对应的问题");
      return;
    }
    const payload = await response.json();
    await send(payload.content);
  }

  async function submitFeedback(message: Message, feedback: "up" | "down") {
    if (message.feedback === feedback) return;
    const response = await fetch(`${API}/api/messages/${message.id}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    });
    if (!response.ok) return;
    const payload = await response.json();
    setMessages((rows) => rows.map((item) =>
      item.id === message.id ? { ...item, feedback: payload.feedback } : item,
    ));
  }

  function showMessageEvidence(message: Message) {
    const rows = message.citations || [];
    setEvidence(rows);
    if (rows[0]) openEvidence(rows[0].chunk_id, rows[0]);
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
        title={`查看证据 E:${id}`}
        aria-label={`查看证据 E:${id}`}
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
  const filteredConversations = useMemo(() => {
    const query = conversationQuery.trim().toLowerCase();
    if (!query) return conversations;
    return conversations.filter((item) => item.title.toLowerCase().includes(query));
  }, [conversations, conversationQuery]);
  const latestAssistant = [...messages].reverse().find((message) => message.role !== "user");
  const currentTrace = latestAssistant?.retrieval_trace || {};
  const menuConversation = conversationMenu ? conversations.find((item) => item.id === conversationMenu.id) : null;

  return (
    <main className={`app ${view === "graph" ? "graph-mode" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className={`left ${sidebarCollapsed ? "collapsed" : ""}`}>
        <div className="brand">
          <b>SL</b>
          <span>
            Simulink Wiki<small>LOCAL KNOWLEDGE OS</small>
          </span>
        </div>
        <button className="sidebar-toggle" type="button" onClick={toggleSidebar} aria-label={sidebarCollapsed ? "展开侧栏" : "收起侧栏"} title={sidebarCollapsed ? "展开侧栏" : "收起侧栏"}>
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d={sidebarCollapsed ? "m8 5 5 5-5 5" : "m12 5-5 5 5 5"} />
          </svg>
        </button>
        <button className="primary" onClick={createConversation} title="新建对话" aria-label="新建对话">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12" /></svg>
          <span>新建对话</span>
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
            <button className={view === key ? "on" : ""} onClick={() => selectView(key)} key={key} title={label} aria-label={label}>
              <NavIcon name={key} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="history-heading">
          <h4>历史对话</h4>
          <span>{conversations.length}</span>
        </div>
        <label className="history-search">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="8.7" cy="8.7" r="4.8" />
            <path d="m12.3 12.3 3.4 3.4" />
          </svg>
          <input
            value={conversationQuery}
            onChange={(event) => setConversationQuery(event.target.value)}
            placeholder="搜索对话"
            aria-label="搜索历史对话"
          />
        </label>
        <div className="history">
          {filteredConversations.map((item) => (
            <div className={`history-item ${conversationId === item.id ? "on" : ""}`} key={item.id}>
              {editingConversationId === item.id ? (
                <form className="history-rename" onSubmit={saveConversationRename}>
                  <input
                    autoFocus
                    value={editingTitle}
                    onChange={(event) => setEditingTitle(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        setEditingConversationId(null);
                        setEditingTitle("");
                      }
                    }}
                    aria-label="新的会话名称"
                  />
                  <button type="submit" aria-label="保存名称" title="保存">✓</button>
                  <button type="button" aria-label="取消重命名" title="取消" onClick={() => setEditingConversationId(null)}>×</button>
                </form>
              ) : (
                <>
                  <button
                    className="history-open"
                    onClick={() => selectConversation(item.id)}
                    title={item.title}
                  >
                    <i className={item.pinned ? "is-pinned" : ""} aria-hidden="true" />
                    <span>{item.title}</span>
                  </button>
                  <button
                    type="button"
                    className="history-more"
                    aria-label={`管理会话：${item.title}`}
                    title="会话操作"
                    aria-expanded={conversationMenu?.id === item.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      const rect = event.currentTarget.getBoundingClientRect();
                      setConversationMenu({
                        id: item.id,
                        top: Math.min(rect.bottom + 5, window.innerHeight - 142),
                        left: Math.max(8, Math.min(rect.right - 156, window.innerWidth - 164)),
                      });
                    }}
                  >
                    <span aria-hidden="true">···</span>
                  </button>
                </>
              )}
            </div>
          ))}
          {!filteredConversations.length && <small className="history-empty">没有匹配的对话</small>}
        </div>
        {sidebarNotice && <small className="sidebar-notice">{sidebarNotice}</small>}
        <footer>
          <span><i /><b>本地模型在线</b></span>
          <small>Ollama · Local</small>
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
            <div
              className="messages"
              ref={messagesRef}
              onScroll={handleMessagesScroll}
              style={{ paddingBottom: composerHeight + 28 }}
            >
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
                        {citationMarkdown(message.content || (
                          role !== "assistant" ? "" : message.status === "generating" ? "正在生成…" :
                            message.status === "failed" ? "本次生成失败。" : "本次生成已中断。"
                        ))}
                      </ReactMarkdown>
                      {role === "assistant" && typeof message.evaluation?.passed === "boolean" && (
                        <small className={message.evaluation.passed ? "pass" : "fail"}>
                          {message.evaluation.passed ? "已通过证据检查" : "需要复核"}
                        </small>
                      )}
                      {role === "assistant" && ["cancelled", "interrupted", "failed"].includes(message.status) && (
                        <small className={`message-state ${message.status === "failed" ? "is-error" : ""}`}>
                          {message.status === "failed" ? "生成失败" : "生成已中断"}
                          {message.content ? " · 已保留部分回答" : ""}
                        </small>
                      )}
                      {role === "assistant" && message.status !== "generating" && (
                        <div className="message-actions" aria-label="回答操作">
                          <button type="button" onClick={() => copyMessage(message)} title="复制回答" aria-label="复制回答">
                            <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="6.5" y="6.5" width="9" height="9" rx="1.5" /><path d="M4.5 13.5h-1v-10h10v1" /></svg>
                            <span>{copiedMessageId === message.id ? "已复制" : "复制"}</span>
                          </button>
                          <button type="button" onClick={() => regenerateMessage(message)} disabled={busy} title="重新生成" aria-label="重新生成">
                            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 7.5A6 6 0 1 0 16 12" /><path d="M15.5 3.5v4h-4" /></svg>
                            <span>{["cancelled", "interrupted", "failed"].includes(message.status) ? "重试" : "重新生成"}</span>
                          </button>
                          <button type="button" className={message.feedback === "up" ? "active" : ""} onClick={() => submitFeedback(message, "up")} title="回答有帮助" aria-label="回答有帮助">
                            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 16H4.5V9H7zM7 15.5h7.1c.8 0 1.3-.5 1.5-1.2l1-4c.2-.9-.5-1.8-1.4-1.8H12l.5-2.5c.2-1-.5-2-1.5-2L7 9z" /></svg>
                          </button>
                          <button type="button" className={message.feedback === "down" ? "active" : ""} onClick={() => submitFeedback(message, "down")} title="回答需改进" aria-label="回答需改进">
                            <svg className="flip" viewBox="0 0 20 20" aria-hidden="true"><path d="M7 16H4.5V9H7zM7 15.5h7.1c.8 0 1.3-.5 1.5-1.2l1-4c.2-.9-.5-1.8-1.4-1.8H12l.5-2.5c.2-1-.5-2-1.5-2L7 9z" /></svg>
                          </button>
                          {!!message.citations?.length && (
                            <button type="button" onClick={() => showMessageEvidence(message)} title="查看本轮证据" aria-label="查看本轮证据">
                              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 10s2.2-4 6.5-4 6.5 4 6.5 4-2.2 4-6.5 4-6.5-4-6.5-4z" /><circle cx="10" cy="10" r="1.8" /></svg>
                              <span>证据 {message.citations.length}</span>
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
            {showScrollToBottom && (
              <button
                type="button"
                className="scroll-to-latest"
                style={{ bottom: composerHeight + 12 }}
                onClick={scrollToLatest}
                aria-label="回到底部"
                title="回到底部"
              >
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M10 4.5v10.75M5.75 11 10 15.25 14.25 11" />
                </svg>
              </button>
            )}
            <div className="compose" ref={composerRef}>
              <small className={`composer-status ${stageKind}`}>
                {stage && <span aria-hidden="true" />}
                {stage}
              </small>
              <div className="composer-shell">
                <textarea
                  ref={textareaRef}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder="询问 Simulink 知识…"
                />
                <button
                  type="button"
                  className={`composer-action ${busy ? "is-stop" : "is-send"}`}
                  onClick={busy ? stopGeneration : () => send()}
                  disabled={stopping || (!busy && !question.trim())}
                  aria-label={busy ? (stopping ? "正在停止生成" : "停止生成") : "发送消息"}
                  title={busy ? (stopping ? "正在停止…" : "停止生成") : "发送消息"}
                >
                  {busy ? (
                    <span className="stop-icon" aria-hidden="true" />
                  ) : (
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <path d="M10 15.5V4.75M5.75 9 10 4.75 14.25 9" />
                    </svg>
                  )}
                </button>
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
      {conversationMenu && menuConversation && typeof document !== "undefined" && createPortal(
        <div
          className="conversation-menu"
          style={{ top: conversationMenu.top, left: conversationMenu.left }}
          role="menu"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button type="button" role="menuitem" onClick={() => toggleConversationPin(menuConversation)}>
            <span>{menuConversation.pinned ? "◇" : "◆"}</span>
            {menuConversation.pinned ? "取消置顶" : "置顶会话"}
          </button>
          <button type="button" role="menuitem" onClick={() => beginConversationRename(menuConversation)}>
            <span>✎</span>重命名
          </button>
          <button type="button" role="menuitem" className="danger" onClick={() => askDeleteConversation(menuConversation)}>
            <span>×</span>删除会话
          </button>
        </div>,
        document.body,
      )}
      {deleteCandidate && typeof document !== "undefined" && createPortal(
        <div
          className="conversation-dialog-backdrop"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) setDeleteCandidate(null);
          }}
        >
          <section className="conversation-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-conversation-title">
            <small>删除会话</small>
            <h3 id="delete-conversation-title">确定删除“{deleteCandidate.title}”吗？</h3>
            <p>该会话及其中的消息会被删除，知识库、Wiki和长期记忆内容不会被删除。</p>
            <div>
              <button type="button" onClick={() => setDeleteCandidate(null)}>取消</button>
              <button type="button" className="danger" onClick={confirmDeleteConversation}>删除</button>
            </div>
          </section>
        </div>,
        document.body,
      )}
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
  const [queryInput, setQueryInput] = useState("");
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
  const openEvidenceRef = useRef(onOpenEvidence);
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
    openEvidenceRef.current = onOpenEvidence;
  }, [onOpenEvidence]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(queryInput), 280);
    return () => window.clearTimeout(timer);
  }, [queryInput]);

  useEffect(() => {
    if (selected && !visibleIds.has(selected.id)) setSelected(null);
  }, [selected, visibleIds]);

  useEffect(() => {
    if (!graphRef.current) return;
    const searchNeedle = query.trim().toLowerCase();
    const elements: ElementDefinition[] = [
      ...visibleNodes.map((node, index) => {
        const matchesSearch = searchNeedle && [node.label, node.id, node.entity_type, node.document_title]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(searchNeedle));
        return {
          data: {
            id: node.id,
            label: node.label,
            displayLabel: searchNeedle ? (matchesSearch ? node.label : "") : (index < 90 ? node.label : ""),
            type: node.type,
            entityType: node.entity_type || node.page_type || node.type,
            size: Math.min(62, 22 + Math.sqrt((degree.get(node.id) || 1) * 18)),
          },
          classes: `node-${node.type}`,
        };
      }),
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
      wheelSensitivity: 0.48,
      hideEdgesOnViewport: true,
      hideLabelsOnViewport: true,
      textureOnViewport: true,
      pixelRatio: 1,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#60a5fa",
            "border-color": "#ffffff",
            "border-width": 2,
            color: "#172033",
            "font-size": 10,
            label: "data(displayLabel)",
            "min-zoomed-font-size": 10,
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
            "curve-style": "straight",
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
        name: query ? "concentric" : "cose",
        animate: false,
        fit: true,
        padding: 42,
        nodeRepulsion: 9000,
        idealEdgeLength: 120,
        edgeElasticity: 90,
        nestingFactor: 1.1,
        numIter: query ? undefined : 360,
        minNodeSpacing: 28,
      } as any,
    });

    cy.on("tap", "node", (event) => {
      const node = byId.get(event.target.id());
      if (!node) return;
      setSelected(node);
      if (node.type === "evidence" && node.chunk_id) openEvidenceRef.current(node.chunk_id, node);
    });
    cy.on("tap", (event) => {
      if (event.target === cy) {
        setSelected(null);
        cy.elements().removeClass("faded highlighted selected");
      }
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, [visibleNodes, visibleEdges, byId, degree, query]);

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
          value={queryInput}
          onChange={(event) => setQueryInput(event.target.value)}
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
