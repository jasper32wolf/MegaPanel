import { Link, useParams } from "react-router-dom";
import { PageHeader, Surface } from "../../components/ui";
import { ProjectWorkspaceLayout } from "./ProjectWorkspaceLayout";

const copy = {
  facts: {
    title: "Факты проекта",
    description: "Сведения о бизнесе и границы приватных данных остаются в существующем рабочем процессе.",
    hint: "Откройте рабочее пространство, чтобы изменить или подтвердить факты. Эта страница намеренно не отправляет повторный запрос данных проекта.",
  },
  pages: {
    title: "Страницы проекта",
    description: "Планы страниц, черновики, предложения ИИ и проверка качества остаются отдельными этапами до пробной сборки.",
    hint: "Откройте рабочее пространство для управления планами и черновиками. На этой странице действия не дублируются.",
  },
  releases: {
    title: "Выпуски проекта",
    description: "Пробные сборки, закрытый предпросмотр, юридическая проверка и публикация выполняются раздельно.",
    hint: "Откройте рабочее пространство, чтобы просмотреть выпуски или управлять ими. Для публикации по-прежнему требуются существующие подтверждения.",
  },
  routing: {
    title: "Маршрутизация заявок",
    description: "Зашифрованные данные получателей остаются в пределах существующей границы приватных данных.",
    hint: "Откройте рабочее пространство, чтобы создать, проверить или активировать правило маршрутизации.",
  },
} as const;

type Section = keyof typeof copy;

export function ProjectSectionPage({ section }: { section: Section }) {
  const { projectId = "" } = useParams();
  const content = copy[section];
  return (
    <ProjectWorkspaceLayout projectId={projectId}>
      <PageHeader title={content.title} description={content.description} />
      <Surface title="Рабочий процесс">
        <p className="muted">{content.hint}</p>
        <Link className="btn" to={`/projects/${projectId}`}>Открыть рабочее пространство</Link>
      </Surface>
    </ProjectWorkspaceLayout>
  );
}
