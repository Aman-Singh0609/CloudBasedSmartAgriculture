// Page-level tests: the real components and router, with a fake backend.

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CONFIG, FEATURES, RESULT, SIGNED_IN, USER, fail, mockApi, ok, renderApp } from "./helpers.jsx";

const EXAMPLE = Object.fromEntries(FEATURES.map((spec) => [spec.key, String(spec.example)]));

async function fillReadings(user, values = EXAMPLE) {
  for (const spec of FEATURES) {
    const input = screen.getByLabelText(new RegExp(`^${spec.label.replace(/[()]/g, "\\$&")}`));
    await user.clear(input);
    if (values[spec.key] !== "") await user.type(input, values[spec.key]);
  }
}

describe("landing page", () => {
  it("renders the main message, call to action and limitations without logging in", async () => {
    mockApi();
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1, name: /make smarter crop decisions/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /analyze my soil/i })[0]).toHaveAttribute("href", "/advisor");
    expect(screen.getByText(/what this tool cannot tell you/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /create account/i })).toHaveAttribute("href", "/signup");
    expect(screen.getByRole("link", { name: /skip to main content/i })).toBeInTheDocument();
  });

  it("still renders when the backend is unreachable", async () => {
    mockApi({ "GET /api/config": new TypeError("Failed to fetch"), "GET /api/auth/me": new TypeError("Failed to fetch") });
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("shows the signed-in navigation for a logged-in user", async () => {
    mockApi(SIGNED_IN);
    renderApp("/");
    expect(await screen.findByText("Asha Patel")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Crop Advisor" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^log in$/i })).not.toBeInTheDocument();
  });
});

describe("protected pages", () => {
  it("send anonymous visitors to log in, remembering where they were going", async () => {
    mockApi();
    renderApp("/history?page=2");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("show a retry when the session check cannot reach the server", async () => {
    mockApi({ "GET /api/auth/me": new TypeError("Failed to fetch") });
    renderApp("/advisor");
    expect(await screen.findByRole("heading", { name: /could not reach the server/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("explain a database outage in the server's own words", async () => {
    mockApi({ "GET /api/auth/me": fail(503, "DATABASE_UNAVAILABLE", "We cannot reach our database right now. Please try again in a few minutes.") });
    renderApp("/history");
    expect(await screen.findByRole("heading", { name: /service temporarily unavailable/i })).toBeInTheDocument();
    expect(screen.getByText(/cannot reach our database right now/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("unknown addresses show a not-found page", async () => {
    mockApi();
    renderApp("/no/such/page");
    expect(await screen.findByRole("heading", { name: /page not found/i })).toBeInTheDocument();
  });
});

describe("login", () => {
  it("logs in and continues to the page originally requested", async () => {
    const user = userEvent.setup();
    let loggedIn = false;
    const calls = mockApi({
      "GET /api/auth/me": () => ok({ authenticated: loggedIn, user: loggedIn ? USER : null }),
      "POST /api/auth/login": () => {
        loggedIn = true;
        return ok({ user: USER });
      },
      "GET /api/predictions": ok({ items: [], pagination: { page: 1, per_page: 10, total: 0, total_pages: 1, has_previous: false, has_next: false } }),
      "GET /api/predictions/trends": ok({ trends: [] }),
    });
    renderApp("/login?next=%2Fhistory");
    await user.type(await screen.findByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "Tr4ctor-Lane-Meadow");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    expect(await screen.findByRole("heading", { name: /no analyses yet/i })).toBeInTheDocument();
    const login = calls.find((call) => call.path === "/api/auth/login");
    expect(login.body).toEqual({ email: "asha@example.test", password: "Tr4ctor-Lane-Meadow" });
    expect(login.headers["X-CSRFToken"]).toBe("csrf-1");
  });

  it("ignores a next address that points to another site", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/auth/login": ok({ user: USER }) });
    renderApp("/login?next=https%3A%2F%2Fevil.example%2Fphish");
    await user.type(await screen.findByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "Tr4ctor-Lane-Meadow");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));
    expect(await screen.findByRole("heading", { name: "Crop Advisor" })).toBeInTheDocument();
  });

  it("shows the server's generic message for bad credentials and clears the password", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/auth/login": fail(401, "INVALID_CREDENTIALS", "Invalid email or password.") });
    renderApp("/login");
    await user.type(await screen.findByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password-1");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid email or password.");
    expect(alert).toHaveFocus();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Email address")).toHaveValue("asha@example.test");
  });

  it("explains rate limiting", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/auth/login": fail(429, "RATE_LIMITED", "You have made too many requests in a short time.") });
    renderApp("/login");
    await user.type(await screen.findByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "whatever-1");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many requests/i);
  });

  it("can reveal and hide the password", async () => {
    const user = userEvent.setup();
    mockApi();
    renderApp("/login");
    const password = await screen.findByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: /show password/i }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: /hide password/i }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("hides the forgot-password link when the server cannot send email", async () => {
    mockApi({ "GET /api/config": ok({ ...CONFIG, password_reset_enabled: false }) });
    renderApp("/login");
    await screen.findByLabelText("Email address");
    await waitFor(() => expect(screen.queryByRole("link", { name: /forgot your password/i })).not.toBeInTheDocument());
  });
});

describe("signup", () => {
  it("validates in the browser before contacting the server", async () => {
    const user = userEvent.setup();
    const calls = mockApi();
    renderApp("/signup");
    await user.click(await screen.findByRole("button", { name: /create account/i }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveFocus();
    expect(within(summary).getByText("Full name is required.")).toBeInTheDocument();
    expect(within(summary).getByText("Email address is required.")).toBeInTheDocument();
    expect(within(summary).getByText("Password is required.")).toBeInTheDocument();
    expect(screen.getByLabelText("Full name")).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("checks that the two passwords match", async () => {
    const user = userEvent.setup();
    mockApi();
    renderApp("/signup");
    await user.type(await screen.findByLabelText("Full name"), "Asha Patel");
    await user.type(screen.getByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "Tr4ctor-Lane-Meadow");
    await user.type(screen.getByLabelText("Confirm password"), "Different-Pass-9");
    await user.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findAllByText("The two passwords do not match.")).not.toHaveLength(0);
  });

  it("creates the account and sends the user to log in", async () => {
    const user = userEvent.setup();
    const calls = mockApi({ "POST /api/auth/signup": ok({ user: USER }, 201) });
    renderApp("/signup");
    await user.type(await screen.findByLabelText("Full name"), "Asha Patel");
    await user.type(screen.getByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "Tr4ctor-Lane-Meadow");
    await user.type(screen.getByLabelText("Confirm password"), "Tr4ctor-Lane-Meadow");
    await user.click(screen.getByRole("button", { name: /create account/i }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/account has been created/i);
    const body = calls.find((call) => call.path === "/api/auth/signup").body;
    expect(Object.keys(body).sort()).toEqual(["confirm_password", "email", "name", "password"]);
  });

  it("shows server-side problems next to the right field", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/auth/signup": fail(409, "EMAIL_ALREADY_REGISTERED", "An account with this email address already exists. Try logging in instead.") });
    renderApp("/signup");
    await user.type(await screen.findByLabelText("Full name"), "Asha Patel");
    await user.type(screen.getByLabelText("Email address"), "asha@example.test");
    await user.type(screen.getByLabelText("Password"), "Tr4ctor-Lane-Meadow");
    await user.type(screen.getByLabelText("Confirm password"), "Tr4ctor-Lane-Meadow");
    await user.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => expect(screen.getByLabelText("Email address")).toHaveAttribute("aria-invalid", "true"));
    expect(screen.getAllByText(/already exists/i).length).toBeGreaterThan(0);
  });

  it("does not ask for a government ID or phone number", async () => {
    mockApi();
    renderApp("/signup");
    await screen.findByLabelText("Full name");
    expect(screen.queryByLabelText(/govt|government|aadhaar|pan card|phone/i)).not.toBeInTheDocument();
  });
});

describe("crop advisor", () => {
  it("groups the seven inputs with labels, hints and ranges", async () => {
    mockApi(SIGNED_IN);
    renderApp("/advisor");
    expect(await screen.findByRole("group", { name: /soil nutrients/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /soil condition/i })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: /climate/i })).toBeInTheDocument();
    for (const spec of FEATURES) {
      const input = screen.getByLabelText(new RegExp(`^${spec.label.replace(/[()]/g, "\\$&")}`));
      expect(input).toHaveAttribute("inputmode", "decimal");
      expect(input).toHaveAccessibleDescription(expect.stringContaining("Model reference range"));
    }
    expect(screen.getByText(/rainfall is never filled in from the weather lookup/i)).toBeInTheDocument();
  });

  it("blocks submission and lists every problem when fields are empty or invalid", async () => {
    const user = userEvent.setup();
    const calls = mockApi(SIGNED_IN);
    renderApp("/advisor");
    await screen.findByRole("group", { name: /soil nutrients/i });
    await user.type(screen.getByLabelText(/^Soil pH/), "15");
    await user.type(screen.getByLabelText(/^Nitrogen/), "abc");
    await user.click(screen.getByRole("button", { name: /get recommendation/i }));

    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(summary).toHaveFocus());
    expect(within(summary).getByText("Soil pH must be between 0 and 14.")).toBeInTheDocument();
    expect(within(summary).getByText("Nitrogen (N) must be a number, for example 90.")).toBeInTheDocument();
    expect(within(summary).getByText("Rainfall is required.")).toBeInTheDocument();
    expect(within(summary).getAllByRole("link")).toHaveLength(7);
    expect(screen.getByLabelText(/^Soil pH/)).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("warns about values outside the model range without blocking them", async () => {
    const user = userEvent.setup();
    mockApi(SIGNED_IN);
    renderApp("/advisor");
    const rainfall = await screen.findByLabelText(/^Rainfall/);
    await user.type(rainfall, "900");
    await user.tab();
    expect(await screen.findByText(/outside the model's reference range \(20\.2–298\.6 mm\)/i)).toBeInTheDocument();
    expect(rainfall).not.toHaveAttribute("aria-invalid");
  });

  it("submits valid readings, shows a loading state, then opens the result", async () => {
    const user = userEvent.setup();
    let release;
    const pending = new Promise((resolve) => {
      release = resolve;
    });
    const calls = mockApi({
      ...SIGNED_IN,
      "POST /api/predictions": ok({ id: RESULT.id }, 201),
      [`GET /api/predictions/${RESULT.id}`]: ok(RESULT),
    });
    const realFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
      const response = await realFetch(url, options);
      if (options && options.method === "POST" && String(url) === "/api/predictions") await pending;
      return response;
    };

    renderApp("/advisor");
    await screen.findByRole("group", { name: /soil nutrients/i });
    await user.click(screen.getByRole("button", { name: /fill with example values/i }));
    const submit = screen.getByRole("button", { name: /get recommendation/i });
    await user.click(submit);

    expect(await screen.findByText(/analyzing soil and climate data/i)).toBeInTheDocument();
    expect(submit).toBeDisabled();
    await user.click(submit); // a second click while busy must not send again
    release();

    expect(await screen.findByRole("heading", { level: 1, name: "Rice" })).toBeInTheDocument();
    const posts = calls.filter((call) => call.method === "POST" && call.path === "/api/predictions");
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual(EXAMPLE);
    expect(posts[0].headers["X-CSRFToken"]).toBe("csrf-1");
  });

  it("shows errors returned by the backend against the right fields", async () => {
    const user = userEvent.setup();
    mockApi({
      ...SIGNED_IN,
      "POST /api/predictions": fail(422, "INVALID_INPUT", "Please correct the highlighted readings.", { humidity: "Humidity must be between 0 and 100 %." }),
    });
    renderApp("/advisor");
    await screen.findByRole("group", { name: /soil nutrients/i });
    await fillReadings(user);
    await user.click(screen.getByRole("button", { name: /get recommendation/i }));
    const summary = await screen.findByRole("alert");
    expect(within(summary).getByText("Humidity must be between 0 and 100 %.")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Humidity/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: /get recommendation/i })).toBeEnabled();
  });

  it("keeps the entered values and explains when the network fails", async () => {
    const user = userEvent.setup();
    mockApi({ ...SIGNED_IN, "POST /api/predictions": new TypeError("Failed to fetch") });
    renderApp("/advisor");
    await screen.findByRole("group", { name: /soil nutrients/i });
    await fillReadings(user);
    await user.click(screen.getByRole("button", { name: /get recommendation/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not reach the server.*your values are still here/i);
    expect(screen.getByLabelText(/^Soil pH/)).toHaveValue("6.5");
    expect(screen.getByRole("button", { name: /get recommendation/i })).toBeEnabled();
  });

  it("fills temperature and humidity from the weather lookup and labels their source", async () => {
    const user = userEvent.setup();
    const calls = mockApi({
      ...SIGNED_IN,
      "GET /api/weather": ok({ location: "Pune, IN", temperature: 27.3, humidity: 64, rainfall: null, observed_rain_last_hour_mm: 0.4 }),
    });
    renderApp("/advisor");
    await user.type(await screen.findByLabelText(/look up current weather/i), "Pune{Enter}");

    await waitFor(() => expect(screen.getByLabelText(/^Temperature/)).toHaveValue("27.3"));
    expect(screen.getByLabelText(/^Humidity/)).toHaveValue("64");
    expect(screen.getByLabelText(/^Rainfall/)).toHaveValue("");
    expect(screen.getAllByText("From weather service: Pune, IN")).toHaveLength(2);
    expect(screen.getByText(/please enter rainfall yourself/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/weather?city=Pune")).toBe(true);
    expect(calls.some((call) => call.method === "POST")).toBe(false); // Enter did not submit the form

    await user.type(screen.getByLabelText(/^Temperature/), "9");
    expect(screen.getByText("Edited by you")).toBeInTheDocument();
  });

  it("keeps manual entry available when the weather lookup fails", async () => {
    const user = userEvent.setup();
    mockApi({
      ...SIGNED_IN,
      "GET /api/weather": fail(503, "WEATHER_UNAVAILABLE", "The weather service is not available right now. Please enter temperature and humidity manually."),
    });
    renderApp("/advisor");
    await user.type(await screen.findByLabelText(/look up current weather/i), "Pune");
    await user.click(screen.getByRole("button", { name: /fetch weather/i }));
    expect(await screen.findByText(/weather service is not available right now/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Temperature/)).toBeEnabled();
    expect(screen.getByRole("button", { name: /fetch weather/i })).toBeEnabled();
  });

  it("explains when weather lookup is not configured", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/config": ok({ ...CONFIG, weather_enabled: false }) });
    renderApp("/advisor");
    expect(await screen.findByText(/weather lookup is not set up on this server/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /fetch weather/i })).not.toBeInTheDocument();
  });

  it("disables submission when the model is unavailable", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/config": ok({ ...CONFIG, model_available: false }) });
    renderApp("/advisor");
    expect(await screen.findByText(/recommendations are temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /get recommendation/i })).toBeDisabled();
  });
});

describe("result page", () => {
  const route = { ...SIGNED_IN, [`GET /api/predictions/${RESULT.id}`]: ok(RESULT) };

  it("separates the machine-learning result from the rule-based analysis", async () => {
    mockApi(route);
    renderApp(`/history/${RESULT.id}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Rice" })).toBeInTheDocument();
    expect(screen.getByText("82%")).toBeInTheDocument();
    expect(screen.getByText("Strong model agreement")).toBeInTheDocument();
    expect(screen.getByText(/it is not the chance of a successful harvest/i)).toBeInTheDocument();
    expect(screen.getByText(/machine-learning recommendation/i)).toBeInTheDocument();
    expect(screen.getAllByText("Rule-based, not ML").length).toBeGreaterThanOrEqual(3);

    const alternatives = screen.getByRole("region", { name: /alternative crops/i });
    expect(within(alternatives).getByText("Jute")).toBeInTheDocument();
    expect(within(alternatives).getByText("12%")).toBeInTheDocument();
    expect(within(alternatives).getByText("Maize")).toBeInTheDocument();
  });

  it("describes each reading in text, not by colour alone", async () => {
    mockApi(route);
    renderApp(`/history/${RESULT.id}`);
    const soil = await screen.findByRole("region", { name: "Soil analysis" });
    expect(within(soil).getByText("Below typical range")).toBeInTheDocument();
    expect(
      within(soil).getByRole("img", { name: "Phosphorus (P): 20. Typical range for this crop: 35–60. Below typical range." }),
    ).toBeInTheDocument();
    expect(within(soil).getByText(/classed as neutral/i)).toBeInTheDocument();
  });

  it("shows where each climate value came from", async () => {
    mockApi(route);
    renderApp(`/history/${RESULT.id}`);
    const climate = await screen.findByRole("region", { name: "Climate summary" });
    expect(within(climate).getAllByText("From weather service")).toHaveLength(2);
    expect(within(climate).getAllByText("Entered by you")).toHaveLength(1);
    expect(within(climate).getByText("Pune, IN")).toBeInTheDocument();
  });

  it("shows crop information, suggestions and limitations", async () => {
    mockApi(route);
    renderApp(`/history/${RESULT.id}`);
    expect(await screen.findByRole("region", { name: /about rice/i })).toHaveTextContent("Rice is the staple food");
    expect(screen.getByRole("region", { name: /what you could improve/i })).toHaveTextContent("Phosphorus is below");
    expect(screen.getByRole("region", { name: /limitations/i })).toHaveTextContent("only the seven readings");
  });

  it("flags reduced reliability and lists cautions", async () => {
    mockApi({
      ...SIGNED_IN,
      [`GET /api/predictions/${RESULT.id}`]: ok({ ...RESULT, reliability: "reduced", warnings: ["Rainfall (900 mm) is outside the range the model was trained on."] }),
    });
    renderApp(`/history/${RESULT.id}`);
    expect(await screen.findByText("Reduced reliability")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /cautions for this result/i })).toHaveTextContent("Rainfall (900 mm)");
  });

  it("handles an older analysis that has no confidence recorded", async () => {
    mockApi({
      ...SIGNED_IN,
      [`GET /api/predictions/${RESULT.id}`]: ok({ ...RESULT, confidence: null, confidence_band: "unknown", confidence_label: "Not recorded", alternatives: [] }),
    });
    renderApp(`/history/${RESULT.id}`);
    expect(await screen.findByText("Not recorded")).toBeInTheDocument();
    expect(screen.getByText(/alternatives were not recorded/i)).toBeInTheDocument();
  });

  it("shows a not-found page for an analysis that does not exist or belongs to someone else", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/predictions/ffffffffffffffffffffffff": fail(404, "NOT_FOUND", "That analysis could not be found.") });
    renderApp("/history/ffffffffffffffffffffffff");
    expect(await screen.findByRole("heading", { name: /analysis not found/i })).toBeInTheDocument();
  });

  it("offers a retry when the server has a problem", async () => {
    mockApi({ ...SIGNED_IN, [`GET /api/predictions/${RESULT.id}`]: fail(503, "DATABASE_UNAVAILABLE", "We cannot reach our database right now.") });
    renderApp(`/history/${RESULT.id}`);
    expect(await screen.findByText(/cannot reach our database/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("asks for confirmation before deleting", async () => {
    const user = userEvent.setup();
    const calls = mockApi({
      ...route,
      [`DELETE /api/predictions/${RESULT.id}`]: ok({ id: RESULT.id }),
      "GET /api/predictions": ok({ items: [], pagination: { page: 1, per_page: 10, total: 0, total_pages: 1, has_previous: false, has_next: false } }),
      "GET /api/predictions/trends": ok({ trends: [] }),
    });
    renderApp(`/history/${RESULT.id}`);
    await user.click(await screen.findByRole("button", { name: /^delete$/i }));
    const dialog = screen.getByRole("dialog", { name: /delete this analysis/i });
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    await user.click(screen.getByRole("button", { name: /^delete$/i }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /delete/i }));
    expect(await screen.findByRole("heading", { name: /no analyses yet/i })).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
  });
});

describe("history", () => {
  const item = (id, crop, extra = {}) => ({
    id,
    recommended_crop: crop.toLowerCase(),
    crop_name: crop,
    confidence: 0.9,
    inputs: { nitrogen: 90, phosphorus: 42, potassium: 43, ph: 6.5, temperature: 20.8, humidity: 82, rainfall: 202.9 },
    reliability: "normal",
    created_at: "2026-11-10T12:00:00Z",
    ...extra,
  });
  const page = (items, pagination = {}) =>
    ok({ items, pagination: { page: 1, per_page: 10, total: items.length, total_pages: 1, has_previous: false, has_next: false, ...pagination } });

  it("shows an empty state with a call to action", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/predictions": page([]), "GET /api/predictions/trends": ok({ trends: [] }) });
    renderApp("/history");
    expect(await screen.findByRole("heading", { name: /no analyses yet/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /analyze my soil/i })).toHaveAttribute("href", "/advisor");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("lists analyses with every reading and a link to open each one", async () => {
    mockApi({
      ...SIGNED_IN,
      "GET /api/predictions": page([item("a1", "Rice"), item("a2", "Maize", { reliability: "reduced", confidence: null })]),
      "GET /api/predictions/trends": ok({
        trends: [{ key: "nitrogen", label: "Nitrogen (N)", count: 2, latest_text: "90", first_text: "60", min_text: "60", max_text: "90", change: "up", points: "0,28 100,2", values_text: "60, 90" }],
      }),
    });
    renderApp("/history");
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers.slice(0, 10)).toEqual(["Date", "Recommended crop", "Confidence", "N", "P", "K", "pH", "Temp (°C)", "Humidity (%)", "Rain (mm)"]);
    expect(within(table).getByRole("link", { name: "Rice" })).toHaveAttribute("href", "/history/a1");
    expect(within(table).getByText("Reduced reliability")).toBeInTheDocument();
    expect(within(table).getByText("—")).toBeInTheDocument();
    expect(within(table).getAllByText("202.9")).toHaveLength(2);
    expect(screen.getByRole("img", { name: "Nitrogen (N) over your last 2 analyses, oldest to newest: 60, 90." })).toBeInTheDocument();
    expect(screen.getByText(/up from 60/)).toBeInTheDocument();
  });

  it("paginates", async () => {
    const calls = mockApi({
      ...SIGNED_IN,
      "GET /api/predictions?page=2": page([item("b1", "Jute")], { page: 2, total: 11, total_pages: 2, has_previous: true }),
      "GET /api/predictions/trends": ok({ trends: [] }),
    });
    renderApp("/history?page=2");
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /newer/i })).toHaveAttribute("href", "/history");
    expect(screen.getByText(/older/i).closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
    expect(calls.some((call) => call.path === "/api/predictions?page=2")).toBe(true);
  });

  it("deletes after confirmation and refreshes the list", async () => {
    const user = userEvent.setup();
    let deleted = false;
    const calls = mockApi({
      ...SIGNED_IN,
      "GET /api/predictions": () => page(deleted ? [item("a2", "Maize")] : [item("a1", "Rice"), item("a2", "Maize")]),
      "GET /api/predictions/trends": ok({ trends: [] }),
      "DELETE /api/predictions/a1": () => {
        deleted = true;
        return ok({ id: "a1" });
      },
    });
    renderApp("/history");
    await user.click(await screen.findByRole("button", { name: "Delete Rice analysis" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("the rice analysis");
    await user.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(screen.queryByRole("link", { name: "Rice" })).not.toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Maize" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/rice analysis has been deleted/i);
    const request = calls.find((call) => call.method === "DELETE");
    expect(request.headers["X-CSRFToken"]).toBe("csrf-1");
  });

  it("offers a retry when the list cannot be loaded", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/predictions": fail(503, "DATABASE_UNAVAILABLE", "We cannot reach our database right now."), "GET /api/predictions/trends": ok({ trends: [] }) });
    renderApp("/history");
    expect(await screen.findByText(/cannot reach our database/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("returns to the login page when the session has expired", async () => {
    mockApi({ ...SIGNED_IN, "GET /api/predictions": fail(401, "AUTHENTICATION_REQUIRED", "Please log in to continue."), "GET /api/predictions/trends": fail(401, "AUTHENTICATION_REQUIRED", "Please log in to continue.") });
    renderApp("/history");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });
});

describe("password reset", () => {
  it("always shows the same confirmation after a request", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/auth/forgot-password": ok({}, 202) });
    renderApp("/forgot-password");
    await user.type(await screen.findByLabelText("Email address"), "asha@example.test");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/if an account exists for that email address/i);
  });

  it("explains when reset by email is not available", async () => {
    mockApi({ "GET /api/config": ok({ ...CONFIG, password_reset_enabled: false }) });
    renderApp("/forgot-password");
    expect(await screen.findByRole("heading", { name: /password reset is not available/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send reset link/i })).not.toBeInTheDocument();
  });

  it("rejects an invalid or expired link", async () => {
    mockApi({ "GET /api/auth/reset-password/badtoken": fail(422, "INVALID_RESET_TOKEN", "This password reset link is invalid or has expired. Please request a new one.") });
    renderApp("/reset-password/badtoken");
    expect(await screen.findByRole("heading", { name: /link not valid/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /request a new link/i })).toHaveAttribute("href", "/forgot-password");
  });

  it("changes the password with a valid link", async () => {
    const user = userEvent.setup();
    const calls = mockApi({
      "GET /api/auth/reset-password/goodtoken": ok({ valid: true }),
      "POST /api/auth/reset-password/goodtoken": ok({}),
    });
    renderApp("/reset-password/goodtoken");
    await user.type(await screen.findByLabelText("New password"), "Orch4rd-Gate-Pasture");
    await user.type(screen.getByLabelText("Confirm new password"), "Orch4rd-Gate-Pasture");
    await user.click(screen.getByRole("button", { name: /change password/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/password has been changed/i);
    expect(calls.find((call) => call.method === "POST").body).toEqual({ password: "Orch4rd-Gate-Pasture", confirm_password: "Orch4rd-Gate-Pasture" });
  });
});

describe("logging out", () => {
  it("from a protected page goes to the home page, not to a login prompt", async () => {
    const user = userEvent.setup();
    mockApi({
      ...SIGNED_IN,
      "POST /api/auth/logout": ok({}),
      "GET /api/predictions": ok({ items: [], pagination: { page: 1, per_page: 10, total: 0, total_pages: 1, has_previous: false, has_next: false } }),
      "GET /api/predictions/trends": ok({ trends: [] }),
    });
    renderApp("/history");
    await screen.findByRole("heading", { name: /no analyses yet/i });
    await user.click(screen.getByRole("button", { name: /log out/i }));
    expect(await screen.findByRole("heading", { level: 1, name: /make smarter crop decisions/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/you have been logged out/i);
    await waitFor(() => expect(screen.getByRole("link", { name: /^log in$/i })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "Log in" })).not.toBeInTheDocument();
  });

  it("ends the session and returns to the home page", async () => {
    const user = userEvent.setup();
    const calls = mockApi({ ...SIGNED_IN, "POST /api/auth/logout": ok({}) });
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /log out/i }));
    expect(await screen.findByRole("link", { name: /^log in$/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/you have been logged out/i);
    expect(calls.some((call) => call.method === "POST" && call.path === "/api/auth/logout")).toBe(true);
  });
});
