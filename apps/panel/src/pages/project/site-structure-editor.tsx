import { useEffect, useMemo, useState, type FormEvent } from "react";
import { DataTable, EmptyState, Surface } from "../../components/ui";
import { api } from "../../lib/auth";

export type StructurePage = {
  key: string;
  parent_key: string | null;
  slug: string;
  title: string;
  meta_description: string;
  h1: string;
  heading_outline: { level: "h2" | "h3" | "h4" | "h5" | "h6"; text: string }[];
  objective: string;
  intent: string | null;
  kit_key: string;
  block_ids: string[];
  risk_notes: string | null;
};

export type StructureRevisionEditorValue = {
  id: string;
  state: string;
  semantic_collection_id: string;
  evidence_ids: string[];
  structure: { pages?: StructurePage[] };
  structure_hash: string;
  source_snapshot: { ai_import?: { ai_run_id: string; prompt_version: string } };
};

type Collection = { id: string; version: number; state: string };
type Evidence = { id: string; kind: string; state: string; title: string | null };
type Kit = { key: string; name?: string; blocks: string[] };

type Props = {
  projectId: string;
  token: string | null;
  revision: StructureRevisionEditorValue | null;
  collections: Collection[];
  evidence: Evidence[];
  kits: Kit[];
  busy: boolean;
  onSaved: (message: string) => Promise<void>;
  onError: (message: string) => void;
};

function defaultPage(): StructurePage {
  return {
    key: "home",
    parent_key: null,
    slug: "/",
    title: "Новая страница",
    meta_description: "",
    h1: "",
    heading_outline: [],
    objective: "Опишите цель страницы",
    intent: null,
    kit_key: "service-local-v1",
    block_ids: ["hero"],
    risk_notes: null,
  };
}

function outlineText(page: StructurePage) {
  return page.heading_outline.map((heading) => `${heading.level}: ${heading.text}`).join("\n");
}

function parseOutline(value: string): StructurePage["heading_outline"] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
    const match = /^(h[2-6])\s*:\s*(.+)$/i.exec(line);
    if (!match) throw new Error("Outline: каждая строка должна иметь формат h2: Текст");
    return { level: match[1].toLowerCase() as "h2" | "h3" | "h4" | "h5" | "h6", text: match[2].trim() };
  });
}

function descendants(key: string, pages: StructurePage[]) {
  const children = new Map<string, string[]>();
  for (const page of pages) {
    if (page.parent_key) children.set(page.parent_key, [...(children.get(page.parent_key) || []), page.key]);
  }
  const result = new Set<string>([key]);
  const visit = (parent: string) => (children.get(parent) || []).forEach((child) => {
    if (!result.has(child)) { result.add(child); visit(child); }
  });
  visit(key);
  return result;
}

