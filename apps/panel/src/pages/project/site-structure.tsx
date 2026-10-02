import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConfirmDialog, DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Revision = {
  id: string;
  version: number;
  state: "draft" | "review" | "approved" | "rejected";
  semantic_collection_id: string;
  evidence_ids: string[];
  structure: { pages?: Page[] };
  source_snapshot: { ai_import?: { ai_run_id: string; prompt_version: string; output_hash: string } };
  materialized_at: string | null;
  materialized_page_plan_ids: string[];
};
type Page = {
  key: string;
  parent_key: string | null;
  slug: string;
  title: string;
  meta_description: string;
  h1: string;
  heading_outline: { level: string; text: string }[];
  kit_key: string;
  block_ids: string[];
  risk_notes: string | null;
};
type Run = { id: string; action: string; status: string; prompt_version: string; created_at: string | null; cost_usd: number | null };
type SemanticCollection = { id: string; version: number; state: string };
type Evidence = { id: string; kind: string; state: string; title: string | null };
type DialogAction = { kind: "import"; run: Run } | { kind: "submit" | "approve" | "reject" | "materialize"; revision: Revision } | null;

function tone(state: Revision["state"]) {
  if (state === "approved") return "ok" as const;
  if (state === "rejected") return "danger" as const;
  if (state === "review") return "accent" as const;
  return "warn" as const;
}

