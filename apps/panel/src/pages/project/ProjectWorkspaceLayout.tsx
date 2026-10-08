import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

const sectionGroups = [
  {
    label: "Рабочий процесс",
    links: [["workflow", "Рабочий процесс"], ["overview", "План развёртывания"]],
  },
  {
    label: "Подготовка",
    links: [["facts", "Данные бизнеса"], ["routing", "Получение заявок"], ["city-projects", "Города"]],
  },
  {
    label: "Структура и контент",
    links: [["site-structure", "Структура сайта"], ["pages", "План и качество страниц"], ["semantic-coverage", "Покрытие запросов"], ["design", "Дизайн"]],
  },
  {
    label: "Сборка и выпуск",
    links: [["releases", "Пробные сборки и выпуск"]],
  },
  {
    label: "История",
    links: [["activity", "История проекта"]],
  },
] as const;

export function ProjectWorkspaceLayout({ projectId, children }: { projectId: string; children: ReactNode }) {
  return (
    <div className="project-workspace">
      <nav className="project-nav" aria-label="Разделы проекта">
        {sectionGroups.map((group) => (
          <div className="project-nav-group" key={group.label}>
            <span className="project-nav-label">{group.label}</span>
            {group.links.map(([slug, label]) => {
              const to = slug === "workflow" ? `/projects/${projectId}` : `/projects/${projectId}/${slug}`;
              return <NavLink
                key={slug}
                data-testid={`project-nav-${slug}`}
                end={slug === "workflow"}
                className={({ isActive }) => `btn btn-ghost${isActive ? " is-active" : ""}`}
                to={to}
              >
                {label}
              </NavLink>;
            })}
          </div>
        ))}
        <div className="project-nav-group">
          <span className="project-nav-label">Исследование</span>
          <Link className="btn btn-ghost" to={`/projects/${projectId}#bukvarix`}>Запросы из Букварикса</Link>
          <Link className="btn btn-ghost" to={`/help?topic=project-workflow&project=${projectId}`}>Справка по проекту</Link>
        </div>
        <Link className="btn btn-ghost" to="/projects">К проектам</Link>
      </nav>
      {children}
    </div>
  );
}