export function SiteStructureEditor({ projectId, token, revision, collections, evidence, kits, busy, onSaved, onError }: Props) {
  const [semanticCollectionId, setSemanticCollectionId] = useState("");
  const [evidenceIds, setEvidenceIds] = useState<string[]>([]);
  const [pages, setPages] = useState<StructurePage[]>([defaultPage()]);
  const [outlines, setOutlines] = useState<Record<string, string>>({});

  useEffect(() => {
    const nextPages = revision?.structure.pages || [defaultPage()];
    setSemanticCollectionId(revision?.semantic_collection_id || collections[0]?.id || "");
    setEvidenceIds(revision?.evidence_ids || []);
    setPages(nextPages);
    setOutlines(Object.fromEntries(nextPages.map((page) => [page.key, outlineText(page)])));
  }, [revision?.id, revision?.structure_hash, collections]);

  const activeKits = useMemo(() => new Map(kits.map((kit) => [kit.key, kit])), [kits]);
  const isEditing = Boolean(revision);

  function updatePage(index: number, patch: Partial<StructurePage>) {
    setPages((current) => current.map((page, pageIndex) => pageIndex === index ? { ...page, ...patch } : page));
  }

  function toggleEvidence(id: string) {
    setEvidenceIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function toggleBlock(index: number, blockId: string) {
    const page = pages[index];
    updatePage(index, { block_ids: page.block_ids.includes(blockId) ? page.block_ids.filter((item) => item !== blockId) : [...page.block_ids, blockId] });
  }

  function addPage() {
    const key = `page-${pages.length + 1}`;
    const page = { ...defaultPage(), key, slug: `/page-${pages.length + 1}`, title: "Новая страница", objective: "Опишите цель страницы" };
    setPages((current) => [...current, page]);
    setOutlines((current) => ({ ...current, [key]: "" }));
  }

  function removePage(index: number) {
    if (pages.length === 1) return;
    const removed = pages[index];
    setPages((current) => current.filter((_, pageIndex) => pageIndex !== index).map((page) => page.parent_key === removed.key ? { ...page, parent_key: null } : page));
    setOutlines((current) => { const next = { ...current }; delete next[removed.key]; return next; });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      const preparedPages = pages.map((page) => ({ ...page, heading_outline: parseOutline(outlines[page.key] || "") }));
      const body = { semantic_collection_id: semanticCollectionId, evidence_ids: evidenceIds, pages: preparedPages };
      if (revision) {
        await api(`/api/v1/projects/${projectId}/site-structure/revisions/${revision.id}`, { method: "PATCH", body: JSON.stringify({ ...body, expected_structure_hash: revision.structure_hash }) }, token);
        await onSaved("Черновик структуры сохранён. Review, approval и materialization остаются отдельными действиями.");
      } else {
        await api(`/api/v1/projects/${projectId}/site-structure/revisions`, { method: "POST", body: JSON.stringify(body) }, token);
        await onSaved("Создан ручной draft структуры. Проверьте страницы перед отправкой на review.");
      }
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "Не удалось сохранить структуру");
    }
  }

  if (collections.length === 0) return <Surface title="Ручная структура"><EmptyState title="Нужна approved semantic collection" hint="Создайте и одобрите semantic collection перед ручной структурой." /></Surface>;

  return <Surface title={isEditing ? "Редактирование draft структуры" : "Новая ручная структура"}>
    <form className="stack" onSubmit={save}>
      {revision?.source_snapshot.ai_import && <p className="muted">Источник draft: AI run <code>{revision.source_snapshot.ai_import.ai_run_id.slice(0, 8)}…</code> · prompt v{revision.source_snapshot.ai_import.prompt_version}. Ручные правки не удаляют эту provenance.</p>}
      <label className="field">Approved semantic collection<select value={semanticCollectionId} onChange={(event) => setSemanticCollectionId(event.target.value)} disabled={busy} required>{collections.map((collection) => <option key={collection.id} value={collection.id}>v{collection.version} · {collection.id.slice(0, 8)}…</option>)}</select></label>
      {evidence.length > 0 && <fieldset className="stack"><legend>Approved competitor evidence (необязательно)</legend>{evidence.map((item) => <label key={item.id}><input type="checkbox" checked={evidenceIds.includes(item.id)} onChange={() => toggleEvidence(item.id)} disabled={busy} /> {item.title || item.kind} · {item.id.slice(0, 8)}…</label>)}</fieldset>}
      <p className="muted">Полный документ проверяется сервером при сохранении. Parent нельзя направить на саму страницу или её потомка; server также отклоняет stale revision и любые cycles.</p>
      <DataTable headers={["Страница", "SEO и назначение", "Структура", "Curated kit/blocks", "Действия"]}>{pages.map((page, index) => {
        const forbiddenParents = descendants(page.key, pages);
        const kit = activeKits.get(page.kit_key);
        return <tr key={`${page.key}-${index}`}><td><label className="field">Key<input value={page.key} onChange={(event) => updatePage(index, { key: event.target.value })} disabled={busy} required /></label><label className="field">Parent<select value={page.parent_key || ""} onChange={(event) => updatePage(index, { parent_key: event.target.value || null })} disabled={busy}><option value="">Root</option>{pages.filter((candidate) => !forbiddenParents.has(candidate.key)).map((candidate) => <option key={candidate.key} value={candidate.key}>{candidate.key}</option>)}</select></label><label className="field">Slug<input value={page.slug} onChange={(event) => updatePage(index, { slug: event.target.value })} disabled={busy} required /></label></td><td><label className="field">Title<input value={page.title} onChange={(event) => updatePage(index, { title: event.target.value })} disabled={busy} required /></label><label className="field">Meta description<input value={page.meta_description} onChange={(event) => updatePage(index, { meta_description: event.target.value })} disabled={busy} /></label><label className="field">H1<input value={page.h1} onChange={(event) => updatePage(index, { h1: event.target.value })} disabled={busy} /></label><label className="field">Цель<textarea value={page.objective} onChange={(event) => updatePage(index, { objective: event.target.value })} disabled={busy} required /></label><label className="field">Intent<input value={page.intent || ""} onChange={(event) => updatePage(index, { intent: event.target.value || null })} disabled={busy} /></label></td><td><label className="field">H2–H6, по одной строке<textarea value={outlines[page.key] || ""} onChange={(event) => setOutlines((current) => ({ ...current, [page.key]: event.target.value }))} placeholder="h2: Услуги\nh3: Срочный ремонт" disabled={busy} /></label><label className="field">Риски<textarea value={page.risk_notes || ""} onChange={(event) => updatePage(index, { risk_notes: event.target.value || null })} disabled={busy} /></label></td><td><label className="field">Kit<select value={page.kit_key} onChange={(event) => updatePage(index, { kit_key: event.target.value, block_ids: [] })} disabled={busy}>{kits.map((item) => <option key={item.key} value={item.key}>{item.name || item.key}</option>)}</select></label>{kit ? <fieldset><legend>Blocks</legend>{kit.blocks.map((block) => <label key={block}><input type="checkbox" checked={page.block_ids.includes(block)} onChange={() => toggleBlock(index, block)} disabled={busy} /> {block}</label>)}</fieldset> : <p className="error">Kit не найден в текущем catalog.</p>}</td><td><button className="btn btn-ghost" type="button" disabled={busy || pages.length === 1} onClick={() => removePage(index)}>Удалить</button></td></tr>;
      })}</DataTable>
      <div className="row"><button className="btn btn-ghost" type="button" disabled={busy || pages.length >= 100} onClick={addPage}>Добавить страницу</button><button className="btn" type="submit" disabled={busy || !semanticCollectionId}>{busy ? "Сохранение…" : isEditing ? "Сохранить draft" : "Создать ручной draft"}</button></div>
    </form>
  </Surface>;
}
