import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { ConfirmDialog, DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Domain = {
  id: string;
  hostname: string;
  site_id: string | null;
  ssl_status: string;
  dns_status: string;
  expires_at: string | null;
  registrar: string | null;
};

type Site = { id: string; domain: string };
type Redirect = {
  id: string;
  site_id: string;
  site_domain: string;
  from_path: string;
  to_url: string;
  code: number;
  created_at: string | null;
};
type Health = { hostname: string; ssl_status: string; dns_status: string; caddy_reachable: boolean; expiry_alerts: number[] };

export function DomainsPage() {
  const { token } = useAuth();
  const [domains, setDomains] = useState<Domain[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [redirects, setRedirects] = useState<Redirect[]>([]);
  const [hostname, setHostname] = useState("");
  const [siteId, setSiteId] = useState("");
  const [redirectSiteId, setRedirectSiteId] = useState("");
  const [redirectPath, setRedirectPath] = useState("");
  const [redirectTarget, setRedirectTarget] = useState("");
  const [redirectCode, setRedirectCode] = useState("301");
  const [health, setHealth] = useState<Health | null>(null);
  const [removingDomain, setRemovingDomain] = useState<Domain | null>(null);
  const [domainConfirmation, setDomainConfirmation] = useState("");
  const [redirectToRemove, setRedirectToRemove] = useState<Redirect | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    const [domainRows, siteRows, redirectRows] = await Promise.all([
      api<Domain[]>("/api/v1/domains", {}, token),
      api<Site[]>("/api/v1/sites", {}, token),
      api<Redirect[]>("/api/v1/domains/redirects", {}, token),
    ]);
    setDomains(domainRows);
    setSites(siteRows);
    setRedirects(redirectRows);
    setSiteId((current) => current || siteRows[0]?.id || "");
    setRedirectSiteId((current) => current || siteRows[0]?.id || "");
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить домены"));
  }, [token]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!siteId) return;
    setBusy("domain-create");
    setError(null);
    try {
      await api("/api/v1/domains", { method: "POST", body: JSON.stringify({ hostname, site_id: siteId }) }, token);
      setHostname("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Домен не добавлен");
    } finally {
      setBusy(null);
    }
  }

  async function checkHealth(id: string) {
    setBusy(`health:${id}`);
    setError(null);
    try {
      setHealth(await api<Health>(`/api/v1/domains/health/${id}`, {}, token));
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Проверка не выполнена");
    } finally {
      setBusy(null);
    }
  }

  async function removeDomain(event: FormEvent) {
    event.preventDefault();
    if (!removingDomain || domainConfirmation !== removingDomain.hostname) return;
    setBusy(`domain-delete:${removingDomain.id}`);
    setError(null);
    try {
      await api(`/api/v1/domains/${removingDomain.id}`, { method: "DELETE" }, token);
      if (health?.hostname === removingDomain.hostname) setHealth(null);
      setRemovingDomain(null);
      setDomainConfirmation("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Домен не удалён");
    } finally {
      setBusy(null);
    }
  }

  async function createRedirect(event: FormEvent) {
    event.preventDefault();
    if (!redirectSiteId) return;
    setBusy("redirect-create");
    setError(null);
    try {
      await api(
        "/api/v1/domains/redirects",
        { method: "POST", body: JSON.stringify({ site_id: redirectSiteId, from_path: redirectPath, to_url: redirectTarget, code: Number(redirectCode) }) },
        token,
      );
      setRedirectPath("");
      setRedirectTarget("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Редирект не создан");
    } finally {
      setBusy(null);
    }
  }

  async function removeRedirect(redirect: Redirect) {
    setBusy(`redirect-delete:${redirect.id}`);
    setError(null);
    try {
      await api(`/api/v1/domains/redirects/${redirect.id}`, { method: "DELETE" }, token);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Редирект не удалён");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader title="Домены" description="Привязывайте публичный hostname к собранному сайту, проверяйте DNS и TLS, затем управляйте редиректами в одном месте." />
      {error && <p className="error">{error}</p>}
      <Surface title="Подключить домен">
        {sites.length === 0 ? <EmptyState title="Сначала создайте сайт" hint="Домен можно привязать только к конкретному static release." /> : (
          <form onSubmit={onCreate} className="stack">
            <label className="field">Сайт<select value={siteId} onChange={(event) => setSiteId(event.target.value)}>{sites.map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}</select></label>
            <label className="field">Hostname<input value={hostname} onChange={(event) => setHostname(event.target.value)} placeholder="example.ru" required /></label>
            <ol className="muted" style={{ margin: 0, paddingLeft: "1.25rem" }}><li>Направьте A/AAAA-запись hostname на IP VPS.</li><li>Подождите распространения DNS и нажмите «Проверить».</li><li>После успешной проверки Caddy выпустит TLS-сертификат.</li></ol>
            <button className="btn" type="submit" disabled={busy !== null || !siteId}>{busy === "domain-create" ? "Подключение…" : "Подключить"}</button>
          </form>
        )}
      </Surface>
      <Surface title="Подключённые домены">
        <DataTable headers={["Hostname", "Сайт", "DNS", "TLS", "Срок", ""]}>
          {domains.map((domain) => (
            <tr key={domain.id}>
              <td><strong>{domain.hostname}</strong></td>
              <td className="muted">{sites.find((site) => site.id === domain.site_id)?.domain || "—"}</td>
              <td><StatusPill tone={domain.dns_status === "ok" ? "ok" : "warn"}>{domain.dns_status}</StatusPill></td>
              <td><StatusPill tone={domain.ssl_status === "ok" ? "ok" : domain.ssl_status === "pending" ? "accent" : "warn"}>{domain.ssl_status}</StatusPill></td>
              <td className="muted">{domain.expires_at?.slice(0, 10) || "—"}</td>
              <td><div className="row row-tight"><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => checkHealth(domain.id)}>{busy === `health:${domain.id}` ? "Проверка…" : "Проверить"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => { setRemovingDomain(domain); setDomainConfirmation(""); }}>Удалить</button></div></td>
            </tr>
          ))}
          {domains.length === 0 && <tr><td colSpan={6}><EmptyState title="Доменов пока нет" /></td></tr>}
        </DataTable>
      </Surface>
      {removingDomain && <Surface title={`Удалить ${removingDomain.hostname}`}><form onSubmit={removeDomain} className="stack"><p className="muted" style={{ margin: 0 }}>Будет удалена привязка hostname и его vhost в Caddy. Введите hostname полностью для подтверждения.</p><label className="field">Подтверждение<input value={domainConfirmation} onChange={(event) => setDomainConfirmation(event.target.value)} placeholder={removingDomain.hostname} autoComplete="off" /></label><div className="row"><button className="btn" type="submit" disabled={busy !== null || domainConfirmation !== removingDomain.hostname}>{busy === `domain-delete:${removingDomain.id}` ? "Удаление…" : "Подтвердить удаление"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => { setRemovingDomain(null); setDomainConfirmation(""); }}>Отмена</button></div></form></Surface>}
      {health && <Surface title={`Проверка · ${health.hostname}`}><div className="row"><StatusPill tone={health.dns_status === "ok" ? "ok" : "warn"}>DNS: {health.dns_status}</StatusPill><StatusPill tone={health.ssl_status === "ok" ? "ok" : "warn"}>TLS: {health.ssl_status}</StatusPill><StatusPill tone={health.caddy_reachable ? "ok" : "danger"}>Caddy: {health.caddy_reachable ? "доступен" : "недоступен"}</StatusPill>{health.expiry_alerts.map((days) => <StatusPill key={days} tone="warn">Срок &lt; {days} дн.</StatusPill>)}</div></Surface>}
      <Surface title="Redirect rules">
        {sites.length === 0 ? <EmptyState title="Нет сайта для редиректа" /> : <form onSubmit={createRedirect} className="stack"><label className="field">Сайт<select value={redirectSiteId} onChange={(event) => setRedirectSiteId(event.target.value)}>{sites.map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}</select></label><label className="field">Исходный путь<input value={redirectPath} onChange={(event) => setRedirectPath(event.target.value)} placeholder="/old-page" required /></label><label className="field">Целевой HTTPS URL<input value={redirectTarget} onChange={(event) => setRedirectTarget(event.target.value)} placeholder={`https://${sites.find((site) => site.id === redirectSiteId)?.domain || "example.ru"}/new-page`} type="url" required /></label><label className="field">Код<select value={redirectCode} onChange={(event) => setRedirectCode(event.target.value)}><option value="301">301 — постоянно</option><option value="302">302 — временно</option><option value="303">303 — see other</option><option value="307">307 — временно, метод сохранён</option><option value="308">308 — постоянно, метод сохранён</option></select></label><p className="muted" style={{ margin: 0 }}>Целевой URL должен использовать HTTPS и домен выбранного сайта.</p><button className="btn" type="submit" disabled={busy !== null || !redirectSiteId}>{busy === "redirect-create" ? "Создание…" : "Создать редирект"}</button></form>}
        <DataTable headers={["Сайт", "Откуда", "Куда", "Код", ""]}>
          {redirects.map((redirect) => <tr key={redirect.id}><td className="muted">{redirect.site_domain}</td><td><strong>{redirect.from_path}</strong></td><td className="muted">{redirect.to_url}</td><td><StatusPill tone="accent">{redirect.code}</StatusPill></td><td><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setRedirectToRemove(redirect)}>{busy === `redirect-delete:${redirect.id}` ? "Удаление…" : "Удалить"}</button></td></tr>)}
          {redirects.length === 0 && <tr><td colSpan={5}><EmptyState title="Правил пока нет" hint="Используйте 301 для постоянного переноса страниц." /></td></tr>}
        </DataTable>
      </Surface>
      <ConfirmDialog
        open={redirectToRemove !== null}
        title="Удалить redirect rule?"
        description={`Будет удален редирект ${redirectToRemove?.from_path || ""} для ${redirectToRemove?.site_domain || ""}.`}
        confirmLabel="Удалить"
        dangerous
        onCancel={() => setRedirectToRemove(null)}
        onConfirm={() => {
          const redirect = redirectToRemove;
          setRedirectToRemove(null);
          if (redirect) void removeRedirect(redirect);
        }}
      />
    </div>
  );
}
