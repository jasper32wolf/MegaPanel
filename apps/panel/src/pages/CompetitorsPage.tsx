import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, HelpTip, PageHeader, StatusPill, Surface } from "../components/ui";

type Scan = {
  id: string;
  seed_url: string;
  status: string;
  urls: string[];
  skeleton: { sample_titles?: string[]; sample_h1?: string[]; sample_faq?: string[] };
  error: string | null;
  created_at: string;
};

function tone(status: string) {
  if (status === "done") return "ok" as const;
  if (status === "blocked" || status === "failed") return "danger" as const;
  return "warn" as const;
}

export function CompetitorsPage() {
  const { token } = useAuth();
  const [urlsText, setUrlsText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [scans, setScans] = useState<Scan[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setScans(await api<Scan[]>("/api/v1/competitors/scans", {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить исследования"));
  }, [token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const urls = urlsText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!urls.length) return;
    setBusy(true);
    setError(null);
    try {
      await api("/api/v1/competitors/scan", {
        method: "POST",
        body: JSON.stringify({ urls, terms_acknowledged: acknowledged }),
      }, token);
      setUrlsText("");
      setAcknowledged(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Исследование не запущено");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Конкуренты и evidence"
        description="Структурный анализ вручную указанных публичных URL для подготовки семантики и будущих AI-предложений. Он не ищет конкурентов, не читает sitemap и не создаёт страницы сайта."
      />
      {error ? <p className="error" role="alert">{error}</p> : null}
      <Surface title="Новый анализ">
        <form className="stack" onSubmit={submit}>
          <label className="field">
            Публичные URL конкурентов
            <textarea value={urlsText} onChange={(event) => setUrlsText(event.target.value)} rows={7} placeholder={"https://example.test/service\nhttps://another.example.test/prices"} required />
            <span className="muted">От 1 до 10 уникальных URL, по одному на строку. Панель загрузит только эти страницы.</span>
          </label>
          <label className="row">
            <input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} required />
            <span>Я отвечаю за право анализа указанных публичных страниц и понимаю, что URL не будут расширяться в crawl.</span>
            <HelpTip label="Что извлекается">Заголовок, meta description, H1–H6 и question-like headings. Контент не считается подтверждённым фактом и не публикуется автоматически.</HelpTip>
          </label>
          <button className="btn" type="submit" disabled={busy || !acknowledged || !urlsText.trim()}>{busy ? "Анализ…" : "Проанализировать URL"}</button>
        </form>
      </Surface>
      <Surface title="Сохранённые исследования">
        {scans.length === 0 ? <EmptyState title="Исследований пока нет" hint="Добавьте вручную выбранные публичные URL. Результат станет evidence для ручного semantic/AI review." /> : <DataTable headers={["Дата", "URL", "Статус", "Извлечённые сигналы"]}>{scans.map((scan) => <tr key={scan.id}><td className="muted">{scan.created_at.slice(0, 19)}</td><td><strong>{scan.seed_url}</strong><br /><span className="muted">{scan.urls.length} URL</span></td><td><StatusPill tone={tone(scan.status)}>{scan.status}</StatusPill>{scan.error ? <p className="error">{scan.error}</p> : null}</td><td>{scan.skeleton.sample_titles?.slice(0, 2).join(" · ") || "—"}</td></tr>)}</DataTable>}
      </Surface>
    </div>
  );
}
