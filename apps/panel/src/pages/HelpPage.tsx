import { Link } from "react-router-dom";
import { PageHeader, Surface } from "../components/ui";

const sections = [
  {
    title: "Рабочий процесс сайта",
    body: "Проект начинается с подтверждённых facts, затем оператор выбирает семантику и проверенную географию, создаёт и одобряет PagePlan, проверяет PageDraft, применяет его к manifest, получает candidate preview и только после этого вручную публикует готовую сборку.",
    link: "/projects",
    label: "Открыть проекты",
  },
  {
    title: "Состояние системы и alerts",
    body: "«Обзор» показывает текущие DB-backed показатели и server-generated alerts. Readiness означает доступность PostgreSQL и Redis, но не доказывает внешний TLS, Caddy hostname, provider delivery или restore drill.",
    link: "/",
    label: "Открыть обзор",
  },
  {
    title: "Семантика, конкуренты и география",
    body: "CSV-семантика и вручную заданные competitor URL — evidence для review, а не команда на генерацию. Панель не выполняет поиск конкурентов, не читает sitemap и не расширяет список URL. География строится как город → район/метро → ориентир и требует ручной проверки.",
    link: "/competitors",
    label: "Открыть исследования",
  },
  {
    title: "AI, prompts и внешняя обработка",
    body: "Перед каждым provider request нужен quote, подтверждение внешней обработки и бюджета. AI output остаётся proposal, проходит server validation и manual approval. Operator prompt revisions уточняют задачу, но не отменяют privacy policy, schema, budget, QA или publish gate.",
    link: "/ai/prompts",
    label: "Открыть prompts",
  },
  {
    title: "Лиды и private email",
    body: "Публичные телефон и адрес могут рендериться по шаблону. Email для заявок хранится отдельно от public facts, не попадает в SSG preview или AI context. Lead PII encrypted at rest; webhook delivery имеет очередь, retry и dead letter.",
    link: "/leads",
    label: "Открыть inbox лидов",
  },
  {
    title: "Сессии и безопасность",
    body: "В настройках отображаются все активные и не более десяти недавних завершённых сессий. Полная история открывается отдельно; записи не удаляются ради очистки интерфейса. Отзыв другой сессии не отменяет уже выданный access token мгновенно.",
    link: "/settings",
    label: "Открыть настройки",
  },
];

export function HelpPage() {
  return (
    <div>
      <PageHeader title="Справка оператора" description="Практические границы и последствия действий в single-operator workflow. Этот раздел описывает только реализованные проверки и не заменяет runbooks для VPS, TLS и restore drill." />
      {sections.map((section) => (
        <Surface key={section.title} title={section.title}>
          <p>{section.body}</p>
          <Link className="btn btn-ghost" to={section.link}>{section.label}</Link>
        </Surface>
      ))}
    </div>
  );
}
