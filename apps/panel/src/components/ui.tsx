import { useEffect, useRef, useState, type ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="row">{actions}</div> : null}
    </header>
  );
}

export function Surface({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`surface ${className}`.trim()}>
      {title ? <h2>{title}</h2> : null}
      {children}
    </section>
  );
}

export function StatusPill({
  children,
  tone = "default",
}: {
  children: ReactNode;
  tone?: "default" | "ok" | "warn" | "danger" | "accent";
}) {
  const cls =
    tone === "ok"
      ? "pill pill-ok"
      : tone === "warn"
        ? "pill pill-warn"
        : tone === "danger"
          ? "pill pill-danger"
          : tone === "accent"
            ? "pill pill-accent"
            : "pill";
  return <span className={cls}>{children}</span>;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {hint ? <span>{hint}</span> : null}
    </div>
  );
}

export function DataTable({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  inputLabel,
  inputMinLength = 0,
  dangerous = false,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  inputLabel?: string;
  inputMinLength?: number;
  dangerous?: boolean;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setValue("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="confirm-dialog"
      aria-labelledby="confirm-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      onClose={() => {
        if (open) onCancel();
      }}
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim().length < inputMinLength) return;
          onConfirm(value.trim());
        }}
      >
        <h2 id="confirm-dialog-title">{title}</h2>
        <p>{description}</p>
        {inputLabel ? (
          <label className="field">
            {inputLabel}
            <textarea
              autoFocus
              value={value}
              onChange={(event) => setValue(event.target.value)}
              required
              minLength={inputMinLength}
              rows={3}
            />
          </label>
        ) : null}
        <div className="row confirm-dialog-actions">
          <button className="btn btn-ghost" type="button" onClick={onCancel}>
            Отмена
          </button>
          <button
            className={`btn${dangerous ? " btn-danger" : ""}`}
            type="submit"
            autoFocus={!inputLabel}
            disabled={value.trim().length < inputMinLength}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
