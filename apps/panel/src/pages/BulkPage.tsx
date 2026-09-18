import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Site = { id: string; domain: string; publish_state: string };
type BulkResult = { operation_id: string; updated: number };

export function BulkPage() {
  const { token } = useAuth();
  const [sites, setSites] = useState<Site[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Site[]>("/api/v1/sites", {}, token)
      .then(setSites)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить сайты"));
  }, [token]);

  const selectedSites = useMemo(
    () => sites.filter((site) => selectedIds.includes(site.id)),
    [selectedIds, sites],
  );
  const contacts = useMemo(
    () => ({
      ...(phone.trim() ? { phone: phone.trim() } : {}),
      ...(email.trim() ? { email: email.trim() } : {}),
    }),
    [email, phone],
  );

  function toggleSite(siteId: string) {
    setSelectedIds((current) =>
      current.includes(siteId) ? current.filter((id) => id !== siteId) : [...current, siteId],
    );
  }

  function toggleAll() {
    setSelectedIds(selectedIds.length === sites.length ? [] : sites.map((site) => site.id));
  }

  async function applyContacts(event: FormEvent) {
    event.preventDefault();
    if (!selectedSites.length || !Object.keys(contacts).length) return;
    const summary = [phone.trim() && `телефон: ${phone.trim()}`, email.trim() && `email: ${email.trim()}`]
      .filter(Boolean)
      .join(", ");
    if (!window.confirm(`Изменить контакты на ${selectedSites.length} сайт(ах): ${summary}?`)) return;

    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api<BulkResult>(
        "/api/v1/bulk/edit",
        { method: "POST", body: JSON.stringify({ site_ids: selectedIds, contacts }) },
        token,
      );
      setResult(response);
      setMessage(`Контакты обновлены на ${response.updated} сайт(ах).`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось обновить контакты");
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    if (!result || !window.confirm("Отменить последнюю массовую замену контактов?")) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/v1/bulk/undo/${result.operation_id}`, { method: "POST" }, token);
      setMessage("Массовое изменение отменено.");
      setResult(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось отменить изменение");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Массовое обновление контактов"
        description="Выберите сайты, проверьте предварительный список и обновите только телефон или email. Операция не создаёт страницы, не запускает проверку качества и не публикует сайты; массовая публикация и IndexNow намеренно недоступны."
        actions={<StatusPill tone={selectedSites.length ? "accent" : "default"}>Выбрано: {selectedSites.length}</StatusPill>}
      />
      {error && <p className="error">{error}</p>}
      {message && <p className="muted">{message}</p>}

      <Surface title="1. Выберите сайты">
        <DataTable headers={["", "Домен", "Публикация"]}>
          {sites.map((site) => (
            <tr key={site.id}>
              <td><input aria-label={`Выбрать ${site.domain}`} type="checkbox" checked={selectedIds.includes(site.id)} onChange={() => toggleSite(site.id)} /></td>
              <td><strong>{site.domain}</strong></td>
              <td><StatusPill tone={site.publish_state === "published" ? "ok" : "accent"}>{site.publish_state}</StatusPill></td>
            </tr>
          ))}
          {sites.length === 0 && <tr><td colSpan={3}><EmptyState title="Сайтов пока нет" hint="Создайте сайт перед массовым обновлением контактов." /></td></tr>}
        </DataTable>
        {sites.length > 0 && <button className="btn btn-ghost" type="button" disabled={busy} onClick={toggleAll}>{selectedIds.length === sites.length ? "Снять выбор" : "Выбрать все"}</button>}
      </Surface>

      <Surface title="2. Предварительный просмотр и подтверждение">
        <form className="stack" onSubmit={applyContacts}>
          <p className="muted" style={{ margin: 0 }}>Изменение затронет: {selectedSites.length ? selectedSites.map((site) => site.domain).join(", ") : "выберите хотя бы один сайт"}.</p>
          <label className="field">Телефон <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+7 (900) 000-00-00" /></label>
          <label className="field">Email <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="hello@example.ru" /></label>
          <div className="row">
            <button className="btn" type="submit" disabled={busy || !selectedSites.length || !Object.keys(contacts).length}>{busy ? "Применение…" : "Подтвердить изменение"}</button>
            {result && <button className="btn btn-ghost" type="button" disabled={busy} onClick={undo}>Отменить последнее изменение</button>}
          </div>
        </form>
      </Surface>
    </div>
  );
}
