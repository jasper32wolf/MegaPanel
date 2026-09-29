import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../lib/auth";

const groups = [
  {
    label: "Работа",
    links: [
      { to: "/", end: true, label: "Обзор" },
      { to: "/projects", label: "Проекты" },
      { to: "/sites", label: "Сайты" },
      { to: "/keywords", label: "Семантика" },
      { to: "/competitors", label: "Конкуренты" },
      { to: "/geo", label: "География" },
      { to: "/projects", label: "Новый проект" },
      { to: "/leads", label: "Лиды" },
    ],
  },
  {
    label: "AI",
    links: [
      { to: "/ai", label: "AI workspace" },
      { to: "/ai/providers", label: "Провайдеры" },
      { to: "/ai/prompts", label: "Системные prompts" },
    ],
  },
  {
    label: "Публикация",
    links: [
      { to: "/domains", label: "Домены" },
      { to: "/blocks", label: "Блоки" },
      { to: "/media", label: "Медиатека" },
      { to: "/bulk", label: "Контакты" },
    ],
  },
  {
    label: "Система",
    links: [
      { to: "/ops", label: "Статус" },
      { to: "/system", label: "Обновления" },
      { to: "/settings", label: "Настройки" },
      { to: "/audit", label: "Audit" },
      { to: "/help", label: "Справка" },
    ],
  },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="shell">
      <aside className="nav">
        <div className="nav-brand">
          <img className="nav-mark" src="/site-panel-mark.svg" width="40" height="40" alt="" />
          <div>
            <h1>Site Panel</h1>
            <small>Programmatic SEO</small>
          </div>
        </div>
        {groups.map((g) => (
          <div className="nav-group" key={g.label}>
            <div className="nav-group-label">{g.label}</div>
            {g.links.map((l) => (
              <NavLink key={l.to} to={l.to} end={"end" in l ? l.end : false}>
                {l.label}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav-footer">
          <button className="btn btn-nav" type="button" onClick={logout}>
            Выйти
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
