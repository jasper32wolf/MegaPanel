import { useEffect, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

type MediaAsset = {
  id: string;
  path: string;
  content_type: string;
  source: string | null;
  license: string | null;
  author: string | null;
  phash: string | null;
  normalized: boolean;
  tags: string[];
};

export function MediaPage() {
  const { token } = useAuth();
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [license, setLicense] = useState("own");
  const [source, setSource] = useState("");
  const [author, setAuthor] = useState("");
  const [normalize, setNormalize] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    setAssets(await api<MediaAsset[]>("/api/v1/media", {}, token));
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить медиатеку"));
  }, [token]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const data = new FormData();
      data.set("file", file);
      data.set("license", license);
      data.set("source", source);
      data.set("author", author);
      data.set("normalize", String(normalize));
      await api<MediaAsset>("/api/v1/media", { method: "POST", body: data }, token);
      setFile(null);
      setSource("");
      setAuthor("");
      setNormalize(false);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Изображение не загружено");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Медиатека" description="Загружайте только файлы с понятной лицензией. Изображения проверяются и сохраняются в безопасном WebP-формате; загрузка сама по себе не вставляет файл в блок и не публикует сайт." />
      {error && <p className="error">{error}</p>}
      <Surface title="Загрузить изображение">
        <form onSubmit={upload} className="stack">
          <label className="field">Файл<input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={(event) => setFile(event.target.files?.[0] || null)} required /></label>
          <label className="field">Лицензия<select value={license} onChange={(event) => { setLicense(event.target.value); if (event.target.value !== "own") setNormalize(false); }}><option value="own">Собственный / лицензированный файл</option><option value="licensed">Внешняя лицензия</option><option value="cc">Creative Commons</option></select></label>
          <label className="field">Источник<input value={source} onChange={(event) => setSource(event.target.value)} placeholder="URL или внутренний реестр лицензии" /></label>
          <label className="field">Автор<input value={author} onChange={(event) => setAuthor(event.target.value)} /></label>
          <label className="row"><input type="checkbox" checked={normalize} disabled={license !== "own"} onChange={(event) => setNormalize(event.target.checked)} /> Нормализовать собственный файл перед сохранением</label>
          {license !== "own" && <p className="muted" style={{ margin: 0 }}>Нормализация доступна только для собственных файлов, чтобы не изменять чужой лицензированный материал.</p>}
          <button className="btn" type="submit" disabled={busy || !file}>{busy ? "Загрузка…" : "Загрузить"}</button>
        </form>
      </Surface>
      <Surface title="Файлы">
        {assets.length === 0 ? <EmptyState title="Медиатека пуста" hint="После загрузки изображения появятся здесь." /> : <div className="kit-grid">{assets.map((asset) => <article className="kit-card" key={asset.id}><img src={asset.path} alt="" style={{ width: "100%", maxHeight: 180, objectFit: "cover", borderRadius: 6 }} /><div><strong>{asset.author || "Без указанного автора"}</strong><p className="muted" style={{ marginBottom: 0 }}>{asset.source || "Источник не указан"}</p></div><div className="row"><StatusPill tone="accent">{asset.license || "—"}</StatusPill>{asset.normalized && <StatusPill tone="warn">нормализован</StatusPill>}</div></article>)}</div>}
      </Surface>
    </div>
  );
}
