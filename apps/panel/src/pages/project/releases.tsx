import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ConfirmDialog,
  DataTable,
  EmptyState,
  InlineAlert,
  PageHeader,
  StatusPill,
  Surface,
} from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = {
  id: string;
  name: string;
  domain: string | null;
  site_id: string | null;
  domain_check_meta: { dns_status?: string };
};
type BuildEvent = {
  sequence: number;
  attempt: number;
  type: string;
  code: string | null;
  details: Record<string, unknown>;
  created_at: string | null;
};
type Build = {
  id: string;
  status: "queued" | "running" | "ready" | "failed";
  build_hash: string | null;
  previous_build_hash: string | null;
  pages_built: number;
  duration_ms: number;
  attempt_count: number;
  failure_code: string | null;
  input_snapshot_hash: string | null;
  snapshot_version: number;
  queue_priority: number;
  not_before: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  activated_at: string | null;
  first_published_at: string | null;
  is_active: boolean;
  is_historical_published: boolean;
  retryable: boolean;
  rollback_eligible: boolean;
  events: BuildEvent[];
  release_gate: { status: string; blockers: string[]; warnings: string[] } | null;
  legal_review: {
    status: "pass" | "block";
    blockers: string[];
    review: {
      state: string;
      evidence_ref: string | null;
      reason: string | null;
      replacement_guidance: string | null;
      reviewed_at: string | null;
    };
  };
  index_promotion_provenance: { slug: string; reason: string; decided_at: string }[];
};
type ScheduledWork = {
  id: string;
  work_type: string;
  source_id: string;
  state: string;
  priority: number;
  not_before: string | null;
  eligible_at: string | null;
  attempt_count: number;
  lease_expires_at: string | null;
  failure_code: string | null;
};
type Confirmation = {
  title: string;
  description: string;
  confirmLabel: string;
  inputLabel?: string;
  inputMinLength?: number;
  requiredValue?: string;
  dangerous?: boolean;
  onConfirm: (value: string) => void;
};

function tone(state: string) {
  if (["ready", "pass", "approved"].includes(state)) return "ok" as const;
  if (["failed", "block", "rejected"].includes(state)) return "danger" as const;
  if (["queued", "running", "pending"].includes(state)) return "warn" as const;
  return "accent" as const;
}

function stamp(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—";
}

