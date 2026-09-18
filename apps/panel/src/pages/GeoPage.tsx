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

export function GeoPage() {
  const { token } = useAuth();
  const [places, setPlaces] = useState<GeoPlace[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("city");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(search = "") {
    const params = new URLSearchParams({ limit: "100" });
    if (search.trim()) params.set("q", search.trim());
    setPlaces(await api<GeoPlace[]>(`/api/v1/geo?${params.toString()}`, {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить гео"));
  }, [token]);

  async function addManual(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/geo", { method: "POST", body: JSON.stringify({ kind, name }) }, token);
      setName("");
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось добавить гео");
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
        <Surface title="Добавить вручную">
          <form onSubmit={addManual}>
            <label className="field">Тип
              <select value={kind} onChange={(event) => setKind(event.target.value)}>
                <option value="country">Страна</option><option value="region">Регион</option><option value="city">Город</option><option value="district">Район</option><option value="metro">Метро</option><option value="landmark">Ориентир</option>
              </select>
            </label>
            <label className="field">Название<input value={name} onChange={(event) => setName(event.target.value)} required /></label>
            <button className="btn" type="submit" disabled={busy}>{busy ? "Сохранение…" : "Добавить"}</button>
          </form>
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
      <Surface title="Справочник"><form className="row" onSubmit={search} style={{ marginBottom: "1rem" }}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск города или района" style={{ minWidth: 260 }} /><button className="btn btn-ghost" type="submit">Найти</button></form><DataTable headers={["Название", "Тип", "Источник", "Население"]}>{places.map((place) => <tr key={place.id}><td><strong>{place.name}</strong></td><td>{place.kind}</td><td>{place.source}</td><td>{place.population?.toLocaleString("ru-RU") || "—"}</td></tr>)}{places.length === 0 && <tr><td colSpan={4}><EmptyState title="Справочник пуст" hint="Добавьте город вручную или импортируйте CSV." /></td></tr>}</DataTable></Surface>
    </div>
  );
}
