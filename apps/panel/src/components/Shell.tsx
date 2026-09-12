import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../lib/auth";

const groups = [
  {
    label: "Работа",
    links: [
      { to: "/", end: true, label: "Обзор" },
      { to: "/sites", label: "Сайты" },
      { to: "/onboarding", label: "Онбординг" },
      { to: "/leads", label: "Лиды" },
    ],
  },
  {
    label: "Публикация",
    links: [
      { to: "/domains", label: "Домены" },
      { to: "/blocks", label: "Блоки" },
    ],
  },
  {
    label: "Система",
    links: [
      { to: "/ops", label: "Ops" },
      { to: "/tools", label: "Инструменты" },
    ],
  },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="shell">
      <aside className="nav">
        <div className="nav-brand">
          <div className="nav-mark" aria-hidden />
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
