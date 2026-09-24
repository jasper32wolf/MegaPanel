import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Project = { id: string; name: string; domain: string | null; niche: string | null; site_id: string | null; current_fact_revision_id: string | null; domain_check_meta: { dns_status?: string; ssl_status?: string; checked_at?: string } };
type FactRevision = { id: string; version: number; state: string; facts: Record<string, unknown>; source_notes: string | null };
type Keyword = { id: string; phrase: string; meta: Record<string, string> };
type ProjectKeyword = { keyword_id: string; phrase: string; cluster: string | null; intent: string | null; priority: number | null };
type GeoPlace = { id: string; name: string; kind: string; is_validated?: boolean };
type ProjectGeo = { geo_id: string; name: string; kind: string; validated: boolean; role: "primary" | "service_area" | "reference"; position: number };
type Plan = { id: string; slug: string; objective: string; intent: string | null; kit_key: string; state: string; version: number; decision_reason: string | null };
type Draft = { id: string; page_plan_id: string; revision: number; state: string; content_hash: string | null; last_qa_verdict: string | null; qa_runs: { verdict: string; findings: { verdict: string; rule: string; evidence: string }[] }[]; page_manifest: Record<string, unknown>; failure_message: string | null };
type Coverage = { selected: number; covered: number; uncovered: { keyword_id: string; phrase: string }[]; plans: number };
type Build = { id: string; status: string; build_hash: string | null; previous_build_hash: string | null; pages_built: number; created_at: string | null; activated_at: string | null };
type AIProvider = { id: string; label: string; provider_id: string; enabled: boolean };
type AIDraftQuote = { provider_id: string; model_id: string; estimated_cost_usd: number; max_cost_usd: number; input_snapshot_hash: string; pricing_source: string; pricing_observed_at: string };
type AIRunBrief = { id: string; action: string; status: string; output: { brief?: Record<string, unknown> }; error_code: string | null; prompt_hash: string; cost_usd: number | null };

function tone(state: string) {
  if (["approved", "applied", "pass", "confirmed"].includes(state)) return "ok" as const;
  if (["rejected", "block", "failed"].includes(state)) return "danger" as const;
  if (["review", "warn"].includes(state)) return "warn" as const;
  return "accent" as const;
}

