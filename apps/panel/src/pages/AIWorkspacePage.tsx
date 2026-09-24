import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, Surface } from "../components/ui";

type Provider = { id: string; label: string; provider_id: string; enabled: boolean };
type Quote = {
  provider_id: string;
  model_id: string;
  estimated_cost_usd: number;
  max_cost_usd: number;
  input_snapshot_hash: string;
  pricing_source: string;
  pricing_observed_at: string;
  expires_in_seconds: number;
};
type Proposal = {
  run_id: string;
  status: string;
  prompt_id: string;
  prompt_version: string;
  prompt_hash: string;
  input_snapshot_hash: string;
  pages: Array<Record<string, unknown>>;
  page_plan_ids: string[];
  page_plans_imported: boolean;
  estimated_cost_usd: number | null;
  max_cost_usd: number | null;
  error_code: string | null;
  requires_operator_approval: true;
};

type RunSummary = {
  id: string;
  action: string;
  status: string;
  provider_id: string | null;
  model_id: string | null;
  prompt_id: string;
  prompt_version: string;
  prompt_hash: string;
  input_snapshot_hash: string;
  usage: { input_tokens?: number; output_tokens?: number };
  cost_usd: number | null;
  error_code: string | null;
  created_at: string | null;
};

export function AIWorkspacePage() {
  const { token } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runProjectId, setRunProjectId] = useState("");
  const [runAction, setRunAction] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [providerId, setProviderId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [model, setModel] = useState("");
  const [constraints, setConstraints] = useState("");
  const [maxCost, setMaxCost] = useState("0.05");
  const [maxOutputTokens, setMaxOutputTokens] = useState("2048");
  const [consented, setConsented] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    api<Provider[]>("/api/v1/ai/providers", {}, token)
      .then((items) => {
        const active = items.filter((item) => item.enabled);
        setProviders(active);
        if (active.length === 1) setProviderId(active[0].id);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить провайдеры"));
  }, [token]);

  async function loadRuns() {
    const params = new URLSearchParams();
    if (runProjectId.trim()) params.set("project_id", runProjectId.trim());
    if (runAction) params.set("action", runAction);
    if (runStatus) params.set("status", runStatus);
    const query = params.size ? `?${params.toString()}` : "";
    setRuns(await api<RunSummary[]>(`/api/v1/ai/runs${query}`, {}, token));
  }

  useEffect(() => {
    loadRuns().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить AI runs"));
  }, [token]);

  async function calculateQuote() {
    setBusy(true);
    setError(null);
    setMessage(null);
    setConsented(false);
    try {
      const result = await api<Quote>(
        `/api/v1/ai/projects/${projectId.trim()}/architecture/quote`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: providerId,
            model: model.trim(),
            max_cost_usd: Number(maxCost),
            max_output_tokens: Number(maxOutputTokens),
            operator_constraints: constraints
              .split("\n")
              .map((item) => item.trim())
              .filter(Boolean),
          }),
        },
        token,
      );
      setQuote(result);
      setMessage("Предварительная оценка рассчитана локальным API; внешний provider не вызывался.");
    } catch (cause) {
      setQuote(null);
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать стоимость");
    } finally {
      setBusy(false);
    }
  }

  async function requestProposal(event: FormEvent) {
    event.preventDefault();
    if (!quote || !consented) {
      setError("Сначала получите предварительную оценку и подтвердите её.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api<Proposal>(
        `/api/v1/ai/projects/${projectId.trim()}/architecture`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: providerId,
            model: model.trim(),
            confirm_external_processing: true,
            confirm_provider_budget: true,
            max_cost_usd: Number(maxCost),
            max_output_tokens: Number(maxOutputTokens),
            confirmed_estimated_cost_usd: quote.estimated_cost_usd,
            quote_snapshot_hash: quote.input_snapshot_hash,
            operator_constraints: constraints
              .split("\n")
              .map((item) => item.trim())
              .filter(Boolean),
          }),
        },
        token,
      );
      setProposal(result);
      await loadRuns();
      setMessage("Предложение создано и остановлено на human-approval gate.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать предложение");
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<Proposal>(
        `/api/v1/ai/runs/${proposal.run_id}/decision`,
        { method: "POST", body: JSON.stringify({ decision }) },
        token,
      );
      setProposal(result);
      await loadRuns();
      setMessage(
        decision === "approve"
          ? "Предложение одобрено. Следующий шаг отдельно создаст draft PagePlan."
          : "Предложение отклонено; проект не изменён.",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Решение не сохранено");
    } finally {
      setBusy(false);
    }
  }

  async function createPagePlans() {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<Proposal>(
        `/api/v1/ai/runs/${proposal.run_id}/page-plans`,
        { method: "POST" },
        token,
      );
      setProposal(result);
      await loadRuns();
      setMessage("Созданы draft PagePlan. Проверка и утверждение выполняются в рабочем процессе проекта.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Черновики PagePlan не созданы");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="AI workspace"
        description="AI анализирует только подтверждённые данные проекта и создаёт проверяемое предложение. PagePlan, публикация и live-сайт не меняются автоматически."
      />
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="success" role="status">{message}</p>}
      <Surface title="История AI runs">
        <div className="row">
          <label className="field">Project ID<input value={runProjectId} onChange={(event) => setRunProjectId(event.target.value)} placeholder="Все проекты" /></label>
          <label className="field">Действие<select value={runAction} onChange={(event) => setRunAction(event.target.value)}><option value="">Все</option><option value="architecture.site-map">Архитектура</option><option value="seo.create-brief">SEO brief</option><option value="content.page-draft-copy">PageDraft copy</option></select></label>
          <label className="field">Статус<select value={runStatus} onChange={(event) => setRunStatus(event.target.value)}><option value="">Все</option><option value="pending_approval">Ожидает решения</option><option value="approved">Одобрен</option><option value="rejected">Отклонён</option><option value="completed">Завершён</option><option value="failed">Ошибка</option></select></label>
          <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => void loadRuns().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось обновить AI runs"))}>Применить фильтры</button>
        </div>
        <p className="muted">{runs.length} последних runs; входной контекст и ответы моделей не показываются в списке.</p>
        {runs.length === 0 ? <p className="muted">AI-запусков пока нет.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Действие</th><th>Статус</th><th>Провайдер / модель</th><th>Токены</th><th>Стоимость</th><th>Когда</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td>{run.action}</td><td>{run.status}{run.error_code && <p className="error">{run.error_code}</p>}</td><td>{run.provider_id || "—"} / {run.model_id || "—"}</td><td>{run.usage.input_tokens ?? 0} / {run.usage.output_tokens ?? 0}</td><td>{run.cost_usd === null ? "—" : `$${run.cost_usd.toFixed(6)}`}</td><td className="muted">{run.created_at?.slice(0, 19) || "—"}</td></tr>)}</tbody></table></div>}
      </Surface>
      <Surface title="Предложить структуру сайта">
        <form className="stack" onSubmit={requestProposal}>
          <label className="field">Project ID<input value={projectId} onChange={(event) => { setProjectId(event.target.value); setQuote(null); setConsented(false); }} placeholder="UUID проекта" required /></label>
          <label className="field">Активное подключение<select value={providerId} onChange={(event) => { setProviderId(event.target.value); setQuote(null); setConsented(false); }} required><option value="">Выберите провайдера</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label} ({provider.provider_id})</option>)}</select></label>
          <label className="field">Model ID<input value={model} onChange={(event) => { setModel(event.target.value); setQuote(null); setConsented(false); }} placeholder="например, glm-4-flash" required /></label>
          <div className="detail-grid">
            <label className="field">Максимальный расчётный бюджет, USD<input type="number" min="0.000001" max="100" step="0.000001" value={maxCost} onChange={(event) => { setMaxCost(event.target.value); setQuote(null); setConsented(false); }} required /></label>
            <label className="field">Лимит выходных токенов<input type="number" min="128" max="4096" step="1" value={maxOutputTokens} onChange={(event) => { setMaxOutputTokens(event.target.value); setQuote(null); setConsented(false); }} required /></label>
          </div>
          <label className="field">Ограничения оператора<textarea value={constraints} onChange={(event) => { setConstraints(event.target.value); setQuote(null); setConsented(false); }} rows={4} placeholder="Одно ограничение на строку" /></label>
          <button className="btn btn-ghost" type="button" onClick={() => void calculateQuote()} disabled={busy || !providerId || !projectId || !model}>{busy ? "Расчёт…" : "Рассчитать стоимость до запроса"}</button>
          {quote && <Surface title="Оценка до генерации"><p><strong>${quote.estimated_cost_usd.toFixed(6)}</strong> (верхняя оценка) · лимит ${quote.max_cost_usd.toFixed(6)}</p><p className="muted">Источник: {quote.pricing_source}; актуально на {quote.pricing_observed_at}. Оценка не гарантирует фактический счёт: дневной/30-дневный лимиты являются preflight-проверкой, а не резервированием средств при одновременных запросах. Для жёсткого ограничения настройте spending cap у провайдера.</p></Surface>}
          <label className="field"><span><input type="checkbox" checked={consented} onChange={(event) => setConsented(event.target.checked)} required disabled={!quote} /> Подтверждаю именно показанную оценку, передачу контекста провайдеру и наличие у провайдера соответствующего spending limit. Фактическая цена/retention определяются провайдером.</span></label>
          <button className="btn" type="submit" disabled={busy || !providerId || !consented || !quote}>{busy ? "Генерация…" : "Подтвердить оценку и создать предложение"}</button>
        </form>
      </Surface>
      {proposal && <Surface title="Результат и approval gate"><div className="detail-grid"><div><strong>Статус</strong><p>{proposal.status}</p></div><div><strong>Расчётная стоимость</strong><p>${proposal.estimated_cost_usd?.toFixed(6) ?? "—"} / ${proposal.max_cost_usd?.toFixed(6) ?? "—"}</p></div><div><strong>Prompt</strong><p>{proposal.prompt_id} · v{proposal.prompt_version}</p></div><div><strong>Prompt hash</strong><p><code>{proposal.prompt_hash.slice(0, 16)}…</code></p></div><div><strong>Input snapshot</strong><p><code>{proposal.input_snapshot_hash.slice(0, 16)}…</code></p></div></div>{proposal.error_code && <p className="error" role="alert">Run остановлен: {proposal.error_code}. Проверьте расчёт/лимит провайдера; предложение не утверждено.</p>}<p className="muted">Предложений страниц: {proposal.pages.length}. Результат остаётся отдельным от PagePlan.</p>{proposal.pages.length > 0 && <pre className="code-block">{JSON.stringify(proposal.pages, null, 2)}</pre>}{proposal.status === "pending_approval" && <div className="row"><button className="btn" type="button" disabled={busy} onClick={() => decide("approve")}>Одобрить предложение</button><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => decide("reject")}>Отклонить</button></div>}{proposal.status === "approved" && !proposal.page_plans_imported && <button className="btn" type="button" disabled={busy} onClick={createPagePlans}>Создать черновики PagePlan</button>}{proposal.page_plan_ids.length > 0 && <p>Черновики PagePlan: {proposal.page_plan_ids.join(", ")}</p>}</Surface>}
    </div>
  );
}
