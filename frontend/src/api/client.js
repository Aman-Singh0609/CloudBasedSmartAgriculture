// HTTP client for the AgricultureCloud API.
//
// Every response uses one envelope:
//   success: { success: true,  data, message }
//   failure: { success: false, error: { code, message, details? }, request_id }
//
// Authentication is a same-origin session cookie. State-changing requests also
// carry a CSRF token in the X-CSRFToken header; the token is fetched on demand
// and refreshed automatically when the session changes (login / logout).

const REQUEST_TIMEOUT_MS = 30000;

export class ApiError extends Error {
  constructor({ status, code, message, details = null, requestId = null }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const NETWORK_MESSAGE =
  "We could not reach the server. Check your internet connection and try again.";
const TIMEOUT_MESSAGE = "The server took too long to respond. Please try again.";
const UNEXPECTED_MESSAGE = "The server sent a response we did not understand. Please try again.";

let csrfToken = null;
let unauthorizedHandler = null;

/** Called by the auth context so an expired session anywhere logs the UI out. */
export function setUnauthorizedHandler(handler) {
  unauthorizedHandler = handler;
}

/** Forget the cached CSRF token (the server issues a new session on login/logout). */
export function resetCsrfToken() {
  csrfToken = null;
}

async function parseEnvelope(response) {
  try {
    const body = await response.json();
    return body && typeof body === "object" ? body : null;
  } catch {
    return null;
  }
}

async function send(method, path, { body, headers = {} } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json", ...headers },
      body,
      signal: controller.signal,
    });
  } catch (error) {
    const timedOut = error && error.name === "AbortError";
    throw new ApiError({
      status: 0,
      code: timedOut ? "TIMEOUT" : "NETWORK_ERROR",
      message: timedOut ? TIMEOUT_MESSAGE : NETWORK_MESSAGE,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function fetchCsrfToken() {
  const response = await send("GET", "/api/csrf-token");
  const envelope = await parseEnvelope(response);
  const token = envelope && envelope.success && envelope.data ? envelope.data.csrf_token : null;
  if (!token) {
    throw toApiError(response, envelope);
  }
  csrfToken = token;
  return token;
}

function toApiError(response, envelope) {
  const error = envelope && envelope.error ? envelope.error : null;
  return new ApiError({
    status: response.status,
    code: error && error.code ? error.code : "UNEXPECTED_RESPONSE",
    message: error && error.message ? error.message : UNEXPECTED_MESSAGE,
    details: error && error.details ? error.details : null,
    requestId: envelope && envelope.request_id ? envelope.request_id : null,
  });
}

/**
 * Perform a request and return the envelope's `data`.
 * Throws ApiError for HTTP errors, network failures and malformed responses.
 */
export async function request(method, path, { body } = {}) {
  const mutating = method !== "GET";

  const attempt = async () => {
    const headers = {};
    let payload;
    if (mutating) {
      headers["X-CSRFToken"] = csrfToken || (await fetchCsrfToken());
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body ?? {});
    }
    const response = await send(method, path, { body: payload, headers });
    return { response, envelope: await parseEnvelope(response) };
  };

  let { response, envelope } = await attempt();

  // The token is tied to the session; if it went stale, get a new one and retry once.
  if (mutating && response.status === 400 && envelope?.error?.code === "CSRF_VALIDATION_FAILED") {
    resetCsrfToken();
    ({ response, envelope } = await attempt());
  }

  if (!response.ok || !envelope || envelope.success !== true) {
    const error = toApiError(response, envelope);
    if (error.status === 401 && error.code === "AUTHENTICATION_REQUIRED" && unauthorizedHandler) {
      unauthorizedHandler();
    }
    throw error;
  }
  return envelope.data;
}

export const api = {
  config: () => request("GET", "/api/config"),

  me: () => request("GET", "/api/auth/me"),
  signup: (values) => request("POST", "/api/auth/signup", { body: values }),
  login: (values) => request("POST", "/api/auth/login", { body: values }),
  logout: () => request("POST", "/api/auth/logout"),
  forgotPassword: (email) => request("POST", "/api/auth/forgot-password", { body: { email } }),
  checkResetToken: (token) => request("GET", `/api/auth/reset-password/${encodeURIComponent(token)}`),
  resetPassword: (token, values) =>
    request("POST", `/api/auth/reset-password/${encodeURIComponent(token)}`, { body: values }),

  weather: (city) => request("GET", `/api/weather?city=${encodeURIComponent(city)}`),

  createPrediction: (readings) => request("POST", "/api/predictions", { body: readings }),
  listPredictions: (page = 1) => request("GET", `/api/predictions?page=${encodeURIComponent(page)}`),
  getPrediction: (id) => request("GET", `/api/predictions/${encodeURIComponent(id)}`),
  deletePrediction: (id) => request("DELETE", `/api/predictions/${encodeURIComponent(id)}`),
  trends: () => request("GET", "/api/predictions/trends"),
};
