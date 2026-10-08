import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { api, useAuth } from "../lib/auth";
import { AsyncFeedback, EmptyState, PageHeader, StatusPill, Surface } from "../components/ui";

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
  availability: "eligible" | "expired" | "rights_missing";
};

type MediaReviewDecision = {
  id: number;
  decision: "approved" | "rejected";
  stored_sha256: string;
  reason: string | null;
  manual_replacement_guidance: string | null;
  evidence: string | null;
  actor_id: string | null;
  created_at: string | null;
  record_hash: string;
};

type MediaReviewHistory = {
  asset_id: string;
  stored_sha256: string;
  current: MediaReviewDecision | null;
  items: MediaReviewDecision[];
};

function assetIdentity(asset: MediaAsset) {
  const hash = asset.hashes.stored_sha256;
  return `Медиа ${asset.id.slice(0, 8)} · ${asset.content_type} · SHA-256 ${hash ? hash.slice(0, 16) : "не указан"}`;
}

export function MediaPage() {
  const { token } = useAuth();
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [reviews, setReviews] = useState<Record<string, MediaReviewHistory>>({});
  const [reviewAsset, setReviewAsset] = useState<MediaAsset | null>(null);
  const [decision, setDecision] = useState<"approved" | "rejected">("approved");
  const [reviewReason, setReviewReason] = useState("");
  const [replacementGuidance, setReplacementGuidance] = useState("");
  const [reviewEvidence, setReviewEvidence] = useState("");
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
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  async function load() {
    const nextAssets = await api<MediaAsset[]>("/api/v1/media", {}, token);
    setAssets(nextAssets);
    const results = await Promise.all(nextAssets.map(async (asset) => {
      try {
        return await api<MediaReviewHistory>(`/api/v1/media/${asset.id}/review-decisions`, {}, token);
      } catch {
        return null;
      }
    }));
    setReviews(Object.fromEntries(results.filter((item): item is MediaReviewHistory => item !== null).map((item) => [item.asset_id, item])));
  }

  function restoreReviewFocus() {
    const opener = openerRef.current;
    openerRef.current = null;
    if (opener?.isConnected && !opener.hasAttribute("disabled")) opener.focus();
  }

  function closeReview(force = false) {
    if (busy && !force) return;
    const dialog = dialogRef.current;
    if (dialog?.open) dialog.close();
    setReviewAsset(null);
    setReviewError(null);
    restoreReviewFocus();
  }

  function openReview(asset: MediaAsset, opener: HTMLElement) {
    openerRef.current = opener;
    setReviewAsset(asset);
    setDecision("approved");
    setReviewReason("");
    setReplacementGuidance("");
    setReviewEvidence("");
    setReviewError(null);
  }

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (reviewAsset && !dialog.open) dialog.showModal();
    if (!reviewAsset && dialog.open) dialog.close();
  }, [reviewAsset]);

  async function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!reviewAsset) return;
    if (decision === "rejected" && (!reviewReason.trim() || !replacementGuidance.trim())) {
      setReviewError("Для отклонения укажите причину и ручную инструкцию по замене.");
      return;
    }
    setBusy(true);
    setReviewError(null);
    try {
      await api<MediaReviewDecision>(`/api/v1/media/${reviewAsset.id}/review-decisions`, {
        method: "POST",
        body: JSON.stringify({
          decision,
          reason: reviewReason || null,
          manual_replacement_guidance: replacementGuidance || null,
          evidence: reviewEvidence || null,
        }),
      }, token);
      await load();
      setMessage("Решение проверки сохранено.");
      closeReview(true);
    } catch (cause) {
      setReviewError(cause instanceof Error ? cause.message : "Не удалось сохранить решение по медиа");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load().catch((cause) => setError(cause instanceof Error ? cause.message : "Не удалось загрузить медиатеку"));
  }, [token]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setMessage(null);
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
      setMessage("Изображение загружено и добавлено в медиатеку.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Изображение не загружено");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Медиатека" description="Загружайте только файлы с понятной лицензией. Изображения проверяются и сохраняются в безопасном WebP-формате; загрузка сама по себе не вставляет файл в блок и не публикует сайт." />
      <AsyncFeedback error={error} message={message} />
      <Surface title="Загрузить изображение">
        <form onSubmit={upload} className="stack" aria-busy={busy}>
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
        {assets.length === 0 ? <EmptyState title="Медиатека пуста" hint="После загрузки изображения появятся здесь." /> : <div className="kit-grid">{assets.map((asset) => {
          const review = reviews[asset.id];
          const current = review?.current;
          return <article className="kit-card" key={asset.id}>
            <img src={asset.path} alt="" style={{ width: "100%", maxHeight: 180, objectFit: "cover", borderRadius: 6 }} />
            <div><strong>{assetIdentity(asset)}</strong><p className="muted" style={{ marginBottom: 0 }}>{asset.author || "Автор не указан"} · {asset.source || "Источник не указан"}</p></div>
            <div className="row"><StatusPill tone="accent">{asset.license || "—"}</StatusPill><StatusPill tone={asset.availability === "eligible" ? "ok" : "danger"}>{asset.availability === "eligible" ? "доступен для draft" : asset.availability === "expired" ? "лицензия истекла" : "нет подтверждённых прав"}</StatusPill>{asset.normalized && <StatusPill tone="warn">нормализован</StatusPill>}</div>
            <p className="muted" style={{ margin: 0 }}>pHash: {asset.phash || "—"}</p><p className="muted" style={{ margin: 0 }}>SHA-256: {asset.hashes.stored_sha256?.slice(0, 16) || "—"}</p>
            {current ? <><StatusPill tone={current.decision === "approved" ? "ok" : "danger"}>{current.decision === "approved" ? "проверка одобрена" : "проверка отклонена"}</StatusPill><p className="muted" style={{ margin: 0 }}>Текущая проверка: {current.created_at ? new Date(current.created_at).toLocaleString() : "—"}</p>{current.reason && <p className="muted" style={{ margin: 0 }}>Причина: {current.reason}</p>}{current.manual_replacement_guidance && <p className="muted" style={{ margin: 0 }}>Ручная замена: {current.manual_replacement_guidance}</p>}</> : <p className="muted" style={{ margin: 0 }}>Решение проверки ещё не записано.</p>}
            {asset.provenance.license_expires_at && <p className="muted" style={{ margin: 0 }}>Лицензия до: {asset.provenance.license_expires_at}</p>}
            <button className="btn secondary" type="button" onClick={(event) => openReview(asset, event.currentTarget)}>Записать решение проверки</button>
            {review && review.items.length > 0 && <details><summary>История проверок ({review.items.length})</summary><div className="stack">{review.items.map((item) => <div key={item.id} className="muted"><strong>{item.decision === "approved" ? "Одобрено" : "Отклонено"}</strong> · SHA {item.stored_sha256.slice(0, 16)} · {item.created_at ? new Date(item.created_at).toLocaleString() : "—"}{item.reason && <><br />Причина: {item.reason}</>}{item.manual_replacement_guidance && <><br />Ручная замена: {item.manual_replacement_guidance}</>}{item.evidence && <><br />Доказательство: {item.evidence}</>}</div>)}</div></details>}
          </article>;
        })}</div>}
      </Surface>
      <dialog ref={dialogRef} className="confirm-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={(event) => { event.preventDefault(); closeReview(); }} onClose={restoreReviewFocus}>
        {reviewAsset ? <form onSubmit={submitReview} className="stack" aria-busy={busy}>
          <h2 id={titleId}>Решение проверки медиа</h2>
          <p id={descriptionId}>{assetIdentity(reviewAsset)}. Решение привязано к текущему сохранённому SHA-256, не заменяет файл автоматически и не изменяет созданные релизы.</p>
          <AsyncFeedback error={reviewError} />
          <label className="field">Решение<select autoFocus value={decision} onChange={(event) => setDecision(event.target.value as "approved" | "rejected")}><option value="approved">Одобрить</option><option value="rejected">Отклонить</option></select></label>
          <label className="field">Причина {decision === "rejected" ? "(обязательно)" : "(необязательно)"}<textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} required={decision === "rejected"} /></label>
          <label className="field">Инструкция по ручной замене {decision === "rejected" ? "(обязательно)" : "(необязательно)"}<textarea value={replacementGuidance} onChange={(event) => setReplacementGuidance(event.target.value)} required={decision === "rejected"} placeholder="Опишите, какой файл оператор должен загрузить вручную; автозамена не выполняется." /></label>
          <label className="field">Доказательство / ссылка на проверку (необязательно)<textarea value={reviewEvidence} onChange={(event) => setReviewEvidence(event.target.value)} /></label>
          <div className="row confirm-dialog-actions"><button className="btn" type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить решение"}</button><button className="btn btn-ghost" type="button" onClick={() => closeReview()} disabled={busy}>Отмена</button></div>
        </form> : null}
      </dialog>
    </div>
  );
}
