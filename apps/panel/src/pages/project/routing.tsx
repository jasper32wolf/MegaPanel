import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../../components/ui";
import { api, useAuth } from "../../lib/auth";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

type Project = { id: string; name: string };
type DeliveryMode = "append_global" | "replace_global";
type CentralDelivery = { configured: boolean; effective_mode: string };
type Destination = { id: string; target_key: string; channel: "email" | "webhook"; required: boolean; configured: boolean };
type Policy = {
  id: string;
  version: number;
  state: string;
  delivery_mode?: DeliveryMode;
  destinations: Destination[];
  submitted_at: string | null;
  reviewed_at: string | null;
  decision_reason: string | null;
  created_at: string | null;
};
type Policies = { items: Policy[]; central_delivery?: CentralDelivery };

function tone(state: string) {
  if (state === "active") return "ok" as const;
  if (state === "rejected") return "danger" as const;
  return "warn" as const;
}

function stateLabel(state: string) {
  return ({ draft: "черновик", review: "на проверке", active: "активна", rejected: "отклонена" } as Record<string, string>)[state] || state;
}

function channelLabel(channel: Destination["channel"]) {
  return channel === "email" ? "электронная почта" : "вебхук";
}

function deliveryModeLabel(mode: DeliveryMode | undefined) {
  if (!mode) return "режим не указан (историческое правило)";
  return mode === "replace_global"
    ? "заменяет центральную SMTP-доставку"
    : "добавляет к центральной SMTP-доставке";
}

function centralDeliveryModeLabel(mode: string) {
  if (mode === "append_global" || mode === "replace_global") return deliveryModeLabel(mode);
  return mode;
}

export function ProjectRoutingPage() {
  const { projectId = "" } = useParams();
  const { token } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [policies, setPolicies] = useState<Policy[] | null>(null);
  const [centralDelivery, setCentralDelivery] = useState<CentralDelivery | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId) return;
    Promise.all([
      api<Project>(`/api/v1/projects/${projectId}`, {}, token),
      api<Policies>(`/api/v1/projects/${projectId}/lead-routing`, {}, token),
    ]).then(([nextProject, nextPolicies]) => {
      setProject(nextProject);
      setPolicies(nextPolicies.items);
      setCentralDelivery(nextPolicies.central_delivery || null);
    }).catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить правила маршрутизации заявок"));
  }, [projectId, token]);

  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader
        title={project ? `Маршрутизация заявок · ${project.name}` : "Маршрутизация заявок"}
        description="История правил доступна только для просмотра. Адреса получателей, URL вебхуков и секреты не возвращаются и не показываются; изменение правила выполняется только явным действием в рабочем пространстве."
        actions={<Link className="btn btn-ghost" to={`/projects/${projectId}`}>Открыть рабочее пространство</Link>}
      />
      {error && <p className="error" role="alert">{error}</p>}
      {!policies && !error ? <p className="muted" aria-live="polite">Загрузка правил маршрутизации…</p> : null}
      {policies && <Surface title="Маршрутизация заявок">
        <div className="row"><StatusPill tone={centralDelivery?.configured ? "ok" : centralDelivery ? "danger" : "accent"}>{centralDelivery?.configured ? "Центральная SMTP-доставка готова" : centralDelivery ? "Центральная SMTP-доставка не настроена" : "Статус центральной SMTP-доставки неизвестен"}</StatusPill><span className="muted">{centralDelivery?.configured ? `Получатель не отображается. Активный режим: ${centralDeliveryModeLabel(centralDelivery.effective_mode)}.` : centralDelivery ? "Центральная SMTP-доставка недоступна для новых правил." : "API пока не предоставил статус центральной SMTP-доставки."}</span></div>
        {policies.length === 0 ? <EmptyState title="Дополнительных правил маршрутизации пока нет" hint="Новые заявки уже используют центральную SMTP-доставку. Создайте правило только для дополнительного или заменяющего маршрута." /> : <DataTable headers={["Версия", "Доставка", "Статус", "Пользовательские маршруты", "Проверка", "Создано"]}>{policies.map((policy) => <tr key={policy.id}><td>v{policy.version}</td><td>{deliveryModeLabel(policy.delivery_mode)}</td><td><StatusPill tone={tone(policy.state)}>{stateLabel(policy.state)}</StatusPill></td><td>{policy.destinations.map((destination) => <div className="row" key={destination.id}><StatusPill tone={destination.configured ? "ok" : "danger"}>{channelLabel(destination.channel)}</StatusPill><span>{destination.target_key} · {destination.required ? "обязательный" : "необязательный"}</span></div>)}</td><td>{policy.decision_reason || (policy.reviewed_at ? new Date(policy.reviewed_at).toLocaleString() : "—")}</td><td>{policy.created_at ? new Date(policy.created_at).toLocaleString() : "—"}</td></tr>)}</DataTable>}
      </Surface>}
    </ProjectWorkspaceLayout>
  );
}
