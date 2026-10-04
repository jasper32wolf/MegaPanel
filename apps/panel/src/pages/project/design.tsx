import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { ConfirmDialog, DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Scope = "family" | "project";
type State = "draft" | "review" | "approved" | "rejected";
type Tokens = {
  primary: string;
  secondary: string;
  background: string;
  surface: string;
  text: string;
  muted: string;
  radius: number;
  density: number;
  font_pair: "sans" | "serif_mix" | "display";
  gradient: "none" | "soft" | "bold";
  motion: "none" | "reduced";
};
type Profile = {
  site_family: string;
  niche_fit: string[];
  tokens: Tokens;
  layout: {
    allowed_kits: string[];
    allowed_variants: string[];
    required_blocks: string[];
    allowed_media_roles: string[];
  };
  imagery_guidance: string[];
  voice_traits: string[];
  differentiation_rationale: string;
  accessibility: {
    minimum_contrast_ratio: number;
    require_descriptive_alt: boolean;
    require_visible_cta: boolean;
    require_reduced_motion: boolean;
  };
  prohibited_patterns: string[];
};
type Revision = {
  id: string;
  project_id: string;
  scope: Scope;
  name: string;
  version: number;
  state: State;
  profile: Profile;
  profile_hash: string;
  supersedes_id: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
  created_at: string | null;
};
type Effective = {
  project_id: string;
  inherited_from_project_id: string | null;
  family_profile: Revision | null;
  project_profile: Revision | null;
  effective_profile: Profile | null;
  effective_profile_hash: string | null;
};
type Kit = { key: string; name: string; blocks: string[] };
type Dialog = { kind: "submit" | "approve" | "reject"; revision: Revision } | null;
type PagePlan = { id: string; slug: string; objective: string; state: string };
type AIProvider = { id: string; label: string; provider_id: string; enabled: boolean };
type Quote = {
  provider_id: string;
  model_id: string;
  estimated_cost_usd: number;
  max_cost_usd: number;
  input_snapshot_hash: string;
  pricing_source: string;
  pricing_observed_at: string;
};
type IntentRun = {
  id: string;
  status: string;
  created_at: string | null;
  output: { proposal?: Record<string, unknown>; page_draft_id?: string };
  error_code: string | null;
};

const DEFAULT_TOKENS: Tokens = {
  primary: "#0f6e5c",
  secondary: "#1a3d34",
  background: "#f7f9f8",
  surface: "#ffffff",
  text: "#14201c",
  muted: "#5f7269",
  radius: 12,
  density: 1,
  font_pair: "sans",
  gradient: "soft",
  motion: "reduced",
};

function tone(state: State) {
  if (state === "approved") return "ok" as const;
  if (state === "rejected") return "danger" as const;
  if (state === "review") return "accent" as const;
  return "warn" as const;
}

function csv(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function ProjectDesignPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [effective, setEffective] = useState<Effective | null>(null);
  const [kits, setKits] = useState<Kit[]>([]);
  const [name, setName] = useState("Профессиональный дизайн услуг");
  const [scope, setScope] = useState<Scope>("project");
  const [generationRuns, setGenerationRuns] = useState<IntentRun[]>([]);
  const [plans, setPlans] = useState<PagePlan[]>([]);
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [intentPlanId, setIntentPlanId] = useState("");
  const [providerId, setProviderId] = useState("");
  const [model, setModel] = useState("");
  const [maxCost, setMaxCost] = useState("0.10");
  const [maxOutput, setMaxOutput] = useState("4096");
  const [intentQuote, setIntentQuote] = useState<Quote | null>(null);
  const [intentConsent, setIntentConsent] = useState(false);
  const [siteFamily, setSiteFamily] = useState("local-service");
  const [allowedKit, setAllowedKit] = useState("service-local-v1");
  const [variants, setVariants] = useState("hero-local-service, proof-process");
  const [requiredBlocks, setRequiredBlocks] = useState("hero, process_steps, faq, lead_form");
  const [imageryGuidance, setImageryGuidance] = useState("Документальная профессиональная съёмка процесса, реальные материалы и команда");
  const [voiceTraits, setVoiceTraits] = useState("ясный, практичный, спокойный");
  const [rationale, setRationale] = useState("Дизайн подчёркивает прозрачный процесс, локальную экспертизу и подтверждаемые условия услуги.");
  const [tokens, setTokens] = useState<Tokens>(DEFAULT_TOKENS);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const effectiveLabel = useMemo(() => {
    if (!effective?.effective_profile) return "Профиль ещё не назначен";
    return `${effective.effective_profile.site_family} · ${effective.effective_profile_hash?.slice(0, 12)}…`;
  }, [effective]);

  async function load() {
    if (!projectId) return;
    const [nextRevisions, nextEffective, nextKits, nextGenerationRuns, nextPlans, nextProviders] = await Promise.all([
      api<Revision[]>(`/api/v1/projects/${projectId}/design-profiles`, {}, token),
      api<Effective>(`/api/v1/projects/${projectId}/design-profile`, {}, token),
      api<Kit[]>("/api/v1/blocks/kits", {}, token),
      api<IntentRun[]>(`/api/v1/projects/${projectId}/intent-generation-runs`, {}, token),
      api<PagePlan[]>(`/api/v1/projects/${projectId}/page-plans`, {}, token),
      api<AIProvider[]>("/api/v1/ai/providers", {}, token),
    ]);
    setRevisions(nextRevisions);
    setEffective(nextEffective);
    setKits(nextKits);
    setGenerationRuns(nextGenerationRuns);
    setPlans(nextPlans);
    setProviders(nextProviders.filter((provider) => provider.enabled));
    if (!kits.length && nextKits[0]) setAllowedKit(nextKits[0].key);
  }

  useEffect(() => {
    void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить дизайн-профили"));
  }, [projectId, token]);

  async function quoteIntentGeneration() {
    if (!intentPlanId || !providerId || !model.trim()) {
      setError("Выберите approved PagePlan, provider и model ID.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    setIntentQuote(null);
    setIntentConsent(false);
    try {
      const quote = await api<Quote>(
        `/api/v1/projects/${projectId}/page-plans/${intentPlanId}/intent-generation/quote`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: providerId,
            model: model.trim(),
            max_cost_usd: Number(maxCost),
            max_output_tokens: Number(maxOutput),
          }),
        },
        token,
      );
      setIntentQuote(quote);
      setMessage("Quote рассчитан без вызова провайдера. Проверьте цену и подтвердите внешнюю обработку отдельно.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать intent generation");
    } finally {
      setBusy(false);
    }
  }

  async function queueIntentGeneration() {
    if (!intentPlanId || !intentQuote || !intentConsent) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api(
        `/api/v1/projects/${projectId}/page-plans/${intentPlanId}/intent-generation`,
        {
          method: "POST",
          body: JSON.stringify({
            provider_connection_id: providerId,
            model: model.trim(),
            max_cost_usd: Number(maxCost),
            max_output_tokens: Number(maxOutput),
            operator_confirmed_external_processing: true,
            operator_confirmed_provider_budget: true,
            confirmed_estimated_cost_usd: intentQuote.estimated_cost_usd,
            quote_snapshot_hash: intentQuote.input_snapshot_hash,
          }),
        },
        token,
      );
      setIntentQuote(null);
      setIntentConsent(false);
      setMessage("Intent proposal поставлен в durable queue. Он не создаёт PageDraft, candidate, index или публикацию до отдельных действий.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось поставить intent generation в очередь");
    } finally {
      setBusy(false);
    }
  }

  async function decideIntentRun(run: IntentRun, decision: "approve" | "reject") {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/ai/runs/${run.id}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      }, token);
      setMessage(decision === "approve" ? "Proposal одобрен. Создание noindex PageDraft остаётся отдельным действием." : "Proposal отклонён.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось принять решение по proposal");
    } finally {
      setBusy(false);
    }
  }

  async function materializeIntentRun(run: IntentRun) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/projects/${projectId}/intent-generation-runs/${run.id}/materialize-page-draft`, { method: "POST" }, token);
      setMessage("Создан новый noindex PageDraft. Далее обязательны QA, review, apply, candidate и отдельная публикация.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось materialize intent proposal");
    } finally {
      setBusy(false);
    }
  }

  async function createProfile() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/v1/projects/${projectId}/design-profiles`, {
        method: "POST",
        body: JSON.stringify({
          scope,
          name,
          profile: {
            site_family: siteFamily,
            niche_fit: [],
            tokens,
            layout: {
              allowed_kits: [allowedKit],
              allowed_variants: csv(variants),
              required_blocks: csv(requiredBlocks),
              allowed_media_roles: ["hero", "process", "proof"],
            },
            imagery_guidance: csv(imageryGuidance),
            voice_traits: csv(voiceTraits),
            differentiation_rationale: rationale,
            accessibility: {
              minimum_contrast_ratio: 4.5,
              require_descriptive_alt: true,
              require_visible_cta: true,
              require_reduced_motion: true,
            },
          },
        }),
      }, token);
      setMessage("Создан draft design profile. Он не меняет сайт, PageDraft, candidate или публикацию до review и отдельной новой генерации.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать дизайн-профиль");
    } finally {
      setBusy(false);
    }
  }

  async function decide(reason: string) {
    if (!dialog) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const endpoint = dialog.kind === "submit" ? "submit-review" : dialog.kind;
      await api(`/api/v1/projects/${projectId}/design-profiles/${dialog.revision.id}/${endpoint}`, {
        method: "POST",
        body: dialog.kind === "submit" ? undefined : JSON.stringify({ reason: reason || undefined }),
      }, token);
      setDialog(null);
      setMessage(
        dialog.kind === "approve"
          ? "Профиль назначен как effective policy. Уже созданные drafts и releases не менялись; создайте новую revision для нового дизайна."
          : "Статус design profile обновлён.",
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция с design profile не выполнена");
    } finally {
      setBusy(false);
    }
  }

  return <ProjectWorkspaceLayout projectId={projectId}>
    <PageHeader
      title="Генерация и дизайн"
      description="Версионный design profile задаёт профессиональную визуальную систему для сайта и городских проектов. Профиль не содержит raw CSS/HTML, не меняет опубликованный сайт и не допускает скрытого или crawler-specific контента."
    />
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p className="success" role="status">{message}</p>}
    <Surface title="Эффективный дизайн">
      <div className="detail-grid">
        <div><strong>Profile</strong><span className="muted">{effectiveLabel}</span></div>
        <div><strong>Family inheritance</strong><span className="muted">{effective?.inherited_from_project_id ? `master ${effective.inherited_from_project_id.slice(0, 8)}…` : "локальный проект"}</span></div>
        <div><strong>Family revision</strong><span className="muted">{effective?.family_profile ? `v${effective.family_profile.version}` : "—"}</span></div>
        <div><strong>Project override</strong><span className="muted">{effective?.project_profile ? `v${effective.project_profile.version}` : "—"}</span></div>
      </div>
      <p className="muted">Для city child сначала используется approved family profile master-проекта, затем только reviewed project override. Facts, контакты, geo, семантика и публикация никогда не наследуются этим профилем.</p>
    </Surface>
    <Surface title="Новый draft design profile">
      <div className="stack">
        <label className="field">Название<input value={name} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
        <label className="field">Scope<select value={scope} onChange={(event) => setScope(event.target.value as Scope)} disabled={busy}><option value="project">Текущий проект</option><option value="family">Семья master-проекта</option></select></label>
        <label className="field">Site family<input value={siteFamily} onChange={(event) => setSiteFamily(event.target.value)} disabled={busy} /><span className="muted">Короткий identifier визуального семейства, например local-service.</span></label>
        <label className="field">Curated kit<select value={allowedKit} onChange={(event) => setAllowedKit(event.target.value)} disabled={busy}>{kits.map((kit) => <option key={kit.key} value={kit.key}>{kit.name || kit.key}</option>)}</select></label>
        <label className="field">Разрешённые layout variants<input value={variants} onChange={(event) => setVariants(event.target.value)} disabled={busy} /><span className="muted">Только будущие server-owned variants; эта форма не принимает HTML/CSS.</span></label>
        <label className="field">Обязательные blocks<input value={requiredBlocks} onChange={(event) => setRequiredBlocks(event.target.value)} disabled={busy} /></label>
        <label className="field">Art direction и shot-list<input value={imageryGuidance} onChange={(event) => setImageryGuidance(event.target.value)} disabled={busy} /><span className="muted">AI сможет использовать guidance для рекомендаций. Он не создаёт и не прикрепляет изображения автоматически.</span></label>
        <label className="field">Voice traits<input value={voiceTraits} onChange={(event) => setVoiceTraits(event.target.value)} disabled={busy} /></label>
        <label className="field">Почему дизайн отличается<textarea value={rationale} onChange={(event) => setRationale(event.target.value)} disabled={busy} /></label>
        <div className="detail-grid">{(["primary", "secondary", "background", "surface", "text", "muted"] as const).map((key) => <label className="field" key={key}>{key}<input value={tokens[key]} onChange={(event) => setTokens((current) => ({ ...current, [key]: event.target.value }))} disabled={busy} /></label>)}</div>
        <div className="detail-grid"><label className="field">Radius<input type="number" min="0" max="24" value={tokens.radius} onChange={(event) => setTokens((current) => ({ ...current, radius: Number(event.target.value) }))} disabled={busy} /></label><label className="field">Density<input type="number" min="0.9" max="1.1" step="0.05" value={tokens.density} onChange={(event) => setTokens((current) => ({ ...current, density: Number(event.target.value) }))} disabled={busy} /></label><label className="field">Typography<select value={tokens.font_pair} onChange={(event) => setTokens((current) => ({ ...current, font_pair: event.target.value as Tokens["font_pair"] }))} disabled={busy}><option value="sans">Sans</option><option value="serif_mix">Serif mix</option><option value="display">Display</option></select></label></div>
        <button className="btn" type="button" disabled={busy || !name.trim() || !siteFamily.trim() || !rationale.trim()} onClick={() => void createProfile()}>{busy ? "Сохранение…" : "Создать draft profile"}</button>
      </div>
    </Surface>
    <Surface title="Intent-driven generation">
      <p className="muted">Для approved PagePlan с effective design profile сервер фиксирует semantic/facts/profile/kit snapshot, сначала рассчитывает quote, затем ставит только UUID proposal в durable queue. AI не создаёт asset, PageDraft, candidate, index или публикацию сам.</p>
      <div className="stack">
        <label className="field">Approved PagePlan<select value={intentPlanId} onChange={(event) => { setIntentPlanId(event.target.value); setIntentQuote(null); setIntentConsent(false); }} disabled={busy}><option value="">Выберите PagePlan</option>{plans.filter((plan) => plan.state === "approved").map((plan) => <option key={plan.id} value={plan.id}>{plan.slug} — {plan.objective}</option>)}</select></label>
        <label className="field">Активный AI provider<select value={providerId} onChange={(event) => { setProviderId(event.target.value); setIntentQuote(null); setIntentConsent(false); }} disabled={busy}><option value="">Выберите подключение</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label} ({provider.provider_id})</option>)}</select></label>
        <label className="field">Model ID<input value={model} onChange={(event) => { setModel(event.target.value); setIntentQuote(null); setIntentConsent(false); }} disabled={busy} placeholder="Model ID из подключённого provider" /></label>
        <div className="detail-grid"><label className="field">Лимит USD<input type="number" min="0.000001" max="100" step="0.000001" value={maxCost} onChange={(event) => { setMaxCost(event.target.value); setIntentQuote(null); }} disabled={busy} /></label><label className="field">Выходные токены<input type="number" min="128" max="4096" step="1" value={maxOutput} onChange={(event) => { setMaxOutput(event.target.value); setIntentQuote(null); }} disabled={busy} /></label></div>
        <button className="btn btn-ghost" type="button" disabled={busy || !intentPlanId || !providerId || !model.trim()} onClick={() => void quoteIntentGeneration()}>Рассчитать intent proposal</button>
        {intentQuote ? <div className="surface"><strong>Предварительная оценка: ${intentQuote.estimated_cost_usd.toFixed(6)}</strong><p className="muted">Лимит ${intentQuote.max_cost_usd.toFixed(6)} · {intentQuote.pricing_source} · {intentQuote.pricing_observed_at}. Quote не отправляет данные провайдеру.</p></div> : null}
        <label className="field"><span><input type="checkbox" checked={intentConsent} disabled={!intentQuote || busy} onChange={(event) => setIntentConsent(event.target.checked)} /> Подтверждаю quote, передачу frozen public context выбранному provider и его spending limit.</span></label>
        <button className="btn" type="button" disabled={busy || !intentQuote || !intentConsent} onClick={() => void queueIntentGeneration()}>{busy ? "Постановка…" : "Поставить intent proposal в очередь"}</button>
      </div>
      {generationRuns.length === 0 ? <p className="muted">Intent-generation runs пока не создавались.</p> : <DataTable headers={["Run", "Статус", "Art direction", "Действия"]}>{generationRuns.map((run) => <tr key={run.id}><td><code>{run.id.slice(0, 8)}…</code><br /><span className="muted">{run.created_at ? new Date(run.created_at).toLocaleString() : "—"}</span></td><td><StatusPill tone={run.status === "approved" ? "ok" : run.status === "failed" ? "danger" : "accent"}>{run.status}</StatusPill>{run.error_code ? <p className="muted">{run.error_code}</p> : null}</td><td>{run.output.proposal?.art_direction ? <details><summary>Показать рекомендацию</summary><pre className="code-block">{JSON.stringify(run.output.proposal.art_direction, null, 2)}</pre></details> : "—"}</td><td className="row">{run.status === "pending_approval" ? <><button className="btn" type="button" disabled={busy} onClick={() => void decideIntentRun(run, "approve")}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => void decideIntentRun(run, "reject")}>Отклонить</button></> : null}{run.status === "approved" && !run.output.page_draft_id ? <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => void materializeIntentRun(run)}>Создать noindex PageDraft</button> : null}{run.output.page_draft_id ? <span className="muted">PageDraft создан; QA остаётся обязательным.</span> : null}</td></tr>)}</DataTable>}
    </Surface>
    <Surface title="История design profile">
      {revisions.length === 0 ? <EmptyState title="Design profiles пока нет" hint="Создайте draft profile, отправьте его на review и одобрите до новой generation revision." /> : <DataTable headers={["Профиль", "Scope", "Состояние", "Дизайн", "Действия"]}>{revisions.map((revision) => <tr key={revision.id}><td><strong>{revision.name}</strong><br /><span className="muted">v{revision.version} · {revision.profile_hash.slice(0, 12)}…</span></td><td>{revision.scope}</td><td><StatusPill tone={tone(revision.state)}>{revision.state}</StatusPill>{revision.decision_reason ? <p className="muted">{revision.decision_reason}</p> : null}</td><td>{revision.profile.site_family}<br /><span className="muted">{revision.profile.layout.allowed_kits.join(", ")} · {revision.profile.tokens.font_pair}</span></td><td className="row">{revision.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDialog({ kind: "submit", revision })}>На review</button>}{revision.state === "review" && <><button className="btn" type="button" disabled={busy} onClick={() => setDialog({ kind: "approve", revision })}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => setDialog({ kind: "reject", revision })}>Отклонить</button></>}</td></tr>)}</DataTable>}
    </Surface>
    <ConfirmDialog
      open={Boolean(dialog)}
      title={dialog?.kind === "approve" ? "Одобрить дизайн-профиль?" : dialog?.kind === "reject" ? "Отклонить дизайн-профиль?" : "Передать дизайн-профиль на review?"}
      description={dialog?.kind === "approve" ? "Профиль станет effective policy для будущих generation revisions. Существующие PageDraft, candidate и public release останутся неизменными." : dialog?.kind === "reject" ? "Profile не станет effective policy. Укажите причину для audit trail." : "Draft станет immutable на время review. Это не меняет страницы, candidate, индексирование или публикацию."}
      confirmLabel={dialog?.kind === "approve" ? "Одобрить" : dialog?.kind === "reject" ? "Отклонить" : "На review"}
      inputLabel={dialog?.kind === "reject" ? "Причина отклонения" : dialog?.kind === "approve" ? "Комментарий (необязательно)" : undefined}
      inputMinLength={dialog?.kind === "reject" ? 1 : 0}
      dangerous={dialog?.kind === "reject"}
      onCancel={() => setDialog(null)}
      onConfirm={(reason) => void decide(reason)}
    />
  </ProjectWorkspaceLayout>;
}