export function ProjectReleasesPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [builds, setBuilds] = useState<Build[] | null>(null);
  const [scheduledWork, setScheduledWork] = useState<ScheduledWork[]>([]);
  const [selectedBuildId, setSelectedBuildId] = useState<string | null>(null);
  const [evidenceRef, setEvidenceRef] = useState("");
  const [rejectionReason, setRejectionReason] = useState("");
  const [rejectionGuidance, setRejectionGuidance] = useState("");
  const [queuePriority, setQueuePriority] = useState("50");
  const [notBefore, setNotBefore] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);

  const selectedBuild = builds?.find((build) => build.id === selectedBuildId) || builds?.[0] || null;

  async function load() {
    if (!projectId) return;
    const [nextProject, nextBuilds, nextScheduledWork] = await Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Build[]>(`/api/v1/projects/${projectId}/builds`, {}, token),
      api<ScheduledWork[]>(`/api/v1/projects/${projectId}/scheduled-work`, {}, token),
    ]);
    setProject(nextProject);
    setBuilds(nextBuilds);
    setScheduledWork(nextScheduledWork);
    setSelectedBuildId((current) =>
      current && nextBuilds.some((build) => build.id === current) ? current : nextBuilds[0]?.id || null,
    );
  }

  useEffect(() => {
    void load().catch((cause) => {
      setError(cause instanceof Error ? cause.message : "Не удалось загрузить candidate-сборки");
    });
  }, [projectId, token]);

  useEffect(() => {
    if (!builds?.some((build) => ["queued", "running"].includes(build.status))) return;
    const timer = window.setInterval(() => {
      void load().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [builds, projectId, token]);

  async function run(action: string, request: () => Promise<unknown>, success: string) {
    setBusy(action);
    setError(null);
    setMessage(null);
    try {
      await request();
      setMessage(success);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция не выполнена");
    } finally {
      setBusy(null);
    }
  }

  async function createCandidate() {
    await run(
      "build",
      () => api(
        `/api/v1/projects/${projectId}/builds`,
        {
          method: "POST",
          body: JSON.stringify({
            queue_priority: Number(queuePriority),
            not_before: notBefore ? new Date(notBefore).toISOString() : null,
          }),
        },
        token,
      ),
      "Снимок зафиксирован. Очередь начнёт сборку в указанное время и не публикует сайт автоматически.",
    );
  }

  async function retry(build: Build) {
    await run(
      `retry:${build.id}`,
      () => api(`/api/v1/projects/${projectId}/builds/${build.id}/retry`, { method: "POST" }, token),
      "Повтор поставлен в очередь и использует тот же зафиксированный snapshot.",
    );
  }

  async function controlScheduledWork(job: ScheduledWork, action: "pause" | "resume" | "cancel") {
    await run(
      `scheduled-work:${action}:${job.id}`,
      () => api(`/api/v1/projects/${projectId}/scheduled-work/${job.id}/${action}`, { method: "POST" }, token),
      action === "pause"
        ? "Сборка поставлена на паузу. Снимок и публикация не изменялись."
        : action === "resume"
          ? "Сборка возвращена в очередь. Проверки и публикация остаются отдельными шагами."
          : "Запуск сборки отменён. Снимок и опубликованный сайт не изменялись.",
    );
  }

  async function checkDomain() {
    await run(
      "domain-check",
      () => api(`/api/v1/projects/${projectId}/domain/check`, { method: "POST" }, token),
      "DNS-проверка сохранена. TLS проверяется только после явной активации Caddy-vhost.",
    );
  }

  async function reviewLegal(build: Build, decision: "approved" | "rejected") {
    if (!evidenceRef.trim()) {
      setError("Укажите ссылку или внутренний идентификатор evidence.");
      return;
    }
    if (decision === "rejected" && (!rejectionReason.trim() || !rejectionGuidance.trim())) {
      setError("Для отклонения укажите причину и рекомендацию по исправлению.");
      return;
    }
    await run(
      `legal:${build.id}`,
      () =>
        api(
          `/api/v1/projects/${projectId}/builds/${build.id}/legal-review`,
          {
            method: "POST",
            body: JSON.stringify({
              decision,
              evidence_ref: evidenceRef.trim(),
              reason: decision === "rejected" ? rejectionReason.trim() : undefined,
              replacement_guidance: decision === "rejected" ? rejectionGuidance.trim() : undefined,
            }),
          },
          token,
        ),
      decision === "approved"
        ? "Legal review сохранён для immutable candidate. Проверьте private preview перед публикацией."
        : "Отклонение сохранено. Candidate и historical releases не изменялись.",
    );
  }

  function publish(build: Build) {
    if (!build.build_hash) return;
    setConfirmation({
      title: "Опубликовать candidate-сборку?",
      description: `Сборка ${build.build_hash} станет публичной для домена проекта. Это единственный путь публикации; завершение queue-задачи не публикует сайт.`,
      confirmLabel: "Опубликовать",
      dangerous: true,
      onConfirm: () => {
        void run(
          `publish:${build.id}`,
          () =>
            api(
              `/api/v1/projects/${projectId}/builds/${build.id}/publish`,
              { method: "POST", body: JSON.stringify({ confirmed: true }) },
              token,
            ),
          "Сборка опубликована после явного подтверждения.",
        );
      },
    });
  }

  function rollback(build: Build) {
    if (!build.build_hash) return;
    const requiredValue = `ROLLBACK ${build.build_hash}`;
    setConfirmation({
      title: "Откатить публичный сайт на historical release?",
      description: `Текущий release будет заменён hash ${build.build_hash}. Введите точную фразу ниже; сервер независимо проверит цель, DNS, gate, legal review и routing.`,
      confirmLabel: "Выполнить откат",
      inputLabel: `Введите: ${requiredValue}`,
      inputMinLength: requiredValue.length,
      requiredValue,
      dangerous: true,
      onConfirm: (confirmationText) => {
        void run(
          `rollback:${build.id}`,
          () =>
            api(
              `/api/v1/projects/${projectId}/rollbacks`,
              {
                method: "POST",
                body: JSON.stringify({ build_hash: build.build_hash, confirmation_text: confirmationText }),
              },
              token,
            ),
          "Откат выполнен после успешной активации и проверки Caddy.",
        );
      },
    });
  }

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Candidate-сборки · ${project.name}` : "Candidate-сборки"}
        description="Очередь строит сайт из зафиксированного snapshot. Private preview, legal review, публикация и откат — независимые ручные границы."
        actions={
          <>
            <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void load()}>
              Обновить
            </button>
            <Link className="btn btn-ghost" to={`/projects/${projectId}`}>
              Открыть workspace
            </Link>
          </>
        }
      />
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="muted" aria-live="polite">{message}</p> : null}
      {!builds && !error ? <p className="muted" aria-live="polite">Загрузка candidate-сборок…</p> : null}

      <Surface title="Новый candidate">
        <p className="muted">
          Перед постановкой сохраняется неизменяемый снимок контента, индексации и файлов. Сборка не публикует сайт и не запускает IndexNow.
        </p>
        <div className="detail-grid">
          <label className="field">Важность сборки<select value={queuePriority} onChange={(event) => setQueuePriority(event.target.value)} disabled={busy !== null}><option value="80">Срочно</option><option value="50">Обычно</option><option value="20">Можно ночью</option></select><span className="muted">Влияет только на порядок работы очереди. Проверки, preview и публикация остаются отдельными шагами.</span></label>
          <label className="field">Начать не раньше<input type="datetime-local" value={notBefore} onChange={(event) => setNotBefore(event.target.value)} disabled={busy !== null} /><span className="muted">Необязательно. Время указано для вашего компьютера.</span></label>
        </div>
        <div className="row">
          <button className="btn btn-ghost" type="button" disabled={busy !== null || !project?.domain} onClick={() => void checkDomain()}>
            {busy === "domain-check" ? "Проверка DNS…" : "Проверить DNS"}
          </button>
          <StatusPill tone={project?.domain_check_meta.dns_status === "ok" ? "ok" : "warn"}>
            DNS: {project?.domain_check_meta.dns_status || "не проверен"}
          </StatusPill>
          <button className="btn" type="button" disabled={busy !== null || !project?.site_id} onClick={() => void createCandidate()}>
            {busy === "build" ? "Постановка…" : "Создать candidate-сборку"}
          </button>
        </div>
      </Surface>

      <Surface title="Очередь сборки">
        <p className="muted">Очередь распределяет только запуск immutable candidate-сборок. Пауза, продолжение и отмена не применяют черновик, не меняют индексацию и не публикуют сайт.</p>
        {scheduledWork.length === 0 ? <EmptyState title="В очереди нет сборок" hint="После создания candidate появится отдельная запись планировщика." /> : <DataTable headers={["Тип", "Состояние", "Порядок", "Попытки", "Действия"]}>{scheduledWork.map((job) => <tr key={job.id}><td>{job.work_type === "site_build" ? "Candidate-сборка" : job.work_type}</td><td><StatusPill tone={tone(job.state)}>{job.state}</StatusPill>{job.failure_code ? <p className="error">{job.failure_code}</p> : null}</td><td>важность: {job.priority}{job.not_before ? <p className="muted">не раньше: {stamp(job.not_before)}</p> : null}</td><td>{job.attempt_count}{job.lease_expires_at ? <p className="muted">lease до: {stamp(job.lease_expires_at)}</p> : null}</td><td className="row">{job.state === "queued" || job.state === "failed" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void controlScheduledWork(job, "pause")}>Пауза</button> : null}{job.state === "paused" ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void controlScheduledWork(job, "resume")}>Продолжить</button> : null}{["queued", "paused", "failed"].includes(job.state) ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void controlScheduledWork(job, "cancel")}>Отменить</button> : null}</td></tr>)}</DataTable>}
      </Surface>

      {builds ? (
        <Surface title="Очередь и история">
          {builds.length === 0 ? (
            <EmptyState title="Сборок пока нет" hint="После применения черновика создайте candidate-сборку." />
          ) : (
            <DataTable headers={["Статус", "Сборка", "Публикация", "Создана", "Действия"]}>
              {builds.map((build) => (
                <tr key={build.id} aria-selected={selectedBuild?.id === build.id}>
                  <td>
                    <StatusPill tone={tone(build.status)}>{build.status}</StatusPill>
                    {build.failure_code ? <p className="error">{build.failure_code}</p> : null}
                  </td>
                  <td>
                    <code>{build.build_hash?.slice(0, 16) || build.input_snapshot_hash?.slice(0, 16) || "—"}</code>
                    <p className="muted">Попытка: {build.attempt_count} · важность: {build.queue_priority}</p>
                    {build.not_before ? <p className="muted">Не раньше: {stamp(build.not_before)}</p> : null}
                  </td>
                  <td>
                    {build.is_active ? <StatusPill tone="ok">активна</StatusPill> : null}
                    {build.is_historical_published ? <StatusPill tone="accent">историческая</StatusPill> : null}
                    {!build.is_active && !build.is_historical_published ? <span className="muted">не опубликована</span> : null}
                  </td>
                  <td>{stamp(build.created_at)}</td>
                  <td className="row">
                    <button className="btn btn-ghost" type="button" onClick={() => setSelectedBuildId(build.id)}>
                      Детали
                    </button>
                    {build.retryable ? <button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => void retry(build)}>Повторить</button> : null}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </Surface>
      ) : null}

      {selectedBuild ? (
        <>
          <Surface title="Детали выбранной сборки">
            <div className="row">
              <StatusPill tone={tone(selectedBuild.status)}>{selectedBuild.status}</StatusPill>
              {selectedBuild.is_active ? <StatusPill tone="ok">active release</StatusPill> : null}
              <span className="muted">Создана: {stamp(selectedBuild.created_at)} · завершена: {stamp(selectedBuild.completed_at)} · {selectedBuild.duration_ms} мс</span>
            </div>
            <p><strong>Hash:</strong> <code>{selectedBuild.build_hash || "Ещё не создан"}</code></p>
            <p className="muted">Input snapshot: <code>{selectedBuild.input_snapshot_hash || "legacy"}</code>, версия {selectedBuild.snapshot_version}.</p>
            {selectedBuild.events.length ? (
              <details open>
                <summary>Безопасная история выполнения · {selectedBuild.events.length}</summary>
                <ul>
                  {selectedBuild.events.map((event) => (
                    <li key={event.sequence}>
                      <StatusPill tone={tone(event.type)}>{event.type}</StatusPill> попытка {event.attempt} · {stamp(event.created_at)}{event.code ? ` · ${event.code}` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            ) : <p className="muted">У legacy-сборки нет новой event history.</p>}
            {selectedBuild.release_gate ? (
              <>
                <p><StatusPill tone={tone(selectedBuild.release_gate.status)}>release gate: {selectedBuild.release_gate.status}</StatusPill></p>
                {selectedBuild.release_gate.blockers.map((blocker) => <p className="error" key={blocker}>{blocker}</p>)}
                {selectedBuild.release_gate.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}
              </>
            ) : null}
            {selectedBuild.index_promotion_provenance.length ? (
              <details>
                <summary>Подтверждений индексации: {selectedBuild.index_promotion_provenance.length}</summary>
                {selectedBuild.index_promotion_provenance.map((promotion) => (
                  <p className="muted" key={promotion.slug}>
                    <strong>{promotion.slug}</strong> · {promotion.reason} · {stamp(promotion.decided_at)}
                  </p>
                ))}
              </details>
            ) : null}
            {selectedBuild.build_hash && project?.site_id && selectedBuild.status === "ready" ? (
              <a className="btn btn-ghost" href={`/api/v1/projects/${projectId}/builds/${selectedBuild.id}/preview/`} target="_blank" rel="noreferrer">
                Открыть private preview
              </a>
            ) : null}
          </Surface>

          {selectedBuild.status === "ready" ? (
            <Surface title="Legal review и явная публикация">
              <p className="muted">Legal review привязан к immutable snapshot этой сборки. Очередь не выполняет этот этап самостоятельно.</p>
              <label className="field">Ссылка или внутренний идентификатор evidence<input value={evidenceRef} onChange={(event) => setEvidenceRef(event.target.value)} minLength={3} maxLength={255} /></label>
              {selectedBuild.legal_review.status === "block" ? (
                <div className="stack">
                  <div className="row">
                    <button className="btn btn-ghost" type="button" disabled={busy !== null || evidenceRef.trim().length < 3} onClick={() => void reviewLegal(selectedBuild, "approved")}>Одобрить legal review</button>
                  </div>
                  <label className="field">Причина отклонения<input value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} minLength={3} maxLength={2000} /></label>
                  <label className="field">Рекомендация по исправлению<input value={rejectionGuidance} onChange={(event) => setRejectionGuidance(event.target.value)} minLength={3} maxLength={2000} /></label>
                  <button className="btn btn-ghost" type="button" disabled={busy !== null || evidenceRef.trim().length < 3 || rejectionReason.trim().length < 3 || rejectionGuidance.trim().length < 3} onClick={() => void reviewLegal(selectedBuild, "rejected")}>Отклонить legal review</button>
                </div>
              ) : <StatusPill tone="ok">legal review: approved</StatusPill>}
              <button className="btn" type="button" disabled={busy !== null || selectedBuild.release_gate?.status === "block" || selectedBuild.legal_review.status === "block"} onClick={() => publish(selectedBuild)}>
                Опубликовать выбранную сборку
              </button>
            </Surface>
          ) : null}

          {selectedBuild.rollback_eligible ? (
            <Surface title="Безопасный rollback">
              <InlineAlert severity="warning" title="Публичная версия будет заменена">
                Выбран historical published release. Откат не доступен для never-published candidate и никогда не выбирает цель автоматически.
              </InlineAlert>
              <button className="btn btn-danger" type="button" disabled={busy !== null} onClick={() => rollback(selectedBuild)}>
                Откатить на выбранный hash
              </button>
            </Surface>
          ) : null}
        </>
      ) : null}

      <ConfirmDialog
        open={confirmation !== null}
        title={confirmation?.title || "Подтверждение"}
        description={confirmation?.description || ""}
        confirmLabel={confirmation?.confirmLabel || "Подтвердить"}
        inputLabel={confirmation?.inputLabel}
        inputMinLength={confirmation?.inputMinLength}
        requiredValue={confirmation?.requiredValue}
        dangerous={confirmation?.dangerous}
        onCancel={() => setConfirmation(null)}
        onConfirm={(value) => {
          const current = confirmation;
          setConfirmation(null);
          current?.onConfirm(value);
        }}
      />
    </ProjectWorkspaceLayout>
  );
}
