import { useEffect, useState, type FormEvent } from "react";
import { EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";
import { api, useAuth } from "../lib/auth";

type Control = { configured: boolean; repository: string | null; message: string };
type Release = { sha: string; updated_at: string | null; workflow_url: string | null };
type Releases = { configured: boolean; releases: Release[] };
type Operation = {
  id: string;
  kind: "update" | "recovery";
  action: string;
  release_sha: string | null;
  snapshot_id: string | null;
  request_id: string;
  workflow: string;
  workflow_run_id: number | null;
  workflow_url: string | null;
  status: "requested" | "queued" | "in_progress" | "success" | "failure" | "cancelled" | "unknown";
  error_code: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
};

type RecoveryAction = "status" | "restart" | "rollback" | "recover" | "restore";

const actionLabels: Record<RecoveryAction, string> = {
  status: "Получить статус",
  restart: "Перезапустить сервисы",
  rollback: "Откатить код",
  recover: "Запустить ограниченное восстановление",
  restore: "Восстановить данные из backup",
};

function operationTone(status: Operation["status"]): "ok" | "warn" | "danger" | "accent" {
  if (status === "success") return "ok";
  if (status === "failure" || status === "cancelled") return "danger";
  if (status === "unknown") return "warn";
  return "accent";
}

function operationTarget(operation: Operation): string {
  return operation.release_sha || operation.snapshot_id || "—";
}

export function SystemOperationsPage() {
  const { token } = useAuth();
  const [control, setControl] = useState<Control | null>(null);
  const [releases, setReleases] = useState<Release[]>([]);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [releaseSha, setReleaseSha] = useState("");
  const [updateConfirmation, setUpdateConfirmation] = useState("");
  const [recoveryAction, setRecoveryAction] = useState<RecoveryAction>("status");
  const [snapshotId, setSnapshotId] = useState("");
  const [recoveryConfirmation, setRecoveryConfirmation] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    const [controlData, releasesData, operationsData] = await Promise.all([
      api<Control>("/api/v1/system/control", {}, token),
      api<Releases>("/api/v1/system/updates/available", {}, token),
      api<Operation[]>("/api/v1/system/operations", {}, token),
    ]);
    setControl(controlData);
    setReleases(releasesData.releases);
    setOperations(operationsData);
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить системные операции"));
  }, [token]);

  useEffect(() => {
    if (!operations.some((operation) => ["requested", "queued", "in_progress"].includes(operation.status))) return;
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [operations, token]);

  async function requestUpdate(event: FormEvent) {
    event.preventDefault();
    setBusy("update");
    setError(null);
    setMessage(null);
    try {
      const operation = await api<Operation>(
        "/api/v1/system/updates",
        { method: "POST", body: JSON.stringify({ release_sha: releaseSha, confirmation: updateConfirmation }) },
        token,
      );
      setMessage(`Обновление ${operation.release_sha} поставлено в очередь GitHub Actions.`);
      setUpdateConfirmation("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось запросить обновление");
    } finally {
      setBusy(null);
    }
  }

  async function requestRecovery(event: FormEvent) {
    event.preventDefault();
    setBusy(`recovery:${recoveryAction}`);
    setError(null);
    setMessage(null);
    try {
      const operation = await api<Operation>(
        "/api/v1/system/recovery",
        {
          method: "POST",
          body: JSON.stringify({
            action: recoveryAction,
            snapshot_id: recoveryAction === "restore" ? snapshotId : null,
            confirmation: recoveryConfirmation,
          }),
        },
        token,
      );
      setMessage(`${actionLabels[recoveryAction]} поставлено в очередь GitHub Actions.`);
      setRecoveryConfirmation("");
      await load();
      if (operation.status === "failure") setError(operation.error_code || "GitHub отклонил запрос");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось запросить восстановление");
    } finally {
      setBusy(null);
    }
  }

  const expectedRecoveryConfirmation = recoveryAction === "restore" ? "RESTORE" : recoveryAction.toUpperCase();

  return (
    <div aria-busy={busy !== null}>
      <PageHeader
        title="Обновления и восстановление"
        description="Панель создаёт только ограниченные запросы в GitHub Actions. Она не получает Docker-доступ, root shell, SSH private keys или backup credentials."
      />
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="success" aria-live="polite">{message}</p>}

      <Surface title="GitHub control">
        <div className="row">
          <StatusPill tone={control?.configured ? "ok" : "warn"}>
            {control?.configured ? "настроен" : "не настроен"}
          </StatusPill>
          {control?.repository && <span className="muted">{control.repository}</span>}
        </div>
        <p className="muted">{control?.message || "Проверка конфигурации…"}</p>
        {!control?.configured && <p className="muted">Обновления из панели недоступны, пока на VPS не заданы GitHub repository и least-privilege control token. Ручные GitHub Actions остаются рабочим резервным путём.</p>}
      </Surface>

      <Surface title="Проверенные обновления">
        <p className="muted">Можно выбрать только SHA с успешным CI в ветке main. GitHub Environment может запросить отдельное approval перед deployment.</p>
        {releases.length === 0 ? (
          <EmptyState title="Проверенных SHA пока нет" hint="Проверьте GitHub control или запустите CI для commit в main." />
        ) : (
          <div className="table-wrap"><table className="table"><thead><tr><th>SHA</th><th>CI завершён</th><th></th></tr></thead><tbody>{releases.map((release) => <tr key={release.sha}><td><code>{release.sha}</code></td><td className="muted">{release.updated_at?.slice(0, 19) || "—"}</td><td><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setReleaseSha(release.sha)}>Выбрать</button></td></tr>)}</tbody></table></div>
        )}
        <form className="stack" onSubmit={requestUpdate}>
          <label className="field">SHA release<input value={releaseSha} onChange={(event) => setReleaseSha(event.target.value.trim().toLowerCase())} pattern="[0-9a-f]{40}([0-9a-f]{24})?" required /></label>
          <label className="field">Для подтверждения введите DEPLOY<input value={updateConfirmation} onChange={(event) => setUpdateConfirmation(event.target.value.toUpperCase())} required /></label>
          <button className="btn" type="submit" disabled={!control?.configured || busy !== null || updateConfirmation !== "DEPLOY"}>Запросить обновление</button>
        </form>
      </Surface>

      <Surface title="Ограниченное восстановление">
        <p className="muted">Restart и rollback меняют только runtime или code release. Автоматическое восстановление базы данных запрещено. Restore заменяет данные выбранным snapshot и требует отдельного подтверждения.</p>
        <form className="stack" onSubmit={requestRecovery}>
          <label className="field">Действие<select value={recoveryAction} onChange={(event) => { setRecoveryAction(event.target.value as RecoveryAction); setRecoveryConfirmation(""); }}><option value="status">Получить status</option><option value="restart">Перезапустить API, worker, panel и Caddy</option><option value="rollback">Откатить code release</option><option value="recover">Restart → code rollback при необходимости</option><option value="restore">RESTORE: заменить данные из restic snapshot</option></select></label>
          {recoveryAction === "restore" && <label className="field">Restic snapshot ID<input value={snapshotId} onChange={(event) => setSnapshotId(event.target.value.trim().toLowerCase())} pattern="[0-9a-f]{8,64}" required /></label>}
          <label className="field">Для подтверждения введите {expectedRecoveryConfirmation}<input value={recoveryConfirmation} onChange={(event) => setRecoveryConfirmation(event.target.value.toUpperCase())} required /></label>
          {recoveryAction === "restore" && <p className="error" role="alert">Restore заменит PostgreSQL и durable volumes. Перед ним workflow обязан создать новый encrypted backup; если это не удастся, restore не начнётся.</p>}
          <button className="btn" type="submit" disabled={!control?.configured || busy !== null || recoveryConfirmation !== expectedRecoveryConfirmation}>{actionLabels[recoveryAction]}</button>
        </form>
      </Surface>

      <Surface title="История операций">
        {operations.length === 0 ? <EmptyState title="Операций пока нет" hint="Здесь появятся запросы обновления и recovery из панели." /> : <div className="table-wrap"><table className="table"><thead><tr><th>Когда</th><th>Действие</th><th>Версия / snapshot</th><th>Статус</th><th>GitHub</th></tr></thead><tbody>{operations.map((operation) => <tr key={operation.id}><td className="muted">{operation.created_at?.slice(0, 19) || "—"}</td><td>{operation.kind} / {operation.action}</td><td><code>{operationTarget(operation)}</code>{operation.error_code && <p className="error">{operation.error_code}</p>}</td><td><StatusPill tone={operationTone(operation.status)}>{operation.status}</StatusPill></td><td>{operation.workflow_url ? <a href={operation.workflow_url} target="_blank" rel="noreferrer">Открыть run</a> : <span className="muted">ожидание run</span>}</td></tr>)}</tbody></table></div>}
      </Surface>
    </div>
  );
}
