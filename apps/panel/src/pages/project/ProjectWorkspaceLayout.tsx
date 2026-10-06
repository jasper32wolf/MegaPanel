import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";

const sections = [
  ["overview", "Обзор"],
  ["design", "Дизайн"],
  ["facts", "Данные бизнеса"],
  ["pages", "Страницы"],
  ["releases", "Сборки и публикация"],
  ["routing", "Получение заявок"],
  ["site-structure", "Структура"],
  ["semantic-coverage", "Покрытие запросов"],
  ["city-projects", "Города"],
  ["activity", "История действий"],
] as const;

export function ProjectWorkspaceLayout({ projectId, children }: { projectId: string; children: ReactNode }) {
  return (
    <div className="project-workspace">
      <nav className="row" aria-label="Project workspace sections">
        {sections.map(([slug, label]) => (
          <NavLink
            key={slug}
            data-testid={`project-nav-${slug}`}
            className={({ isActive }) => `btn btn-ghost${isActive ? " is-active" : ""}`}
            to={`/projects/${projectId}/${slug}`}
          >
            {label}
          </NavLink>
        ))}
        <Link className="btn btn-ghost" to={`/projects/${projectId}#bukvarix`}>Букварикс</Link>
        <Link className="btn btn-ghost" to="/projects">К проектам</Link>
      </nav>
      {children}
    </div>
  );
}
