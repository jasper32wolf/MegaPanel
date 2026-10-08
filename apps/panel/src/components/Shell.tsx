import { NavLink, useLocation } from "react-router-dom";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, useAuth } from "../lib/auth";

const groups = [
  {
    label: "Главная и проекты",
    links: [
      { to: "/", end: true, label: "Главная" },
      { to: "/projects", label: "Проекты" },
      { to: "/sites", label: "Реестр сайтов" },
    ],
  },
  {
    label: "Заявки",
    links: [{ to: "/leads", label: "Заявки" }],
  },
  {
    label: "Материалы для сайтов",
    links: [
      { to: "/keywords", label: "Запросы и семантика" },
      { to: "/geo", label: "География" },
      { to: "/competitors", label: "Исследование конкурентов" },
      { to: "/domains", label: "Домены" },
      { to: "/blocks", label: "Блоки сайта" },
      { to: "/media", label: "Медиатека" },
      { to: "/bulk", label: "Массовое обновление контактов" },
    ],
  },
  {
    label: "Подготовка с AI",
    links: [
      { to: "/ai", label: "AI-предложения" },
      { to: "/ai/providers", label: "Провайдеры AI" },
      { to: "/ai/prompts", label: "Шаблоны запросов AI" },
    ],
  },
  {
    label: "Система",
    links: [
      { to: "/ops", label: "Статус системы" },
      { to: "/alerts", label: "Оповещения" },
      { to: "/system", label: "Обновления и восстановление" },
      { to: "/audit", label: "Журнал аудита" },
      { to: "/settings", label: "Настройки" },
    ],
  },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  const location = useLocation();
  const [unreadAlerts, setUnreadAlerts] = useState(0);
  const mainRef = useRef<HTMLElement>(null);
  const previousRoute = useRef(`${location.pathname}${location.search}`);

  useEffect(() => {
    const nextRoute = `${location.pathname}${location.search}`;
    if (nextRoute === previousRoute.current) return;
    previousRoute.current = nextRoute;
    if (location.hash) return;
    mainRef.current?.focus();
  }, [location.hash, location.pathname, location.search]);

  useEffect(() => {
    let active = true;
    const loadUnread = () => api<{ unread: number }>("/api/v1/panel/alerts?limit=1")
      .then((summary) => { if (active) setUnreadAlerts(summary.unread); })
      .catch(() => { if (active) setUnreadAlerts(0); });
    void loadUnread();
    const timer = window.setInterval(() => void loadUnread(), 30_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Перейти к основному содержимому</a>
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
                {l.to === "/alerts" && unreadAlerts > 0 ? `${l.label} (${unreadAlerts})` : l.label}
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav-footer">
          <NavLink to="/help">Помощь оператору</NavLink>
          <button className="btn btn-nav" type="button" onClick={logout}>
            Выйти
          </button>
        </div>
      </aside>
      <main ref={mainRef} id="main-content" className="main" tabIndex={-1}>{children}</main>
    </div>
  );
}
