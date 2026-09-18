import { useEffect, useState } from "react";
import { api, useAuth } from "../lib/auth";
import { PageHeader, StatusPill, Surface } from "../components/ui";

type Kit = {
  key: string;
  name: string;
  version: string;
  description: string;
  niches: string[];
  blocks: string[];
};

type KitsResponse = {
  library_version: string;
  kits: Kit[];
};

type PreviewResponse = {
  kit_key: string;
  html: string;
  css_vars: Record<string, string>;
  theme: Record<string, unknown>;
};

export function BlocksPage() {
  const { token } = useAuth();
  const [data, setData] = useState<KitsResponse | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<KitsResponse>("/api/v1/blocks/kits", {}, token)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Error"));
  }, [token]);

  async function showPreview(key: string) {
    setSelected(key);
    setError(null);
    try {
      setPreview(await api<PreviewResponse>(`/api/v1/blocks/kits/${key}/preview`, {}, token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed");
    }
  }

  async function syncKit(key: string) {
    setBusy(true);
    setSyncMsg(null);
    setError(null);
    try {
      const r = await api<{ synced: number; kit_key: string; library_version: string }>(
        `/api/v1/blocks/kits/${key}/sync`,
        { method: "POST" },
        token,
      );
      setSyncMsg(`Подготовлено ${r.synced} блоков (${r.kit_key} @ ${r.library_version})`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось подготовить комплект");
    } finally {
      setBusy(false);
    }
  }

  const srcDoc = preview
    ? `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
:root{${Object.entries(preview.css_vars)
        .map(([k, v]) => `--${k}:${v}`)
        .join(";")}}
body{margin:0;font-family:system-ui,sans-serif;background:var(--sp-bg,#fff);color:var(--sp-text,#111)}
</style></head><body>${preview.html}</body></html>`
    : "";

  return (
    <div>
      <PageHeader
        title="Библиотека блоков"
        description="Версионируемые комплекты для сайтов услуг. Theme morph: палитра, радиусы, градиенты."
        actions={
          data ? <StatusPill tone="accent">lib {data.library_version}</StatusPill> : undefined
        }
      />
      {error && <p className="error">{error}</p>}
      {syncMsg && <p className="muted">{syncMsg}</p>}

      <div className="kit-grid">
        {(data?.kits || []).map((kit) => (
          <article className="kit-card" key={kit.key}>
            <div>
              <h3>{kit.name}</h3>
              <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.9rem" }}>
                {kit.description}
              </p>
            </div>
            <div className="block-chips">
              {kit.blocks.map((b) => (
                <span className="pill" key={b}>
                  {b}
                </span>
              ))}
            </div>
            <div className="row">
              <button className="btn btn-ghost" type="button" onClick={() => showPreview(kit.key)}>
                Превью
              </button>
              <button className="btn" type="button" disabled={busy} onClick={() => syncKit(kit.key)}>
                Подготовить комплект
              </button>
            </div>
            {selected === kit.key && <StatusPill tone="ok">выбран</StatusPill>}
          </article>
        ))}
      </div>

      {preview && (
        <Surface title={`Превью · ${preview.kit_key}`}>
          <div className="row" style={{ marginBottom: "0.75rem" }}>
            {Object.entries(preview.css_vars)
              .slice(0, 6)
              .map(([k, v]) => (
                <StatusPill key={k}>
                  {k}: {v}
                </StatusPill>
              ))}
          </div>
          <div className="preview-frame">
            <iframe title="kit-preview" srcDoc={srcDoc} sandbox="" />
          </div>
        </Surface>
      )}
    </div>
  );
}
