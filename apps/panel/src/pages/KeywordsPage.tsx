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
type ImportPreview = ImportResult & { preview: Array<{ phrase: string; category: string | null; meta: Record<string, string> }> };
type ColumnMapping = {
  phrase: string;
  group: string;
  frequency: string;
  intent: string;
  city: string;
  priority: string;
};

const defaultMapping: ColumnMapping = {
  phrase: "phrase",
  group: "group",
  frequency: "frequency",
  intent: "intent",
  city: "city",
  priority: "priority",
};

function signature(file: File | null, delimiter: string, mapping: ColumnMapping) {
  if (!file) return "";
  return [file.name, file.size, file.lastModified, delimiter, ...Object.values(mapping)].join("\u0000");
}

export function KeywordsPage() {
  const { token } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [delimiter, setDelimiter] = useState(",");
  const [mapping, setMapping] = useState<ColumnMapping>(defaultMapping);
  const [keywords, setKeywords] = useState<Keyword[]>([]);
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [previewSignature, setPreviewSignature] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const currentSignature = signature(file, delimiter, mapping);
  const previewIsCurrent = Boolean(preview && previewSignature === currentSignature);

  async function load(search = "") {
    const params = new URLSearchParams({ limit: "100" });
    if (search.trim()) params.set("q", search.trim());
    const data = await api<KeywordResponse>(`/api/v1/keywords?${params.toString()}`, {}, token);
    setKeywords(data.items);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить семантику"));
  }, [token]);

  function invalidatePreview() {
    setPreview(null);
    setPreviewSignature("");
    setResult(null);
  }

  function buildCsvFormData() {
    if (!file) throw new Error("Выберите CSV-файл");
    const body = new FormData();
    body.set("file", file);
    body.set("phrase_column", mapping.phrase.trim());
    body.set("group_column", mapping.group.trim());
    body.set("frequency_column", mapping.frequency.trim());
    body.set("intent_column", mapping.intent.trim());
    body.set("city_column", mapping.city.trim());
    body.set("priority_column", mapping.priority.trim());
    body.set("delimiter", delimiter);
    return body;
  }

  function selectFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] || null);
    invalidatePreview();
  }

  function changeDelimiter(value: string) {
    setDelimiter(value);
    invalidatePreview();
  }

  function changeMapping(key: keyof ColumnMapping, value: string) {
    setMapping((current) => ({ ...current, [key]: value }));
    invalidatePreview();
  }

  async function requestPreview() {
    if (!file || !mapping.phrase.trim()) return;
    setBusy(true);
    setError(null);
    invalidatePreview();
    try {
      const imported = await api<ImportPreview>(
        "/api/v1/keywords/import-csv/preview",
        { method: "POST", body: buildCsvFormData() },
        token,
      );
      setPreview(imported);
      setPreviewSignature(currentSignature);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось проверить CSV");
    } finally {
      setBusy(false);
    }
  }

  async function importCheckedCsv() {
    if (!previewIsCurrent || !preview || preview.created === 0) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const imported = await api<ImportResult>(
        "/api/v1/keywords/import-csv",
        { method: "POST", body: buildCsvFormData() },
        token,
      );
      setResult(imported);
      setFile(null);
      setPreview(null);
      setPreviewSignature("");
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
        description="Проверьте UTF-8 CSV без записи, затем отдельно подтвердите импорт ключевых фраз. Семантика остаётся рабочим материалом до согласования плана страниц и не запускает генерацию или публикацию."
        actions={<button className="btn btn-ghost" type="button" onClick={downloadTemplate}>Скачать шаблон CSV</button>}
      />
      {error && <p className="error" role="alert">{error}</p>}

      <Surface title="Проверка и импорт CSV">
        <div className="stack">
          <label className="field">
            CSV-файл (UTF-8, до 10 МБ)
            <input type="file" accept=".csv,text/csv" onChange={selectFile} />
          </label>
          <label className="field">
            Разделитель
            <select value={delimiter} onChange={(event) => changeDelimiter(event.target.value)} disabled={busy}>
              <option value=",">Запятая (,)</option>
              <option value=";">Точка с запятой (;)</option>
              <option value="\t">Табуляция</option>
            </select>
          </label>
          <div className="detail-grid">
            <label className="field">Колонка фразы<input value={mapping.phrase} onChange={(event) => changeMapping("phrase", event.target.value)} required disabled={busy} /></label>
            <label className="field">Колонка группы<input value={mapping.group} onChange={(event) => changeMapping("group", event.target.value)} placeholder="Не импортировать" disabled={busy} /></label>
            <label className="field">Колонка частотности<input value={mapping.frequency} onChange={(event) => changeMapping("frequency", event.target.value)} placeholder="Не импортировать" disabled={busy} /></label>
            <label className="field">Колонка intent<input value={mapping.intent} onChange={(event) => changeMapping("intent", event.target.value)} placeholder="Не импортировать" disabled={busy} /></label>
            <label className="field">Колонка города<input value={mapping.city} onChange={(event) => changeMapping("city", event.target.value)} placeholder="Не импортировать" disabled={busy} /></label>
            <label className="field">Колонка приоритета<input value={mapping.priority} onChange={(event) => changeMapping("priority", event.target.value)} placeholder="Не импортировать" disabled={busy} /></label>
          </div>
          <p className="muted" style={{ margin: 0 }}>Выбор файла и mapping не отправляют данные. Сначала нажмите «Проверить CSV»: сервер рассчитает результат без создания ключей и audit-записи.</p>
          <div className="row">
            <button className="btn btn-ghost" type="button" disabled={!file || !mapping.phrase.trim() || busy} onClick={() => void requestPreview()}>{busy ? "Проверка…" : "Проверить CSV"}</button>
            <button className="btn" type="button" disabled={!previewIsCurrent || !preview || preview.created === 0 || busy} onClick={() => void importCheckedCsv()}>{busy ? "Импорт…" : `Импортировать проверенный CSV${previewIsCurrent && preview ? ` (${preview.created})` : ""}`}</button>
            {file && <StatusPill tone="accent">{file.name}</StatusPill>}
          </div>
        </div>
      </Surface>

      {previewIsCurrent && preview && <Surface title="Preview CSV без записи">
        <p className="muted">Это ожидаемый результат на момент проверки, а не резервирование. До подтверждения ключи не созданы; при параллельном импорте фактический результат может отличаться.</p>
        <div className="row">
          <StatusPill tone="ok">Будет добавлено: {preview.created}</StatusPill>
          <StatusPill>Будет пропущено: {preview.skipped}</StatusPill>
          <StatusPill tone={preview.invalid_rows ? "warn" : "ok"}>Ошибок строк: {preview.invalid_rows}</StatusPill>
        </div>
        {preview.errors.length > 0 && <ul className="import-errors">{preview.errors.map((item) => <li key={`${item.line}-${item.error}`}>Строка {item.line}: {item.error}</li>)}</ul>}
        <DataTable headers={["Фраза", "Группа", "Intent", "Гео", "Частотность", "Приоритет"]}>{preview.preview.map((item) => <tr key={item.phrase}><td><strong>{item.phrase}</strong></td><td>{item.category || "—"}</td><td>{item.meta.intent || "—"}</td><td>{item.meta.city || "—"}</td><td>{item.meta.frequency || "—"}</td><td>{item.meta.priority || "—"}</td></tr>)}{preview.preview.length === 0 && <tr><td colSpan={6}><EmptyState title="Новых фраз для импорта нет" hint="Все строки уже существуют, повторяются в файле или невалидны." /></td></tr>}</DataTable>
      </Surface>}

      {result && <Surface title="Фактический результат импорта">
        <div className="row">
          <StatusPill tone="ok">Добавлено: {result.created}</StatusPill>
          <StatusPill>Пропущено: {result.skipped}</StatusPill>
          <StatusPill tone={result.invalid_rows ? "warn" : "ok"}>Ошибок строк: {result.invalid_rows}</StatusPill>
        </div>
        {result.errors.length > 0 && <ul className="import-errors">{result.errors.map((item) => <li key={`${item.line}-${item.error}`}>Строка {item.line}: {item.error}</li>)}</ul>}
      </Surface>}

      <Surface title="Импортированные фразы">
        <form className="row" onSubmit={search} style={{ marginBottom: "1rem" }}>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по фразе" style={{ minWidth: 260 }} />
          <button className="btn btn-ghost" type="submit">Найти</button>
        </form>
        <DataTable headers={["Фраза", "Группа", "Intent", "Гео", "Частотность", "Приоритет"]}>
          {keywords.map((keyword) => <tr key={keyword.id}><td><strong>{keyword.phrase}</strong></td><td>{keyword.category || "—"}</td><td>{keyword.meta.intent || "—"}</td><td>{keyword.meta.city || "—"}</td><td>{keyword.meta.frequency || "—"}</td><td>{keyword.meta.priority || "—"}</td></tr>)}
          {keywords.length === 0 && <tr><td colSpan={6}><EmptyState title="Семантика пока не загружена" hint="Скачайте шаблон, проверьте CSV и отдельно подтвердите импорт." /></td></tr>}
        </DataTable>
      </Surface>
    </div>
  );
}
