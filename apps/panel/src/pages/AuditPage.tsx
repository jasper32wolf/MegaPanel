import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type AuditEntry = {
  id: number;
  action: string;
  actor_id: string | null;
  created_at: string | null;
  record_hash: string;
};
type AuditHistory = { items: AuditEntry[]; offset: number; limit: number; total: number };
type Integrity = { status: "valid" | "invalid"; checked_records: number; invalid_record_ids: number[] };

export function AuditPage() {
  const { token } = useAuth();
  const [action, setAction] = useState("");
  const [history, setHistory] = useState<AuditHistory | null>(null);
  const [integrity, setIntegrity] = useState<Integrity | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(offset = 0) {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "25", offset: String(offset) });
      if (action.trim()) params.set("action", action.trim());
      const [nextHistory, nextIntegrity] = await Promise.all([
        api<AuditHistory>(`/api/v1/security/audit?${params.toString()}`, {}, token),
        api<Integrity>("/api/v1/security/audit/integrity", {}, token).catch(() => null),
      ]);
      setHistory(nextHistory);
      setIntegrity(nextIntegrity);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить audit history");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, [token]);

  return (
    <div>
      <PageHeader title="Audit history" description="Только metadata событий: payload, PII, recipient адреса и secrets не выводятся в панели." />
      {error ? <p className="error" role="alert">{error}</p> : null}
      <Surface title="Integrity hash chain">
        {integrity ? <div className="row"><StatusPill tone={integrity.status === "valid" ? "ok" : "danger"}>{integrity.status === "valid" ? "chain valid" : "chain invalid"}</StatusPill><span className="muted">Проверено записей: {integrity.checked_records}</span>{integrity.invalid_record_ids.length ? <span className="error">Нарушены записи: {integrity.invalid_record_ids.join(", ")}</span> : null}</div> : <p className="muted">Integrity status доступен только superadmin.</p>}
      </Surface>
      <Surface title="События">
        <div className="row"><label className="field">Action<input value={action} onChange={(event) => setAction(event.target.value)} placeholder="Например: project.build.publish" /></label><button className="btn btn-ghost" type="button" disabled={loading} onClick={() => void load()}>{loading ? "Загрузка…" : "Применить"}</button></div>
        <div className="table-wrap"><table className="table"><thead><tr><th>Когда</th><th>Action</th><th>Actor</th><th>Hash</th></tr></thead><tbody>{history?.items.map((entry) => <tr key={entry.id}><td className="muted">{entry.created_at?.slice(0, 19) || "—"}</td><td>{entry.action}</td><td className="muted">{entry.actor_id ? entry.actor_id.slice(0, 12) : "system"}</td><td><code>{entry.record_hash.slice(0, 16)}…</code></td></tr>)}{history?.items.length === 0 ? <tr><td colSpan={4} className="muted">Событий не найдено</td></tr> : null}</tbody></table></div>
        {history ? <div className="row"><span className="muted">Показано {history.items.length ? history.offset + 1 : 0}–{Math.min(history.offset + history.items.length, history.total)} из {history.total}</span><button className="btn btn-ghost" type="button" disabled={loading || history.offset === 0} onClick={() => void load(Math.max(0, history.offset - history.limit))}>Назад</button><button className="btn btn-ghost" type="button" disabled={loading || history.offset + history.items.length >= history.total} onClick={() => void load(history.offset + history.limit)}>Далее</button></div> : null}
      </Surface>
    </div>
  );
}
