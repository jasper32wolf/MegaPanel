import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type GeoPlace = {
  id: string;
  kind: string;
  name: string;
  source: string;
  parent_id: string | null;
  population: number | null;
};

type ImportResult = { created_or_updated: number; invalid_rows: number; errors: { line: number; error: string }[] };
type Provider = { id: string; label: string; provider_id: string; enabled: boolean };
type AIQuote = { estimated_cost_usd: number; max_cost_usd: number; input_snapshot_hash: string; pricing_source: string; pricing_observed_at: string };
type GeoProposalNode = { key: string; kind: "district" | "metro" | "landmark"; name: string; parent_key: string; notes: string[] };
type GeoProposalRun = { id: string; status: string; output: { nodes: GeoProposalNode[]; warnings: string[] }; prompt_hash: string; error_code: string | null };

export function GeoPage() {
  const { token } = useAuth();
  const [places, setPlaces] = useState<GeoPlace[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("city");
  const [parentId, setParentId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [editing, setEditing] = useState<GeoPlace | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [aiCityId, setAiCityId] = useState("");
  const [aiProviderId, setAiProviderId] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiGuidance, setAiGuidance] = useState("");
  const [aiMaxCost, setAiMaxCost] = useState("0.05");
  const [aiMaxPlaces, setAiMaxPlaces] = useState("20");
  const [aiQuote, setAiQuote] = useState<AIQuote | null>(null);
  const [aiConsented, setAiConsented] = useState(false);
  const [aiRun, setAiRun] = useState<GeoProposalRun | null>(null);

  const hierarchy = places.map((place) => {
    const parents = new Map(places.map((item) => [item.id, item]));
    let depth = 0;
    let parent = place.parent_id ? parents.get(place.parent_id) : undefined;
    while (parent && depth < 4) {
      depth += 1;
      parent = parent.parent_id ? parents.get(parent.parent_id) : undefined;
    }
    return { ...place, depth };
  }).sort((left, right) => left.name.localeCompare(right.name, "ru"));

  const parentCandidates = places.filter((place) => {
    if (place.id === editing?.id) return false;
    if (kind === "district" || kind === "metro") return place.kind === "city";
    if (kind === "landmark") return ["city", "district", "metro"].includes(place.kind);
    if (kind === "street") return ["city", "district"].includes(place.kind);
    if (kind === "city") return ["country", "region"].includes(place.kind);
    if (kind === "region") return place.kind === "country";
    return false;
  });

  const parentRequired = ["region", "city", "district", "metro", "landmark", "street"].includes(kind);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(search = "") {
    const params = new URLSearchParams({ limit: "100" });
    if (search.trim()) params.set("q", search.trim());
    const items = await api<GeoPlace[]>(`/api/v1/geo?${params.toString()}`, {}, token);
    setPlaces(items);
    if (!aiCityId) setAiCityId(items.find((place) => place.kind === "city")?.id || "");
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить гео"));
  }, [token]);

  useEffect(() => {
    api<Provider[]>("/api/v1/ai/providers", {}, token)
      .then((items) => {
        const active = items.filter((item) => item.enabled);
        setProviders(active);
        if (active.length === 1) setAiProviderId(active[0].id);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить AI-провайдеры"));
  }, [token]);

  async function addManual(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(
        editing ? `/api/v1/geo/${editing.id}` : "/api/v1/geo",
        {
          method: editing ? "PATCH" : "POST",
          body: JSON.stringify(editing ? { name, parent_id: parentId || null } : { kind, name, parent_id: parentId || undefined }),
        },
        token,
      );
      setName("");
      setParentId("");
      setEditing(null);
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось добавить гео");
    } finally {
      setBusy(false);
    }
  }

  function startEditing(place: GeoPlace) {
    setEditing(place);
    setName(place.name);
    setKind(place.kind);
    setParentId(place.parent_id || "");
  }

  function cancelEditing() {
    setEditing(null);
    setName("");
    setParentId("");
  }

  function geoAIRequest() {
    return {
      city_id: aiCityId,
      provider_connection_id: aiProviderId,
      model: aiModel.trim(),
      max_cost_usd: Number(aiMaxCost),
      max_output_tokens: 2048,
      max_places: Number(aiMaxPlaces),
      operator_guidance: aiGuidance.split("\n").map((item) => item.trim()).filter(Boolean),
    };
  }

  async function quoteGeoAI() {
    setBusy(true);
    setError(null);
    setAiRun(null);
    setAiConsented(false);
    try {
      setAiQuote(await api<AIQuote>("/api/v1/geo/ai-proposals/quote", { method: "POST", body: JSON.stringify(geoAIRequest()) }, token));
    } catch (cause) {
      setAiQuote(null);
      setError(cause instanceof Error ? cause.message : "Не удалось рассчитать AI-предложение");
    } finally {
      setBusy(false);
    }
  }

  async function proposeGeoAI() {
    if (!aiQuote || !aiConsented) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<GeoProposalRun>("/api/v1/geo/ai-proposals", {
        method: "POST",
        body: JSON.stringify({ ...geoAIRequest(), operator_confirmed_external_processing: true, operator_confirmed_provider_budget: true, confirmed_estimated_cost_usd: aiQuote.estimated_cost_usd, quote_snapshot_hash: aiQuote.input_snapshot_hash }),
      }, token);
      setAiRun(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось создать AI-предложение");
    } finally {
      setBusy(false);
    }
  }

  function updateProposalNode(index: number, field: "name" | "parent_key", value: string) {
    if (!aiRun) return;
    const nodes = aiRun.output.nodes.map((node, nodeIndex) => nodeIndex === index ? { ...node, [field]: value } : node);
    setAiRun({ ...aiRun, output: { ...aiRun.output, nodes } });
  }

  async function approveAndApplyGeoAI() {
    if (!aiRun) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/ai/runs/${aiRun.id}/decision`, { method: "POST", body: JSON.stringify({ decision: "approve" }) }, token);
      await api(`/api/v1/geo/ai-proposals/${aiRun.id}/apply`, { method: "POST", body: JSON.stringify({ nodes: aiRun.output.nodes }) }, token);
      await load(query);
      setAiRun(null);
      setAiQuote(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось применить гео-предложение");
    } finally {
      setBusy(false);
    }
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body = new FormData();
      body.set("file", file);
      body.set("delimiter", ",");
      const imported = await api<ImportResult>("/api/v1/geo/import-csv", { method: "POST", body }, token);
      setResult(imported);
      setFile(null);
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось импортировать CSV");
    } finally {
      setBusy(false);
    }
  }

  async function downloadTemplate() {
    try {
      const response = await fetch("/api/v1/geo/template.csv", { credentials: "include" });
      if (!response.ok) throw new Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "site-panel-geo-template.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось скачать шаблон");
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    try {
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Поиск не выполнен");
    }
  }

  return (
    <div>
      <PageHeader
        title="География"
        description="Локальный справочник городов и районов для морфологически корректных страниц. Полноту и корректность данных проверяет оператор; внешние источники не используются без явной настройки."
        actions={<button className="btn btn-ghost" type="button" onClick={downloadTemplate}>Скачать шаблон CSV</button>}
      />
      {error && <p className="error">{error}</p>}
      <div className="two-col">
        <Surface title={editing ? "Редактировать объект" : "Добавить вручную"}>
          <form onSubmit={addManual}>
            <label className="field">Тип
              <select value={kind} disabled={Boolean(editing)} onChange={(event) => { setKind(event.target.value); setParentId(""); }}>
                <option value="country">Страна</option><option value="region">Регион</option><option value="city">Город</option><option value="district">Район</option><option value="metro">Метро</option><option value="landmark">Ориентир</option><option value="street">Улица</option>
              </select>
            </label>
            <label className="field">Название<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
            {parentRequired ? <label className="field">Родитель<select value={parentId} onChange={(event) => setParentId(event.target.value)} required><option value="">Выберите родительский объект</option>{parentCandidates.map((place) => <option key={place.id} value={place.id}>{place.name} · {place.kind}</option>)}</select><span className="muted">{kind === "district" || kind === "metro" ? "Район и метро создаются только внутри города." : kind === "landmark" ? "Ориентир можно вложить в город, район или метро." : "Выберите допустимый родительский объект."}</span></label> : <p className="muted">Страна — корневой элемент и не имеет родителя.</p>}
            <div className="row"><button className="btn" type="submit" disabled={busy || (parentRequired && !parentId)}>{busy ? "Сохранение…" : editing ? "Сохранить" : "Добавить"}</button>{editing && <button className="btn btn-ghost" type="button" disabled={busy} onClick={cancelEditing}>Отмена</button>}</div>
          </form>
        </Surface>
        <Surface title="AI-предложение иерархии">
          <p className="muted">AI получает только выбранный город, существующих прямых потомков и ваши указания. Узлы не создаются до quote, явного согласия, review и отдельного apply.</p>
          <div className="stack">
            <label className="field">Город<select value={aiCityId} onChange={(event) => { setAiCityId(event.target.value); setAiQuote(null); }} required><option value="">Выберите город</option>{places.filter((place) => place.kind === "city").map((place) => <option key={place.id} value={place.id}>{place.name}</option>)}</select></label>
            <label className="field">AI-провайдер<select value={aiProviderId} onChange={(event) => { setAiProviderId(event.target.value); setAiQuote(null); }} required><option value="">Выберите активное подключение</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label} · {provider.provider_id}</option>)}</select></label>
            <label className="field">Модель<input value={aiModel} onChange={(event) => { setAiModel(event.target.value); setAiQuote(null); }} placeholder="Модель из настроенного подключения" required /></label>
            <label className="field">Указания оператору<textarea value={aiGuidance} onChange={(event) => { setAiGuidance(event.target.value); setAiQuote(null); }} rows={3} placeholder="Одна проверяемая задача на строку" /></label>
            <div className="two-col"><label className="field">Лимит мест<input type="number" min="1" max="50" value={aiMaxPlaces} onChange={(event) => { setAiMaxPlaces(event.target.value); setAiQuote(null); }} /></label><label className="field">Лимит стоимости, USD<input type="number" min="0.000001" step="0.01" value={aiMaxCost} onChange={(event) => { setAiMaxCost(event.target.value); setAiQuote(null); }} /></label></div>
            <button className="btn btn-ghost" type="button" disabled={busy || !aiCityId || !aiProviderId || !aiModel.trim()} onClick={quoteGeoAI}>{busy ? "Расчёт…" : "Рассчитать quote"}</button>
            {aiQuote && <><p className="muted">Estimate: {aiQuote.estimated_cost_usd.toFixed(6)} USD; pricing: {aiQuote.pricing_source}, observed {aiQuote.pricing_observed_at}. Внешний provider ещё не вызывался.</p><label className="field"><input type="checkbox" checked={aiConsented} onChange={(event) => setAiConsented(event.target.checked)} /> Подтверждаю передачу ограниченного geo-context провайдеру и лимит бюджета.</label><button className="btn" type="button" disabled={busy || !aiConsented} onClick={proposeGeoAI}>Создать proposal для review</button></>}
          </div>
        </Surface>
        <Surface title="Импорт CSV">
          <form className="stack" onSubmit={upload}>
            <label className="field">CSV-файл (UTF-8, до 10 МБ)<input type="file" accept=".csv,text/csv" required onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] || null)} /></label>
            <p className="muted" style={{ margin: 0 }}>Нужны колонки kind, external_id и name. Для района укажите parent_external_id города выше в файле.</p>
            <button className="btn" type="submit" disabled={!file || busy}>{busy ? "Импорт…" : "Импортировать"}</button>
          </form>
        </Surface>
      </div>
      {result && <Surface title="Результат импорта"><div className="row"><StatusPill tone="ok">Добавлено/обновлено: {result.created_or_updated}</StatusPill><StatusPill tone={result.invalid_rows ? "warn" : "ok"}>Ошибок: {result.invalid_rows}</StatusPill></div>{result.errors.length > 0 && <ul className="import-errors">{result.errors.map((item) => <li key={`${item.line}-${item.error}`}>Строка {item.line}: {item.error}</li>)}</ul>}</Surface>}
      {aiRun && <Surface title="Review AI-предложения"><p className="muted">Статус: {aiRun.status}. Перед apply измените названия и parent key при необходимости; server повторно валидирует hierarchy и не создаёт места без explicit approval.</p>{aiRun.output.warnings.map((warning) => <p key={warning} className="muted">Предупреждение: {warning}</p>)}<div className="stack">{aiRun.output.nodes.map((node, index) => <div className="detail-grid" key={node.key}><div><strong>{node.kind}</strong><p>{node.key}</p></div><label className="field">Название<input value={node.name} onChange={(event) => updateProposalNode(index, "name", event.target.value)} /></label><label className="field">Parent key<select value={node.parent_key} onChange={(event) => updateProposalNode(index, "parent_key", event.target.value)}><option value="city">city</option>{aiRun.output.nodes.filter((candidate) => candidate.kind === "district" || candidate.kind === "metro").map((candidate) => <option key={candidate.key} value={candidate.key}>{candidate.key}</option>)}</select></label></div>)}</div><button className="btn" type="button" disabled={busy || aiRun.status !== "pending_approval"} onClick={approveAndApplyGeoAI}>{busy ? "Применение…" : "Одобрить и применить проверенный proposal"}</button></Surface>}
      <Surface title="Иерархия справочника"><p className="muted">Город → район или метро → ориентир. Дерево строится по parent link; поиск показывает совпадающие объекты из локального справочника.</p><form className="row" onSubmit={search} style={{ marginBottom: "1rem" }}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск города, района, метро или ориентира" style={{ minWidth: 260 }} /><button className="btn btn-ghost" type="submit">Найти</button></form><DataTable headers={["Название", "Тип", "Источник", "Население", ""]}>{hierarchy.map((place) => <tr key={place.id}><td><strong style={{ paddingLeft: `${place.depth * 1.25}rem` }}>{place.depth ? "↳ " : ""}{place.name}</strong></td><td>{place.kind}</td><td>{place.source}</td><td>{place.population?.toLocaleString("ru-RU") || "—"}</td><td><button className="btn btn-ghost" type="button" disabled={busy} onClick={() => startEditing(place)}>Изменить</button></td></tr>)}{places.length === 0 && <tr><td colSpan={5}><EmptyState title="Справочник пуст" hint="Добавьте страну и город вручную или импортируйте CSV." /></td></tr>}</DataTable></Surface>
    </div>
  );
}
