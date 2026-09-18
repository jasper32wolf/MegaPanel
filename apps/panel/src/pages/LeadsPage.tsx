import { useEffect, useState, type FormEvent } from "react";
import { api, download, useAuth } from "../lib/auth";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type Site = { id: string; domain: string };
type Lead = {
  id: string;
  site_id: string;
  site_domain: string;
  status: string;
  qualification: string | null;
  page_slug: string | null;
  created_at: string | null;
  crm_status: string | null;
  delivery_status: string | null;
};
type Inbox = { items: Lead[]; offset: number; limit: number; total: number };
type LeadDetail = Lead & { notes: string | null; utm: Record<string, unknown> };
type LeadPii = { id: string; phone: string | null; email: string | null; name: string | null; message: string | null };
type DeliveryAttempt = { sequence: number; trigger: string; status: string; http_status: number | null; error: string | null; started_at: string | null; finished_at: string | null };
type Delivery = { id: string; target: string; status: string; attempt_count: number; max_attempts: number; next_attempt_at: string | null; last_error: string | null; last_http_status: number | null };
type DeliveryHistory = { delivery: Delivery | null; attempts: DeliveryAttempt[] };

const statuses = ["new", "qualified", "spam", "sent", "failed"] as const;
const pageSize = 50;

function statusTone(status: string) {
  if (status === "qualified" || status === "sent") return "ok" as const;
  if (status === "failed" || status === "spam") return "danger" as const;
  return "default" as const;
}

