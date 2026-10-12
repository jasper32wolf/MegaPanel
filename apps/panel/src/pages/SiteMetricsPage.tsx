import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AsyncFeedback, DataTable, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";
import { api, useAuth } from "../lib/auth";

type Point = {
  date: string;
  page_views?: number;
  consented_session_days?: number | null;
  low_sample?: boolean;
  count?: number;
};
type Seo = { urls: number; indexed_urls: number; noindex_urls: number; thin_urls: number };
type Health = {
  state: "ok" | "warning" | "error" | "not_observed";
  dns_status: string | null;
  ssl_status: string | null;
  last_observed_at: string | null;
  open_incidents: number;
  worst_severity: string | null;
};
type SiteMetric = {
  site_id: string;
  project_id: string | null;
  domain: string;
  publish_state: string;
  page_views: number;
  leads: number;
  delivery_attention: number;
  health: Health;
  seo: Seo;
};
type Report = {
  observed_at: string;
  days: number;
  privacy: {
    consent_required: boolean;
    ip_stored: boolean;
    low_sample_threshold: number;
    session_metric: string;
    raw_retention_days: number;
    aggregate_retention_days: number;
  };
  totals: { page_views: number; leads: number; outcomes: number; sites_with_errors: number; delivery_attention: number };
  traffic: Point[];
  leads: Point[];
  outcomes: Point[];
  monitor_errors: Point[];
  sites: SiteMetric[];
  selected_site: {
    site_id: string;
    domain: string;
    traffic: Point[];
    leads: Point[];
    monitor_errors: Point[];
    top_pages: { path: string; page_views: number | null; consented_session_days: number | null; low_sample: boolean }[];
    seo: Seo;
  } | null;
  search_data: { google_search_console: string; yandex_webmaster: string };
};

const ranges = [7, 30, 90] as const;

function healthTone(state: Health["state"]) {
  return state === "ok" ? "ok" as const : state === "error" ? "danger" as const : state === "warning" ? "warn" as const : "default" as const;
}

function healthLabel(state: Health["state"]) {
  return { ok: "без активных ошибок", warning: "требует внимания", error: "есть ошибки", not_observed: "ещё не наблюдался" }[state];
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short" }).format(new Date(`${value}T00:00:00Z`));
}

