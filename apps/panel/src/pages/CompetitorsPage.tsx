import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, HelpTip, PageHeader, StatusPill, Surface } from "../components/ui";

type Project = { id: string; name: string; slug: string };
type Scan = {
  id: string;
  project_id: string | null;
  seed_url: string;
  status: string;
  urls: string[];
  skeleton: { sample_titles?: string[]; sample_h1?: string[]; sample_faq?: string[] };
  error: string | null;
  created_at: string;
};
type Evidence = {
  id: string;
  source_scan_id: string | null;
  approved_at: string | null;
  content: { signals?: { sample_titles?: string[]; sample_h1?: string[]; sample_faq?: string[] } };
};

function tone(status: string) {
  if (status === "done" || status === "approved") return "ok" as const;
  if (status === "blocked" || status === "failed") return "danger" as const;
  return "warn" as const;
}

export function CompetitorsPage() {
  const { token } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [urlsText, setUrlsText] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [scans, setScans] = useState<Scan[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadProjectData(id = projectId) {
    if (!id) {
      setScans([]);
      setEvidence([]);
      return;
    }
    const [nextScans, nextEvidence] = await Promise.all([
      api<Scan[]>(`/api/v1/competitors/projects/${id}/scans`, {}, token),
      api<Evidence[]>(`/api/v1/competitors/projects/${id}/evidence`, {}, token),
    ]);
    setScans(nextScans);
    setEvidence(nextEvidence);
  }

  useEffect(() => {
    api<Project[]>("/api/v1/projects", {}, token)
      .then((nextProjects) => {
        setProjects(nextProjects);
        if (nextProjects.length === 1) setProjectId(nextProjects[0].id);
      })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить проекты"));
  }, [token]);

  useEffect(() => {
    loadProjectData().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить исследования"));
  }, [projectId, token]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const urls = urlsText.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    if (!projectId || !urls.length) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/competitors/projects/${projectId}/scans`, {
        method: "POST",
        body: JSON.stringify({ urls, terms_acknowledged: acknowledged }),
      }, token);
      setUrlsText("");
      setAcknowledged(false);
      await loadProjectData();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Исследование не запущено");
    } finally {
      setBusy(false);
    }
  }

  async function approve(scan: Scan) {
    if (!projectId) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/competitors/projects/${projectId}/scans/${scan.id}/approve-evidence`, { method: "POST" }, token);
      await loadProjectData();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Evidence не одобрен");
    } finally {
      setBusy(false);
    }
  }

  const approvedScanIds = new Set(evidence.map((item) => item.source_scan_id));

  return (
    <div>
      <PageHeader
        title="Конкуренты и evidence"
        description="Ручной анализ точно указанных публичных URL в рамках одного проекта. Он не ищет конкурентов, не читает sitemap, не импортирует keywords и не создаёт страницы сайта."
      />
      {error ? <p className="error" role="alert">{error}</p> : null}
      <Surface title="Проект и новый анализ">
        <form className="stack" onSubmit={submit}>
          <label className="field">
            Проект
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)} required>
              <option value="">Выберите проект</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name} ({project.slug})</option>)}
            </select>
            <span className="muted">Scan и последующее evidence навсегда относятся к выбранному проекту.</span>
          </label>
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
          <button className="btn" type="submit" disabled={busy || !projectId || !acknowledged || !urlsText.trim()}>{busy ? "Анализ…" : "Проанализировать URL"}</button>
        </form>
      </Surface>
      <Surface title="Сохранённые исследования">
        {!projectId ? <EmptyState title="Выберите проект" hint="Источники и evidence намеренно не смешиваются между проектами." /> : scans.length === 0 ? <EmptyState title="Исследований пока нет" hint="Добавьте вручную выбранные публичные URL. После проверки результат можно отдельно одобрить как reference-only evidence." /> : <DataTable headers={["Дата", "URL", "Статус", "Извлечённые сигналы", "Evidence"]}>{scans.map((scan) => <tr key={scan.id}><td className="muted">{scan.created_at.slice(0, 19)}</td><td><strong>{scan.seed_url}</strong><br /><span className="muted">{scan.urls.length} URL</span></td><td><StatusPill tone={tone(scan.status)}>{scan.status}</StatusPill>{scan.error ? <p className="error">{scan.error}</p> : null}</td><td>{scan.skeleton.sample_titles?.slice(0, 2).join(" · ") || "—"}</td><td>{approvedScanIds.has(scan.id) ? <StatusPill tone="ok">approved</StatusPill> : scan.status === "done" ? <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => approve(scan)}>Одобрить evidence</button> : "—"}</td></tr>)}</DataTable>}
      </Surface>
      <Surface title="Approved evidence">
        {!projectId || evidence.length === 0 ? <EmptyState title="Одобренного evidence пока нет" hint="Только после явного одобрения компактные reference-only сигналы могут попасть в architecture quote выбранного проекта." /> : <DataTable headers={["Одобрено", "Сигналы", "Граница"]}>{evidence.map((item) => <tr key={item.id}><td className="muted">{item.approved_at?.slice(0, 19) || "—"}</td><td>{item.content.signals?.sample_titles?.slice(0, 2).join(" · ") || item.content.signals?.sample_h1?.slice(0, 2).join(" · ") || "—"}</td><td>reference-only; не business facts, не keywords и не PagePlan</td></tr>)}</DataTable>}
      </Surface>
    </div>
  );
}
