// Display helpers.

export function percent(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

let dateTimeFormatter = null;

/** Format an ISO timestamp in the visitor's own locale and time zone. */
export function formatDateTime(iso) {
  if (!iso) return "Unknown";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Unknown";
  if (!dateTimeFormatter) {
    dateTimeFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  }
  return dateTimeFormatter.format(date);
}

/**
 * Only allow redirects to paths inside this application.
 *
 * Rejects absolute URLs, scheme-relative URLs ("//evil.example"), backslash
 * variants some browsers normalise to "//", and control characters, so a
 * crafted "?next=" link cannot send someone to another site after login.
 */
export function safeNextPath(next, fallback = "/advisor") {
  if (typeof next !== "string" || next.length === 0 || next.length > 512) {
    return fallback;
  }
  if (!next.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return fallback;
  }
  for (let index = 0; index < next.length; index += 1) {
    const code = next.charCodeAt(index);
    if (code < 32 || code === 127) {
      return fallback;
    }
  }
  return next;
}
