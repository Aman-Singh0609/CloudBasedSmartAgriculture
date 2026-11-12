// Small presentational components shared across pages.
//
// None of them use inline `style` attributes: the production server sends a
// Content-Security-Policy that forbids inline styles, so sizes and positions
// are expressed with CSS classes and SVG geometry attributes instead.

import { createElement, useEffect, useId, useRef, useState } from "react";
import { ICONS } from "./iconPaths.js";

export function Icon({ name, className = "" }) {
  const shapes = ICONS[name] || ICONS.info;
  return (
    <svg className={`icon ${className}`.trim()} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {shapes.map(([tag, attrs], index) => createElement(tag, { key: index, ...attrs }))}
    </svg>
  );
}

const ALERT_ICONS = { success: "check-circle", info: "info", warning: "alert", error: "error" };

export function Alert({ kind = "info", title, children, onDismiss, focusOnMount = false }) {
  const ref = useRef(null);
  useEffect(() => {
    if (focusOnMount && ref.current) ref.current.focus();
  }, [focusOnMount]);

  return (
    <div
      ref={ref}
      className={`alert alert--${kind}`}
      role={kind === "error" ? "alert" : "status"}
      tabIndex={focusOnMount ? -1 : undefined}
    >
      <Icon name={ALERT_ICONS[kind] || "info"} />
      <div className="alert__body">
        {title ? <p className="alert__title">{title}</p> : null}
        {children}
      </div>
      {onDismiss ? (
        <button className="alert__dismiss" type="button" onClick={onDismiss}>
          <Icon name="x" />
          <span className="visually-hidden">Dismiss message</span>
        </button>
      ) : null}
    </div>
  );
}

/** List of validation problems with links that move focus to each field. */
export function ErrorSummary({ errors, summaryRef }) {
  if (!errors || errors.length === 0) return null;
  return (
    <div className="error-summary" role="alert" tabIndex={-1} ref={summaryRef}>
      <h2>Please correct the following</h2>
      <ul>
        {errors.map((error) => (
          <li key={error.field || error.message}>
            {error.field ? (
              <a
                href={`#${error.field}`}
                onClick={(event) => {
                  event.preventDefault();
                  document.getElementById(error.field)?.focus();
                }}
              >
                {error.message}
              </a>
            ) : (
              error.message
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function TextField({
  id,
  label,
  type = "text",
  value,
  onChange,
  error,
  hint,
  autoComplete,
  maxLength,
  revealable = false,
}) {
  const [revealed, setRevealed] = useState(false);
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`field${error ? " is-invalid" : ""}`}>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      {hint ? (
        <span className="field__hint" id={hintId}>
          {hint}
        </span>
      ) : null}
      <div className={`input-wrap${revealable ? " input-wrap--action" : ""}`}>
        <input
          className="input"
          id={id}
          name={id}
          type={revealable && revealed ? "text" : type}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          maxLength={maxLength}
          required
          aria-invalid={error ? "true" : undefined}
          aria-describedby={describedBy}
        />
        {revealable ? (
          <button
            className="input-action"
            type="button"
            aria-label={revealed ? "Hide password" : "Show password"}
            onClick={() => setRevealed((current) => !current)}
          >
            {revealed ? "Hide" : "Show"}
          </button>
        ) : null}
      </div>
      {error ? (
        <span className="field__message field__message--error" id={errorId}>
          <Icon name="error" />
          <span>{error}</span>
        </span>
      ) : null}
    </div>
  );
}

const STATUS_ICONS = { within: "check", below: "arrow-down", above: "arrow-up", caution: "alert" };

/** Status chip: icon + text, so meaning never depends on colour alone. */
export function StatusChip({ status, label }) {
  return (
    <span className={`status status--${status}`}>
      <Icon name={STATUS_ICONS[status] || "info"} />
      {label}
    </span>
  );
}

/** Horizontal bar for a 0–1 share, drawn with SVG geometry (no inline styles). */
export function ShareBar({ value, className = "" }) {
  const width = Math.max(0, Math.min(100, Math.round((value || 0) * 1000) / 10));
  return (
    <svg className={`bar ${className}`.trim()} viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <rect className="bar__track" width="100" height="10" />
      <rect className="bar__fill" width={width} height="10" />
    </svg>
  );
}

/** One reading compared with the typical range for the recommended crop. */
export function ReadingRow({ reading, sourceLabel }) {
  const { chart } = reading;
  return (
    <li className="reading">
      <div className="reading__head">
        <span className="reading__name">{reading.label}</span>
        <span className="cluster">
          <span className="reading__value">{reading.value_text}</span>
          <StatusChip status={reading.status} label={reading.status_label} />
        </span>
      </div>
      <svg
        className={`gauge gauge--${reading.status}`}
        viewBox="0 0 100 10"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${reading.label}: ${reading.value_text}. Typical range for this crop: ${reading.typical_text}. ${reading.status_label}.`}
      >
        <rect className="gauge__track" width="100" height="10" />
        <rect className="gauge__band" x={chart.band_start} width={chart.band_width} height="10" />
        <line className="gauge__marker" x1={chart.marker} x2={chart.marker} y1="0" y2="10" />
      </svg>
      <div className="gauge-scale" aria-hidden="true">
        <span>{chart.scale_min}</span>
        <span>{chart.scale_max}</span>
      </div>
      <div className="reading__meta">
        <span>
          Typical for this crop: <strong>{reading.typical_text}</strong>
        </span>
        {sourceLabel ? <span>{sourceLabel}</span> : null}
      </div>
    </li>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

/** Whole-page loading placeholder, announced to assistive technology. */
export function PageLoading({ label = "Loading…" }) {
  return (
    <div className="container page-state" role="status" aria-live="polite">
      <Spinner />
      <p>{label}</p>
    </div>
  );
}

/** Whole-page failure with a way forward. */
export function PageError({ title, message, onRetry, children }) {
  return (
    <div className="container error-page">
      <h1>{title}</h1>
      <p>{message}</p>
      <div className="cluster form-actions error-page__actions">
        {onRetry ? (
          <button className="btn" type="button" onClick={onRetry}>
            <Icon name="refresh" /> Try again
          </button>
        ) : null}
        {children}
      </div>
    </div>
  );
}

/**
 * Confirmation dialog built on the native <dialog> element, which provides
 * the focus trap, Escape-to-close and inert background for free.
 */
export function ConfirmDialog({ open, title, children, confirmLabel, busy, onConfirm, onCancel }) {
  const ref = useRef(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
      <div className="dialog__actions">
        <button className="btn btn--ghost" type="button" onClick={onCancel} disabled={busy} autoFocus>
          Cancel
        </button>
        <button className="btn btn--danger" type="button" onClick={onConfirm} disabled={busy}>
          {busy ? <Spinner /> : null} {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

/** Set the document title for the current page (restored on unmount). */
export function useDocumentTitle(title) {
  useEffect(() => {
    const previous = document.title;
    document.title = title ? `${title} – AgricultureCloud` : "AgricultureCloud";
    return () => {
      document.title = previous;
    };
  }, [title]);
}
