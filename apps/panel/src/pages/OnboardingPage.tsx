import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Session = {
  id: string;
  step: string;
  payload: Record<string, unknown>;
  completed: boolean;
};

const STEPS = ["niche", "geo", "template", "domain", "build"] as const;

export function OnboardingPage() {
  const { token } = useAuth();
  const [niche, setNiche] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [city, setCity] = useState("");
  const [service, setService] = useState("");
  const [domain, setDomain] = useState("");
  const [phone, setPhone] = useState("");
  const [kitKey, setKitKey] = useState("service-local-v1");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function start() {
    if (!niche.trim()) return;
    setError(null);
    try {
      const created = await api<Session>(
        "/api/v1/onboarding/start",
        { method: "POST", body: JSON.stringify({ niche: niche.trim() }) },
        token,
      );
      setSession(created);
      setMessage("Проект создан. Продолжайте по шагам ниже.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось начать проект");
    }
  }

  async function next(step: (typeof STEPS)[number], payload: Record<string, unknown>) {
    if (!session) return;
    setError(null);
    try {
      const updated = await api<Session>(
        `/api/v1/onboarding/${session.id}/step`,
        { method: "POST", body: JSON.stringify({ step, payload }) },
        token,
      );
      setSession(updated);
      setMessage(step === "build" ? "Сборка завершена. Проверьте сайт перед публикацией." : "Шаг сохранён.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить шаг");
    }
  }

  return (
    <div>
      <PageHeader
        title="Новый сайт"
        description="Текущий упрощённый путь: собственные данные, локальный справочник гео, комплект блоков и домен. Отдельные план страниц и проверка качества относятся к дорожной карте развития."
        actions={session ? <StatusPill tone="accent">шаг: {session.step}</StatusPill> : undefined}
      />
      {error && <p className="error">{error}</p>}

      <Surface title="1. Ниша">
        <label className="field">
          Ниша
          <input value={niche} onChange={(event) => setNiche(event.target.value)} placeholder="Например: ремонт" required />
        </label>
        <button className="btn" type="button" disabled={!niche.trim()} onClick={start}>Начать</button>
      </Surface>

      {session && (
        <>
          <Surface title="2. Город">
            <p className="muted">Сначала добавьте город в локальный справочник. Он будет проверен перед сборкой и использован для склонений.</p>
            <label className="field">
              Город из справочника
              <input value={city} onChange={(event) => setCity(event.target.value)} placeholder="Например: Казань" required />
            </label>
            <div className="row">
              <button className="btn" type="button" disabled={!city.trim()} onClick={() => next("geo", { city: city.trim() })}>Сохранить гео</button>
              <Link className="btn btn-ghost" to="/geo">Открыть справочник</Link>
            </div>
          </Surface>
          <Surface title="3. Услуга и комплект">
            <label className="field">
              Комплект блоков
              <select value={kitKey} onChange={(event) => setKitKey(event.target.value)}>
                <option value="service-local-v1">Локальные услуги</option>
                <option value="home-repair-v1">Домашний ремонт</option>
              </select>
            </label>
            <label className="field">
              Услуга
              <input value={service} onChange={(event) => setService(event.target.value)} placeholder="Например: ремонт стиральных машин" required />
            </label>
            <button className="btn" type="button" disabled={!service.trim()} onClick={() => next("template", { service: service.trim(), slug: "main", modifier: "", kit_key: kitKey })}>Применить комплект</button>
          </Surface>
          <Surface title="4. Домен и контакты">
            <label className="field">
              Домен
              <input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="example.ru" required />
            </label>
            <label className="field">
              Телефон
              <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+7 (900) 000-00-00" required />
            </label>
            <button className="btn" type="button" disabled={!domain.trim() || !phone.trim()} onClick={() => next("domain", { domain: domain.trim(), phone: phone.trim() })}>Сохранить домен</button>
          </Surface>
          <Surface title="5. Сборка">
            <p className="muted">Сборка создаст черновой static release. Перед публикацией проверьте контент, форму и домен.</p>
            <button className="btn" type="button" onClick={() => next("build", {})}>Собрать сайт</button>
            {session.completed && <p className="muted">Черновик собран. Откройте список сайтов для дальнейшей публикации.</p>}
          </Surface>
        </>
      )}
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
