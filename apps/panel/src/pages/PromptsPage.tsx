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
  stale: boolean;
  runtime_using_packaged_baseline: boolean;
  baseline_hash: string;
  activation: { eligible: boolean; blockers: string[] };
  activation_eligible: boolean;
  effective_diff: string | null;
  created_at: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
};

type Prompt = {
  id: string;
  baseline_version: string;
  baseline_hash: string;
  path: string;
  revisions: Revision[];
};

type Evaluation = {
  id: string;
  status: string;
  baseline_hash: string;
  effective_prompt_hash: string;
  fixture_hash: string;
  ruleset_version: string;
  case_count: number;
  passed_count: number;
  error: string | null;
  completed_at: string | null;
  cases: { name: string; status: string; assertion_keys: string[]; diagnostic: string | null }[];
};

const activationBlockerText: Record<string, string> = {
  baseline_unavailable: "Packaged baseline недоступен.",
  readiness_unavailable: "Актуальная readiness проверяется при загрузке истории revisions.",
  not_approved: "Для активации требуется approved revision.",
  baseline_changed: "Packaged baseline изменился; runtime использует packaged baseline.",
  fixture_unavailable: "Текущий offline fixture недоступен или невалиден.",
  evaluation_missing: "Выполните текущую offline fixture evaluation перед активацией.",
  prompt_changed: "Сохранённая evaluation относится к другой effective prompt revision.",
  fixture_changed: "Сохранённая evaluation относится к предыдущей версии fixture.",
  evaluation_failed: "Текущая offline fixture evaluation не прошла.",
};

function revisionTone(revision: Revision) {
  if (revision.active || revision.state === "active") return "ok" as const;
  if (revision.state === "review") return "warn" as const;
  return "default" as const;
}

function statusTime(value: string | null) {
  return value?.slice(0, 19) || "—";
}

