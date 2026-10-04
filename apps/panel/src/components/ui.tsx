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
  id,
  title,
  children,
  className = "",
}: {
  id?: string;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`surface ${className}`.trim()}>
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

export function HelpTip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <details className="help-tip">
      <summary aria-label={`Справка: ${label}`}>?</summary>
      <div role="note">
        <strong>{label}</strong>
        <div>{children}</div>
      </div>
    </details>
  );
}

export function InlineAlert({
  severity,
  title,
  children,
}: {
  severity: "info" | "warning" | "critical";
  title: string;
  children: ReactNode;
}) {
  return (
    <div className={`inline-alert inline-alert-${severity}`} role={severity === "critical" ? "alert" : "status"}>
      <strong>{title}</strong>
      <div>{children}</div>
    </div>
  );
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
  requiredValue,
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
  requiredValue?: string;
  dangerous?: boolean;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState("");
  const confirmationValid = requiredValue
    ? value.trim() === requiredValue
    : value.trim().length >= inputMinLength;

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
          if (!confirmationValid) return;
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
            disabled={!confirmationValid}
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