function MetricsLine({ title, points, field, label }: { title: string; points: Point[]; field: "page_views" | "count"; label: string }) {
  const [active, setActive] = useState<number | null>(null);
  const values = points.map((point) => point[field] ?? 0);
  const maximum = Math.max(1, ...values);
  const width = 720;
  const height = 220;
  const padding = { top: 20, right: 22, bottom: 36, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const coordinates = values.map((value, index) => ({
    x: padding.left + (points.length <= 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth),
    y: padding.top + plotHeight - (value / maximum) * plotHeight,
    value,
  }));
  const path = coordinates.map((point, index) => `${index === 0 ? "M" : "L"}${point.x} ${point.y}`).join(" ");
  const selected = active === null ? null : { point: points[active], value: values[active], coord: coordinates[active] };

  return <div className="site-metrics-chart" aria-label={title}>
    <div className="row row-tight"><strong>{title}</strong><span className="muted">{label}</span></div>
    {points.length === 0 ? <EmptyState title="Данных за выбранный период пока нет" /> : <>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${field}-${title.replaceAll(" ", "-")}-title`}>
        <title id={`${field}-${title.replaceAll(" ", "-")}-title`}>{title}. Максимум за день: {maximum}.</title>
        {[0, 0.5, 1].map((fraction) => {
          const y = padding.top + plotHeight - fraction * plotHeight;
          return <g key={fraction}><line x1={padding.left} x2={width - padding.right} y1={y} y2={y} stroke="var(--border)" strokeWidth="1" /><text x={padding.left - 8} y={y + 4} textAnchor="end" fill="var(--muted)" fontSize="11">{Math.round(maximum * fraction)}</text></g>;
        })}
        <path d={path} fill="none" stroke={field === "page_views" ? "var(--accent)" : "var(--danger)"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {coordinates.map((coord, index) => <circle key={points[index].date} cx={coord.x} cy={coord.y} r="5" fill="var(--surface)" stroke={field === "page_views" ? "var(--accent)" : "var(--danger)"} strokeWidth="2" tabIndex={0} aria-label={`${formatDate(points[index].date)}: ${coord.value}`} onFocus={() => setActive(index)} onMouseEnter={() => setActive(index)} onBlur={() => setActive(null)} onMouseLeave={() => setActive(null)} />)}
        <text x={padding.left} y={height - 12} fill="var(--muted)" fontSize="11">{formatDate(points[0].date)}</text>
        <text x={width - padding.right} y={height - 12} textAnchor="end" fill="var(--muted)" fontSize="11">{formatDate(points[points.length - 1].date)}</text>
        {selected && <g pointerEvents="none"><line x1={selected.coord.x} x2={selected.coord.x} y1={padding.top} y2={padding.top + plotHeight} stroke="var(--muted)" strokeDasharray="3 3" /><rect x={Math.min(width - 165, selected.coord.x + 8)} y={Math.max(4, selected.coord.y - 36)} width="154" height="30" rx="4" fill="var(--text)" /><text x={Math.min(width - 158, selected.coord.x + 15)} y={Math.max(23, selected.coord.y - 17)} fill="var(--surface)" fontSize="11">{formatDate(selected.point.date)}: {selected.value}</text></g>}
      </svg>
      <details><summary>Таблица значений графика</summary><DataTable headers={["Дата", label]}>{points.map((point) => <tr key={point.date}><td>{formatDate(point.date)}</td><td>{point[field] ?? 0}</td></tr>)}</DataTable></details>
    </>}
  </div>;
}

export function SiteMetricsPage() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const parsedDays = Number(searchParams.get("days"));
  const days = ranges.includes(parsedDays as 7 | 30 | 90) ? parsedDays : 30;
  const siteId = searchParams.get("siteId") || "";
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    const params = new URLSearchParams({ days: String(days) });
    if (siteId) params.set("site_id", siteId);
    api<Report>(`/api/v1/panel/reports/site-metrics?${params.toString()}`, {}, token)
      .then(setReport)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить аналитику сайтов"));
  }, [days, siteId, token]);

  const selectedMetric = useMemo(() => report?.sites.find((site) => site.site_id === siteId) || null, [report, siteId]);
  function updateSelection(nextDays: number, nextSiteId = siteId) {
    const params = new URLSearchParams({ days: String(nextDays) });
    if (nextSiteId) params.set("siteId", nextSiteId);
    setSearchParams(params);
  }

  return <div>
    <PageHeader title="Аналитика сайтов" description="Посещаемость, заявки, техническая готовность к индексации и последние сохранённые наблюдения по всему портфелю сайтов." />
    <AsyncFeedback error={error} message={null} />
    <Surface title="Период и источник данных">
      <div className="row"><div className="row row-tight" role="group" aria-label="Период аналитики">{ranges.map((range) => <button key={range} className={days === range ? "btn" : "btn btn-ghost"} type="button" onClick={() => updateSelection(range)}>{range} дней</button>)}</div><span className="muted">Данные актуальны на {report?.observed_at ? new Date(report.observed_at).toLocaleString("ru-RU") : "—"}</span></div>
      <p className="muted">Посещаемость собирается только при согласии пользователя на аналитику. IP и идентификаторы сессий не отображаются и не сохраняются в отчёте. Метрика сессий — это сумма уникальных consented сессий по дням, а не число уникальных посетителей за весь период.</p>
    </Surface>
    {!report && !error && <Surface title="Загрузка"><p className="muted">Собираем сводку по сайтам…</p></Surface>}
    {report && <>
      <div className="detail-grid" style={{ margin: "1rem 0" }}>
        <Surface title="Просмотры страниц"><strong>{report.totals.page_views}</strong><p className="muted">за {days} дней</p></Surface>
        <Surface title="Новые заявки"><strong>{report.totals.leads}</strong><p className="muted">без раскрытия контактов</p></Surface>
        <Surface title="Сайты с ошибками"><strong>{report.totals.sites_with_errors}</strong><p className="muted">по последним сохранённым наблюдениям</p></Surface>
        <Surface title="Недоставленные заявки"><strong>{report.totals.delivery_attention}</strong><p className="muted">требуют внимания оператора</p></Surface>
      </div>
      <Surface title="Динамика по всем сайтам"><MetricsLine title="Просмотры страниц" points={report.traffic} field="page_views" label="Просмотры" /><MetricsLine title="Новые заявки" points={report.leads} field="count" label="Заявки" /><MetricsLine title="Ошибки мониторинга" points={report.monitor_errors} field="count" label="Ошибки" /></Surface>
      <Surface title="Сайты: трафик, здоровье и SEO"><p className="muted">Статус DNS/TLS и incidents — это последняя сохранённая проверка монитора, а не гарантия текущей доступности или SLA.</p><DataTable headers={["Сайт", "Просмотры", "Заявки", "Здоровье", "SEO URL", "Действие"]}>{report.sites.map((site) => <tr key={site.site_id}><td><strong>{site.domain}</strong><br /><span className="muted">{site.publish_state}</span></td><td>{site.page_views}</td><td>{site.leads}</td><td><StatusPill tone={healthTone(site.health.state)}>{healthLabel(site.health.state)}</StatusPill><br /><span className="muted">Инциденты: {site.health.open_incidents} · проверено: {site.health.last_observed_at ? new Date(site.health.last_observed_at).toLocaleString("ru-RU") : "нет данных"}</span></td><td>{site.seo.indexed_urls} в индексе · {site.seo.noindex_urls} noindex · {site.seo.thin_urls} тонких</td><td><button className="btn btn-ghost" type="button" onClick={() => updateSelection(days, site.site_id)}>Подробнее</button></td></tr>)}{report.sites.length === 0 && <tr><td colSpan={6}><EmptyState title="Сайтов в доступном контуре пока нет" /></td></tr>}</DataTable></Surface>
      {report.selected_site && selectedMetric && <Surface title={`Подробно: ${report.selected_site.domain}`}><div className="row"><button className="btn btn-ghost" type="button" onClick={() => updateSelection(days, "")}>Вернуться ко всем сайтам</button><Link className="btn btn-ghost" to="/sites">Открыть реестр сайтов</Link>{selectedMetric.project_id && <Link className="btn btn-ghost" to={`/projects/${selectedMetric.project_id}`}>Открыть проект</Link>}</div><MetricsLine title="Посещаемость сайта" points={report.selected_site.traffic} field="page_views" label="Просмотры" /><MetricsLine title="Заявки сайта" points={report.selected_site.leads} field="count" label="Заявки" /><MetricsLine title="Ошибки мониторинга сайта" points={report.selected_site.monitor_errors} field="count" label="Ошибки" /><div className="detail-grid"><div><strong>Индексация</strong><p>{report.selected_site.seo.indexed_urls} URL в индексе, {report.selected_site.seo.noindex_urls} noindex, {report.selected_site.seo.thin_urls} тонких.</p></div><div><strong>Технический статус</strong><p>{healthLabel(selectedMetric.health.state)} · DNS: {selectedMetric.health.dns_status || "—"} · TLS: {selectedMetric.health.ssl_status || "—"}</p></div></div><DataTable headers={["Страница", "Просмотры", "Сессии по дням"]}>{report.selected_site.top_pages.map((page) => <tr key={page.path}><td>{page.path}</td><td>{page.page_views ?? "недостаточно данных"}</td><td>{page.consented_session_days ?? "—"}</td></tr>)}{report.selected_site.top_pages.length === 0 && <tr><td colSpan={3}><EmptyState title="Для страниц пока недостаточно consented telemetry" /></td></tr>}</DataTable></Surface>}
      <Surface title="Данные поисковых систем"><p className="muted">Google Search Console и Яндекс Вебмастер пока не подключены. Поэтому здесь намеренно не показываются вымышленные показы, клики, позиции или статусы индексации поисковых систем.</p><div className="row"><StatusPill tone="default">Google Search Console: не подключено</StatusPill><StatusPill tone="default">Яндекс Вебмастер: не подключено</StatusPill></div></Surface>
    </>}
  </div>;
}
