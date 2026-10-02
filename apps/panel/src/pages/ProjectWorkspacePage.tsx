import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { ConfirmDialog, DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";
import { ProjectWorkspaceLayout } from "./project/ProjectWorkspaceLayout";

type Project = { id: string; name: string; domain: string | null; niche: string | null; site_id: string | null; current_fact_revision_id: string | null; domain_check_meta: { dns_status?: string; ssl_status?: string; checked_at?: string } };
type FactRevision = { id: string; version: number; state: string; facts: Record<string, unknown>; has_private_lead_email: boolean; source_notes: string | null };
type LeadRoutingPolicy = { id: string; version: number; state: string; destinations: { id: string; target_key: string; channel: "email" | "webhook"; required: boolean; configured: boolean }[]; submitted_at: string | null; reviewed_at: string | null; decision_reason: string | null; created_at: string | null };
type Keyword = { id: string; phrase: string; meta: Record<string, string> };
type ProjectKeyword = { id: string; keyword_id: string; phrase: string; cluster: string | null; intent: string | null; priority: number | null };
type SemanticCollection = { id: string; name: string; state: string; version: number; source_refs: { evidence?: unknown[]; manual_source_run_ids?: string[] }; members: { id: string; project_keyword_id: string; keyword_id: string; cluster: string | null; intent: string | null; geo_bindings: { id: string; project_geo_place_id: string; scope: string }[] }[] };
type SemanticSignals = { totals: { members: number; bindings: number; covered: number; planned: number; uncovered: number; unbound: number }; cannibalization: { plans: { slug: string }[]; reason: string }[]; unmapped_plans: { slug: string; state: string }[] };
type GeoPlace = { id: string; name: string; kind: string; is_validated?: boolean };
type ProjectGeo = { id: string; geo_id: string; name: string; kind: string; validated: boolean; role: "primary" | "service_area" | "reference"; position: number };
type ClaimSlotBinding = { block_id: string; slot: string; claim_index: number };
type Plan = { id: string; slug: string; objective: string; intent: string | null; kit_key: string; block_selection: { blocks?: string[]; claim_slot_bindings?: ClaimSlotBinding[] }; state: string; version: number; decision_reason: string | null };
type StructureRevision = { id: string; version: number; state: string };
type BukvarixStatus = { enabled: false; status: "disabled_unsafe_transport"; message: string; supported_modes: ("domain" | "compare" | "multi_domain")[] };
type SemanticSourceRun = { id: string; project_id: string; provider: "bukvarix"; acquisition: "manual_export"; mode: "domain" | "compare" | "multi_domain"; source_label: string; observed_at: string; notes: string | null; selected_keyword_count: number; created_at: string | null };
type Draft = { id: string; page_plan_id: string; revision: number; state: string; content_hash: string | null; last_qa_verdict: string | null; qa_runs: { verdict: string; findings: { verdict: string; rule: string; evidence: string }[] }[]; page_manifest: { blocks?: { type?: unknown }[] } & Record<string, unknown>; failure_message: string | null };
type Coverage = { selected: number; covered: number; uncovered: { keyword_id: string; phrase: string }[]; plans: number };
type LegalReviewHistory = { decision: string; evidence_ref: string | null; reason: string | null; replacement_guidance: string | null; legal_snapshot_hash: string | null; actor_id: string | null; reviewed_at: string | null };
type IndexPromotionProvenance = { slug: string; reason: string; decided_at: string };
type Build = { id: string; status: string; build_hash: string | null; previous_build_hash: string | null; pages_built: number; created_at: string | null; activated_at: string | null; release_gate: { status: string; blockers: string[]; warnings: string[] } | null; legal_review: { status: "pass" | "block"; blockers: string[]; review: { state: string; evidence_ref: string | null; reason: string | null; replacement_guidance: string | null; reviewed_at: string | null }; history: LegalReviewHistory[] }; index_promotion_provenance: IndexPromotionProvenance[] };
type IndexPromotion = { id: string; reason: string; decided_at: string | null };
type IndexPromotionCandidate = { slug: string; source_hash: string; qa_verdict: string | null; status: "approved" | "eligible" | "stale"; promotion: IndexPromotion | null };
type AIProvider = { id: string; label: string; provider_id: string; enabled: boolean };
type AIDraftQuote = { provider_id: string; model_id: string; estimated_cost_usd: number; max_cost_usd: number; input_snapshot_hash: string; pricing_source: string; pricing_observed_at: string };
type AIRunBrief = { id: string; action: string; status: string; output: { brief?: Record<string, unknown>; page_draft_id?: string; slot_copy?: BlockSlotCopy }; error_code: string | null; prompt_hash: string; cost_usd: number | null };
type BlockSlotSchema = { block_id: string; slots: Record<string, { type: string; max_length: number }> };
type BlockSlotCopy = { block_id: string; slots: Record<string, string | null>; fact_keys: string[]; warnings: string[] };
type MediaAsset = { id: string; author: string | null; license: string | null; availability: "eligible" | "expired" | "rights_missing"; hashes: { stored_sha256?: string }; provenance: { rights_confirmed?: boolean; license_expires_at?: string | null } };
type AssetUsage = { scope: "draft" | "candidate" | "published" | "historical"; source: { draft_id?: string; revision?: number; build_id?: string; build_hash?: string | null }; slug: string; placement: string; asset_id: string; expected_sha256: string; alt: string; current_status: "verified" | "missing_asset" | "hash_mismatch" | "unavailable" };
type Confirmation = {
  title: string;
  description: string;
  confirmLabel: string;
  inputLabel?: string;
  inputMinLength?: number;
  dangerous?: boolean;
  onConfirm: (value: string) => void;
};

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
  const [projectKeywords, setProjectKeywords] = useState<ProjectKeyword[]>([]);
  const [places, setPlaces] = useState<GeoPlace[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [semanticCollections, setSemanticCollections] = useState<SemanticCollection[]>([]);
  const [semanticSignals, setSemanticSignals] = useState<SemanticSignals | null>(null);
  const [structureRevisions, setStructureRevisions] = useState<StructureRevision[]>([]);
  const [bukvarixStatus, setBukvarixStatus] = useState<BukvarixStatus | null>(null);
  const [semanticSourceRuns, setSemanticSourceRuns] = useState<SemanticSourceRun[]>([]);
  const [sourceKeywordIds, setSourceKeywordIds] = useState<string[]>([]);
  const [sourceLabel, setSourceLabel] = useState("");
  const [sourceMode, setSourceMode] = useState<SemanticSourceRun["mode"]>("domain");
  const [semanticSourceNotes, setSemanticSourceNotes] = useState("");
  const [sourceConfirmed, setSourceConfirmed] = useState(false);
  const [semanticCollectionSourceRunIds, setSemanticCollectionSourceRunIds] = useState<string[]>([]);
  const [semanticName, setSemanticName] = useState("Основная семантика");
  const [builds, setBuilds] = useState<Build[]>([]);
  const [indexPromotions, setIndexPromotions] = useState<IndexPromotionCandidate[]>([]);
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
  const [seoRuns, setSeoRuns] = useState<AIRunBrief[]>([]);
  const [slotPlanId, setSlotPlanId] = useState("");
  const [slotBlockId, setSlotBlockId] = useState("");
  const [slotSchema, setSlotSchema] = useState<BlockSlotSchema | null>(null);
  const [slotQuote, setSlotQuote] = useState<AIDraftQuote | null>(null);
  const [slotConsent, setSlotConsent] = useState(false);
  const [slotRun, setSlotRun] = useState<AIRunBrief | null>(null);
  const [slotRuns, setSlotRuns] = useState<AIRunBrief[]>([]);
  const [mediaAssets, setMediaAssets] = useState<MediaAsset[]>([]);
  const [assetUsage, setAssetUsage] = useState<AssetUsage[]>([]);
  const [mediaDraftId, setMediaDraftId] = useState("");
  const [mediaBlockId, setMediaBlockId] = useState("");
  const [mediaAssetId, setMediaAssetId] = useState("");
  const [mediaAlt, setMediaAlt] = useState("");
  const [legalRejections, setLegalRejections] = useState<Record<string, { reason: string; guidance: string }>>({});
  const [organization, setOrganization] = useState("");
  const [service, setService] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [workHours, setWorkHours] = useState("");
  const [privateLeadEmail, setPrivateLeadEmail] = useState("");
  const [routingPolicies, setRoutingPolicies] = useState<LeadRoutingPolicy[]>([]);
  const [routingEmail, setRoutingEmail] = useState("");
  const [routingWebhookUrl, setRoutingWebhookUrl] = useState("");
  const [routingWebhookSecret, setRoutingWebhookSecret] = useState("");
  const [privacyEmail, setPrivacyEmail] = useState("");
  const [legal, setLegal] = useState("");
  const [legalJurisdiction, setLegalJurisdiction] = useState("");
  const [inn, setInn] = useState("");
  const [companyHistory, setCompanyHistory] = useState("");
  const [mission, setMission] = useState("");
  const [legalEntities, setLegalEntities] = useState("");
  const [paymentTerms, setPaymentTerms] = useState("");
  const [allowedClaimsText, setAllowedClaimsText] = useState("");
  const [sourceNotes, setSourceNotes] = useState("");
  const [selectedKeywordIds, setSelectedKeywordIds] = useState<string[]>([]);
  const [selectedGeoIds, setSelectedGeoIds] = useState<string[]>([]);
  const [projectGeoBindings, setProjectGeoBindings] = useState<ProjectGeo[]>([]);
  const [primaryGeoId, setPrimaryGeoId] = useState("");
  const [planSlug, setPlanSlug] = useState("/");
  const [planObjective, setPlanObjective] = useState("");
  const [planIntent, setPlanIntent] = useState("");
  const [planClaimSlot, setPlanClaimSlot] = useState("hero.unique_core");
  const [planClaimIndex, setPlanClaimIndex] = useState("");
  const [planClaimBindings, setPlanClaimBindings] = useState<ClaimSlotBinding[]>([]);
  const [planSemanticCollectionId, setPlanSemanticCollectionId] = useState("");
  const [planSemanticSelections, setPlanSemanticSelections] = useState<string[]>([]);
  const [kitKey, setKitKey] = useState("service-local-v1");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);

  const latestFact = facts[0] || null;
  const confirmedFact = useMemo(() => facts.find((fact) => fact.state === "confirmed") || null, [facts]);
  const selectedKeywordSet = useMemo(() => new Set(selectedKeywordIds), [selectedKeywordIds]);
  const selectedGeoSet = useMemo(() => new Set(selectedGeoIds), [selectedGeoIds]);
  const approvedClaims = useMemo(() => {
    const claims = confirmedFact?.facts.allowed_claims;
    return Array.isArray(claims) ? claims.filter((claim): claim is string => typeof claim === "string" && Boolean(claim.trim())) : [];
  }, [confirmedFact]);
  const mediaDraftBlocks = useMemo(() => {
    const blocks = drafts.find((draft) => draft.id === mediaDraftId)?.page_manifest.blocks;
    return Array.isArray(blocks)
      ? blocks.flatMap((block) => typeof block.type === "string" ? [block.type] : [])
      : [];
  }, [drafts, mediaDraftId]);
  const approvedStructure = useMemo(
    () => structureRevisions.find((revision) => revision.state === "approved") || null,
    [structureRevisions],
  );

  async function load() {
    const [nextProject, nextFacts, nextKeywords, nextProjectKeywords, nextPlaces, nextProjectGeo, nextPlans, nextDrafts, nextCoverage, nextBuilds, nextAssetUsage, nextIndexPromotions, nextSeoRuns, nextSlotRuns, nextCollections, nextSignals, nextStructureRevisions, nextBukvarixStatus, nextSourceRuns] = await Promise.all([
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
      api<AssetUsage[]>(`/api/v1/projects/${projectId}/asset-usage`, {}, token),
      api<IndexPromotionCandidate[]>(`/api/v1/projects/${projectId}/index-promotions`, {}, token),
      api<AIRunBrief[]>(`/api/v1/projects/${projectId}/seo-briefs`, {}, token),
      api<AIRunBrief[]>(`/api/v1/projects/${projectId}/block-slot-proposals`, {}, token),
      api<SemanticCollection[]>(`/api/v1/projects/${projectId}/semantic-collections`, {}, token),
      api<SemanticSignals>(`/api/v1/projects/${projectId}/semantic-signals`, {}, token),
      api<StructureRevision[]>(`/api/v1/projects/${projectId}/site-structure/revisions`, {}, token),
      api<BukvarixStatus>(`/api/v1/projects/${projectId}/semantic-sources/bukvarix/status`, {}, token),
      api<SemanticSourceRun[]>(`/api/v1/projects/${projectId}/semantic-source-runs`, {}, token),
    ]);
    setProject(nextProject);
    setFacts(nextFacts);
    setKeywords(nextKeywords.items);
    setProjectKeywords(nextProjectKeywords);
    setSelectedKeywordIds(nextProjectKeywords.map((item) => item.keyword_id));
    setPlaces(nextPlaces);
    setProjectGeoBindings(nextProjectGeo);
    setSelectedGeoIds(nextProjectGeo.map((item) => item.geo_id));
    setPrimaryGeoId(nextProjectGeo.find((item) => item.role === "primary")?.geo_id || "");
    setPlans(nextPlans);
    setDrafts(nextDrafts);
    setCoverage(nextCoverage);
    setBuilds(nextBuilds);
    setAssetUsage(nextAssetUsage);
    setIndexPromotions(nextIndexPromotions);
    setSeoRuns(nextSeoRuns);
    setSeoRun((current) => nextSeoRuns.find((item) => item.id === current?.id) || nextSeoRuns[0] || null);
    setSlotRuns(nextSlotRuns);
    setSlotRun((current) => nextSlotRuns.find((item) => item.id === current?.id) || nextSlotRuns[0] || null);
    setSemanticCollections(nextCollections);
    setSemanticSignals(nextSignals);
    setStructureRevisions(nextStructureRevisions);
    setBukvarixStatus(nextBukvarixStatus);
    setSemanticSourceRuns(nextSourceRuns);
    if (nextProject.site_id) {
      const routing = await api<{ items: LeadRoutingPolicy[] }>(`/api/v1/projects/${projectId}/lead-routing`, {}, token);
      setRoutingPolicies(routing.items);
    } else {
      setRoutingPolicies([]);
    }
  }

  useEffect(() => {
    const claims = latestFact?.facts.allowed_claims;
    setAllowedClaimsText(
      Array.isArray(claims)
        ? claims.filter((claim): claim is string => typeof claim === "string").join(String.fromCharCode(10))
        : "",
    );
  }, [latestFact?.id]);

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

  useEffect(() => {
    api<MediaAsset[]>("/api/v1/media", {}, token)
      .then(setMediaAssets)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить медиатеку"));
  }, [token]);

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
    await run(
      "facts",
      () =>
        api(
          `/api/v1/projects/${projectId}/facts`,
          {
            method: "POST",
            body: JSON.stringify({
              facts: {
                organization,
                service,
                contacts: { phone, address: address || undefined, work_hours: workHours || undefined },
                legal: {
                  org: legal || organization,
                  inn: inn || undefined,
                  privacy_email: privacyEmail || undefined,
                  jurisdiction: legalJurisdiction || undefined,
                  address: address || undefined,
                },
                company_history: companyHistory || undefined,
                mission: mission || undefined,
                legal_entities: legalEntities || undefined,
                payment_terms: paymentTerms || undefined,
                allowed_claims: allowedClaimsText
                  .split(String.fromCharCode(10))
                  .map((item) => item.trim())
                  .filter(Boolean),
              },
              private_lead_email: privateLeadEmail || undefined,
              source_notes: sourceNotes || null,
            }),
          },
          token,
        ),
      "Черновик фактов сохранён. Подтвердите его перед планированием страниц.",
    );
  }

  async function createCommercialPagePlans() {
    await run(
      "commercial-pages",
      () => api(`/api/v1/projects/${projectId}/commercial-page-plans`, { method: "POST" }, token),
      "Созданы draft PagePlan для заполненных коммерческих facts. Проверьте и одобрите их по обычному workflow.",
    );
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

  function toggleSourceKeyword(projectKeywordId: string) {
    setSourceKeywordIds((current) => current.includes(projectKeywordId)
      ? current.filter((item) => item !== projectKeywordId)
      : [...current, projectKeywordId]);
  }

  function toggleSemanticCollectionSourceRun(sourceRunId: string) {
    setSemanticCollectionSourceRunIds((current) => current.includes(sourceRunId)
      ? current.filter((item) => item !== sourceRunId)
      : [...current, sourceRunId]);
  }

  async function recordManualBukvarixExport() {
    if (!sourceLabel.trim() || !sourceKeywordIds.length || !sourceConfirmed) {
      setError("Для ручного источника нужны название, выбранные ключи и явное подтверждение.");
      return;
    }
    await run(
      "semantic-source-run",
      () => api(`/api/v1/projects/${projectId}/semantic-source-runs`, {
        method: "POST",
        body: JSON.stringify({
          provider: "bukvarix",
          acquisition: "manual_export",
          mode: sourceMode,
          source_label: sourceLabel.trim(),
          observed_at: new Date().toISOString(),
          notes: semanticSourceNotes.trim() || null,
          project_keyword_ids: sourceKeywordIds,
          confirm_record_manual_export: true,
        }),
      }, token),
      "Зафиксирован ручной экспорт Букварикса как provenance. Данные не загружались из провайдера и никакие страницы не созданы.",
    );
    setSourceKeywordIds([]);
    setSourceLabel("");
    setSemanticSourceNotes("");
    setSourceConfirmed(false);
  }

  async function createSemanticCollection() {
    const selected = nextProjectKeywordIds();
    if (!semanticName.trim() || !selected.length || !selectedGeoIds.length) {
      setError("Для коллекции нужны название, выбранные ключи и география проекта.");
      return;
    }
    await run(
      "semantic-create",
      () => api(`/api/v1/projects/${projectId}/semantic-collections`, {
        method: "POST",
        body: JSON.stringify({
          name: semanticName.trim(),
          manual_source_run_ids: semanticCollectionSourceRunIds,
          members: selected.map((item) => ({
            project_keyword_id: item.id,
            cluster: item.cluster,
            intent: item.intent,
            priority: item.priority,
            geo_bindings: projectGeoBindings.filter((binding) => selectedGeoIds.includes(binding.geo_id)).map((binding) => ({ project_geo_place_id: binding.id, scope: binding.geo_id === primaryGeoId ? "primary" : "service_area" })),
          })),
        }),
      }, token),
      "Semantic collection создана как draft. Отправьте её на review и одобрение.",
    );
    setSemanticCollectionSourceRunIds([]);
  }

  function nextProjectKeywordIds() {
    return projectKeywords.filter((item) => selectedKeywordSet.has(item.keyword_id));
  }

  async function saveGeo() {
    if (!primaryGeoId) {
      setError("Выберите основной город или район проекта.");
      return;
    }
    await run("geo", () => api(`/api/v1/projects/${projectId}/geo`, { method: "PUT", body: JSON.stringify({ items: selectedGeoIds.map((geo_id, position) => ({ geo_id, role: geo_id === primaryGeoId ? "primary" : "service_area", position })) }) }, token), "География проекта сохранена.");
  }

  function addPlanClaimBinding() {
    const [block_id, slot] = planClaimSlot.split(".");
    const claim_index = Number(planClaimIndex);
    if (!block_id || !slot || !Number.isInteger(claim_index) || !approvedClaims[claim_index]) return;
    if (planClaimBindings.some((binding) => binding.block_id === block_id && binding.slot === slot) || planClaimBindings.some((binding) => binding.claim_index === claim_index)) {
      setError("Для каждого curated-слота и claim доступна только одна дословная привязка.");
      return;
    }
    setPlanClaimBindings((current) => [...current, { block_id, slot, claim_index }]);
    setPlanClaimIndex("");
  }

  function togglePlanSemanticTarget(targetKey: string) {
    setPlanSemanticSelections((current) => current.includes(targetKey)
      ? current.filter((item) => item !== targetKey)
      : [...current, targetKey]);
  }

  async function createPlan(event: FormEvent) {
    event.preventDefault();
    const selectedCollection = semanticCollections.find((item) => item.id === planSemanticCollectionId && item.state === "approved");
    const semanticTargets = selectedCollection
      ? selectedCollection.members.flatMap((member) => {
          const geo_binding_ids = member.geo_bindings
            .filter((binding) => planSemanticSelections.includes(`${member.id}:${binding.id}`))
            .map((binding) => binding.id);
          return geo_binding_ids.length ? [{ collection_keyword_id: member.id, geo_binding_ids }] : [];
        })
      : [];
    const semantic_target = semanticTargets.length && selectedCollection
      ? { collection_id: selectedCollection.id, targets: semanticTargets }
      : undefined;
    await run("plan", () => api(`/api/v1/projects/${projectId}/page-plans`, { method: "POST", body: JSON.stringify({ slug: planSlug, objective: planObjective, intent: planIntent || null, kit_key: kitKey, claim_slot_bindings: planClaimBindings, ...(semantic_target ? { semantic_target } : {}) }) }, token), "Черновик плана страницы создан.");
  }

  async function performPlanDecision(
    plan: Plan,
    action: "submit-review" | "approve" | "reject",
    reason?: string,
  ) {
    await run(
      `${action}:${plan.id}`,
      () => api(`/api/v1/projects/${projectId}/page-plans/${plan.id}/${action}`, { method: "POST", body: JSON.stringify({ reason }) }, token),
      action === "approve" ? "План одобрен." : action === "reject" ? "План отклонён." : "План отправлен на проверку.",
    );
  }

  function decision(plan: Plan, action: "submit-review" | "approve" | "reject") {
    if (action === "submit-review") {
      void performPlanDecision(plan, action);
      return;
    }
    setConfirmation({
      title: action === "approve" ? "Одобрить план страницы?" : "Отклонить план страницы?",
      description: action === "approve" ? `План ${plan.slug} станет доступен для создания черновика.` : `План ${plan.slug} не будет изменять опубликованный сайт.`,
      confirmLabel: action === "approve" ? "Одобрить" : "Отклонить",
      inputLabel: action === "reject" ? "Причина отклонения" : undefined,
      inputMinLength: action === "reject" ? 1 : 0,
      dangerous: action === "reject",
      onConfirm: (reason) => void performPlanDecision(plan, action, reason || undefined),
    });
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

  async function importApprovedSEOBrief() {
    if (!seoRun || seoRun.status !== "approved" || seoRun.output.page_draft_id) return;
    await run(
      `seo-import:${seoRun.id}`,
      () => api<Draft>(`/api/v1/projects/${projectId}/seo-briefs/${seoRun.id}/drafts`, { method: "POST" }, token),
      "Из одобренного SEO brief создан PageDraft без публикации. Проверьте QA и отправьте черновик на ручную проверку.",
    );
  }

  async function loadSlotSchema(planId: string, blockId: string) {
    if (!planId || !blockId) {
      setSlotSchema(null);
      return;
    }
    setBusy("slot-schema");
    setError(null);
    try {
      setSlotSchema(await api<BlockSlotSchema>(`/api/v1/projects/${projectId}/page-plans/${planId}/blocks/${blockId}/slot-schema`, {}, token));
    } catch (cause) {
      setSlotSchema(null);
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить контракт слотов блока");
    } finally {
      setBusy(null);
    }
  }

  function resetSlotQuote() {
    setSlotQuote(null);
    setSlotConsent(false);
  }

  async function quoteBlockSlotCopy() {
    if (!slotPlanId || !slotBlockId || !aiProviderId || !aiModel.trim()) return;
    setBusy("slot-quote");
    setError(null);
    setMessage(null);
    resetSlotQuote();
    try {
      const quote = await api<AIDraftQuote>(
        `/api/v1/projects/${projectId}/page-plans/${slotPlanId}/block-slot-copy/quote`,
        { method: "POST", body: JSON.stringify({ provider_connection_id: aiProviderId, model: aiModel.trim(), block_id: slotBlockId, max_cost_usd: Number(aiMaxCost), max_output_tokens: Number(aiMaxOutput) }) },
        token,
      );
      setSlotQuote(quote);
      setMessage("Оценка block-slot proposal рассчитана без внешнего вызова.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать block-slot proposal");
    } finally {
      setBusy(null);
    }
  }

  async function generateBlockSlotCopy() {
    if (!slotPlanId || !slotBlockId || !slotQuote || !slotConsent) return;
    const quote = slotQuote;
    setBusy(`slot-copy:${slotPlanId}`);
    setError(null);
    try {
      const result = await api<AIRunBrief>(
        `/api/v1/projects/${projectId}/page-plans/${slotPlanId}/block-slot-copy`,
        { method: "POST", body: JSON.stringify({ provider_connection_id: aiProviderId, model: aiModel.trim(), block_id: slotBlockId, max_cost_usd: Number(aiMaxCost), max_output_tokens: Number(aiMaxOutput), operator_confirmed_external_processing: true, operator_confirmed_provider_budget: true, confirmed_estimated_cost_usd: quote.estimated_cost_usd, quote_snapshot_hash: quote.input_snapshot_hash }) },
        token,
      );
      setSlotRun(result);
      resetSlotQuote();
      setMessage("Текст блока создан как отдельное proposal; PageDraft и сайт не изменены.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать proposal текста блока");
    } finally {
      setBusy(null);
    }
  }

  async function decideSlotRun(decision: "approve" | "reject") {
    if (!slotRun) return;
    await run(`slot-decision:${slotRun.id}`, () => api(`/api/v1/ai/runs/${slotRun.id}/decision`, { method: "POST", body: JSON.stringify({ decision }) }, token).then((result) => { setSlotRun(result as AIRunBrief); }), decision === "approve" ? "Текст блока одобрен; создание PageDraft остаётся отдельным этапом." : "Предложение текста блока отклонено.");
  }

  async function importApprovedSlotRun() {
    if (!slotRun || slotRun.status !== "approved" || slotRun.output.page_draft_id) return;
    await run(`slot-import:${slotRun.id}`, () => api<Draft>(`/api/v1/projects/${projectId}/block-slot-proposals/${slotRun.id}/drafts`, { method: "POST" }, token), "Из одобренного текста блока создан noindex PageDraft. Запустите QA и ручную проверку.");
  }

  async function attachDraftMedia() {
    if (!mediaDraftId || !mediaAssetId || !mediaAlt.trim()) return;
    const blockPlacement = Boolean(mediaBlockId);
    await run(
      `draft-media:${mediaDraftId}`,
      () => api(
        `/api/v1/projects/${projectId}/page-drafts/${mediaDraftId}/${blockPlacement ? "block-media" : "media"}`,
        { method: "POST", body: JSON.stringify({ asset_id: mediaAssetId, alt: mediaAlt.trim(), ...(blockPlacement ? { block_id: mediaBlockId } : {}) }) },
        token,
      ),
      blockPlacement
        ? "Файл размещён после curated-блока. QA и ручную проверку нужно выполнить заново."
        : "Файл прикреплён к черновику. QA и ручную проверку нужно выполнить заново.",
    );
    setMediaAlt("");
  }

  async function qa(draft: Draft) {
    await run(`qa:${draft.id}`, () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/qa`, { method: "POST" }, token), "Проверка качества завершена.");
  }

  async function submitDraft(draft: Draft) {
    await run(`draft-review:${draft.id}`, () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/submit-review`, { method: "POST" }, token), "Черновик отправлен на ручную проверку.");
  }

  function apply(draft: Draft) {
    const warning = draft.last_qa_verdict === "warn";
    setConfirmation({
      title: "Применить черновик к манифесту?",
      description: "Это не собирает и не публикует сайт. Сборка и публикация остаются отдельными действиями.",
      confirmLabel: "Применить",
      inputLabel: warning ? "Причина применения с предупреждениями" : undefined,
      inputMinLength: warning ? 10 : 0,
      onConfirm: (justification) => {
        const body = warning ? { reason: "operator_review", justification } : undefined;
        void run(
          `apply:${draft.id}`,
          () => api(`/api/v1/projects/${projectId}/page-drafts/${draft.id}/apply`, { method: "POST", body: body ? JSON.stringify(body) : undefined }, token),
          "Черновик применён к манифесту. Сборка и публикация остаются отдельными действиями.",
        );
      },
    });
  }

  async function materializeBuild() {
    await run("build", () => api(`/api/v1/projects/${projectId}/builds`, { method: "POST" }, token), "Candidate-сборка готова. Откройте приватный preview перед публикацией.");
  }

  function reviewBuildLegal(build: Build, decision: "approved" | "rejected") {
    const rejection = legalRejections[build.id] || { reason: "", guidance: "" };
    setConfirmation({
      title: decision === "approved" ? "Подтвердить legal review candidate-сборки?" : "Отклонить legal review candidate-сборки?",
      description: decision === "approved"
        ? "Решение привязывается к legal snapshot этой candidate-сборки. Изменение facts потребует новую сборку и review."
        : "Отклонение не меняет immutable candidate. Исправьте facts или явно прикрепите eligible media к draft, затем повторите QA, review и candidate build.",
      confirmLabel: decision === "approved" ? "Подтвердить legal review" : "Подтвердить отклонение",
      inputLabel: "Ссылка или внутренний идентификатор evidence",
      inputMinLength: 3,
      dangerous: decision === "rejected",
      onConfirm: (evidence_ref) => {
        void run(
          `legal-review:${build.id}`,
          () => api(`/api/v1/projects/${projectId}/builds/${build.id}/legal-review`, {
            method: "POST",
            body: JSON.stringify({
              decision,
              evidence_ref,
              reason: decision === "rejected" ? rejection.reason.trim() : undefined,
              replacement_guidance: decision === "rejected" ? rejection.guidance.trim() || undefined : undefined,
            }),
          }, token),
          decision === "approved"
            ? "Legal review сохранён для immutable candidate. Перед публикацией проверьте preview."
            : "Отклонение сохранено в history. Candidate и historical releases не изменялись.",
        );
      },
    });
  }

  async function checkDomain() {
    await run("domain-check", () => api(`/api/v1/projects/${projectId}/domain/check`, { method: "POST" }, token), "DNS-проверка сохранена. TLS проверяется после активации Caddy-vhost.");
  }

  function promoteForIndex(item: IndexPromotionCandidate) {
    setConfirmation({
      title: `Разрешить индексацию ${item.slug}?`,
      description: "Решение привязывается к текущему QA и content hash. Оно попадёт только в следующую candidate-сборку; preview и публикация остаются отдельными действиями.",
      confirmLabel: "Разрешить индексацию",
      inputLabel: "Причина решения",
      inputMinLength: 10,
      onConfirm: (reason) => {
        void run(
          `index-promote:${item.slug}`,
          () => api(`/api/v1/projects/${projectId}/index-promotions`, { method: "POST", body: JSON.stringify({ slug: item.slug, reason, confirmed: true }) }, token),
          "Индексация разрешена для текущего content hash. Создайте новый candidate и проверьте приватный preview.",
        );
      },
    });
  }

  async function createRoutingPolicy() {
    const destinations = [] as { target_key: string; channel: "email" | "webhook"; required: boolean; recipient?: string; webhook_url?: string; webhook_secret?: string }[];
    if (routingEmail.trim()) {
      destinations.push({ target_key: "private_email", channel: "email", required: true, recipient: routingEmail.trim() });
    }
    if (routingWebhookUrl.trim() || routingWebhookSecret.trim()) {
      destinations.push({ target_key: "site_webhook", channel: "webhook", required: false, webhook_url: routingWebhookUrl.trim(), webhook_secret: routingWebhookSecret });
    }
    if (!destinations.length) {
      setError("Добавьте private email или полные URL и secret webhook-получателя.");
      return;
    }
    await run(
      "routing-create",
      () => api(`/api/v1/projects/${projectId}/lead-routing`, { method: "POST", body: JSON.stringify({ destinations }) }, token),
      "Создана draft policy маршрутизации. Отправьте её на review перед активацией.",
    );
    setRoutingWebhookSecret("");
  }

  async function submitRoutingPolicy(policy: LeadRoutingPolicy) {
    await run(
      `routing-submit:${policy.id}`,
      () => api(`/api/v1/projects/${projectId}/lead-routing/${policy.id}/submit`, { method: "POST", body: JSON.stringify({ confirmed: true }) }, token),
      "Policy маршрутизации отправлена на review.",
    );
  }

  function rejectRoutingPolicy(policy: LeadRoutingPolicy) {
    setConfirmation({
      title: "Отклонить policy маршрутизации?",
      description: "Получатели не будут активированы. Укажите причину для audit trail.",
      confirmLabel: "Отклонить",
      inputLabel: "Причина отклонения",
      inputMinLength: 3,
      dangerous: true,
      onConfirm: (reason) => {
        void run(
          `routing-reject:${policy.id}`,
          () => api(`/api/v1/projects/${projectId}/lead-routing/${policy.id}/reject`, { method: "POST", body: JSON.stringify({ confirmed: true, reason }) }, token),
          "Policy маршрутизации отклонена.",
        );
      },
    });
  }

  function activateRoutingPolicy(policy: LeadRoutingPolicy) {
    setConfirmation({
      title: "Активировать policy маршрутизации?",
      description: "Новые заявки будут получать неизменяемые snapshots только этой policy. Уже созданные delivery не меняются.",
      confirmLabel: "Активировать",
      dangerous: true,
      onConfirm: (reason) => {
        void run(
          `routing-activate:${policy.id}`,
          () => api(`/api/v1/projects/${projectId}/lead-routing/${policy.id}/activate`, { method: "POST", body: JSON.stringify({ confirmed: true, reason: reason || undefined }) }, token),
          "Policy маршрутизации активирована для новых заявок.",
        );
      },
    });
  }

  function publishBuild(build: Build) {
    if (!build.build_hash) return;
    setConfirmation({
      title: "Опубликовать candidate-сборку?",
      description: `Сборка ${build.build_hash.slice(0, 12)} станет публичной для домена проекта.`,
      confirmLabel: "Опубликовать",
      dangerous: true,
      onConfirm: () => void run(
        `publish:${build.id}`,
        () => api(`/api/v1/projects/${projectId}/builds/${build.id}/publish`, { method: "POST", body: JSON.stringify({ confirmed: true }) }, token),
        "Сборка опубликована.",
      ),
    });
  }

  function rollbackBuild(build: Build) {
    if (!build.build_hash) return;
    setConfirmation({
      title: "Откатить сайт на эту сборку?",
      description: `Публичная версия будет заменена сборкой ${build.build_hash.slice(0, 12)}.`,
      confirmLabel: "Откатить",
      dangerous: true,
      onConfirm: () => void run(
        `rollback:${build.id}`,
        () => api(`/api/v1/projects/${projectId}/rollbacks`, { method: "POST", body: JSON.stringify({ build_hash: build.build_hash, confirmed: true }) }, token),
        "Откат выполнен.",
      ),
    });
  }

  if (!project) return <p className="muted" aria-live="polite">Загрузка проекта…</p>;

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <div aria-busy={busy !== null}>
      <PageHeader title={project.name} description="Факты → семантика → география → план страниц → черновик и проверка качества. Публикация не выполняется автоматически." actions={<div className="row"><Link className="btn btn-ghost" to={`/projects/${projectId}/activity`}>Activity</Link><Link className="btn btn-ghost" to="/projects">К проектам</Link></div>} />
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="muted" aria-live="polite">{message}</p>}
      <Surface title="Подготовка структуры">
        {approvedStructure ? <p className="muted">Одобрена структура сайта v{approvedStructure.version}. Новые direct PagePlan будут сохранены с её server-owned provenance; для полного дерева используйте materialization в <Link to={`/projects/${projectId}/site-structure`}>разделе структуры</Link>.</p> : <p className="error">Перед созданием новых PagePlan одобрите структуру сайта. Существующие планы, черновики, candidate builds и release workflow остаются доступными. <Link to={`/projects/${projectId}/site-structure`}>Открыть структуру сайта</Link></p>}
      </Surface>
      <Surface title="1. Факты бизнеса">
        <form className="stack" onSubmit={saveFacts}>
          <p className="muted">Публичные поля используются в контенте и контактах сайта после подтверждения facts. Адрес доставки лидов хранится отдельно, не попадает в preview, SSG или AI-контекст.</p>
          <label className="field">Организация<input value={organization} onChange={(event) => setOrganization(event.target.value)} placeholder="Название организации" required /><span className="muted">Появится в коммерческих и юридических страницах после ручной проверки.</span></label>
          <label className="field">Основная услуга<input value={service} onChange={(event) => setService(event.target.value)} placeholder="Например: ремонт стиральных машин" required /><span className="muted">Основа семантики, структуры и коммерческих страниц.</span></label>
          <label className="field">Публичный телефон<input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+7 (900) 000-00-00" required /><span className="muted">Может отображаться на сайте и используется в форме заявки.</span></label>
          <label className="field">Публичный адрес<input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="Город, улица, дом" /><span className="muted">Отображается только в блоках, где адрес включён шаблоном.</span></label>
          <label className="field">Часы работы<input value={workHours} onChange={(event) => setWorkHours(event.target.value)} placeholder="Пн–Вс, 09:00–20:00" /></label>
          <label className="field">Email для заявок<input type="email" value={privateLeadEmail} onChange={(event) => setPrivateLeadEmail(event.target.value)} placeholder="leads@example.com" /><span className="muted">Приватный маршрут доставки. Не публикуется на сайте и не отправляется AI-провайдерам.</span></label>
          <label className="field">Публичный email для privacy/DSAR<input type="email" value={privacyEmail} onChange={(event) => setPrivacyEmail(event.target.value)} placeholder="privacy@example.ru" /><span className="muted">Публичный юридический контакт для legal pages. Не используйте адрес для заявок, если он должен оставаться private.</span></label>
          <label className="field">Юридическое наименование<input value={legal} onChange={(event) => setLegal(event.target.value)} placeholder="ООО «Организация»" /></label>
          <label className="field">Юрисдикция для legal pages<input value={legalJurisdiction} onChange={(event) => setLegalJurisdiction(event.target.value)} placeholder="Например: Российская Федерация" /><span className="muted">Обязательна перед public publish; preview и candidate build доступны для review без неё.</span></label>
          <label className="field">ИНН<input value={inn} onChange={(event) => setInn(event.target.value)} placeholder="1234567890" inputMode="numeric" /></label>
          <label className="field">История компании<textarea value={companyHistory} onChange={(event) => setCompanyHistory(event.target.value)} placeholder="Проверенные факты для страницы «История компании»" /></label>
          <label className="field">Миссия<textarea value={mission} onChange={(event) => setMission(event.target.value)} placeholder="Проверенная формулировка миссии" /></label>
          <label className="field">Для юридических лиц<textarea value={legalEntities} onChange={(event) => setLegalEntities(event.target.value)} placeholder="Условия и особенности работы с компаниями" /></label>
          <label className="field">Оплата<textarea value={paymentTerms} onChange={(event) => setPaymentTerms(event.target.value)} placeholder="Проверенные способы и условия оплаты" /></label>
          <label className="field">Подтверждённые claims<textarea value={allowedClaimsText} onChange={(event) => setAllowedClaimsText(event.target.value)} placeholder={`Например: Письменная гарантия на работы
Например: Выезд в пределах Казани`} /><span className="muted">По одному проверяемому утверждению на строку. Список сохраняется как facts и не добавляет claim в страницу автоматически.</span></label>
          <label className="field">Источник фактов<textarea value={sourceNotes} onChange={(event) => setSourceNotes(event.target.value)} placeholder="Откуда оператор подтвердил сведения" /></label>
          <button className="btn" type="submit" disabled={busy !== null}>{busy === "facts" ? "Сохранение…" : "Сохранить новую версию фактов"}</button>
        </form>
        {facts.length === 0 ? <EmptyState title="Факты ещё не сохранены" hint="Без подтверждённых фактов план страницы не перейдёт на проверку." /> : <div className="row"><StatusPill tone={latestFact?.state === "confirmed" ? "ok" : "warn"}>версия {latestFact?.version}: {latestFact?.state}</StatusPill>{latestFact?.state === "draft" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => run(`confirm:${latestFact.id}`, () => api(`/api/v1/projects/${projectId}/facts/${latestFact.id}/confirm`, { method: "POST" }, token), "Факты подтверждены.")}>Подтвердить факты</button> : null}{latestFact?.state === "confirmed" ? <button className="btn btn-ghost" type="button" disabled={busy !== null || !approvedStructure} onClick={() => void createCommercialPagePlans()}>{busy === "commercial-pages" ? "Создание…" : "Создать коммерческие PagePlan"}</button> : null}</div>}
      </Surface>
      <Surface title="2. Маршрутизация заявок">
        <p className="muted">Policy хранит encrypted получателей и применяется только к новым заявкам после явной активации. Сохранённые адреса и webhook secret не отображаются повторно.</p>
        {!project.site_id ? <EmptyState title="Сначала примените черновик к манифесту" hint="После создания site можно настроить маршрутизацию заявок до candidate и публикации." /> : <>
          <div className="stack">
            <label className="field">Private email получателя<input type="email" value={routingEmail} onChange={(event) => setRoutingEmail(event.target.value)} placeholder="leads@example.com" /><span className="muted">Отличается от публичного privacy email и никогда не показывается в сайте или inbox.</span></label>
            <label className="field">Webhook URL (необязательно)<input value={routingWebhookUrl} onChange={(event) => setRoutingWebhookUrl(event.target.value)} placeholder="https://hooks.example.com/lead" /></label>
            <label className="field">Webhook secret (необязательно)<input type="password" value={routingWebhookSecret} onChange={(event) => setRoutingWebhookSecret(event.target.value)} placeholder="Минимум 16 символов" autoComplete="new-password" /></label>
            <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void createRoutingPolicy()}>{busy === "routing-create" ? "Сохранение…" : "Создать draft policy"}</button>
          </div>
          {routingPolicies.length === 0 ? <EmptyState title="Активной policy пока нет" hint="До явной активации сохраняется legacy delivery compatibility; настройте policy перед следующей публикацией." /> : <DataTable headers={["Версия", "Получатели", "Статус", "Действия"]}>{routingPolicies.map((policy) => <tr key={policy.id}><td>v{policy.version}</td><td>{policy.destinations.map((destination) => <span key={destination.id} className="row"><StatusPill tone={destination.configured ? "ok" : "danger"}>{destination.channel}</StatusPill><span>{destination.target_key}{destination.required ? " · required" : " · optional"}</span></span>)}</td><td><StatusPill tone={tone(policy.state)}>{policy.state}</StatusPill></td><td className="row">{policy.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void submitRoutingPolicy(policy)}>На review</button>}{policy.state === "review" && <><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => rejectRoutingPolicy(policy)}>Отклонить</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => activateRoutingPolicy(policy)}>Активировать</button></>}</td></tr>)}</DataTable>}
        </>}
      </Surface>
      <Surface title="3. Семантика проекта">
        <p className="muted">Выберите уже импортированные ключевые фразы. Это не создаёт страницы и не запускает генерацию.</p>
        <DataTable headers={["", "Фраза", "Намерение"]}>{keywords.map((keyword) => <tr key={keyword.id}><td><input aria-label={`Выбрать ${keyword.phrase}`} type="checkbox" disabled={busy !== null} checked={selectedKeywordSet.has(keyword.id)} onChange={() => toggleKeyword(keyword.id)} /></td><td>{keyword.phrase}</td><td>{keyword.meta?.intent || "—"}</td></tr>)}</DataTable>
        {keywords.length === 0 && <EmptyState title="В библиотеке нет ключевых фраз" hint="Сначала импортируйте CSV в разделе «Семантика»." />}
        <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={saveKeywords}>Сохранить выбранные ключи</button>
      </Surface>
      <Surface title="3. География проекта">
        <p className="muted">Выберите проверенные места из локального справочника и один основной город/район для страниц.</p>
        <DataTable headers={["", "Основное", "Место", "Тип"]}>{places.map((place) => <tr key={place.id}><td><input aria-label={`Добавить ${place.name}`} type="checkbox" disabled={busy !== null} checked={selectedGeoSet.has(place.id)} onChange={() => toggleGeo(place.id)} /></td><td><input aria-label={`Основное место ${place.name}`} type="radio" name="primary-geo" disabled={busy !== null || !selectedGeoSet.has(place.id)} checked={primaryGeoId === place.id} onChange={() => setPrimaryGeoId(place.id)} /></td><td>{place.name}</td><td>{place.kind}</td></tr>)}</DataTable>
        {places.length === 0 && <EmptyState title="Справочник географии пуст" hint="Добавьте город или район в разделе «География»." />}
        <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={saveGeo}>Сохранить географию</button>
      </Surface>
      <Surface title="3.5. Источник семантики: Букварикс">
        <p className="muted">{bukvarixStatus?.message || "Проверка статуса Букварикса…"}</p>
        <p className="muted">Панель не принимает API key, endpoint или URL Букварикса и не отправляет запросы поставщику. Здесь можно только зафиксировать provenance уже вручную полученного экспорта для ранее сохранённых project keywords.</p>
        <div className="stack">
          <label className="field">Название ручного экспорта<input value={sourceLabel} onChange={(event) => setSourceLabel(event.target.value)} placeholder="Например: экспорт семантики за 2026-10-02" /></label>
          <label className="field">Режим<select value={sourceMode} onChange={(event) => setSourceMode(event.target.value as SemanticSourceRun["mode"])}><option value="domain">Один домен</option><option value="compare">Сравнение доменов</option><option value="multi_domain">Несколько доменов</option></select></label>
          <label className="field">Заметка (необязательно)<textarea value={semanticSourceNotes} onChange={(event) => setSemanticSourceNotes(event.target.value)} placeholder="Без URL, API key и других секретов" /></label>
          <p className="muted">Выберите уже добавленные в проект ключи, присутствовавшие в вручную полученном экспорте.</p>
          {projectKeywords.length === 0 ? <EmptyState title="В проекте нет сохранённых ключей" hint="Сначала выберите ключи из библиотеки проекта; эта форма не импортирует новые фразы." /> : <DataTable headers={["", "Фраза", "Intent", "Priority"]}>{projectKeywords.map((item) => <tr key={item.id}><td><input aria-label={`Источник Букварикс: ${item.phrase}`} type="checkbox" checked={sourceKeywordIds.includes(item.id)} onChange={() => toggleSourceKeyword(item.id)} disabled={busy !== null} /></td><td>{item.phrase}</td><td>{item.intent || "—"}</td><td>{item.priority ?? "—"}</td></tr>)}</DataTable>}
          <label className="field"><span><input type="checkbox" checked={sourceConfirmed} onChange={(event) => setSourceConfirmed(event.target.checked)} disabled={busy !== null} /> Подтверждаю, что этот экспорт получен вручную; панель не выполняет обращение к Буквариксу и не хранит его credentials.</span></label>
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !sourceLabel.trim() || !sourceKeywordIds.length || !sourceConfirmed} onClick={() => void recordManualBukvarixExport()}>{busy === "semantic-source-run" ? "Сохранение…" : "Записать ручной экспорт как provenance"}</button>
        </div>
        {semanticSourceRuns.length === 0 ? <p className="muted">Ручные provenance runs пока не записаны.</p> : <DataTable headers={["Экспорт", "Режим", "Ключи", "Наблюдалось", "Создано"]}>{semanticSourceRuns.map((item) => <tr key={item.id}><td><strong>{item.source_label}</strong>{item.notes && <p className="muted">{item.notes}</p>}</td><td>{item.mode}</td><td>{item.selected_keyword_count}</td><td>{new Date(item.observed_at).toLocaleString()}</td><td>{item.created_at ? new Date(item.created_at).toLocaleString() : "—"}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="4. Семантические коллекции и сигналы">
        <p className="muted">Коллекция группирует только выбранные ключи и выбранную географию проекта. Сначала создаётся draft, затем отдельные review/approve; это не создаёт PagePlan и не запускает генерацию.</p>
        <div className="stack">
          <label className="field">Название коллекции<input value={semanticName} onChange={(event) => setSemanticName(event.target.value)} placeholder="Например: Ремонт стиральных машин — Казань" /></label>
          {semanticSourceRuns.length > 0 && <fieldset className="stack"><legend>Ручные provenance runs (необязательно)</legend>{semanticSourceRuns.map((run) => <label key={run.id}><input type="checkbox" checked={semanticCollectionSourceRunIds.includes(run.id)} onChange={() => toggleSemanticCollectionSourceRun(run.id)} disabled={busy !== null} /> {run.source_label} · {run.selected_keyword_count} keywords · {run.mode}</label>)}</fieldset>}
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !selectedKeywordIds.length || !selectedGeoIds.length} onClick={() => void createSemanticCollection()}>Создать draft collection из выбранных ключей и географии</button>
        </div>
        {semanticSignals && <div className="detail-grid"><div><strong>{semanticSignals.totals.covered}</strong><span className="muted"> covered targets</span></div><div><strong>{semanticSignals.totals.planned}</strong><span className="muted"> planned targets</span></div><div><strong>{semanticSignals.totals.uncovered}</strong><span className="muted"> uncovered targets</span></div><div><strong>{semanticSignals.totals.unbound}</strong><span className="muted"> unbound keywords</span></div><div><strong>{semanticSignals.cannibalization.length}</strong><span className="muted"> collision warnings</span></div></div>}
        <p className="muted">Сигналы семантического покрытия — только advisory: они не блокируют candidate build, публикацию или другие этапы.</p>
        <Link className="btn btn-ghost" to={`/projects/${projectId}/semantic-coverage`}>Открыть обзор semantic coverage</Link>
        {semanticCollections.length > 0 ? <DataTable headers={["Коллекция", "Состав", "Статус", "Действия"]}>{semanticCollections.map((collection) => <tr key={collection.id}><td>{collection.name}</td><td>{collection.members.length} keywords{collection.source_refs.manual_source_run_ids?.length ? <><br /><span className="muted">manual sources: {collection.source_refs.manual_source_run_ids.map((id) => semanticSourceRuns.find((run) => run.id === id)?.source_label || id.slice(0, 8)).join(", ")}</span></> : null}</td><td><StatusPill tone={tone(collection.state)}>{collection.state}</StatusPill></td><td className="row">{collection.state === "draft" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void run(`semantic-submit:${collection.id}`, () => api(`/api/v1/projects/${projectId}/semantic-collections/${collection.id}/submit-review`, { method: "POST" }, token), "Коллекция отправлена на review.")}>На review</button>}{collection.state === "review" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void run(`semantic-approve:${collection.id}`, () => api(`/api/v1/projects/${projectId}/semantic-collections/${collection.id}/approve`, { method: "POST", body: JSON.stringify({}) }, token), "Коллекция одобрена.")}>Одобрить</button>}</td></tr>)}</DataTable> : <EmptyState title="Коллекций пока нет" hint="Создайте draft из сохранённых project keyword и geo selections." />}
        {semanticSignals?.cannibalization.map((collision, index) => <p className="muted" key={`${collision.reason}-${index}`}>Предупреждение: {collision.reason} — {collision.plans.map((plan) => plan.slug).join(", ")}</p>)}
        {semanticSignals?.unmapped_plans.length ? <p className="muted">Legacy PagePlan без explicit semantic target: {semanticSignals.unmapped_plans.map((plan) => plan.slug).join(", ")}. Они не считаются покрытием.</p> : null}
      </Surface>
      <Surface title="4. План страниц">
        <p className="muted">Coverage: {coverage?.covered || 0} из {coverage?.selected || 0} выбранных ключей связаны с планами.</p>
        <form className="stack" onSubmit={createPlan}>
          <label className="field">Путь страницы<input value={planSlug} onChange={(event) => setPlanSlug(event.target.value)} placeholder="/repair-washing-machines" required /><span className="muted">Латиница в нижнем регистре, цифры, дефисы и вложенные пути через `/`.</span></label>
          <label className="field">Цель страницы<input value={planObjective} onChange={(event) => setPlanObjective(event.target.value)} placeholder="Какую потребность закрывает страница" required /></label>
          <label className="field">Намерение<input value={planIntent} onChange={(event) => setPlanIntent(event.target.value)} placeholder="Например: заказать услугу" /></label>
          <label className="field">Комплект<select value={kitKey} onChange={(event) => setKitKey(event.target.value)}><option value="service-local-v1">Локальные услуги</option><option value="home-repair-v1">Домашний ремонт</option></select></label>
          <div className="surface"><strong>Semantic target (необязательно)</strong><p className="muted">Выберите только approved collection и конкретные связки keyword × geography. Без выбора план останется unmapped; это разрешено и ничего не создаёт автоматически.</p><label className="field">Approved collection<select value={planSemanticCollectionId} onChange={(event) => { setPlanSemanticCollectionId(event.target.value); setPlanSemanticSelections([]); }}><option value="">Не связывать с семантикой</option>{semanticCollections.filter((collection) => collection.state === "approved").map((collection) => <option key={collection.id} value={collection.id}>{collection.name}</option>)}</select></label>{planSemanticCollectionId && (() => { const collection = semanticCollections.find((item) => item.id === planSemanticCollectionId); return collection ? <DataTable headers={["", "Keyword", "Geo binding"]}>{collection.members.flatMap((member) => member.geo_bindings.map((binding) => { const targetKey = `${member.id}:${binding.id}`; const keyword = projectKeywords.find((item) => item.id === member.project_keyword_id); const geo = projectGeoBindings.find((item) => item.id === binding.project_geo_place_id); return <tr key={targetKey}><td><input aria-label={`Выбрать semantic target ${keyword?.phrase || member.project_keyword_id}`} type="checkbox" checked={planSemanticSelections.includes(targetKey)} onChange={() => togglePlanSemanticTarget(targetKey)} /></td><td>{keyword?.phrase || member.project_keyword_id}</td><td>{geo?.name || binding.project_geo_place_id} · {binding.scope}</td></tr>; }))}</DataTable> : null; })()}</div>
          <div className="surface"><strong>Дословные утверждённые claims</strong><p className="muted">Не AI-текст: выбранное утверждение будет скопировано без перефразирования в серверный curated text slot после freeze facts и review плана.</p><div className="detail-grid"><label className="field">Curated slot<select value={planClaimSlot} onChange={(event) => setPlanClaimSlot(event.target.value)}><option value="hero.unique_core">hero · unique_core</option><option value="hero.hero_supporting_text">hero · hero_supporting_text</option></select></label><label className="field">Claim<select value={planClaimIndex} onChange={(event) => setPlanClaimIndex(event.target.value)}><option value="">Выберите подтверждённый claim</option>{approvedClaims.map((claim, index) => <option key={`${index}-${claim}`} value={index}>{index + 1}. {claim}</option>)}</select></label></div><button className="btn btn-ghost" type="button" disabled={busy !== null || !planClaimIndex} onClick={addPlanClaimBinding}>Привязать дословно</button>{planClaimBindings.length > 0 && <ul>{planClaimBindings.map((binding) => <li key={`${binding.block_id}.${binding.slot}`}><code>{binding.block_id}.{binding.slot}</code> ← #{binding.claim_index + 1}: {approvedClaims[binding.claim_index]} <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setPlanClaimBindings((current) => current.filter((item) => item !== binding))}>Убрать</button></li>)}</ul>}{approvedClaims.length === 0 && <p className="muted">Сначала сохраните и подтвердите claims в facts. Без binding обычный deterministic draft не изменяется.</p>}</div>
          <button className="btn" type="submit" disabled={busy !== null || !planObjective.trim() || !approvedStructure}>{busy === "plan" ? "Сохранение…" : "Создать черновик плана"}</button>
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
          {aiQuote && <div className="surface"><strong>Предварительная оценка: ${aiQuote.estimated_cost_usd.toFixed(6)}</strong><p className="muted">Лимит ${aiQuote.max_cost_usd.toFixed(6)} · источник {aiQuote.pricing_source} · тариф на {aiQuote.pricing_observed_at}. Перед подтверждённым вызовом дневной/30-дневный budget reservation учитывает estimate; provider-side spending cap остаётся обязательным независимым ограничением.</p></div>}
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
          {seoRuns.length > 0 && <label className="field">История SEO briefs<select value={seoRun?.id || ""} onChange={(event) => setSeoRun(seoRuns.find((item) => item.id === event.target.value) || null)}>{seoRuns.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.status}</option>)}</select></label>}
          {seoRun?.status === "approved" && !seoRun.output?.page_draft_id && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={importApprovedSEOBrief}>Создать PageDraft из одобренного brief</button>}
          {seoRun?.output?.page_draft_id && <p className="muted">PageDraft: {seoRun.output.page_draft_id}. Далее выполните QA и ручную проверку.</p>}
          {seoRun && <div className="surface"><p>Статус: {seoRun.status}</p>{seoRun.output?.brief && <pre className="code-block">{JSON.stringify(seoRun.output.brief, null, 2)}</pre>}{seoRun.status === "pending_approval" && <div className="row"><button className="btn" type="button" disabled={busy !== null} onClick={() => void decideSEORun("approve")}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void decideSEORun("reject")}>Отклонить</button></div>}</div>}
        </div>
      </Surface>
      <Surface title="4.3. Proposal текста curated-блока">
        <p className="muted">Доступны только серверные plain-text слоты утверждённых curated-блоков. Предложение требует отдельного одобрения и создаёт новый noindex PageDraft только после явного импорта.</p>
        <div className="stack">
          <label className="field">Утверждённый план<select value={slotPlanId} onChange={(event) => { setSlotPlanId(event.target.value); setSlotBlockId(""); setSlotSchema(null); resetSlotQuote(); }}><option value="">Выберите PagePlan</option>{plans.filter((plan) => plan.state === "approved").map((plan) => <option key={plan.id} value={plan.id}>{plan.slug} — {plan.objective}</option>)}</select></label>
          <label className="field">Curated-блок<select value={slotBlockId} disabled={!slotPlanId} onChange={(event) => { const nextBlockId = event.target.value; setSlotBlockId(nextBlockId); resetSlotQuote(); void loadSlotSchema(slotPlanId, nextBlockId); }}><option value="">Выберите блок</option>{(plans.find((plan) => plan.id === slotPlanId)?.block_selection?.blocks || []).map((blockId: string) => <option key={blockId} value={blockId}>{blockId}</option>)}</select></label>
          {slotSchema && <div className="surface"><strong>Серверный контракт слотов</strong><ul>{Object.entries(slotSchema.slots).map(([name, schema]) => <li key={name}><code>{name}</code> · текст до {schema.max_length} символов</li>)}</ul></div>}
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !slotSchema || Object.keys(slotSchema.slots).length === 0 || !aiProviderId || !aiModel.trim()} onClick={quoteBlockSlotCopy}>{busy === "slot-quote" ? "Расчёт…" : "Рассчитать текст блока"}</button>
          {slotQuote && <p className="muted">Оценка ${slotQuote.estimated_cost_usd.toFixed(6)} · лимит ${slotQuote.max_cost_usd.toFixed(6)} · {slotQuote.pricing_source} на {slotQuote.pricing_observed_at}</p>}
          <label className="field"><span><input type="checkbox" disabled={!slotQuote} checked={slotConsent} onChange={(event) => setSlotConsent(event.target.checked)} /> Подтверждаю оценку, внешнюю обработку контекста и настроенный spending limit у провайдера.</span></label>
          <button className="btn" type="button" disabled={busy !== null || !slotQuote || !slotConsent} onClick={generateBlockSlotCopy}>Создать proposal текста блока</button>
          {slotRuns.length > 0 && <label className="field">История proposal<select value={slotRun?.id || ""} onChange={(event) => setSlotRun(slotRuns.find((item) => item.id === event.target.value) || null)}>{slotRuns.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.status}</option>)}</select></label>}
          {slotRun && <div className="surface"><p>Статус: {slotRun.status}</p>{slotRun.output.slot_copy && <pre className="code-block">{JSON.stringify(slotRun.output.slot_copy, null, 2)}</pre>}{slotRun.status === "pending_approval" && <div className="row"><button className="btn" type="button" disabled={busy !== null} onClick={() => void decideSlotRun("approve")}>Одобрить</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void decideSlotRun("reject")}>Отклонить</button></div>}{slotRun.status === "approved" && !slotRun.output.page_draft_id && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={importApprovedSlotRun}>Создать PageDraft из одобренного текста</button>}{slotRun.output.page_draft_id && <p className="muted">PageDraft: {slotRun.output.page_draft_id}. Далее запустите QA.</p>}</div>}
        </div>
      </Surface>
      <Surface title="4.4. Медиа для черновика">
        <p className="muted">Можно прикрепить только уже загруженный файл с подтверждёнными правами: в конце страницы или после одного выбранного curated-блока. В черновике сохраняется UUID и SHA-256, а candidate build копирует проверенные bytes в локальный immutable release. Attachment сбрасывает QA; URL и HTML сюда не принимаются.</p>
        <div className="stack">
          <label className="field">Черновик<select value={mediaDraftId} onChange={(event) => { setMediaDraftId(event.target.value); setMediaBlockId(""); }}><option value="">Выберите draft</option>{drafts.filter((draft) => draft.state === "draft").map((draft) => <option key={draft.id} value={draft.id}>{plans.find((plan) => plan.id === draft.page_plan_id)?.slug || draft.id} · rev {draft.revision}</option>)}</select></label>
          <label className="field">Размещение<select value={mediaBlockId} disabled={!mediaDraftId} onChange={(event) => setMediaBlockId(event.target.value)}><option value="">В конце страницы (gallery)</option>{mediaDraftBlocks.map((blockId) => <option key={blockId} value={blockId}>После блока: {blockId}</option>)}</select></label>
          <label className="field">Файл из медиатеки<select value={mediaAssetId} onChange={(event) => setMediaAssetId(event.target.value)}><option value="">Выберите файл</option>{mediaAssets.filter((asset) => asset.availability === "eligible").map((asset) => <option key={asset.id} value={asset.id}>{asset.author || "Без автора"} · {asset.license || "rights declared"} · {asset.hashes.stored_sha256?.slice(0, 12)}</option>)}</select></label>
          <label className="field">Alt-текст<input value={mediaAlt} onChange={(event) => setMediaAlt(event.target.value)} maxLength={255} placeholder="Кратко и по делу опишите изображение" /></label>
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !mediaDraftId || !mediaAssetId || !mediaAlt.trim()} onClick={() => void attachDraftMedia()}>{busy?.startsWith("draft-media:") ? "Прикрепление…" : "Прикрепить к draft"}</button>
        </div>
      </Surface>
      <Surface title="4.5. Использование media в snapshots">
        <p className="muted">Это derived projection из draft и immutable snapshots всех ready, текущих и исторических release builds. Current status проверяет живую запись, rights, expiry, hash и локальный файл; недоступность не удаляет историческое использование и не переписывает release.</p>
        {assetUsage.length === 0 ? <EmptyState title="В draft и build snapshots нет прикреплённых media" hint="Использование появится после прикрепления файла к draft." /> : <DataTable headers={["Scope", "Страница", "Размещение", "Ассет", "Current status"]}>{assetUsage.map((usage) => <tr key={`${usage.scope}-${usage.source.draft_id || usage.source.build_id}-${usage.slug}-${usage.placement}-${usage.asset_id}`}><td><StatusPill tone={usage.scope === "published" ? "ok" : usage.scope === "candidate" ? "warn" : "accent"}>{usage.scope}</StatusPill></td><td>{usage.slug}</td><td>{usage.placement}</td><td>{usage.asset_id.slice(0, 8)} · {usage.expected_sha256.slice(0, 12)}</td><td><StatusPill tone={usage.current_status === "verified" ? "ok" : "danger"}>{usage.current_status}</StatusPill></td></tr>)}</DataTable>}
      </Surface>
      <Surface title="5. Черновики и проверка качества">
        {drafts.length === 0 ? <EmptyState title="Черновиков пока нет" hint="Одобрите план страницы, затем создайте детерминированный черновик." /> : <DataTable headers={["План", "Версия", "Статус", "QA", "Действия"]}>{drafts.map((draft) => <tr key={draft.id}><td>{plans.find((plan) => plan.id === draft.page_plan_id)?.slug || draft.page_plan_id}</td><td>{draft.revision}</td><td><StatusPill tone={tone(draft.state)}>{draft.state}</StatusPill>{draft.failure_message && <p className="error" role="alert">{draft.failure_message}</p>}</td><td><StatusPill tone={tone(draft.last_qa_verdict || "draft")}>{draft.last_qa_verdict || "не запускалась"}</StatusPill>{draft.qa_runs.at(-1)?.findings.map((finding) => <p className="muted" key={finding.rule}>{finding.rule}: {finding.evidence}</p>)}</td><td className="row">{draft.state === "draft" && <><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => qa(draft)}>Проверить</button>{draft.last_qa_verdict && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => submitDraft(draft)}>На ручную проверку</button>}</>}{draft.state === "review" && draft.last_qa_verdict !== "block" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => apply(draft)}>Применить</button>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="6. Индексация страниц">
        <p className="muted">Новые и изменённые страницы остаются noindex, пока оператор не подтвердит индексацию для текущего passing QA. Решение не меняет публичный сайт: оно требует следующую candidate-сборку, preview и отдельную публикацию.</p>
        {!project.site_id ? <EmptyState title="Сначала примените черновик" hint="После применения страницы будут доступны для QA и явного решения об индексации." /> : indexPromotions.length === 0 ? <EmptyState title="В текущем манифесте нет страниц" /> : <DataTable headers={["Путь", "QA", "Статус", "Действие"]}>{indexPromotions.map((item) => <tr key={item.slug}><td>{item.slug}</td><td><StatusPill tone={tone(item.qa_verdict || "stale")}>{item.qa_verdict || "требуется QA"}</StatusPill></td><td><StatusPill tone={item.status === "approved" ? "ok" : item.status === "eligible" ? "accent" : "warn"}>{item.status === "approved" ? "разрешена" : item.status === "eligible" ? "можно подтвердить" : "устарело"}</StatusPill>{item.promotion?.decided_at && <p className="muted">решение: {new Date(item.promotion.decided_at).toLocaleString()}</p>}</td><td>{item.status === "eligible" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => promoteForIndex(item)}>Разрешить индексацию</button> : <span className="muted">{item.status === "approved" ? "Создайте candidate для применения" : "Повторно примените и проверьте текущий черновик"}</span>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="7. Candidate-сборки, preview и публикация">
        <p className="muted">Candidate создаётся без активации. Preview приватен, публикация и откат требуют отдельного подтверждения.</p>
        <div className="row"><button className="btn btn-ghost" type="button" disabled={busy !== null || !project.domain} onClick={checkDomain}>{busy === "domain-check" ? "Проверка DNS…" : "Проверить DNS"}</button><StatusPill tone={project.domain_check_meta?.dns_status === "ok" ? "ok" : "warn"}>DNS: {project.domain_check_meta?.dns_status || "не проверен"}</StatusPill><button className="btn" type="button" disabled={busy !== null || !project.site_id} onClick={materializeBuild}>{busy === "build" ? "Сборка…" : "Создать candidate-сборку"}</button>{!project.site_id && <span className="muted">Сначала примените черновик страницы.</span>}</div>
        {builds.length === 0 ? <EmptyState title="Сборок пока нет" hint="После применения черновика создайте candidate-сборку." /> : <DataTable headers={["Статус", "Release gate", "Индексация", "Hash", "Страниц", "Действия"]}>{builds.map((build) => <tr key={build.id}><td><StatusPill tone={tone(build.status)}>{build.status}</StatusPill></td><td><StatusPill tone={build.release_gate?.status === "pass" ? "ok" : "warn"}>{build.release_gate?.status || "не проверен"}</StatusPill>{build.release_gate?.blockers.map((blocker) => <p className="error" key={blocker}>{blocker}</p>)}{build.release_gate?.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}<StatusPill tone={build.legal_review.status === "pass" ? "ok" : "warn"}>legal review: {build.legal_review.review.state}</StatusPill>{build.legal_review.review.reason && <p className="error">{build.legal_review.review.reason}</p>}{build.legal_review.review.replacement_guidance && <p className="muted">{build.legal_review.review.replacement_guidance}</p>}{build.legal_review.blockers.map((blocker) => <p className="error" key={blocker}>{blocker}</p>)}{build.legal_review.history.length > 0 && <details><summary>Legal review history · {build.legal_review.history.length}</summary><ul>{build.legal_review.history.map((event, index) => <li key={`${event.reviewed_at}-${index}`}><StatusPill tone={tone(event.decision)}>{event.decision}</StatusPill> {event.reviewed_at?.slice(0, 19) || "—"} · {event.evidence_ref || "—"}{event.reason ? ` · ${event.reason}` : ""}{event.replacement_guidance ? ` · ${event.replacement_guidance}` : ""}</li>)}</ul></details>}</td><td>{build.index_promotion_provenance.length === 0 ? <span className="muted">Нет snapshot provenance / legacy build</span> : <details><summary>Подтверждений индексации: {build.index_promotion_provenance.length}</summary>{build.index_promotion_provenance.map((promotion) => <p className="muted" key={promotion.slug}><strong>{promotion.slug}</strong> · {promotion.reason}<br /><time dateTime={promotion.decided_at}>{new Date(promotion.decided_at).toLocaleString()}</time></p>)}</details>}</td><td className="muted">{build.build_hash?.slice(0, 16) || "—"}</td><td>{build.pages_built}</td><td className="row">{build.build_hash && project.site_id && <a className="btn btn-ghost" href={`/api/v1/projects/${project.id}/builds/${build.id}/preview/`} target="_blank" rel="noreferrer">Preview</a>}{build.status === "ready" && build.legal_review.status === "block" && <><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => reviewBuildLegal(build, "approved")}>Одобрить legal review</button><label className="field">Причина отклонения<input value={legalRejections[build.id]?.reason || ""} minLength={3} maxLength={2000} onChange={(event) => setLegalRejections((current) => ({ ...current, [build.id]: { reason: event.target.value, guidance: current[build.id]?.guidance || "" } }))} /></label><label className="field">Рекомендация по исправлению<input value={legalRejections[build.id]?.guidance || ""} minLength={3} maxLength={2000} onChange={(event) => setLegalRejections((current) => ({ ...current, [build.id]: { reason: current[build.id]?.reason || "", guidance: event.target.value } }))} placeholder="Исправьте facts или выберите новый eligible asset для draft" /></label><button className="btn btn-ghost" type="button" disabled={busy !== null || (legalRejections[build.id]?.reason || "").trim().length < 3 || (legalRejections[build.id]?.guidance || "").trim().length < 3} onClick={() => reviewBuildLegal(build, "rejected")}>Отклонить legal review</button></>}{build.status === "ready" && <button className="btn btn-ghost" type="button" disabled={busy !== null || build.release_gate?.status === "block" || build.legal_review.status === "block"} onClick={() => publishBuild(build)}>Опубликовать</button>}{build.status === "published" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => rollbackBuild(build)}>Откатить на эту сборку</button>}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="Следующий шаг"><p className="muted">После применения черновик меняет только манифест проекта. Candidate-сборка не становится публичной до явной публикации.</p>{project.site_id && <Link className="btn btn-ghost" to="/sites">Открыть сайт и сборки</Link>}</Surface>
      <ConfirmDialog
        open={confirmation !== null}
        title={confirmation?.title || "Подтверждение"}
        description={confirmation?.description || ""}
        confirmLabel={confirmation?.confirmLabel || "Подтвердить"}
        inputLabel={confirmation?.inputLabel}
        inputMinLength={confirmation?.inputMinLength}
        dangerous={confirmation?.dangerous}
        onCancel={() => setConfirmation(null)}
        onConfirm={(value) => {
          const current = confirmation;
          setConfirmation(null);
          current?.onConfirm(value);
        }}
      />
      </div>
    </ProjectWorkspaceLayout>
  );
}
