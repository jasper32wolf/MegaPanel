import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Keyword = {
  id: string;
  phrase: string;
  category: string | null;
  meta: Record<string, string>;
  created_at: string | null;
};

type KeywordResponse = { items: Keyword[]; offset: number; limit: number };
type ImportResult = { created: number; skipped: number; invalid_rows: number; errors: { line: number; error: string }[] };

export function KeywordsPage() {
  const { token } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(search = "") {
    const params = new URLSearchParams({ limit: "100" });
    if (search.trim()) params.set("q", search.trim());
    const data = await api<KeywordResponse>(`/api/v1/keywords?${params.toString()}`, {}, token);
    setKeywords(data.items);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить семантику"));
  }, [token]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body = new FormData();
      body.set("file", file);
      body.set("phrase_column", "phrase");
      body.set("group_column", "group");
      body.set("frequency_column", "frequency");
      body.set("intent_column", "intent");
      body.set("city_column", "city");
      body.set("priority_column", "priority");
      body.set("delimiter", ",");
      const imported = await api<ImportResult>("/api/v1/keywords/import-csv", { method: "POST", body }, token);
      setResult(imported);
      setFile(null);
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось импортировать CSV");
    } finally {
      setBusy(false);
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await load(query);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Поиск не выполнен");
    }
  }

  async function downloadTemplate() {
    try {
      const response = await fetch("/api/v1/keywords/template.csv", { credentials: "include" });
      if (!response.ok) throw new Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "site-panel-keywords-template.csv";
      link.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось скачать шаблон");
    }
  }

  return (
    <div>
      <PageHeader
        title="Семантика"
        description="Импортируйте ключевые фразы в UTF-8 CSV. До отдельного согласования плана страниц семантика остаётся рабочим материалом и не запускает генерацию или публикацию."
        actions={<button className="btn btn-ghost" type="button" onClick={downloadTemplate}>Скачать шаблон CSV</button>}
      />
      {error && <p className="error">{error}</p>}

      <Surface title="Импорт CSV">
        <form className="stack" onSubmit={upload}>
          <label className="field">
            CSV-файл (UTF-8, до 10 МБ)
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] || null)}
              required
            />
          </label>
          <p className="muted" style={{ margin: 0 }}>
            Ожидаемые колонки: phrase, frequency, group, intent, city, priority. Пустые фразы попадут в отчёт и не будут импортированы.
          </p>
          <div className="row">
            <button className="btn" type="submit" disabled={!file || busy}>
              {busy ? "Импорт…" : "Импортировать"}
            </button>
            {file && <StatusPill tone="accent">{file.name}</StatusPill>}
          </div>
        </form>
      </Surface>

      {result && (
        <Surface title="Результат импорта">
          <div className="row">
            <StatusPill tone="ok">Добавлено: {result.created}</StatusPill>
            <StatusPill>Пропущено: {result.skipped}</StatusPill>
            <StatusPill tone={result.invalid_rows ? "warn" : "ok"}>Ошибок строк: {result.invalid_rows}</StatusPill>
          </div>
          {result.errors.length > 0 && (
            <ul className="import-errors">
              {result.errors.map((item) => <li key={`${item.line}-${item.error}`}>Строка {item.line}: {item.error}</li>)}
            </ul>
          )}
        </Surface>
      )}

      <Surface title="Импортированные фразы">
        <form className="row" onSubmit={search} style={{ marginBottom: "1rem" }}>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по фразе" style={{ minWidth: 260 }} />
          <button className="btn btn-ghost" type="submit">Найти</button>
        </form>
        <DataTable headers={["Фраза", "Группа", "Intent", "Гео", "Частотность", "Приоритет"]}>
          {keywords.map((keyword) => (
            <tr key={keyword.id}>
              <td><strong>{keyword.phrase}</strong></td>
              <td>{keyword.category || "—"}</td>
              <td>{keyword.meta.intent || "—"}</td>
              <td>{keyword.meta.city || "—"}</td>
              <td>{keyword.meta.frequency || "—"}</td>
              <td>{keyword.meta.priority || "—"}</td>
            </tr>
          ))}
          {keywords.length === 0 && (
            <tr><td colSpan={6}><EmptyState title="Семантика пока не загружена" hint="Скачайте шаблон, заполните фразы и импортируйте CSV." /></td></tr>
          )}
        </DataTable>
      </Surface>
    </div>
  );
}
