import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Site = {
  id: string;
  domain: string;
  publish_state: string;
  build_hash: string | null;
  version: number;
};

type Build = {
  id: string;
  status: string;
  build_hash: string | null;
  previous_build_hash: string | null;
  pages_built: number;
  duration_ms: number | null;
  created_at: string | null;
};

type SitePage = {
  id: string;
  slug: string;
  publish_state: string;
  index_state: string;
  thin: boolean;
  content_chars: number;
};

type WebhookSettings = {
  target_url: string | null;
  secret_configured: boolean;
  configured: boolean;
};

export function SitesPage() {
  const { token } = useAuth();
  const [sites, setSites] = useState<Site[]>([]);
  const [selected, setSelected] = useState<Site | null>(null);
  const [history, setHistory] = useState<Build[]>([]);
  const [pages, setPages] = useState<SitePage[]>([]);
  const [webhook, setWebhook] = useState<WebhookSettings | null>(null);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    setSites(await api<Site[]>("/api/v1/sites", {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить сайты"));
  }, [token]);

  async function openHistory(site: Site) {
    setSelected(site);
    setError(null);
    try {
      const [buildRows, pageRows, webhookSettings] = await Promise.all([
        api<Build[]>(`/api/v1/panel/builds/${site.id}`, {}, token),
        api<SitePage[]>(`/api/v1/sites/${site.id}/pages`, {}, token),
        api<WebhookSettings>(`/api/v1/sites/${site.id}/webhook`, {}, token),
      ]);
      setHistory(buildRows);
      setPages(pageRows);
      setWebhook(webhookSettings);
      setWebhookUrl(webhookSettings.target_url || "");
      setWebhookSecret("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить данные сайта");
      setHistory([]);
      setPages([]);
      setWebhook(null);
    }
  }

  async function saveWebhook(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setBusy(`${selected.id}:webhook`);
    setError(null);
    try {
      const settings = await api<WebhookSettings>(
        `/api/v1/sites/${selected.id}/webhook`,
        {
          method: "PUT",
          body: JSON.stringify({ url: webhookUrl, secret: webhookSecret }),
        },
        token,
      );
      setWebhook(settings);
      setWebhookUrl(settings.target_url || "");
      setWebhookSecret("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Настройки webhook не сохранены");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader title="Сайты" description="Просмотр статуса, истории сборок, страниц и webhook. Сборка, публикация и откат выполняются только из Project workspace через candidate build и явное подтверждение." />
      {error && <p className="error">{error}</p>}
      <Surface>
        <DataTable headers={["Домен", "Статус", "Сборка", "Версия", "Действия"]}>
          {sites.map((site) => (
            <tr key={site.id}>
              <td><strong>{site.domain}</strong></td>
              <td><StatusPill tone={site.publish_state === "published" ? "ok" : "accent"}>{site.publish_state}</StatusPill></td>
              <td className="muted">{site.build_hash ? site.build_hash.slice(0, 12) : "—"}</td>
              <td>{site.version}</td>
              <td>
                <div className="row row-tight">
                  <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => openHistory(site)}>Детали</button>
                </div>
              </td>
            </tr>
          ))}
          {sites.length === 0 && <tr><td colSpan={5}><EmptyState title="Сайтов пока нет" hint="Создайте первый сайт через раздел «Новый сайт»." /></td></tr>}
        </DataTable>
      </Surface>
      {selected && (
        <Surface title={`История сборок · ${selected.domain}`}>
          <DataTable headers={["Когда", "Статус", "Hash", "Страниц", "Время"]}>
            {history.map((build) => (
              <tr key={build.id}>
                <td>{build.created_at?.slice(0, 19) || "—"}</td>
                <td><StatusPill tone={build.status === "success" ? "ok" : "danger"}>{build.status}</StatusPill></td>
                <td className="muted">{build.build_hash?.slice(0, 12) || "—"}</td>
                <td>{build.pages_built}</td>
                <td>{build.duration_ms === null ? "—" : `${build.duration_ms} мс`}</td>
              </tr>
            ))}
            {history.length === 0 && <tr><td colSpan={5}><EmptyState title="История сборок пуста" /></td></tr>}
          </DataTable>
        </Surface>
      )}
      {selected && (
        <Surface title={`Webhook лидов · ${selected.domain}`}>
          <form className="stack" onSubmit={saveWebhook}>
            <p className="muted" style={{ margin: 0 }}>
              Доставка использует HTTPS, HMAC и повторные попытки. Секрет не отображается после сохранения и не попадает в опубликованный сайт.
            </p>
            <label className="field">
              Webhook URL
              <input type="url" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="https://crm.example.test/leads" required />
            </label>
            <label className="field">
              Новый signing secret
              <input value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} type="password" autoComplete="new-password" minLength={16} required />
            </label>
            <div className="row">
              <StatusPill tone={webhook?.configured ? "ok" : "warn"}>{webhook?.configured ? "настроен" : "не настроен"}</StatusPill>
              {webhook?.secret_configured && <span className="muted">Секрет сохранён. Введите новый, только если меняете настройку.</span>}
            </div>
            <button className="btn" type="submit" disabled={busy !== null || !webhookUrl.trim() || webhookSecret.length < 16}>
              {busy === `${selected.id}:webhook` ? "Сохранение…" : "Сохранить webhook"}
            </button>
          </form>
        </Surface>
      )}
      {selected && (
        <Surface title={`Страницы · ${selected.domain}`}>
          <DataTable headers={["URL", "Публикация", "Индексация", "Контент", ""]}>
            {pages.map((page) => (
              <tr key={page.id}>
                <td><strong>{page.slug || "/"}</strong></td>
                <td><StatusPill tone={page.publish_state === "published" ? "ok" : "accent"}>{page.publish_state}</StatusPill></td>
                <td><StatusPill tone={page.index_state === "indexed" ? "ok" : page.index_state === "noindex" ? "warn" : "accent"}>{page.index_state}</StatusPill></td>
                <td className="muted">{page.content_chars} знаков{page.thin ? " · thin" : ""}</td>
                <td>{page.thin ? <StatusPill tone="warn">нужна доработка</StatusPill> : ""}</td>
              </tr>
            ))}
            {pages.length === 0 && <tr><td colSpan={5}><EmptyState title="Страницы появятся после первой сборки" /></td></tr>}
          </DataTable>
        </Surface>
      )}
    </div>
  );
}
