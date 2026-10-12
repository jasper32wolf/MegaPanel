import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { HelpTip, InlineAlert, PageHeader, StatusPill, Surface } from "../components/ui";
import { getPromptGuide } from "../content/promptGuides";

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

type ActivationBlocker = { explanation: string; nextStep: string };

const activationBlockers: Record<string, ActivationBlocker> = {
  baseline_unavailable: { explanation: "Встроенный промпт сейчас недоступен.", nextStep: "Повторите попытку после восстановления каталога встроенных промптов." },
  readiness_unavailable: { explanation: "Актуальная готовность проверяется при загрузке истории версий.", nextStep: "Обновите историю версий и проверьте состояние ещё раз." },
  not_approved: { explanation: "Для активации нужна одобренная версия промпта.", nextStep: "Сначала отправьте черновик на проверку и одобрите его." },
  baseline_changed: { explanation: "Встроенный промпт изменился, поэтому сейчас используется его актуальная встроенная версия.", nextStep: "Создайте новую версию уточнения для актуального встроенного промпта." },
  fixture_unavailable: { explanation: "Контрольные примеры для этой задачи недоступны или не проходят проверку формата.", nextStep: "Не активируйте версию; проверьте состояние каталога и повторите позже." },
  evaluation_missing: { explanation: "Для этой версии ещё нет актуальной проверки на контрольных примерах.", nextStep: "Запустите проверку на контрольных примерах." },
  prompt_changed: { explanation: "Сохранённая проверка относится к другой версии итогового промпта.", nextStep: "Запустите проверку на контрольных примерах после последнего изменения." },
  fixture_changed: { explanation: "Сохранённая проверка относится к предыдущей версии контрольных примеров.", nextStep: "Запустите проверку на актуальных контрольных примерах." },
  evaluation_failed: { explanation: "Актуальная проверка на контрольных примерах не пройдена.", nextStep: "Разберите результат проверки и создайте новую версию уточнения при необходимости." },
};

const revisionLabels: Record<string, string> = {
  draft: "Черновик",
  review: "На проверке",
  approved: "Одобрено",
  active: "Активно",
  rejected: "Отклонено",
  superseded: "Заменено",
};

const evaluationLabels: Record<string, string> = {
  passed: "Проверка пройдена",
  failed: "Проверка не пройдена",
};

function revisionTone(revision: Revision) {
  if (revision.active || revision.state === "active") return "ok" as const;
  if (revision.state === "review") return "warn" as const;
  return "default" as const;
}

function statusTime(value: string | null) {
  return value?.slice(0, 19) || "—";
}

function revisionLabel(revision: Revision) {
  return revision.stale ? "Неактуальна" : revision.active ? "Активно" : revisionLabels[revision.state] || revision.state;
}