export function LeadsPage() {
  const { token } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [detail, setDetail] = useState<LeadDetail | null>(null);
  const [delivery, setDelivery] = useState<DeliveryHistory | null>(null);
  const [pii, setPii] = useState<LeadPii | null>(null);
  const [status, setStatus] = useState("");
  const [siteId, setSiteId] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [notes, setNotes] = useState("");
  const [exportConfirmation, setExportConfirmation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
    if (status) params.set("status", status);
    if (siteId) params.set("site_id", siteId);
    if (query) params.set("q", query);
    const inbox = await api<Inbox>(`/api/v1/leads/inbox?${params.toString()}`, {}, token);
    setLeads(inbox.items);
    setTotal(inbox.total);
  }

  useEffect(() => {
    api<Site[]>("/api/v1/sites", {}, token)
      .then(setSites)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить сайты"));
  }, [token]);

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить лиды"));
  }, [token, status, siteId, query, offset]);

  async function openDetail(lead: Lead) {
    setBusy(`detail:${lead.id}`);
    setError(null);
    setPii(null);
    setDelivery(null);
    try {
      const result = await api<LeadDetail>(`/api/v1/leads/${lead.id}`, {}, token);
      setDetail(result);
      setNotes(result.notes || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось открыть лид");
    } finally {
      setBusy(null);
    }
  }

  async function revealPii() {
    if (!detail) return;
    setBusy(`reveal:${detail.id}`);
    setError(null);
    try {
      setPii(await api<LeadPii>(`/api/v1/leads/${detail.id}/reveal`, { method: "POST" }, token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось раскрыть контактные данные");
    } finally {
      setBusy(null);
    }
  }

  async function loadDelivery() {
    if (!detail) return;
    setBusy(`delivery:${detail.id}`);
    setError(null);
    try {
      setDelivery(await api<DeliveryHistory>(`/api/v1/leads/${detail.id}/delivery`, {}, token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить историю доставки");
    } finally {
      setBusy(null);
    }
  }

  async function resendDelivery() {
    if (!detail || !delivery?.delivery) return;
    setBusy(`resend:${detail.id}`);
    setError(null);
    try {
      await api(`/api/v1/leads/${detail.id}/delivery/resend`, { method: "POST" }, token);
      await load();
      setDelivery(await api<DeliveryHistory>(`/api/v1/leads/${detail.id}/delivery`, {}, token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Повторная доставка не запущена");
    } finally {
      setBusy(null);
    }
  }

  async function updateStatus(lead: Lead, nextStatus: string) {
    setBusy(`status:${lead.id}`);
    setError(null);
    try {
      await api(`/api/v1/leads/${lead.id}`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) }, token);
      await load();
      if (detail?.id === lead.id) setDetail({ ...detail, status: nextStatus });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Статус не обновлён");
    } finally {
      setBusy(null);
    }
  }

  async function saveNotes() {
    if (!detail) return;
    setBusy(`notes:${detail.id}`);
    setError(null);
    try {
      const result = await api<{ notes: string | null }>(
        `/api/v1/leads/${detail.id}`,
        { method: "PATCH", body: JSON.stringify({ status: detail.status, notes }) },
        token,
      );
      setDetail({ ...detail, notes: result.notes });
      setNotes(result.notes || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Заметка не сохранена");
    } finally {
      setBusy(null);
    }
  }

  async function exportLeads() {
    setBusy("export");
    setError(null);
    try {
      const blob = await download(
        "/api/v1/leads/export",
        { method: "POST", body: JSON.stringify({ status: status || null, site_id: siteId || null, q: query || null }) },
        token,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "leads.csv";
      link.click();
      URL.revokeObjectURL(url);
      setExportConfirmation(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось экспортировать лиды");
    } finally {
      setBusy(null);
    }
  }


  function applySearch(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setQuery(searchDraft.trim());
  }

  function setFilter(setter: (value: string) => void, value: string) {
    setter(value);
    setOffset(0);
  }

  return (
    <div>
      <PageHeader title="Лиды" description="В списке нет расшифрованных контактов. Откройте карточку, затем отдельно подтвердите раскрытие PII только при необходимости. Исход лида не изменяет контент автоматически; петля обратной связи относится к дорожной карте развития." />
      {error && <p className="error">{error}</p>}
      <Surface>
        <form onSubmit={applySearch} className="row" style={{ marginBottom: "1rem" }}>
          <label className="field" style={{ margin: 0, minWidth: 180 }}>Статус<select value={status} onChange={(event) => setFilter(setStatus, event.target.value)}><option value="">Все</option>{statuses.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="field" style={{ margin: 0, minWidth: 180 }}>Сайт<select value={siteId} onChange={(event) => setFilter(setSiteId, event.target.value)}><option value="">Все</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.domain}</option>)}</select></label>
          <label className="field" style={{ margin: 0, minWidth: 240 }}>Поиск<input value={searchDraft} onChange={(event) => setSearchDraft(event.target.value)} placeholder="Домен, статус или страница" /></label>
          <button className="btn" type="submit" disabled={busy !== null}>Найти</button>
        </form>
        <div className="row" style={{ marginBottom: "1rem" }}><p className="muted" style={{ margin: 0 }}>Найдено: {total}</p><button className="btn btn-ghost" type="button" disabled={busy !== null || total === 0} onClick={() => setExportConfirmation(true)}>Экспорт CSV</button></div>
        <DataTable headers={["Статус", "Сайт", "Квалификация", "Страница", "CRM", "Доставка", "Когда", ""]}>
          {leads.map((lead) => (
            <tr key={lead.id}>
              <td><StatusPill tone={statusTone(lead.status)}>{lead.status}</StatusPill></td>
              <td className="muted">{lead.site_domain}</td>
              <td>{lead.qualification || "—"}</td>
              <td className="muted">{lead.page_slug || "—"}</td>
              <td>{lead.crm_status || "—"}</td>
              <td>{lead.delivery_status ? <StatusPill tone={lead.delivery_status === "delivered" ? "ok" : lead.delivery_status === "dead_letter" ? "danger" : "warn"}>{lead.delivery_status}</StatusPill> : "—"}</td>
              <td className="muted">{lead.created_at?.slice(0, 19) || "—"}</td>
              <td><div className="row row-tight"><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => openDetail(lead)}>{busy === `detail:${lead.id}` ? "Открытие…" : "Открыть"}</button><select aria-label="Изменить статус" disabled={busy !== null} value={lead.status} onChange={(event) => updateStatus(lead, event.target.value)}>{statuses.map((item) => <option key={item} value={item}>{item}</option>)}</select></div></td>
            </tr>
          ))}
          {leads.length === 0 && <tr><td colSpan={8}><EmptyState title="Лидов пока нет" hint="После публикации формы новые заявки появятся здесь." /></td></tr>}
        </DataTable>
        <div className="row" style={{ marginTop: "1rem" }}><button className="btn btn-ghost" type="button" disabled={busy !== null || offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Назад</button><span className="muted">{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + leads.length, total)} из ${total}`}</span><button className="btn btn-ghost" type="button" disabled={busy !== null || offset + leads.length >= total} onClick={() => setOffset(offset + pageSize)}>Далее</button></div>
      </Surface>
      {exportConfirmation && <Surface title="Экспортировать лиды"><p className="muted">CSV содержит контактные данные и сообщение всех {total} лидов из текущей выборки. Файл нельзя безопасно передавать третьим лицам. Экспорт будет записан в audit log.</p><div className="row"><button className="btn" type="button" disabled={busy !== null} onClick={exportLeads}>{busy === "export" ? "Подготовка…" : "Подтвердить экспорт"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setExportConfirmation(false)}>Отмена</button></div></Surface>}
      {detail && <Surface title="Карточка лида"><div className="detail-grid"><div><strong>Сайт</strong><p>{detail.site_domain}</p></div><div><strong>Статус</strong><p>{detail.status}</p></div><div><strong>Страница</strong><p>{detail.page_slug || "—"}</p></div><div><strong>CRM</strong><p>{detail.crm_status || "—"}</p></div></div>{pii ? <><div className="detail-grid"><div><strong>Телефон</strong><p>{pii.phone || "—"}</p></div><div><strong>Email</strong><p>{pii.email || "—"}</p></div><div><strong>Имя</strong><p>{pii.name || "—"}</p></div></div><div><strong>Сообщение</strong><p>{pii.message || "—"}</p></div></> : <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={revealPii}>{busy === `reveal:${detail.id}` ? "Раскрытие…" : "Раскрыть контакты и сообщение"}</button>}<label className="field" style={{ marginTop: "1rem" }}>Заметка<textarea value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={4000} rows={4} /></label><div className="row"><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={saveNotes}>{busy === `notes:${detail.id}` ? "Сохранение…" : "Сохранить заметку"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={loadDelivery}>{busy === `delivery:${detail.id}` ? "Загрузка…" : "История доставки"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => { setDetail(null); setDelivery(null); setPii(null); setNotes(""); }}>Закрыть карточку</button></div></Surface>}
      {detail && delivery && <Surface title="Доставка webhook"><div className="detail-grid"><div><strong>Статус</strong><p><StatusPill tone={delivery.delivery?.status === "delivered" ? "ok" : delivery.delivery?.status === "dead_letter" ? "danger" : "warn"}>{delivery.delivery?.status || "не настроена"}</StatusPill></p></div>{delivery.delivery && <><div><strong>Попытки</strong><p>{delivery.delivery.attempt_count} / {delivery.delivery.max_attempts}</p></div><div><strong>Следующая попытка</strong><p>{delivery.delivery.next_attempt_at?.slice(0, 19) || "—"}</p></div><div><strong>Последний ответ</strong><p>{delivery.delivery.last_http_status || "—"}</p></div></>}</div>{delivery.delivery?.last_error && <p className="error">{delivery.delivery.last_error}</p>}{delivery.delivery?.status === "dead_letter" && <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={resendDelivery}>{busy === `resend:${detail.id}` ? "Запуск…" : "Повторить доставку"}</button>}<DataTable headers={["#", "Тип", "Статус", "HTTP", "Ошибка", "Когда"]}>{delivery.attempts.map((attempt) => <tr key={attempt.sequence}><td>{attempt.sequence}</td><td>{attempt.trigger}</td><td><StatusPill tone={attempt.status === "delivered" ? "ok" : attempt.status === "dead_letter" ? "danger" : "warn"}>{attempt.status}</StatusPill></td><td>{attempt.http_status || "—"}</td><td className="muted">{attempt.error || "—"}</td><td className="muted">{attempt.started_at?.slice(0, 19) || "—"}</td></tr>)}{delivery.attempts.length === 0 && <tr><td colSpan={6}><EmptyState title="Попыток пока нет" /></td></tr>}</DataTable></Surface>}
    </div>
  );
}