export function ProjectSiteStructurePage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [collections, setCollections] = useState<SemanticCollection[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [collectionId, setCollectionId] = useState("");
  const [evidenceIds, setEvidenceIds] = useState<string[]>([]);
  const [dialog, setDialog] = useState<DialogAction>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    if (!projectId) return;
    const [nextRevisions, nextRuns, nextCollections, nextEvidence] = await Promise.all([
      api<Revision[]>(`/api/v1/projects/${projectId}/site-structure/revisions`, {}, token),
      api<Run[]>(`/api/v1/ai/runs?project_id=${encodeURIComponent(projectId)}&action=architecture.site-map&status=approved`, {}, token),
      api<SemanticCollection[]>(`/api/v1/projects/${projectId}/semantic-collections`, {}, token),
      api<Evidence[]>(`/api/v1/competitors/projects/${projectId}/evidence`, {}, token),
    ]);
    setRevisions(nextRevisions);
    setRuns(nextRuns);
    setCollections(nextCollections.filter((item) => item.state === "approved"));
    setEvidence(nextEvidence.filter((item) => item.state === "approved"));
    if (!collectionId && nextCollections.some((item) => item.state === "approved")) {
      setCollectionId(nextCollections.find((item) => item.state === "approved")!.id);
    }
  }

  useEffect(() => {
    void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить структуру сайта"));
  }, [projectId, token]);

  function toggleEvidence(id: string) {
    setEvidenceIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  async function confirm(reason: string) {
    if (!dialog || !projectId) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (dialog.kind === "import") {
        const result = await api<{ revision: Revision; imported: boolean }>(
          `/api/v1/projects/${projectId}/site-structure/revisions/import-approved-ai-run`,
          { method: "POST", body: JSON.stringify({ ai_run_id: dialog.run.id, semantic_collection_id: collectionId, evidence_ids: evidenceIds, confirm_create_draft: true }) },
          token,
        );
        setMessage(result.imported ? `Создан draft структуры v${result.revision.version}. Review, approval и materialization остаются отдельными действиями.` : `Этот AI run уже импортирован в структуру v${result.revision.version}.`);
      } else {
        const endpoint = dialog.kind === "submit" ? "submit-review" : dialog.kind;
        await api(`/api/v1/projects/${projectId}/site-structure/revisions/${dialog.revision.id}/${endpoint}`, {
          method: "POST",
          body: dialog.kind === "approve" || dialog.kind === "reject" ? JSON.stringify({ reason: reason || undefined }) : undefined,
        }, token);
        setMessage(dialog.kind === "materialize" ? "Созданы только draft PagePlan. Генерация, build и публикация не запускались." : "Статус версии структуры обновлён.");
      }
      setDialog(null);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция со структурой не выполнена");
    } finally {
      setBusy(false);
    }
  }

  const activeRevision = revisions.find((item) => item.state === "draft" || item.state === "review");
  const importedRunIds = new Set(revisions.map((item) => item.source_snapshot.ai_import?.ai_run_id).filter(Boolean));
  const importableRuns = runs.filter((run) => !importedRunIds.has(run.id));

  return <ProjectWorkspaceLayout projectId={projectId}>
    <PageHeader
      title="Структура сайта"
      description="AI-предложение сначала становится отдельной версией структуры. Только утверждённая версия может создать draft PagePlan; генерация, build и публикация всегда остаются отдельными этапами."
      actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть workspace</Link>}
    />
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    <Surface title="Импорт утверждённого AI-предложения">
      <p className="muted">Сервер повторно проверит AI-output, approved semantic collection, evidence и curated blocks. Импорт создаёт только один draft Site Structure Revision и безопасно повторяется.</p>
      {activeRevision && <p className="error">Сначала завершите или отклоните текущую структуру v{activeRevision.version} ({activeRevision.state}).</p>}
      {collections.length === 0 ? <p className="error">Для импорта нужна approved semantic collection.</p> : <label className="field">Approved semantic collection<select value={collectionId} onChange={(event) => setCollectionId(event.target.value)} disabled={busy || Boolean(activeRevision)}>{collections.map((item) => <option key={item.id} value={item.id}>v{item.version} · {item.id.slice(0, 8)}…</option>)}</select></label>}
      {evidence.length > 0 && <fieldset className="stack"><legend>Approved competitor evidence (необязательно)</legend>{evidence.map((item) => <label key={item.id}><input type="checkbox" checked={evidenceIds.includes(item.id)} onChange={() => toggleEvidence(item.id)} disabled={busy || Boolean(activeRevision)} /> {item.title || item.kind} · {item.id.slice(0, 8)}…</label>)}</fieldset>}
      {importableRuns.length === 0 ? <EmptyState title="Нет доступных утверждённых AI-предложений" hint="Одобрите architecture proposal в AI workspace либо завершите текущую версию структуры." /> : <DataTable headers={["AI run", "Prompt", "Стоимость", "Действие"]}>{importableRuns.map((run) => <tr key={run.id}><td><code>{run.id.slice(0, 8)}…</code><br /><span className="muted">{run.created_at?.slice(0, 19) || "—"}</span></td><td>v{run.prompt_version}</td><td>{run.cost_usd === null ? "—" : `$${run.cost_usd.toFixed(6)}`}</td><td><button className="btn" type="button" disabled={busy || !collectionId || Boolean(activeRevision)} onClick={() => setDialog({ kind: "import", run })}>Создать draft структуры</button></td></tr>)}</DataTable>}
    </Surface>
    <Surface title="Версии структуры">
      {revisions.length === 0 ? <EmptyState title="Версий пока нет" hint="Импортируйте утверждённое AI-предложение или создайте структуру вручную через API." /> : <DataTable headers={["Версия", "Источники", "Страницы", "Состояние", "Действия"]}>{revisions.map((revision) => <tr key={revision.id}><td>v{revision.version}<br /><span className="muted"><code>{revision.id.slice(0, 8)}…</code></span></td><td>semantic <code>{revision.semantic_collection_id.slice(0, 8)}…</code><br />evidence: {revision.evidence_ids.length}<br />{revision.source_snapshot.ai_import && <span className="muted">AI run <code>{revision.source_snapshot.ai_import.ai_run_id.slice(0, 8)}…</code></span>}</td><td>{revision.structure.pages?.length || 0}</td><td><StatusPill tone={tone(revision.state)}>{revision.state}</StatusPill>{revision.materialized_at && <p className="muted">materialized</p>}</td><td><div className="row">{revision.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDialog({ kind: "submit", revision })}>На review</button>}{revision.state === "review" && <><button className="btn" type="button" disabled={busy} onClick={() => setDialog({ kind: "approve", revision })}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDialog({ kind: "reject", revision })}>Отклонить</button></>}{revision.state === "approved" && !revision.materialized_at && <button className="btn" type="button" disabled={busy} onClick={() => setDialog({ kind: "materialize", revision })}>Создать draft PagePlan</button>}</div></td></tr>)}</DataTable>}
    </Surface>
    {revisions.map((revision) => <Surface key={`${revision.id}-preview`} title={`Preview структуры · v${revision.version}`}><DataTable headers={["Страница", "SEO", "Outline", "Blocks", "Риски"]}>{(revision.structure.pages || []).map((page) => <tr key={page.key}><td><strong>{page.slug}</strong><br /><span className="muted">{page.parent_key ? `parent: ${page.parent_key}` : "root"}</span></td><td><strong>{page.title}</strong><br />H1: {page.h1 || "—"}<br /><span className="muted">{page.meta_description || "—"}</span></td><td>{page.heading_outline.map((heading) => `${heading.level.toUpperCase()}: ${heading.text}`).join(" · ") || "—"}</td><td>{page.kit_key}<br /><span className="muted">{page.block_ids.join(", ") || "—"}</span></td><td>{page.risk_notes || "—"}</td></tr>)}</DataTable></Surface>)}
    <ConfirmDialog
      open={Boolean(dialog)}
      title={dialog?.kind === "import" ? "Создать черновик структуры?" : dialog?.kind === "materialize" ? "Создать draft PagePlan?" : dialog?.kind === "approve" ? "Одобрить структуру?" : dialog?.kind === "reject" ? "Отклонить структуру?" : "Передать структуру на review?"}
      description={dialog?.kind === "import" ? "Будет создан только draft Site Structure Revision из уже одобренного AI run. Никакие PagePlan, генерация, build или публикация сейчас не создаются." : dialog?.kind === "materialize" ? "Будут созданы только draft PagePlan. Генерация содержимого, build и публикация не запускаются." : dialog?.kind === "approve" ? "Утверждённая структура станет immutable; materialization останется отдельным действием." : dialog?.kind === "reject" ? "Версия будет отклонена. Укажите причину для audit trail." : "Сервер зафиксирует approved sources, confirmed facts и selected keyword/geo snapshots для независимой проверки."}
      confirmLabel={dialog?.kind === "import" ? "Создать draft" : dialog?.kind === "materialize" ? "Создать draft PagePlan" : dialog?.kind === "approve" ? "Одобрить" : dialog?.kind === "reject" ? "Отклонить" : "На review"}
      inputLabel={dialog?.kind === "reject" ? "Причина отклонения" : dialog?.kind === "approve" ? "Комментарий (необязательно)" : undefined}
      inputMinLength={dialog?.kind === "reject" ? 1 : 0}
      dangerous={dialog?.kind === "reject"}
      onCancel={() => setDialog(null)}
      onConfirm={(reason) => void confirm(reason)}
    />
  </ProjectWorkspaceLayout>;
}
