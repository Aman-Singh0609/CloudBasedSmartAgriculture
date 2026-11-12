// Test helpers: a fake backend for `fetch` and a render wrapper with providers.

import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import App from "../App.jsx";
import { resetCsrfToken } from "../api/client.js";
import { AuthProvider } from "../context/AuthContext.jsx";
import { ConfigProvider } from "../context/ConfigContext.jsx";

export const FEATURES = [
  { key: "nitrogen", label: "Nitrogen (N)", short_label: "N", unit: "", group: "nutrients", hard_min: 0, hard_max: 300, model_min: 0, model_max: 140, example: 90, help: "Nitrogen content." },
  { key: "phosphorus", label: "Phosphorus (P)", short_label: "P", unit: "", group: "nutrients", hard_min: 0, hard_max: 300, model_min: 5, model_max: 145, example: 42, help: "Phosphorus content." },
  { key: "potassium", label: "Potassium (K)", short_label: "K", unit: "", group: "nutrients", hard_min: 0, hard_max: 400, model_min: 5, model_max: 205, example: 43, help: "Potassium content." },
  { key: "ph", label: "Soil pH", short_label: "pH", unit: "", group: "condition", hard_min: 0, hard_max: 14, model_min: 3.5, model_max: 9.9, example: 6.5, help: "Acidity." },
  { key: "temperature", label: "Temperature", short_label: "Temp", unit: "°C", group: "climate", hard_min: -10, hard_max: 60, model_min: 8.8, model_max: 43.7, example: 20.8, help: "Air temperature." },
  { key: "humidity", label: "Humidity", short_label: "Humidity", unit: "%", group: "climate", hard_min: 0, hard_max: 100, model_min: 14.3, model_max: 100, example: 82, help: "Relative humidity." },
  { key: "rainfall", label: "Rainfall", short_label: "Rain", unit: "mm", group: "climate", hard_min: 0, hard_max: 3000, model_min: 20.2, model_max: 298.6, example: 202.9, help: "Rainfall." },
];

export const CONFIG = {
  version: "2.0.0",
  features: FEATURES,
  field_groups: [
    { key: "nutrients", title: "Soil nutrients", hint: "From a soil test report." },
    { key: "condition", title: "Soil condition", hint: "Also on a soil test report." },
    { key: "climate", title: "Climate", hint: "Typical conditions." },
  ],
  weather_enabled: true,
  model_available: true,
  password_reset_enabled: true,
};

export const USER = { id: "u1", name: "Asha Patel", email: "asha@example.test" };

export function ok(data, status = 200, message = "OK") {
  return { status, body: { success: true, data, message } };
}

export function fail(status, code, message, details) {
  const error = { code, message };
  if (details) error.details = details;
  return { status, body: { success: false, error, request_id: "req-test" } };
}

/**
 * Install a fake `fetch`. `routes` maps "METHOD /path" to a reply object
 * ({status, body}) or a function (request) => reply. Unknown routes fail the test.
 * Returns the list of calls made, for assertions.
 */
export function mockApi(routes = {}) {
  resetCsrfToken();
  const calls = [];
  const table = {
    "GET /api/csrf-token": ok({ csrf_token: "csrf-1" }),
    "GET /api/config": ok(CONFIG),
    "GET /api/auth/me": ok({ authenticated: false, user: null }),
    ...routes,
  };

  const fetchMock = vi.fn(async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    const path = String(url);
    const call = {
      method,
      path,
      headers: options.headers || {},
      body: options.body ? JSON.parse(options.body) : undefined,
      credentials: options.credentials,
    };
    calls.push(call);

    const key = `${method} ${path}`;
    const pathOnly = `${method} ${path.split("?")[0]}`;
    const handler = key in table ? table[key] : table[pathOnly];
    if (handler === undefined) {
      throw new Error(`Unexpected request in test: ${key}`);
    }
    if (handler instanceof Error) {
      throw handler;
    }
    const reply = typeof handler === "function" ? handler(call) : handler;
    return {
      ok: reply.status >= 200 && reply.status < 300,
      status: reply.status,
      json: async () => {
        if (reply.body === "__invalid__") throw new SyntaxError("Unexpected token");
        return reply.body;
      },
    };
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/** Render the whole application at a given URL. */
export function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ConfigProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ConfigProvider>
    </MemoryRouter>,
  );
}

export const SIGNED_IN = { "GET /api/auth/me": ok({ authenticated: true, user: USER }) };

export const RESULT = {
  id: "abc123abc123abc123abc123",
  recommended_crop: "rice",
  crop_name: "Rice",
  confidence: 0.82,
  confidence_band: "high",
  confidence_label: "Strong model agreement",
  inputs: { nitrogen: 90, phosphorus: 20, potassium: 43, ph: 6.5, temperature: 27.3, humidity: 64, rainfall: 202.9 },
  reliability: "normal",
  created_at: "2026-11-10T12:00:00Z",
  alternatives: [
    { crop: "jute", crop_name: "Jute", probability: 0.12, summary: "A fibre crop of hot, humid regions." },
    { crop: "maize", crop_name: "Maize", probability: 0.06, summary: "A versatile warm-season cereal." },
  ],
  input_sources: { nitrogen: "manual", phosphorus: "manual", potassium: "manual", ph: "manual", temperature: "weather_api", humidity: "weather_api", rainfall: "manual" },
  weather: { location: "Pune, IN", provider: "openweathermap", observed_at: "2026-11-10T11:50:00Z" },
  out_of_range_fields: [],
  model: { model_type: "RandomForestClassifier" },
  crop: {
    label: "rice",
    name: "Rice",
    also_known_as: "Paddy, Dhan",
    scientific_name: "Oryza sativa",
    category: "Cereal",
    perennial: false,
    summary: "A staple cereal that thrives in warm, humid conditions.",
    description: "Rice is the staple food for much of Asia.",
    growing_conditions: { climate: "Warm and humid.", soil: "Clay soils.", water: "High." },
    season: "Mainly kharif.",
    cautions: ["Water demand is high."],
  },
  soil_analysis: {
    method: "rule_based",
    compared_with: "rice",
    ph_class: "Neutral",
    ph_description: "Suitable for most crops.",
    within_typical_range: 5,
    total_readings: 7,
    suggestions: ["Phosphorus is below the typical range for rice."],
    amount_advice: "Amounts depend on your field.",
    readings: [
      ["nitrogen", "Nitrogen (N)", "nutrients", "90", "60–99", "within", "Within typical range"],
      ["phosphorus", "Phosphorus (P)", "nutrients", "20", "35–60", "below", "Below typical range"],
      ["potassium", "Potassium (K)", "nutrients", "43", "35–45", "within", "Within typical range"],
      ["ph", "Soil pH", "condition", "6.5", "5–7.9", "within", "Within typical range"],
      ["temperature", "Temperature", "climate", "27.3 °C", "20–27 °C", "above", "Above typical range"],
      ["humidity", "Humidity", "climate", "64 %", "80–85 %", "below", "Below typical range"],
      ["rainfall", "Rainfall", "climate", "202.9 mm", "183–298 mm", "within", "Within typical range"],
    ].map(([key, label, group, value_text, typical_text, status, status_label]) => ({
      key,
      label,
      group,
      value_text,
      typical_text,
      status,
      status_label,
      chart: { band_start: 40, band_width: 20, marker: 50, scale_min: "0", scale_max: "100" },
    })),
  },
  warnings: [],
  limitations: ["The recommendation uses only the seven readings you entered."],
};