function evaluationLabel(status: string) {
  return evaluationLabels[status] || status;
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
  const guide = getPromptGuide(selected?.id || "");
  const promptsByCategory = useMemo(() => {
    const categories = new Map<string, Prompt[]>();
    prompts.forEach((prompt) => {
      const category = getPromptGuide(prompt.id).category;
      categories.set(category, [...(categories.get(category) || []), prompt]);
    });
    return [...categories.entries()];
  }, [prompts]);

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
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить результаты проверок");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить системные промпты"));
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
      setMessage("Черновик версии сохранён. Он не используется ИИ до явной активации.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить версию промпта");
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
          ? "Версия промпта отправлена на проверку."
          : action === "approve"
            ? "Версия промпта одобрена. Перед активацией выполните актуальную проверку на контрольных примерах."
            : action === "reject"
              ? "Версия промпта отклонена с сохранённой причиной."
              : `Активирована версия ${revision.version}.`,
      );
      if (action === "reject") setRejectionReasons((current) => ({ ...current, [revision.id]: "" }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция с версией промпта не выполнена");
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
      setMessage("Возвращён встроенный промпт. Сайт и планы страниц не изменялись.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось вернуть встроенный промпт");
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
      setMessage(`Проверка на контрольных примерах: ${result.passed_count}/${result.case_count} · ${result.ruleset_version}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось выполнить проверку на контрольных примерах");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Системные промпты"
        description="Встроенные инструкции задают безопасные границы задач ИИ. Вы можете создать версионное уточнение, но оно не заменяет схему результата, проверку фактов, решения оператора и серверные ограничения."
      />
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" role="status">{message}</p> : null}
      <Surface title="Выберите задачу ИИ">
        <label className="field">
          Системный промпт
          <select value={selected?.id || ""} onChange={(event) => setSelectedId(event.target.value)}>
            {promptsByCategory.map(([category, categoryPrompts]) => (
              <optgroup key={category} label={category}>
                {categoryPrompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{getPromptGuide(prompt.id).title}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        {selected ? (
          <details>
            <summary>Технические сведения</summary>
            <p className="muted">Идентификатор: {selected.id} · встроенная версия {selected.baseline_version} · исходный файл: {selected.path} · хеш: {selected.baseline_hash.slice(0, 16)}…</p>
          </details>
        ) : <p className="muted">Загрузка каталога задач…</p>}
      </Surface>
      {selected ? (
        <>
          <Surface title={guide.title}>
            <p>{guide.summary}</p>
            <div className="detail-grid">
              <div><strong>За что отвечает промпт</strong><p className="muted">{guide.purpose}</p></div>
              <div><strong>Какой результат вы получите</strong><p className="muted">{guide.result}</p></div>
              <div><strong>Что не происходит автоматически</strong><p className="muted">{guide.doesNotDo}</p></div>
            </div>
            <h3>Как составить уточнение</h3>
            <ul>
              {guide.advice.map((item) => <li key={item}>{item}</li>)}
            </ul>
            <details>
              <summary>Пример допустимого уточнения</summary>
              <p className="code-block">{guide.example}</p>
              <p className="muted">Это образец уточнения, а не текст, который нужно копировать без изменений.</p>
            </details>
            {guide.caution ? <InlineAlert severity="warning" title="Нужна дополнительная справка">{guide.caution}</InlineAlert> : null}
            <p className="muted">После сохранения используйте обычный порядок: черновик → проверка → одобрение → контрольные примеры → активация.</p>
          </Surface>
          <Surface title="Новое уточнение к промпту">
            <form className="stack" onSubmit={createRevision}>
              <label className="field">
                Дополнительные инструкции для этой задачи
                <textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={10} placeholder={guide.example} required />
                <span className="muted">Этот текст добавляется к встроенному промпту, а не заменяет его. Не помещайте сюда секреты, контакты заявок, персональные данные или команды обхода проверки, одобрения и публикации.</span>
              </label>
              <button className="btn" type="submit" disabled={busy !== null || !instructions.trim()}>{busy === "create" ? "Сохранение…" : "Создать черновик версии"}</button>
            </form>
          </Surface>
          <Surface title="История версий">
            <p className="muted">Порядок работы: черновик → проверка → одобрено → активно. Активация требует актуальной пройденной проверки на контрольных примерах и повторно проверяется сервером.</p>
            <InlineAlert severity="info" title="О границах проверки">Проверка на контрольных примерах подтверждает актуальность встроенного промпта и формат контрольных примеров. Она не оценивает качество реального ответа провайдера и ничего не публикует.<HelpTip label="Подробнее о системных промптах" topicId="system-prompts">Версия промпта дополняет встроенную инструкцию. Откройте справку, чтобы узнать, когда создавать версию, как пройти все этапы и что означает возврат к встроенному промпту.</HelpTip></InlineAlert>
            <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void rollbackBaseline()}>{busy === "rollback" ? "Возврат…" : "Вернуть встроенный промпт"}</button>
            {selected.revisions.length === 0 ? <div className="empty"><p>Сохранённых версий для этой задачи пока нет.</p><p className="muted">Встроенный промпт уже используется. Создавайте уточнение только когда требуется обоснованно изменить поведение этой конкретной задачи.</p><Link className="btn btn-ghost" to="/help?topic=system-prompts">Открыть справку о системных промптах</Link></div> : (
              <div className="table-wrap"><table className="table"><thead><tr><th>Версия</th><th>Статус и готовность</th><th>Уточнение и проверки</th><th>Создана</th><th>Действия</th></tr></thead><tbody>
                {selected.revisions.map((revision) => {
                  const runs = evaluations[revision.id];
                  return <tr key={revision.id}>
                    <td>{revision.version}</td>
                    <td>
                      <StatusPill tone={revision.stale ? "danger" : revisionTone(revision)}>{revisionLabel(revision)}</StatusPill>
                      {revision.runtime_using_packaged_baseline ? <InlineAlert severity="warning" title="Сейчас используется встроенный промпт">Создайте новую версию уточнения, проверьте её на контрольных примерах и активируйте после одобрения.</InlineAlert> : null}
                      {revision.activation.blockers.map((blocker) => {
                        const detail = activationBlockers[blocker];
                        return <p className="muted" key={blocker}>{detail ? <><strong>{detail.explanation}</strong> {detail.nextStep}</> : blocker}</p>;
                      })}
                    </td>
                    <td>
                      <details><summary>Дополнительные инструкции</summary><pre className="code-block">{revision.instructions}</pre></details>
                      {revision.effective_diff ? <details><summary>Изменения относительно встроенного промпта</summary><pre className="code-block">{revision.effective_diff}</pre></details> : null}
                      {revision.decision_reason ? <p className="muted">Причина решения: {revision.decision_reason}</p> : null}
                      {runs ? <details><summary>История проверок · {runs.length}</summary><ul>{runs.map((run) => <li key={run.id}><StatusPill tone={run.status === "passed" ? "ok" : "danger"}>{evaluationLabel(run.status)}</StatusPill> {statusTime(run.completed_at)} · {run.passed_count}/{run.case_count} · {run.ruleset_version}{run.error ? <p className="error">{run.error}</p> : null}<details><summary>Контрольные случаи</summary><ul>{run.cases.map((item) => <li key={item.name}><StatusPill tone={item.status === "passed" ? "ok" : "danger"}>{evaluationLabel(item.status)}</StatusPill> {item.name} · {item.assertion_keys.join(", ")}{item.diagnostic ? ` · ${item.diagnostic}` : ""}</li>)}</ul></details></li>)}</ul></details> : null}
                    </td>
                    <td className="muted">{statusTime(revision.created_at)}</td>
                    <td className="row">
                      {revision.state === "draft" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "submit-review")}>{busy === `submit-review:${revision.id}` ? "Отправка…" : "На проверку"}</button> : null}
                      {revision.state === "review" ? <><label className="field">Причина отклонения<input value={rejectionReasons[revision.id] || ""} onChange={(event) => setRejectionReasons((current) => ({ ...current, [revision.id]: event.target.value }))} minLength={3} maxLength={4000} /></label><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void transition(revision, "approve")}>{busy === `approve:${revision.id}` ? "Одобрение…" : "Одобрить"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null || (rejectionReasons[revision.id] || "").trim().length < 3} onClick={() => void transition(revision, "reject")}>{busy === `reject:${revision.id}` ? "Отклонение…" : "Отклонить"}</button></> : null}
                      <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void loadEvaluations(revision)}>{busy === `evaluations:${revision.id}` ? "Загрузка…" : runs ? "Обновить историю" : "Показать историю"}</button>
                      {revision.state === "approved" ? <><button className="btn btn-ghost" type="button" disabled={busy !== null || revision.stale} onClick={() => void evaluate(revision)}>{busy === `evaluate:${revision.id}` ? "Проверка…" : "Проверить на контрольных примерах"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null || !revision.activation.eligible} onClick={() => void transition(revision, "activate")}>{busy === `activate:${revision.id}` ? "Активация…" : "Активировать"}</button></> : null}
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