export function PromptsPage() {
  const { token } = useAuth();
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [instructions, setInstructions] = useState("");
  const [rejectionReasons, setRejectionReasons] = useState<Record<string, string>>({});
  const [evaluations, setEvaluations] = useState<Record<string, Evaluation[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const selected = prompts.find((prompt) => prompt.id === selectedId) || prompts[0] || null;

  async function load() {
    const items = await api<Prompt[]>("/api/v1/ai/prompts", {}, token);
    setPrompts(items);
    setSelectedId((current) => current || items[0]?.id || "");
  }

  async function loadEvaluations(revision: Revision) {
    if (!selected) return;
    setBusy(`evaluations:${revision.id}`);
    setError(null);
    try {
      const runs = await api<Evaluation[]>(
        `/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/revisions/${revision.id}/evaluations`,
        {},
        token,
      );
      setEvaluations((current) => ({ ...current, [revision.id]: runs }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить результаты evaluation");
    } finally {
      setBusy(null);
    }
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

  async function transition(revision: Revision, action: "submit-review" | "approve" | "reject" | "activate") {
    if (!selected) return;
    setBusy(`${action}:${revision.id}`);
    setError(null);
    try {
      await api(`/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/revisions/${revision.id}/${action}`, {
        method: "POST",
        body: action === "approve" ? JSON.stringify({}) : action === "reject" ? JSON.stringify({ reason: rejectionReasons[revision.id]?.trim() }) : undefined,
      }, token);
      await load();
      setMessage(
        action === "submit-review"
          ? "Revision отправлена на review."
          : action === "approve"
            ? "Revision одобрена; для активации нужна current offline evaluation."
            : action === "reject"
              ? "Revision отклонена с сохранённой причиной."
              : `Активирована revision ${revision.version}.`,
      );
      if (action === "reject") setRejectionReasons((current) => ({ ...current, [revision.id]: "" }));
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

  async function evaluate(revision: Revision) {
    if (!selected) return;
    setBusy(`evaluate:${revision.id}`);
    setError(null);
    try {
      const result = await api<{ case_count: number; passed_count: number; ruleset_version: string }>(
        `/api/v1/ai/prompts/${encodeURIComponent(selected.id)}/revisions/${revision.id}/evaluate`,
        { method: "POST" },
        token,
      );
      await Promise.all([load(), loadEvaluations(revision)]);
      setMessage(`Offline fixture evaluation: ${result.passed_count}/${result.case_count} · ${result.ruleset_version}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить offline evaluation");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Системные prompts"
        description="Версионируемые инструкции для AI действий. Offline fixture evaluation проверяет контракт packaged fixtures, а не качество provider output."
      />
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" role="status">{message}</p> : null}
      <Surface title="Каталог действий">
        <label className="field">
          Prompt
          <select value={selected?.id || ""} onChange={(event) => setSelectedId(event.target.value)}>
            {prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.id} · baseline v{prompt.baseline_version}</option>)}
          </select>
        </label>
        {selected ? (
          <p className="muted">
            Built-in source: {selected.path} · hash {selected.baseline_hash.slice(0, 16)}…
            <HelpTip label="Baseline prompt">Packed baseline неизменяем в панели. Активная operator revision добавляется после него как ограниченное уточнение и остаётся traceable в AIRun.</HelpTip>
          </p>
        ) : <p className="muted">Загрузка каталога…</p>}
      </Surface>
      {selected ? (
        <>
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
            <p className="muted">Lifecycle: draft → review → approved → active. Активация требует current passed offline fixture evidence и повторно проверяется сервером.</p>
            <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void rollbackBaseline()}>{busy === "rollback" ? "Возврат…" : "Вернуть packaged baseline"}</button>
            {selected.revisions.length === 0 ? <p className="muted">Operator revisions пока нет. Используется packaged baseline.</p> : (
              <div className="table-wrap"><table className="table"><thead><tr><th>Версия</th><th>Статус и readiness</th><th>Инструкции и evidence</th><th>Создана</th><th>Действия</th></tr></thead><tbody>
                {selected.revisions.map((revision) => {
                  const runs = evaluations[revision.id];
                  return <tr key={revision.id}>
                    <td>{revision.version}</td>
                    <td>
                      <StatusPill tone={revision.stale ? "danger" : revisionTone(revision)}>{revision.stale ? "stale" : revision.active ? "активна" : revision.state}</StatusPill>
                      {revision.activation.blockers.map((blocker) => <p className="muted" key={blocker}>{activationBlockerText[blocker] || blocker}</p>)}
                    </td>
                    <td>
                      <details><summary>Инструкции</summary><pre className="code-block">{revision.instructions}</pre></details>
                      {revision.effective_diff ? <details><summary>Diff к packaged baseline</summary><pre className="code-block">{revision.effective_diff}</pre></details> : null}
                      {revision.decision_reason ? <p className="muted">Причина решения: {revision.decision_reason}</p> : null}
                      {runs ? <details><summary>Evaluation history · {runs.length} run(s)</summary><ul>{runs.map((run) => <li key={run.id}><StatusPill tone={run.status === "passed" ? "ok" : "danger"}>{run.status}</StatusPill> {statusTime(run.completed_at)} · {run.passed_count}/{run.case_count} · {run.ruleset_version}{run.error ? <p className="error">{run.error}</p> : null}<details><summary>Fixture cases</summary><ul>{run.cases.map((item) => <li key={item.name}><StatusPill tone={item.status === "passed" ? "ok" : "danger"}>{item.status}</StatusPill> {item.name} · {item.assertion_keys.join(", ")}{item.diagnostic ? ` · ${item.diagnostic}` : ""}</li>)}</ul></details></li>)}</ul></details> : null}
                    </td>
                    <td className="muted">{statusTime(revision.created_at)}</td>
                    <td className="row">
                      {revision.state === "draft" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "submit-review")}>{busy === `submit-review:${revision.id}` ? "Отправка…" : "На review"}</button> : null}
                      {revision.state === "review" ? <><label className="field">Причина отклонения<input value={rejectionReasons[revision.id] || ""} onChange={(event) => setRejectionReasons((current) => ({ ...current, [revision.id]: event.target.value }))} minLength={3} maxLength={4000} /></label><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "approve")}>{busy === `approve:${revision.id}` ? "Одобрение…" : "Одобрить"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null || (rejectionReasons[revision.id] || "").trim().length < 3} onClick={() => void transition(revision, "reject")}>{busy === `reject:${revision.id}` ? "Отклонение…" : "Отклонить"}</button></> : null}
                      <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void loadEvaluations(revision)}>{busy === `evaluations:${revision.id}` ? "Загрузка…" : runs ? "Обновить history" : "Показать history"}</button>
                      {revision.state === "approved" ? <><button className="btn btn-ghost" type="button" disabled={busy !== null || revision.stale} onClick={() => void evaluate(revision)}>{busy === `evaluate:${revision.id}` ? "Проверка…" : "Проверить fixtures"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null || !revision.activation.eligible} onClick={() => void transition(revision, "activate")}>{busy === `activate:${revision.id}` ? "Активация…" : "Активировать"}</button></> : null}
                    </td>
                  </tr>;
                })}
              </tbody></table></div>
            )}
          </Surface>
        </>
      ) : null}
    </div>
  );
}
