// Browser-side validation of the seven readings.
//
// The limits are not defined here: they arrive from GET /api/config, which
// serves the very objects the backend validates against. This file only
// applies them, for instant feedback. The backend validates everything again.

// Plain decimals only, mirroring the backend: no exponents, no "NaN", no
// digit-group separators.
const DECIMAL = /^[+-]?(?:\d{1,12}(?:\.\d{0,6})?|\.\d{1,6})$/;

export function formatNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
}

function unitSuffix(spec) {
  return spec.unit ? ` ${spec.unit}` : "";
}

/**
 * Classify one raw text value against a feature spec.
 * Returns { level: "empty" | "error" | "warning" | "ok", message }.
 * "warning" means physically valid but outside the model's reference range.
 */
export function checkReading(spec, raw) {
  const text = String(raw ?? "").trim();
  if (text === "") {
    return { level: "empty", message: `${spec.label} is required.` };
  }
  if (!DECIMAL.test(text)) {
    return {
      level: "error",
      message: `${spec.label} must be a number, for example ${formatNumber(spec.example)}.`,
    };
  }
  const value = Number.parseFloat(text);
  if (!Number.isFinite(value) || value < spec.hard_min || value > spec.hard_max) {
    return {
      level: "error",
      message: `${spec.label} must be between ${formatNumber(spec.hard_min)} and ${formatNumber(
        spec.hard_max,
      )}${unitSuffix(spec)}.`,
    };
  }
  if (value < spec.model_min || value > spec.model_max) {
    return {
      level: "warning",
      message: `Outside the model's reference range (${formatNumber(spec.model_min)}–${formatNumber(
        spec.model_max,
      )}${unitSuffix(spec)}). The result will be less reliable.`,
    };
  }
  return { level: "ok", message: "" };
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function emailProblem(value) {
  const text = String(value ?? "").trim();
  if (!text) return "Email address is required.";
  if (text.length > 254 || !EMAIL.test(text)) {
    return "Enter a valid email address, for example name@example.com.";
  }
  return null;
}

export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;

/** Mirrors the backend's basic password rules (the server applies further checks). */
export function passwordProblem(value) {
  const text = String(value ?? "");
  if (!text) return "Password is required.";
  if (text.length < PASSWORD_MIN_LENGTH) {
    return `Password must be at least ${PASSWORD_MIN_LENGTH} characters long.`;
  }
  if (text.length > PASSWORD_MAX_LENGTH) {
    return `Password must be at most ${PASSWORD_MAX_LENGTH} characters long.`;
  }
  if (!/\p{L}/u.test(text) || !/\d/.test(text)) {
    return "Password must contain at least one letter and one number.";
  }
  return null;
}

export function nameProblem(value) {
  const text = String(value ?? "").trim();
  if (!text) return "Full name is required.";
  if (text.length < 2 || text.length > 80) return "Full name must be between 2 and 80 characters.";
  return null;
}
