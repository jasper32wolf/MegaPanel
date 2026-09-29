import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { HelpTip, PageHeader, StatusPill, Surface } from "../components/ui";

type Revision = {
  id: string;
  key: string;
  version: number;
  instructions: string;
  active: boolean;
  state: string;
  effective_diff: string | null;
  created_at: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
};
type Prompt = {
  id: string;
  baseline_version: string;
  baseline_hash: string;
  path: string;
  revisions: Revision[];
};

function revisionTone(revision: Revision) {
  if (revision.active || revision.state === "active") return "ok" as const;
  if (revision.state === "review") return "warn" as const;
  return "default" as const;
}

export function PromptsPage() {
  const { token } = useAuth();
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selected = prompts.find((prompt) => prompt.id === selectedId) || prompts[0] || null;

  async function load() {
    const items = await api<Prompt[]>("/api/v1/ai/prompts", {}, token);
    setPrompts(items);
    setSelectedId((current) => current || items[0]?.id || "");
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить prompts"));
  }, [token]);

  async function createRevision(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy("create");
    setError(null);
    setMessage(null);
    try {
      await api(`/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/revisions`, {
        method: "POST",
        body: JSON.stringify({ instructions }),
      }, token);
      setInstructions("");
      await load();
      setMessage("Черновик revision сохранён. Он не используется AI до явной активации.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить revision");
    } finally {
      setBusy(null);
    }
  }

  async function transition(revision: Revision, action: "submit-review" | "approve" | "activate") {
    if (!selected) return;
    setBusy(`${action}:${revision.id}`);
    setError(null);
    try {
      await api(`/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/revisions/${revision.id}/${action}`, {
        method: "POST",
        body: action === "approve" ? JSON.stringify({}) : undefined,
      }, token);
      await load();
      setMessage(action === "submit-review" ? "Revision отправлена на review." : action === "approve" ? "Revision одобрена; теперь её можно активировать." : `Активирована revision ${revision.version}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция с revision не выполнена");
    } finally {
      setBusy(null);
    }
  }

  async function rollbackBaseline() {
    if (!selected) return;
    setBusy("rollback");
    setError(null);
    try {
      await api(`/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/rollback-baseline`, { method: "POST" }, token);
      await load();
      setMessage("Возвращён packaged baseline. Сайт и PagePlan не изменялись.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось вернуть baseline");
    } finally {
      setBusy(null);
    }
  }

  async function activate(revision: Revision) {
    await transition(revision, "activate");
  }

  return (
    <div>
      <PageHeader title="Системные prompts" description="Версионируемые инструкции для AI действий. Они уточняют задачу, но не могут отключить server-side privacy policy, schema validation, budget или human approval." />
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" role="status">{message}</p> : null}
      <Surface title="Каталог действий">
        <label className="field">
          Prompt
          <select value={selected?.id || ""} onChange={(event) => setSelectedId(event.target.value)}>
            {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.id} · baseline v{prompt.baseline_version}</option>)}
          </select>
        </label>
        {selected ? <p className="muted">Built-in source: {selected.path} · hash {selected.baseline_hash.slice(0, 16)}… <HelpTip label="Baseline prompt">Packed baseline неизменяем в панели. Активная operator revision добавляется после него как ограниченное уточнение и остаётся traceable в AIRun.</HelpTip></p> : <p className="muted">Загрузка каталога…</p>}
      </Surface>
      {selected ? <>
        <Surface title="Новая revision">
          <form className="stack" onSubmit={createRevision}>
            <label className="field">
              Инструкции оператора
              <textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={10} placeholder="Например: сначала объясняй неопределённости, не предлагай страницы без подтверждённой семантики." required />
              <span className="muted">Не помещайте сюда секреты, контакты лидов или команды обхода approval/publish. Такие правила контролируются сервером и не могут быть переопределены prompt text.</span>
            </label>
            <button className="btn" type="submit" disabled={busy !== null || !instructions.trim()}>{busy === "create" ? "Сохранение…" : "Создать draft revision"}</button>
          </form>
        </Surface>
        <Surface title="Revision history">
          <p className="muted">Lifecycle: draft → review → approved → active. Возврат к packaged baseline не публикует сайт и не меняет PagePlan.</p>
          <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void rollbackBaseline()}>{busy === "rollback" ? "Возврат…" : "Вернуть packaged baseline"}</button>
          {selected.revisions.length === 0 ? <p className="muted">Operator revisions пока нет. Используется packaged baseline.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Версия</th><th>Статус</th><th>Инструкции</th><th>Создана</th><th></th></tr></thead><tbody>{selected.revisions.map((revision) => <tr key={revision.id}><td>{revision.version}</td><td><StatusPill tone={revisionTone(revision)}>{revision.active ? "активна" : revision.state}</StatusPill></td><td><details><summary>Инструкции</summary><pre className="code-block">{revision.instructions}</pre></details>{revision.effective_diff && <details><summary>Diff к packaged baseline</summary><pre className="code-block">{revision.effective_diff}</pre></details>}</td><td className="muted">{revision.created_at?.slice(0, 19) || "—"}</td><td className="row">{revision.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "submit-review")}>{busy === `submit-review:${revision.id}` ? "Отправка…" : "На review"}</button>}{revision.state === "review" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "approve")}>{busy === `approve:${revision.id}` ? "Одобрение…" : "Одобрить"}</button>}{revision.state === "approved" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void activate(revision)}>{busy === `activate:${revision.id}` ? "Активация…" : "Активировать"}</button>}</td></tr>)}</tbody></table></div>}
        </Surface>
      </> : null}
    </div>
  );
}
