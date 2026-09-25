import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { ConfirmDialog, PageHeader, StatusPill, Surface } from "../components/ui";

type Provider = {
  id: string;
  provider_id: string;
  label: string;
  kind: "native" | "openai_compatible";
  base_url: string | null;
  credential_last4: string | null;
  enabled: boolean;
};

type ProviderTest = { ok: boolean; provider_id: string; message?: string; code?: string };

export function AIProvidersPage() {
  const { token } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerId, setProviderId] = useState("zhipu_glm");
  const [kind, setKind] = useState<Provider["kind"]>("native");
  const [label, setLabel] = useState("GLM / Zhipu");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState("glm-4-flash");
  const [pricing, setPricing] = useState("{}");
  const [rotationId, setRotationId] = useState<string | null>(null);
  const [replacementKey, setReplacementKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{ provider: Provider; action: "activate" | "delete" } | null>(null);

  async function load() {
    setProviders(await api<Provider[]>("/api/v1/ai/providers", {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить провайдеры"));
  }, [token]);

  async function addProvider(event: FormEvent) {
    event.preventDefault();
    setBusy("add");
    setError(null);
    setMessage(null);
    try {
      const modelPricing = JSON.parse(pricing) as Record<string, unknown>;
      await api<Provider>("/api/v1/ai/providers", {
        method: "POST",
        body: JSON.stringify({
          provider_id: providerId.trim(),
          label: label.trim(),
          kind,
          base_url: kind === "openai_compatible" ? baseUrl.trim() : null,
          api_key: apiKey,
          models: models.split(",").map((value) => value.trim()).filter(Boolean),
          model_pricing: modelPricing,
        }),
      }, token);
      setApiKey("");
      setMessage("Подключение сохранено выключенным. Перед генерацией добавьте актуальные тарифы, активируйте connection и настройте лимит у провайдера.");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось сохранить подключение");
    } finally {
      setBusy(null);
    }
  }

  async function changeProvider(provider: Provider, action: "activate" | "disable" | "test" | "delete") {
    setBusy(`${action}:${provider.id}`);
    setError(null);
    setMessage(null);
    try {
      if (action === "delete") {
        await api<void>(`/api/v1/ai/providers/${provider.id}`, { method: "DELETE" }, token);
      } else {
        const result = await api<Provider | ProviderTest>(`/api/v1/ai/providers/${provider.id}/${action}`, { method: "POST" }, token);
        if (action === "test") {
          const test = result as ProviderTest;
          setMessage(test.ok ? "Шифрование и egress policy проверены; внешний API не вызывался." : `${test.code || "Ошибка"}: ${test.message || "проверка не пройдена"}`);
        } else {
          setMessage(action === "activate" ? "Провайдер активирован." : "Провайдер отключён.");
        }
      }
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Операция провайдера не выполнена");
    } finally {
      setBusy(null);
    }
  }

  async function replaceKey(provider: Provider) {
    if (!replacementKey) return;
    setBusy(`rotate:${provider.id}`);
    setError(null);
    setMessage(null);
    try {
      await api<Provider>(
        `/api/v1/ai/providers/${provider.id}`,
        { method: "PATCH", body: JSON.stringify({ api_key: replacementKey }) },
        token,
      );
      setReplacementKey("");
      setRotationId(null);
      setMessage(`API key для ${provider.label} заменён.`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Не удалось заменить API key");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader title="AI-провайдеры" description="VPS-wide подключения. Ключи write-only и шифруются сервером." />
      {error && <p className="error" role="alert">{error}</p>}
      {message && <p className="success" role="status">{message}</p>}
      <Surface title="Добавить подключение">
        <form className="stack" onSubmit={addProvider}>
          <div className="detail-grid">
            <label className="field">Provider ID<input value={providerId} onChange={(event) => setProviderId(event.target.value)} required /></label>
            <label className="field">Название<input value={label} onChange={(event) => setLabel(event.target.value)} required /></label>
            <label className="field">Тип<select value={kind} onChange={(event) => setKind(event.target.value as Provider["kind"])}><option value="native">Native adapter</option><option value="openai_compatible">OpenAI-compatible / gateway</option></select></label>
            <label className="field">Модели<input value={models} onChange={(event) => setModels(event.target.value)} placeholder="model-a, model-b" /></label>
          </div>
          {kind === "openai_compatible" && <label className="field">HTTPS base URL<input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://gateway.example/v1" required /></label>}
          <label className="field">API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" required /></label>
          <label className="field">Тарифы модели — JSON<textarea value={pricing} onChange={(event) => setPricing(event.target.value)} rows={6} spellCheck={false} /><span className="muted">Ключ объекта — model ID; укажите input/output USD за 1 млн токенов, источник тарифа и ISO timestamp observed_at. Нулевую цену указывайте только если это подтверждено и пометьте is_free.</span></label>
          <p className="muted">Free-модели не выбираются автоматически; цена/доступность/retention изменчивы. Произвольные gateway требуют `AI_ENDPOINT_ALLOWLIST` на сервере.</p>
          <button className="btn" type="submit" disabled={busy !== null || !apiKey}>{busy === "add" ? "Сохранение…" : "Сохранить выключенное подключение"}</button>
        </form>
      </Surface>
      <Surface title="Подключения">
        {providers.length === 0 ? <p className="muted">Подключений пока нет.</p> : <div className="table-wrap"><table className="table"><thead><tr><th>Провайдер</th><th>Endpoint</th><th>Ключ</th><th>Состояние</th><th>Действия</th></tr></thead><tbody>{providers.map((provider) => <tr key={provider.id}><td><strong>{provider.label}</strong><p className="muted">{provider.provider_id} · {provider.kind}</p></td><td className="muted">{provider.base_url || "native"}</td><td><code>••••{provider.credential_last4 || "—"}</code></td><td><StatusPill tone={provider.enabled ? "ok" : "warn"}>{provider.enabled ? "активен" : "выключен"}</StatusPill></td><td><div className="row"><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => changeProvider(provider, "test")}>Проверить конфигурацию</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => provider.enabled ? changeProvider(provider, "disable") : setConfirmation({ provider, action: "activate" })}>{provider.enabled ? "Отключить" : "Активировать"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setRotationId(rotationId === provider.id ? null : provider.id)}>Заменить ключ</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => setConfirmation({ provider, action: "delete" })}>Удалить</button></div>{rotationId === provider.id && <form className="stack" onSubmit={(event) => { event.preventDefault(); void replaceKey(provider); }}><label className="field">Новый API key<input type="password" value={replacementKey} onChange={(event) => setReplacementKey(event.target.value)} autoComplete="new-password" required /></label><div className="row"><button className="btn" type="submit" disabled={busy !== null || !replacementKey}>{busy === `rotate:${provider.id}` ? "Замена…" : "Сохранить новый ключ"}</button><button className="btn btn-ghost" type="button" disabled={busy !== null} onClick={() => { setRotationId(null); setReplacementKey(""); }}>Отмена</button></div></form>}</td></tr>)}</tbody></table></div>}
      </Surface>
      <ConfirmDialog
        open={confirmation !== null}
        title={confirmation?.action === "delete" ? "Удалить AI-подключение?" : "Активировать AI-подключение?"}
        description={confirmation?.action === "delete" ? `Подключение ${confirmation?.provider.label || ""} и его зашифрованный ключ будут удалены.` : "Подключение станет доступно для явно подтверждённых оператором запросов."}
        confirmLabel={confirmation?.action === "delete" ? "Удалить" : "Активировать"}
        dangerous={confirmation?.action === "delete"}
        onCancel={() => setConfirmation(null)}
        onConfirm={() => {
          const current = confirmation;
          setConfirmation(null);
          if (current) void changeProvider(current.provider, current.action);
        }}
      />
    </div>
  );
}
