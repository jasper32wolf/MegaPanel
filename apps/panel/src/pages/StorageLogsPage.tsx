import { useEffect, useState } from "react";
import { AsyncFeedback, ConfirmDialog, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";
import { api, useAuth } from "../lib/auth";

type StorageRun = {
  id: string;
  status: "preview" | "queued" | "processing" | "succeeded" | "failed";
  policy_hash: string;
  preview: { policy: Record<string, number>; sources: Record<string, number>; protected: Record<string, string> };
  result: Record<string, number>;
  error_code: string | null;
  expires_at: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
};

type StorageStatus = {
  policy: Record<string, number>;
  protected_sources: string[];
  runs: StorageRun[];
};

const labels: Record<string, string> = {
  auth_session_retention_days: "Истёкшие и отозванные сессии",
  notification_retention_days: "Прочитанные завершённые оповещения",
  operational_event_retention_days: "Операционные события",
  delivery_attempt_retention_days: "Попытки доставок webhook",
  dsar_export_ttl_hours: "Временные DSAR-экспорты",
  auth_sessions: "Сессии",
  read_terminal_notifications: "Оповещения",
  operational_events: "Операционные события",
  delivered_webhook_attempts: "Попытки webhook с успешной доставкой",
};

const statusLabels: Record<StorageRun["status"], string> = {
  preview: "предпросмотр",
  queued: "в очереди",
  processing: "выполняется",
  succeeded: "завершено",
  failed: "ошибка",
};

const errorLabels: Record<string, string> = {
  policy_changed: "Политика хранения изменилась",
  retention_failed: "Очистка не завершилась",
};

function tone(status: StorageRun["status"]) {
  if (status === "succeeded") return "ok" as const;
  if (status === "failed") return "danger" as const;
  if (status === "preview") return "default" as const;
  return "warn" as const;
}

export function StorageLogsPage() {
  const { token } = useAuth();
  const [status, setStatus] = useState<StorageStatus | null>(null);
  const [preview, setPreview] = useState<StorageRun | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    const next = await api<StorageStatus>("/api/v1/system/storage", {}, token);
    setStatus(next);
  }

  useEffect(() => {
    void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить хранение журналов"));
  }, [token]);

  useEffect(() => {
    if (!status?.runs.some((run) => run.status === "queued" || run.status === "processing")) return;
    const timer = window.setInterval(() => void load().catch(() => undefined), 10_000);
    return () => window.clearInterval(timer);
  }, [status, token]);

  async function createPreview() {
    setBusy("preview");
    setError(null);
    setMessage(null);
    try {
      const next = await api<StorageRun>("/api/v1/system/storage/previews", { method: "POST" }, token);
      setPreview(next);
      setConfirmation("");
      setMessage("Предпросмотр готов. Данные ещё не удалялись.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось подготовить предпросмотр");
    } finally {
      setBusy(null);
    }
  }

  async function queueRun() {
    if (!preview) return;
    setConfirmOpen(false);
    setBusy("run");
    setError(null);
    setMessage(null);
    try {
      await api<StorageRun>(
        `/api/v1/system/storage/previews/${preview.id}/run`,
        { method: "POST", body: JSON.stringify({ confirmation }) },
        token,
      );
      setPreview(null);
      setConfirmation("");
      setMessage("Очистка поставлена в очередь worker. Открытые инциденты, активные доставки и audit-цепочка исключены.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось поставить очистку в очередь");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div aria-busy={busy !== null}>
      <PageHeader
        title="Хранение и журналы"
        description="Панель ограничивает рост технических данных безопасными политиками. Она не получает доступ к Docker, shell-командам, произвольным путям или содержимому журналов."
        actions={<button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось обновить состояние"))}>Обновить</button>}
      />
      <AsyncFeedback error={error} message={message} />
      <Surface title="Автоматические лимиты">
        {!status ? <p className="muted">Загрузка политики…</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Источник</th><th>Хранение</th><th>Режим</th></tr></thead><tbody>{Object.entries(status.policy).map(([key, value]) => <tr key={key}><td>{labels[key] || key}</td><td>{key.endsWith("_hours") ? `${value} ч` : `${value} дней`}</td><td><StatusPill tone="ok">автоматически</StatusPill></td></tr>)}</tbody></table></div>}
        <p className="muted">Docker stdout/stderr: 10 MiB × 3 файла на сервис. Redis: фиксированная память без вытеснения очередей и AOF-rewrite. Локальные runtime-логи: 5 MiB × 5 поколений. VPS release audit: 5 MiB × 52 сжатые генерации. Артефакты сайтов: ежедневно сохраняются current/previous, ready и опубликованные сборки, последние 3 и всё моложе 90 дней.</p>
      </Surface>
      <Surface title="Защищённые данные">
        <p className="muted">Не попадают в обычную очистку: hash-chained audit ledger, evidence публикаций и rollback, открытые инциденты, активные доставки и финальные approval-записи.</p>
        <div className="row">{status?.protected_sources.map((source) => <StatusPill key={source} tone="accent">{source}</StatusPill>)}</div>
      </Surface>
      <Surface title="Безопасная очистка">
        <p className="muted">Сначала рассчитывается неизменяемый предпросмотр. Очистка удаляет только просроченные сессии, прочитанные оповещения с доставкой, старые операционные события и попытки успешно доставленных webhook. Не используйте её для расследования активного инцидента.</p>
        <button className="btn" type="button" disabled={busy !== null} onClick={() => void createPreview()}>{busy === "preview" ? "Расчёт…" : "Рассчитать предпросмотр"}</button>
        {preview && <div className="stack"><div className="table-wrap"><table className="table"><thead><tr><th>Источник</th><th>Будет удалено</th></tr></thead><tbody>{Object.entries(preview.preview.sources).map(([source, count]) => <tr key={source}><td>{labels[source] || source}</td><td>{count}</td></tr>)}</tbody></table></div><label className="field">Для подтверждения введите PURGE EXPIRED DATA<input value={confirmation} onChange={(event) => setConfirmation(event.target.value.toUpperCase())} required /></label><button className="btn btn-danger" type="button" disabled={busy !== null || confirmation !== "PURGE EXPIRED DATA"} onClick={() => setConfirmOpen(true)}>Поставить очистку в очередь</button></div>}
      </Surface>
      <Surface title="История очисток">
        {!status || status.runs.length === 0 ? <EmptyState title="Очисток пока не было" hint="Предпросмотр не удаляет данные; очередь создаётся только после точного подтверждения." /> : <div className="table-wrap"><table className="table"><thead><tr><th>Когда</th><th>Статус</th><th>Результат</th><th>Ошибка</th></tr></thead><tbody>{status.runs.map((run) => <tr key={run.id}><td className="muted">{run.created_at?.slice(0, 19) || "—"}</td><td><StatusPill tone={tone(run.status)}>{statusLabels[run.status]}</StatusPill></td><td>{Object.entries(run.result).map(([source, count]) => `${labels[source] || source}: ${count}`).join(" · ") || "—"}</td><td className="muted">{run.error_code ? errorLabels[run.error_code] || "Ошибка обработки" : "—"}</td></tr>)}</tbody></table></div>}
      </Surface>
      <ConfirmDialog
        open={confirmOpen}
        title="Поставить удаление просроченных данных в очередь?"
        description="Worker повторно проверит политику и состояние записей перед удалением. Активные доставки, открытые инциденты, audit ledger и evidence будут исключены."
        confirmLabel="Поставить в очередь"
        dangerous
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void queueRun()}
      />
    </div>
  );
}
