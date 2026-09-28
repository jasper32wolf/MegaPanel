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
  provenance: { rights_basis?: "own" | "licensed" | "cc"; rights_confirmed?: boolean; source_reference?: string | null; source_url?: string | null; license_name?: string | null; license_expires_at?: string | null };
  hashes: { original_sha256?: string; stored_sha256?: string };
};

export function MediaPage() {
  const { token } = useAuth();
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [rightsBasis, setRightsBasis] = useState("own");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [licenseName, setLicenseName] = useState("");
  const [licenseUrl, setLicenseUrl] = useState("");
  const [licenseExpiresAt, setLicenseExpiresAt] = useState("");
  const [author, setAuthor] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
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
      data.set("rights_basis", rightsBasis);
      data.set("rights_confirmed", String(rightsConfirmed));
      data.set("source_url", sourceUrl);
      data.set("source_reference", sourceReference);
      data.set("license_name", licenseName);
      data.set("license_url", licenseUrl);
      data.set("license_expires_at", licenseExpiresAt);
      data.set("author", author);
      data.set("normalize", String(normalize));
      await api<MediaAsset>("/api/v1/media", { method: "POST", body: data }, token);
      setFile(null);
      setSourceUrl("");
      setSourceReference("");
      setLicenseName("");
      setLicenseUrl("");
      setLicenseExpiresAt("");
      setAuthor("");
      setRightsConfirmed(false);
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
          <label className="field">Основание прав<select value={rightsBasis} onChange={(event) => { setRightsBasis(event.target.value); if (event.target.value !== "own") setNormalize(false); }}><option value="own">Собственный оригинал</option><option value="licensed">Внешняя лицензия</option><option value="cc">Creative Commons</option></select><span className="muted">Это декларация оператора, а не автоматическая юридическая проверка.</span></label>
          <label className="field">Источник URL (необязательно)<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://source.example/asset" /></label>
          <label className="field">Внутренний reference прав<input value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} placeholder="Договор, реестр или подтверждение оригинала" required={!sourceUrl} /></label>
          {rightsBasis !== "own" && <><label className="field">Название лицензии<input value={licenseName} onChange={(event) => setLicenseName(event.target.value)} placeholder="Например: CC BY 4.0" required /></label><label className="field">URL лицензии (необязательно)<input type="url" value={licenseUrl} onChange={(event) => setLicenseUrl(event.target.value)} placeholder="https://license.example/terms" /></label><label className="field">Дата окончания лицензии (если есть)<input type="date" value={licenseExpiresAt} onChange={(event) => setLicenseExpiresAt(event.target.value)} /></label></>}
          <label className="field">Автор<input value={author} onChange={(event) => setAuthor(event.target.value)} /></label>
          <label className="row"><input type="checkbox" checked={rightsConfirmed} onChange={(event) => setRightsConfirmed(event.target.checked)} required /> Подтверждаю, что имею право использовать этот файл.</label>
          <label className="row"><input type="checkbox" checked={normalize} disabled={rightsBasis !== "own"} onChange={(event) => setNormalize(event.target.checked)} /> Нормализовать собственный файл перед сохранением</label>
          {rightsBasis !== "own" && <p className="muted" style={{ margin: 0 }}>Нормализация доступна только для собственных файлов, чтобы не изменять чужой лицензированный материал.</p>}
          <button className="btn" type="submit" disabled={busy || !file || !rightsConfirmed}>{busy ? "Загрузка…" : "Загрузить"}</button>
        </form>
      </Surface>
      <Surface title="Файлы">
        {assets.length === 0 ? <EmptyState title="Медиатека пуста" hint="После загрузки изображения появятся здесь." /> : <div className="kit-grid">{assets.map((asset) => <article className="kit-card" key={asset.id}><img src={asset.path} alt="" style={{ width: "100%", maxHeight: 180, objectFit: "cover", borderRadius: 6 }} /><div><strong>{asset.author || "Без указанного автора"}</strong><p className="muted" style={{ marginBottom: 0 }}>{asset.source || "Источник не указан"}</p></div><div className="row"><StatusPill tone="accent">{asset.license || "—"}</StatusPill>{asset.normalized && <StatusPill tone="warn">нормализован</StatusPill>}</div><p className="muted" style={{ margin: 0 }}>pHash: {asset.phash || "—"}</p><p className="muted" style={{ margin: 0 }}>SHA-256: {asset.hashes.stored_sha256?.slice(0, 16) || "—"}</p>{asset.provenance.license_expires_at && <p className="muted" style={{ margin: 0 }}>Лицензия до: {asset.provenance.license_expires_at}</p>}</article>)}</div>}
      </Surface>
    </div>
  );
}