export function ProjectWorkspacePage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [facts, setFacts] = useState<FactRevision[]>([]);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [places, setPlaces] = useState<GeoPlace[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [builds, setBuilds] = useState<Build[]>([]);
  const [aiProviders, setAiProviders] = useState<AIProvider[]>([]);
  const [aiProviderId, setAiProviderId] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiPlanId, setAiPlanId] = useState("");
  const [aiMaxCost, setAiMaxCost] = useState("0.05");
  const [aiMaxOutput, setAiMaxOutput] = useState("2048");
  const [aiQuote, setAiQuote] = useState<AIDraftQuote | null>(null);
  const [aiConsent, setAiConsent] = useState(false);
  const [aiProviderError, setAiProviderError] = useState<string | null>(null);
  const [seoPlanId, setSeoPlanId] = useState("");
  const [seoMaxCost, setSeoMaxCost] = useState("0.02");
  const [seoMaxOutput, setSeoMaxOutput] = useState("1024");
  const [seoQuote, setSeoQuote] = useState<AIDraftQuote | null>(null);
  const [seoConsent, setSeoConsent] = useState(false);
  const [seoRun, setSeoRun] = useState<AIRunBrief | null>(null);
  const [organization, setOrganization] = useState("");
  const [service, setService] = useState("");
  const [phone, setPhone] = useState("");
  const [legal, setLegal] = useState("");
  const [sourceNotes, setSourceNotes] = useState("");
  const [selectedKeywordIds, setSelectedKeywordIds] = useState<string[]>([]);
  const [selectedGeoIds, setSelectedGeoIds] = useState<string[]>([]);
  const [primaryGeoId, setPrimaryGeoId] = useState("");
  const [planSlug, setPlanSlug] = useState("/");
  const [planObjective, setPlanObjective] = useState("");
  const [planIntent, setPlanIntent] = useState("");
  const [kitKey, setKitKey] = useState("service-local-v1");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const latestFact = facts[0] || null;
  const selectedKeywordSet = useMemo(() => new Set(selectedKeywordIds), [selectedKeywordIds]);
  const selectedGeoSet = useMemo(() => new Set(selectedGeoIds), [selectedGeoIds]);

  async function load() {
    const [nextProject, nextFacts, nextKeywords, nextProjectKeywords, nextPlaces, nextProjectGeo, nextPlans, nextDrafts, nextCoverage, nextBuilds] = await Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<FactRevision[]>(`/api/v1/projects/${projectId}/facts`, {}, token),
      api<{ items: Keyword[] }>("/api/v1/keywords?limit=100", {}, token),
      api<ProjectKeyword[]>(`/api/v1/projects/${projectId}/keywords`, {}, token),
      api<GeoPlace[]>("/api/v1/geo?limit=100", {}, token),
      api<ProjectGeo[]>(`/api/v1/projects/${projectId}/geo`, {}, token),
      api<Plan[]>(`/api/v1/projects/${projectId}/page-plans`, {}, token),
      api<Draft[]>(`/api/v1/projects/${projectId}/page-drafts`, {}, token),
      api<Coverage>(`/api/v1/projects/${projectId}/coverage`, {}, token),
      api<Build[]>(`/api/v1/projects/${projectId}/builds`, {}, token),
    ]);
    setProject(nextProject);
    setFacts(nextFacts);
    setKeywords(nextKeywords.items);
    setSelectedKeywordIds(nextProjectKeywords.map((item) => item.keyword_id));
    setPlaces(nextPlaces);
    setSelectedGeoIds(nextProjectGeo.map((item) => item.geo_id));
    setPrimaryGeoId(nextProjectGeo.find((item) => item.role === "primary")?.geo_id || "");
    setPlans(nextPlans);
    setDrafts(nextDrafts);
    setCoverage(nextCoverage);
    setBuilds(nextBuilds);
  }

  useEffect(() => {
    api<AIProvider[]>("/api/v1/ai/providers", {}, token)
      .then((items) => {
        const active = items.filter((item) => item.enabled);
        setAiProviders(active);
        if (!active.some((item) => item.id === aiProviderId)) {
          setAiProviderId(active.length === 1 ? active[0].id : "");
        }
        setAiProviderError(null);
      })
      .catch((cause) => {
        setAiProviders([]);
        setAiProviderError(cause instanceof Error ? cause.message : "Нет доступа к AI-провайдерам");
      });
  }, [token]);

  useEffect(() => {
    if (!projectId) return;
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить проект"));
  }, [projectId, token]);

  async function run(action: string, request: () => Promise<unknown>, success: string) {
    setBusy(action);
    setError(null);
    setMessage(null);
    try {
      await request();
      setMessage(success);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция не выполнена");
    } finally {
      setBusy(null);
    }
  }

  async function saveFacts(event: FormEvent) {
    event.preventDefault();
    await run("facts", () => api(`/api/v1/projects/${projectId}/facts`, { method: "POST", body: JSON.stringify({ facts: { organization, service, contacts: { phone }, legal: legal ? { operator: legal } : {}, allowed_claims: [] }, source_notes: sourceNotes || null }) }, token), "Черновик фактов сохранён. Подтвердите его перед планированием страниц.");
  }

  function toggleKeyword(id: string) {
    setSelectedKeywordIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function toggleGeo(id: string) {
    setSelectedGeoIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
    if (primaryGeoId === id) setPrimaryGeoId("");
  }

  async function saveKeywords() {
    await run("keywords", () => api(`/api/v1/projects/${projectId}/keywords`, { method: "PUT", body: JSON.stringify({ items: selectedKeywordIds.map((keyword_id) => ({ keyword_id })) }) }, token), "Семантика проекта сохранена.");
  }

  async function saveGeo() {
    if (!primaryGeoId) {
      setError("Выберите основной город или район проекта.");
      return;
    }
    await run("geo", () => api(`/api/v1/projects/${projectId}/geo`, { method: "PUT", body: JSON.stringify({ items: selectedGeoIds.map((geo_id, position) => ({ geo_id, role: geo_id === primaryGeoId ? "primary" : "service_area", position })) }) }, token), "География проекта сохранена.");
  }

  async function createPlan(event: FormEvent) {
    event.preventDefault();
    await run("plan", () => api(`/api/v1/projects/${projectId}/page-plans`, { method: "POST", body: JSON.stringify({ slug: planSlug, objective: planObjective, intent: planIntent || null, kit_key: kitKey }) }, token), "Черновик плана страницы создан.");
  }

  async function decision(plan: Plan, action: "submit-review" | "approve" | "reject") {
    let reason: string | undefined;
    if (action === "reject") {
      reason = window.prompt("Укажите причину отклонения плана:") || undefined;
      if (!reason) return;
    }
    if (action === "approve" && !window.confirm(`Одобрить план страницы ${plan.slug}?`)) return;
    await run(`${action}:${plan.id}`, () => api(`/api/v1/projects/${projectId}/page-plans/${plan.id}/${action}`, { method: "POST", body: JSON.stringify({ reason }) }, token), action === "approve" ? "План одобрен." : action === "reject" ? "План отклонён." : "План отправлен на проверку.");
  }

  async function generate(plan: Plan) {
    await run(`generate:${plan.id}`, () => api(`/api/v1/projects/${projectId}/page-plans/${plan.id}/drafts`, { method: "POST", body: "{}" }, token), "Черновик создан. Запустите проверку качества.");
  }

  async function quoteAIDraft() {
    if (!aiPlanId || !aiProviderId || !aiModel.trim()) {
      setError("Выберите утверждённый план, провайдера и модель.");
      return;
    }
    setBusy("ai-quote");
    setError(null);
    setMessage(null);
    setAiQuote(null);
    setAiConsent(false);
    try {
      const quote = await api<AIDraftQuote>(
        `/api/v1/projects/${projectId}/page-plans/${aiPlanId}/drafts/ai/quote`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: aiProviderId,
            model: aiModel.trim(),
            max_cost_usd: Number(aiMaxCost),
            max_output_tokens: Number(aiMaxOutput),
          }),
        },
        token,
      );
      setAiQuote(quote);
      setMessage("Предварительная оценка рассчитана без вызова внешнего провайдера.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать стоимость AI-черновика");
    } finally {
      setBusy(null);
    }
  }

  async function generateAIDraft() {
    if (!aiPlanId || !aiQuote || !aiConsent) return;
    const quote = aiQuote;
    await run(
      `ai-draft:${aiPlanId}`,
      () => api(
        `/api/v1/projects/${projectId}/page-plans/${aiPlanId}/drafts/ai`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: aiProviderId,
            model: aiModel.trim(),
            max_cost_usd: Number(aiMaxCost),
            max_output_tokens: Number(aiMaxOutput),
            operator_confirmed_external_processing: true,
            operator_confirmed_provider_budget: true,
            confirmed_estimated_cost_usd: quote.estimated_cost_usd,
            quote_snapshot_hash: quote.input_snapshot_hash,
          }),
        },
        token,
      ),
      "AI PageDraft создан. Запустите обязательный QA перед отправкой на ручную проверку.",
    );
    setAiQuote(null);
    setAiConsent(false);
  }

  async function quoteSEOBrief() {
    if (!seoPlanId || !aiProviderId || !aiModel.trim()) {
      setError("Выберите утверждённый план, провайдера и модель для SEO brief.");
      return;
    }
    setBusy("seo-quote");
    setError(null);
    setMessage(null);
    setSeoQuote(null);
    setSeoConsent(false);
    try {
      const quote = await api<AIDraftQuote>(
        `/api/v1/projects/${projectId}/page-plans/${seoPlanId}/seo-brief/quote`,
        { method: "POST", body: JSON.stringify({ provider_connection_id: aiProviderId, model: aiModel.trim(), max_cost_usd: Number(seoMaxCost), max_output_tokens: Number(seoMaxOutput) }) },
        token,
      );
      setSeoQuote(quote);
      setMessage("SEO brief quote рассчитан без вызова внешнего провайдера.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать SEO brief quote");
    } finally {
      setBusy(null);
    }
  }

  async function generateSEOBrief() {
    if (!seoPlanId || !seoQuote || !seoConsent) return;
    const quote = seoQuote;
    setBusy(`seo:${seoPlanId}`);
    setError(null);
    try {
      const result = await api<AIRunBrief>(
        `/api/v1/projects/${projectId}/page-plans/${seoPlanId}/seo-brief`,
        { method: "POST", body: JSON.stringify({ provider_connection_id: aiProviderId, model: aiModel.trim(), max_cost_usd: Number(seoMaxCost), max_output_tokens: Number(seoMaxOutput), operator_confirmed_external_processing: true, operator_confirmed_provider_budget: true, confirmed_estimated_cost_usd: quote.estimated_cost_usd, quote_snapshot_hash: quote.input_snapshot_hash }) },
        token,
      );
      setSeoRun(result);
      setSeoQuote(null);
      setSeoConsent(false);
      setMessage("SEO brief создан как отдельный proposal; PageDraft и публикация не изменены.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать SEO brief");
    } finally {
      setBusy(null);
    }
  }

  async function decideSEORun(decision: "approve" | "reject") {
    if (!seoRun) return;
    await run(`seo-decision:${seoRun.id}`, () => api(`/api/v1/ai/runs/${seoRun.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) }, token).then((result) => { setSeoRun(result as AIRunBrief); }), decision === "approve" ? "SEO brief одобрен; применение к PageDraft остаётся отдельным этапом." : "SEO brief отклонён.");
  }

  async function qa(draft: Draft) {
    await run(`qa:${draft.id}`, () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/qa`, { method: "POST" }, token), "Проверка качества завершена.");
  }

  async function submitDraft(draft: Draft) {
    await run(`draft-review:${draft.id}`, () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/submit-review`, { method: "POST" }, token), "Черновик отправлен на ручную проверку.");
  }

  async function apply(draft: Draft) {
    const warning = draft.last_qa_verdict === "warn";
    let body: Record<string, string> | undefined;
    if (warning) {
      const justification = window.prompt("Опишите причину применения черновика с предупреждениями:");
      if (!justification || justification.trim().length < 10) return;
      body = { reason: "operator_review", justification };
    }
    if (!window.confirm("Применить черновик к манифесту? Это не собирает и не публикует сайт.")) return;
    await run(`apply:${draft.id}`, () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/apply`, { method: "POST", body: body ? JSON.stringify(body) : undefined }, token), "Черновик применён к манифесту. Сборка и публикация остаются отдельными действиями.");
  }

  async function materializeBuild() {
    await run("build", () => api(`/api/v1/projects/${projectId}/builds`, { method: "POST" }, token), "Candidate-сборка готова. Откройте приватный preview перед публикацией.");
  }

  async function checkDomain() {
    await run("domain-check", () => api(`/api/v1/projects/${projectId}/domain/check`, { method: "POST" }, token), "DNS-проверка сохранена. TLS проверяется после активации Caddy-vhost.");
  }

  async function publishBuild(build: Build) {
    if (!build.build_hash || !window.confirm(`Опубликовать сборку ${build.build_hash.slice(0, 12)}?`)) return;
    await run(`publish:${build.id}`, () => api(`/api/v1/projects/${projectId}/builds/${build.id}/publish`, { method: "POST", body: JSON.stringify({ confirmed: true }) }, token), "Сборка опубликована.");
  }

  async function rollbackBuild(build: Build) {
    if (!build.build_hash || !window.confirm(`Откатить сайт на ${build.build_hash.slice(0, 12)}?`)) return;
    await run(`rollback:${build.id}`, () => api(`/api/v1/projects/${projectId}/rollbacks`, { method: "POST", body: JSON.stringify({ build_hash: build.build_hash, confirmed: true }) }, token), "Откат выполнен.");
  }

  if (!project) return <p className="muted" aria-live="polite">Загрузка проекта…</p>;

  return (
    <div aria-busy={busy !== null}>
      <PageHeader title={project.name} description="Факты → семантика → география → план страниц → черновик и проверка качества. Публикация не выполняется автоматически." actions={<Link className="btn btn-ghost" to="/projects">К проектам</Link>} />
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="muted" aria-live="polite">{message}</p>}
      <Surface title="1. Факты бизнеса">
        <form className="stack" onSubmit={saveFacts}>
          <label className="field">Организация<input value={organization} onChange={(event) => setOrganization(event.target.value)} placeholder="Название организации" required /></label>
          <label className="field">Основная услуга<input value={service} onChange={(event) => setService(event.target.value)} placeholder="Например: ремонт стиральных машин" required /></label>
          <label className="field">Телефон<input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+7 (900) 000-00-00" required /></label>
          <label className="field">Юридические данные<input value={legal} onChange={(event) => setLegal(event.target.value)} placeholder="Оператор и реквизиты после юридической проверки" /></label>
          <label className="field">Источник фактов<textarea value={sourceNotes} onChange={(event) => setSourceNotes(event.target.value)} placeholder="Откуда оператор подтвердил сведения" /></label>
          <button className="btn" type="submit" disabled={busy !== null}>{busy === "facts" ? "Сохранение…" : "Сохранить новую версию фактов"}</button>
        </form>
        {facts.length === 0 ? <EmptyState title="Факты ещё не сохранены" hint="Без подтверждённых фактов план страницы не перейдёт на проверку." /> : <div className="row"><StatusPill tone={latestFact?.state === "confirmed" ? "ok" : "warn"}>версия {latestFact?.version}: {latestFact?.state}</StatusPill>{latestFact?.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => run(`confirm:${latestFact.id}`, () => api(`/api/v1/projects/${projectId}/facts/${latestFact.id}/confirm`, { method: "POST" }, token), "Факты подтверждены.")}>Подтвердить факты</button>}</div>}
      </Surface>
      <Surface title="2. Семантика проекта">
        <p className="muted">Выберите уже импортированные ключевые фразы. Это не создаёт страницы и не запускает генерацию.</p>
        <DataTable headers={["", "Фраза", "Намерение"]}>{keywords.map((keyword) => <tr key={keyword.id}><td><input aria-label={`Выбрать ${keyword.phrase}`} type="checkbox" checked={selectedKeywordSet.has(keyword.id)} onChange={() => toggleKeyword(keyword.id)} /></td><td>{keyword.phrase}</td><td>{keyword.meta?.intent || "—"}</td></tr>)}</DataTable>
        {keywords.length === 0 && <EmptyState title="В библиотеке нет ключевых фраз" hint="Сначала импортируйте CSV в разделе «Семантика»." />}
        <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={saveKeywords}>Сохранить выбранные ключи</button>
      </Surface>
      <Surface title="3. География проекта">
        <p className="muted">Выберите проверенные места из локального справочника и один основной город/район для страниц.</p>
        <DataTable headers={["", "Основное", "Место", "Тип"]}>{places.map((place) => <tr key={place.id}><td><input aria-label={`Добавить ${place.name}`} type="checkbox" checked={selectedGeoSet.has(place.id)} onChange={() => toggleGeo(place.id)} /></td><td><input aria-label={`Основное место ${place.name}`} type="radio" name="primary-geo" disabled={!selectedGeoSet.has(place.id)} checked={primaryGeoId === place.id} onChange={() => setPrimaryGeoId(place.id)} /></td><td>{place.name}</td><td>{place.kind}</td></tr>)}</DataTable>
        {places.length === 0 && <EmptyState title="Справочник географии пуст" hint="Добавьте город или район в разделе «География»." />}
        <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={saveGeo}>Сохранить географию</button>
      </Surface>
      <Surface title="4. План страниц">
        <p className="muted">Coverage: {coverage?.covered || 0} из {coverage?.selected || 0} выбранных ключей связаны с планами.</p>
        <form className="stack" onSubmit={createPlan}>
          <label className="field">Путь страницы<input value={planSlug} onChange={(event) => setPlanSlug(event.target.value)} placeholder="/" required /></label>
          <label className="field">Цель страницы<input value={planObjective} onChange={(event) => setPlanObjective(event.target.value)} placeholder="Какую потребность закрывает страница" required /></label>
          <label className="field">Намерение<input value={planIntent} onChange={(event) => setPlanIntent(event.target.value)} placeholder="Например: заказать услугу" /></label>
          <label className="field">Комплект<select value={kitKey} onChange={(event) => setKitKey(event.target.value)}><option value="service-local-v1">Локальные услуги</option><option value="home-repair-v1">Домашний ремонт</option></select></label>
          <button className="btn" type="submit" disabled={busy !== null || !planObjective.trim()}>{busy === "plan" ? "Сохранение…" : "Создать черновик плана"}</button>
        </form>
        {plans.length === 0 ? <EmptyState title="Планов страниц пока нет" /> : <DataTable headers={["Путь", "Цель", "Статус", "Действия"]}>{plans.map((plan) => <tr key={plan.id}><td>{plan.slug}</td><td>{plan.objective}</td><td><StatusPill tone={tone(plan.state)}>{plan.state}</StatusPill></td><td className="row">{plan.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => decision(plan, "submit-review")}>На проверку</button>}{plan.state === "review" && <><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => decision(plan, "approve")}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => decision(plan, "reject")}>Отклонить</button></>}{plan.state === "approved" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => generate(plan)}>Создать черновик</button>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="4.1. AI-черновик текста и SEO">
        {aiProviderError && <p className="muted" role="status">AI-операции недоступны: {aiProviderError}. Основной проектный workflow продолжает работать.</p>}
        <p className="muted">Доступен только для утверждённых PagePlan. AI изменяет текстовые поля нового PageDraft, сохраняет curated blocks и не применяет результат к сайту. После генерации обязателен обычный QA и ручная проверка.</p>
        <div className="stack">
          <label className="field">Утверждённый план<select value={aiPlanId} onChange={(event) => { setAiPlanId(event.target.value); setAiQuote(null); setAiConsent(false); }}><option value="">Выберите PagePlan</option>{plans.filter((plan) => plan.state === "approved").map((plan) => <option key={plan.id} value={plan.id}>{plan.slug} — {plan.objective}</option>)}</select></label>
          <label className="field">Активный AI provider<select value={aiProviderId} onChange={(event) => { setAiProviderId(event.target.value); setAiQuote(null); setAiConsent(false); }}><option value="">Выберите подключение</option>{aiProviders.map((provider) => <option key={provider.id} value={provider.id}>{provider.label} ({provider.provider_id})</option>)}</select></label>
          <label className="field">Model ID<input value={aiModel} onChange={(event) => { setAiModel(event.target.value); setAiQuote(null); setAiConsent(false); }} placeholder="Model ID из каталога подключения" /></label>
          <div className="detail-grid">
            <label className="field">Лимит оценки, USD<input type="number" min="0.000001" max="100" step="0.000001" value={aiMaxCost} onChange={(event) => { setAiMaxCost(event.target.value); setAiQuote(null); setAiConsent(false); }} /></label>
            <label className="field">Лимит выходных токенов<input type="number" min="128" max="4096" step="1" value={aiMaxOutput} onChange={(event) => { setAiMaxOutput(event.target.value); setAiQuote(null); setAiConsent(false); }} /></label>
          </div>
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !aiPlanId || !aiProviderId || !aiModel.trim()} onClick={quoteAIDraft}>{busy === "ai-quote" ? "Расчёт…" : "Рассчитать до внешнего вызова"}</button>
          {aiQuote && <div className="surface"><strong>Предварительная оценка: ${aiQuote.estimated_cost_usd.toFixed(6)}</strong><p className="muted">Лимит ${aiQuote.max_cost_usd.toFixed(6)} · источник {aiQuote.pricing_source} · тариф на {aiQuote.pricing_observed_at}. Это оценка, не гарантия фактического счёта.</p></div>}
          <label className="field"><span><input type="checkbox" checked={aiConsent} disabled={!aiQuote} onChange={(event) => setAiConsent(event.target.checked)} /> Подтверждаю показанную оценку, отправку контекста этому провайдеру и настроенный у него spending limit.</span></label>
          <button className="btn" type="button" disabled={busy !== null || !aiQuote || !aiConsent} onClick={generateAIDraft}>{busy?.startsWith("ai-draft:") ? "Генерация…" : "Подтвердить оценку и создать AI PageDraft"}</button>
        </div>
      </Surface>
      <Surface title="4.2. SEO brief proposal">
        <p className="muted">SEO brief остаётся отдельным proposal и не меняет PageDraft без ручного решения.</p>
        <div className="stack">
          <label className="field">Утверждённый план<select value={seoPlanId} onChange={(event) => { setSeoPlanId(event.target.value); setSeoQuote(null); setSeoConsent(false); }}><option value="">Выберите PagePlan</option>{plans.filter((plan) => plan.state === "approved").map((plan) => <option key={plan.id} value={plan.id}>{plan.slug} — {plan.objective}</option>)}</select></label>
          <div className="detail-grid"><label className="field">Лимит USD<input type="number" min="0.000001" max="100" step="0.000001" value={seoMaxCost} onChange={(event) => { setSeoMaxCost(event.target.value); setSeoQuote(null); }} /></label><label className="field">Выходные токены<input type="number" min="128" max="4096" value={seoMaxOutput} onChange={(event) => { setSeoMaxOutput(event.target.value); setSeoQuote(null); }} /></label></div>
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !seoPlanId || !aiProviderId || !aiModel} onClick={quoteSEOBrief}>Рассчитать SEO brief</button>
          {seoQuote && <p className="muted">Оценка ${seoQuote.estimated_cost_usd.toFixed(6)} · {seoQuote.pricing_source}</p>}
          <label className="field"><span><input type="checkbox" disabled={!seoQuote} checked={seoConsent} onChange={(event) => setSeoConsent(event.target.checked)} /> Подтверждаю оценку и внешнюю обработку.</span></label>
          <button className="btn" type="button" disabled={busy !== null || !seoQuote || !seoConsent} onClick={generateSEOBrief}>Создать SEO brief</button>
          {seoRun && <div className="surface"><p>Статус: {seoRun.status}</p>{seoRun.output?.brief && <pre className="code-block">{JSON.stringify(seoRun.output.brief, null, 2)}</pre>}{seoRun.status === "pending_approval" && <div className="row"><button className="btn" type="button" onClick={() => void decideSEORun("approve")}>Одобрить</button><button className="btn btn-ghost" type="button" onClick={() => void decideSEORun("reject")}>Отклонить</button></div>}</div>}
        </div>
      </Surface>
      <Surface title="5. Черновики и проверка качества">
        {drafts.length === 0 ? <EmptyState title="Черновиков пока нет" hint="Одобрите план страницы, затем создайте детерминированный черновик." /> : <DataTable headers={["План", "Версия", "Статус", "QA", "Действия"]}>{drafts.map((draft) => <tr key={draft.id}><td>{plans.find((plan) => plan.id === draft.page_plan_id)?.slug || draft.page_plan_id}</td><td>{draft.revision}</td><td><StatusPill tone={tone(draft.state)}>{draft.state}</StatusPill>{draft.failure_message && <p className="error" role="alert">{draft.failure_message}</p>}</td><td><StatusPill tone={tone(draft.last_qa_verdict || "draft")}>{draft.last_qa_verdict || "не запускалась"}</StatusPill>{draft.qa_runs.at(-1)?.findings.map((finding) => <p className="muted" key={finding.rule}>{finding.rule}: {finding.evidence}</p>)}</td><td className="row">{draft.state === "draft" && <><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => qa(draft)}>Проверить</button>{draft.last_qa_verdict && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => submitDraft(draft)}>На ручную проверку</button>}</>}{draft.state === "review" && draft.last_qa_verdict !== "block" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => apply(draft)}>Применить</button>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="6. Candidate-сборки, preview и публикация">
        <p className="muted">Candidate создаётся без активации. Preview приватен, публикация и откат требуют отдельного подтверждения.</p>
        <div className="row"><button className="btn btn-ghost" type="button" disabled={busy !== null || !project.domain} onClick={checkDomain}>{busy === "domain-check" ? "Проверка DNS…" : "Проверить DNS"}</button><StatusPill tone={project.domain_check_meta?.dns_status === "ok" ? "ok" : "warn"}>DNS: {project.domain_check_meta?.dns_status || "не проверен"}</StatusPill><button className="btn" type="button" disabled={busy !== null || !project.site_id} onClick={materializeBuild}>{busy === "build" ? "Сборка…" : "Создать candidate-сборку"}</button>{!project.site_id && <span className="muted">Сначала примените черновик страницы.</span>}</div>
        {builds.length === 0 ? <EmptyState title="Сборок пока нет" hint="После применения черновика создайте candidate-сборку." /> : <DataTable headers={["Статус", "Hash", "Страниц", "Действия"]}>{builds.map((build) => <tr key={build.id}><td><StatusPill tone={tone(build.status)}>{build.status}</StatusPill></td><td className="muted">{build.build_hash?.slice(0, 16) || "—"}</td><td>{build.pages_built}</td><td className="row">{build.build_hash && project.site_id && <a className="btn btn-ghost" href={`/api/v1/projects/${project.id}/builds/${build.id}/preview/`} target="_blank" rel="noreferrer">Preview</a>}{build.status === "ready" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => publishBuild(build)}>Опубликовать</button>}{build.status === "published" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => rollbackBuild(build)}>Откатить на эту сборку</button>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="Следующий шаг"><p className="muted">После применения черновик меняет только манифест проекта. Candidate-сборка не становится публичной до явной публикации.</p>{project.site_id && <Link className="btn btn-ghost" to="/sites">Открыть сайт и сборки</Link>}</Surface>
    </div>
  );
}
