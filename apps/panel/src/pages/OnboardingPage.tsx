import { useState } from "react";
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
  const [niche, setNiche] = useState("ремонт");
  const [session, setSession] = useState<Session | null>(null);
  const [city, setCity] = useState("Москва");
  const [service, setService] = useState("Ремонт стиральных машин");
  const [domain, setDomain] = useState("demo-remont.local");
  const [phone, setPhone] = useState("+7 (900) 000-00-00");
  const [kitKey, setKitKey] = useState("service-local-v1");
  const [log, setLog] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    try {
      const s = await api<Session>(
        "/api/v1/onboarding/start",
        { method: "POST", body: JSON.stringify({ niche }) },
        token,
      );
      setSession(s);
      setLog(`Старт: ${s.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    }
  }

  async function next(step: (typeof STEPS)[number], payload: Record<string, unknown>) {
    if (!session) return;
    setError(null);
    try {
      const s = await api<Session>(
        `/api/v1/onboarding/${session.id}/step`,
        { method: "POST", body: JSON.stringify({ step, payload }) },
        token,
      );
      setSession(s);
      setLog(JSON.stringify(s.payload, null, 2));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    }
  }

  async function seedGeo() {
    try {
      const r = await api<Record<string, number>>("/api/v1/geo/seed-demo", { method: "POST" }, token);
      setLog(`Geo seed: ${JSON.stringify(r)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    }
  }

  return (
    <div>
      <PageHeader
        title="Онбординг"
        description="Ниша → гео → комплект блоков → домен → сборка."
        actions={session ? <StatusPill tone="accent">шаг: {session.step}</StatusPill> : undefined}
      />
      {error && <p className="error">{error}</p>}

      <Surface title="1. Ниша">
        <label className="field">
          Ниша
          <input value={niche} onChange={(e) => setNiche(e.target.value)} />
        </label>
        <div className="row">
          <button className="btn" type="button" onClick={start}>
            Начать
          </button>
          <button className="btn btn-ghost" type="button" onClick={seedGeo}>
            Seed гео
          </button>
        </div>
      </Surface>

      {session && (
        <>
          <Surface title="2. Город">
            <label className="field">
              Город (из справочника)
              <input value={city} onChange={(e) => setCity(e.target.value)} />
            </label>
            <button className="btn" type="button" onClick={() => next("geo", { city })}>
              Сохранить гео
            </button>
          </Surface>
          <Surface title="3. Комплект / услуга">
            <label className="field">
              Комплект блоков
              <select value={kitKey} onChange={(e) => setKitKey(e.target.value)}>
                <option value="service-local-v1">service-local-v1</option>
                <option value="home-repair-v1">home-repair-v1</option>
              </select>
            </label>
            <label className="field">
              Услуга
              <input value={service} onChange={(e) => setService(e.target.value)} />
            </label>
            <button
              className="btn"
              type="button"
              onClick={() => next("template", { service, slug: "main", modifier: "", kit_key: kitKey })}
            >
              Применить комплект
            </button>
          </Surface>
          <Surface title="4. Домен">
            <label className="field">
              Домен
              <input value={domain} onChange={(e) => setDomain(e.target.value)} />
            </label>
            <label className="field">
              Телефон
              <input value={phone} onChange={(e) => setPhone(e.target.value)} />
            </label>
            <button className="btn" type="button" onClick={() => next("domain", { domain, phone })}>
              Сохранить домен
            </button>
          </Surface>
          <Surface title="5. Сборка">
            <button className="btn" type="button" onClick={() => next("build", {})}>
              Собрать сайт
            </button>
            {session.completed && <p className="muted">Готово — смотрите payload ниже.</p>}
          </Surface>
        </>
      )}

      {log && (
        <Surface title="Состояние">
          <pre style={{ whiteSpace: "pre-wrap", color: "var(--muted)", fontSize: 13, margin: 0 }}>{log}</pre>
        </Surface>
      )}
    </div>
  );
}
