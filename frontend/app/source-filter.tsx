"use client";

type Conversation = { source_filter?: { document_ids?: number[]; releases?: string[] } };
type Document = { id: number; title: string; status: string; enabled: boolean; release?: string };
type Filter = { document_ids?: number[]; releases?: string[] };

export default function SourceFilter({current, documents, onChange}:{
  current?: Conversation;
  documents: Document[];
  onChange: (filter: Filter) => Promise<void>;
}) {
  const filter = current?.source_filter || {};
  const activeDocuments = documents.filter(item => item.enabled !== false && item.status === "ready");
  const releases = Array.from(new Set(activeDocuments.map(item => item.release).filter(Boolean))) as string[];
  const count = (filter.document_ids?.length || 0) + (filter.releases?.length || 0);
  const toggle = (key: keyof Filter, value: number | string, checked: boolean) => {
    const old = (filter[key] || []) as (number | string)[];
    const next = checked ? [...old, value] : old.filter(item => item !== value);
    return onChange({...filter, [key]: next.length ? next : undefined});
  };

  return <details className="filter-menu">
    <summary>资料范围 · {count ? `已选 ${count}` : "全库"}</summary>
    <div className="filter-popover">
      <header><b>检索范围</b><button onClick={() => onChange({})}>恢复全库</button></header>
      {releases.length > 0 && <fieldset><legend>Simulink 版本</legend>{releases.map(release =>
        <label key={release}><input type="checkbox" checked={filter.releases?.includes(release) || false}
          onChange={event => toggle("releases", release, event.target.checked)}/><span>{release}</span></label>
      )}</fieldset>}
      <fieldset><legend>文档</legend>{activeDocuments.map(item =>
        <label key={item.id}><input type="checkbox" checked={filter.document_ids?.includes(item.id) || false}
          onChange={event => toggle("document_ids", item.id, event.target.checked)}/><span>{item.title}</span><small>{item.release || "未标注版本"}</small></label>
      )}</fieldset>
      <p>不选任何项时检索全库；文档和版本同时选择时取交集。</p>
    </div>
  </details>;
}
