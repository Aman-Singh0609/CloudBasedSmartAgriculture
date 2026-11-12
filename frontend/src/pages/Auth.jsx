// Account pages: sign up, log in, request a password reset, choose a new password.

import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api/client.js";
import { Alert, ErrorSummary, Icon, PageLoading, Spinner, TextField, useDocumentTitle } from "../components/ui.jsx";
import { useAuth } from "../context/AuthContext.jsx";
import { useConfig } from "../context/ConfigContext.jsx";
import { safeNextPath } from "../lib/format.js";
import { emailProblem, nameProblem, passwordProblem } from "../lib/validation.js";

const FIELD_ORDER = ["name", "email", "password", "confirm_password"];

function toErrorList(errors) {
  return FIELD_ORDER.filter((field) => errors[field]).map((field) => ({ field, message: errors[field] }));
}

/** Turn an API failure into per-field errors plus an optional general message. */
function readApiError(error) {
  if (error instanceof ApiError) {
    if (error.status === 422 && error.details) {
      return { fields: error.details, general: null };
    }
    if (error.code === "EMAIL_ALREADY_REGISTERED") {
      return { fields: { email: error.message }, general: null };
    }
    return { fields: {}, general: error.message };
  }
  return { fields: {}, general: "Something went wrong. Please try again." };
}

function useFocusWhen(condition) {
  const ref = useRef(null);
  useEffect(() => {
    if (condition && ref.current) ref.current.focus();
  }, [condition]);
  return ref;
}

function AuthCard({ title, intro, children }) {
  return (
    <div className="container container--narrow page">
      <div className="card stack">
        <div>
          <h1>{title}</h1>
          {intro ? <p className="muted">{intro}</p> : null}
        </div>
        {children}
      </div>
    </div>
  );
}

function SubmitButton({ busy, children }) {
  return (
    <button className="btn btn--block" type="submit" disabled={busy}>
      {busy ? <Spinner /> : null} {children}
    </button>
  );
}

// --------------------------------------------------------------------- signup
export function Signup() {
  useDocumentTitle("Create account");
  const { user, status } = useAuth();
  const navigate = useNavigate();
  const [values, setValues] = useState({ name: "", email: "", password: "", confirm_password: "" });
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const summaryRef = useFocusWhen(attempt > 0 && Object.keys(errors).length > 0);

  if (status === "ready" && user) {
    return <Navigate to="/advisor" replace />;
  }

  const set = (field) => (value) => setValues((current) => ({ ...current, [field]: value }));

  const validate = () => {
    const found = {};
    const name = nameProblem(values.name);
    const email = emailProblem(values.email);
    const password = passwordProblem(values.password);
    if (name) found.name = name;
    if (email) found.email = email;
    if (password) found.password = password;
    else if (values.confirm_password !== values.password) {
      found.confirm_password = "The two passwords do not match.";
    }
    return found;
  };

  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setGeneral(null);
    const found = validate();
    setErrors(found);
    setAttempt((count) => count + 1);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      await api.signup(values);
      navigate("/login", {
        state: { notice: { kind: "success", text: "Your account has been created. Please log in." } },
      });
    } catch (error) {
      const parsed = readApiError(error);
      setErrors(parsed.fields);
      setGeneral(parsed.general);
      setAttempt((count) => count + 1);
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Create your account" intro="Save your soil analyses and see how your readings change over time.">
      {general ? (
        <Alert kind="error" focusOnMount>
          {general}
        </Alert>
      ) : null}
      <ErrorSummary errors={toErrorList(errors)} summaryRef={summaryRef} />

      <form className="stack" onSubmit={onSubmit} noValidate>
        <TextField id="name" label="Full name" value={values.name} onChange={set("name")} error={errors.name} autoComplete="name" maxLength={80} />
        <TextField
          id="email"
          label="Email address"
          type="email"
          value={values.email}
          onChange={set("email")}
          error={errors.email}
          autoComplete="email"
          maxLength={254}
          hint="Used to log in. We do not send marketing email."
        />
        <TextField
          id="password"
          label="Password"
          type="password"
          value={values.password}
          onChange={set("password")}
          error={errors.password}
          autoComplete="new-password"
          maxLength={128}
          revealable
          hint="At least 8 characters, with at least one letter and one number."
        />
        <TextField
          id="confirm_password"
          label="Confirm password"
          type="password"
          value={values.confirm_password}
          onChange={set("confirm_password")}
          error={errors.confirm_password}
          autoComplete="new-password"
          maxLength={128}
        />
        <SubmitButton busy={busy}>Create account</SubmitButton>
      </form>

      <p className="small muted form-footer">
        <Icon name="lock" /> We only ask for what the service needs. No government ID or phone number is collected.
      </p>
      <p className="form-footer">
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </AuthCard>
  );
}

// ---------------------------------------------------------------------- login
export function Login() {
  useDocumentTitle("Log in");
  const { user, status, login } = useAuth();
  const { config } = useConfig();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const nextPath = safeNextPath(searchParams.get("next"));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  if (status === "ready" && user && !busy) {
    return <Navigate to={nextPath} replace />;
  }

  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    if (!email.trim() || !password) {
      setError("Enter your email address and password.");
      setAttempt((count) => count + 1);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(nextPath, { replace: true });
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Something went wrong. Please try again.");
      setAttempt((count) => count + 1);
      setPassword("");
      setBusy(false);
    }
  };

  return (
    <AuthCard title="Log in" intro="Welcome back. Log in to run a new analysis or view your history.">
      {error ? (
        <Alert kind="error" focusOnMount key={attempt}>
          {error}
        </Alert>
      ) : null}

      <form className="stack" onSubmit={onSubmit} noValidate>
        <TextField id="email" label="Email address" type="email" value={email} onChange={setEmail} autoComplete="username" maxLength={254} />
        <TextField
          id="password"
          label="Password"
          type="password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          maxLength={128}
          revealable
        />
        <SubmitButton busy={busy}>Log in</SubmitButton>
      </form>

      {config?.password_reset_enabled ? (
        <p className="form-footer">
          <Link to="/forgot-password">Forgot your password?</Link>
        </p>
      ) : null}
      <p className="form-footer">
        New here? <Link to="/signup">Create an account</Link>
      </p>
    </AuthCard>
  );
}

// ------------------------------------------------------------ forgot password
function ResetUnavailable() {
  return (
    <AuthCard title="Password reset is not available">
      <Alert kind="info">
        Password reset by email is not set up on this server. Please contact the site administrator if you cannot
        log in.
      </Alert>
      <p className="form-footer">
        <Link to="/login">Back to log in</Link>
      </p>
    </AuthCard>
  );
}

export function ForgotPassword() {
  useDocumentTitle("Reset your password");
  const { status, config } = useConfig();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [error, setError] = useState(null);
  const [general, setGeneral] = useState(null);
  const [busy, setBusy] = useState(false);

  if (status === "loading") return <PageLoading />;
  if (status === "ready" && !config.password_reset_enabled) return <ResetUnavailable />;

  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setGeneral(null);
    const problem = emailProblem(email);
    setError(problem);
    if (problem) {
      document.getElementById("email")?.focus();
      return;
    }
    setBusy(true);
    try {
      await api.forgotPassword(email);
      navigate("/login", {
        state: {
          notice: {
            kind: "info",
            text: "If an account exists for that email address, we have sent a link to reset the password. The link is valid for a limited time.",
          },
        },
      });
    } catch (failure) {
      const parsed = readApiError(failure);
      setError(parsed.fields.email || null);
      setGeneral(parsed.general);
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title="Reset your password"
      intro="Enter the email address you signed up with. If it matches an account, we will email a link to choose a new password."
    >
      {general ? (
        <Alert kind="error" focusOnMount>
          {general}
        </Alert>
      ) : null}
      <form className="stack" onSubmit={onSubmit} noValidate>
        <TextField id="email" label="Email address" type="email" value={email} onChange={setEmail} error={error} autoComplete="email" maxLength={254} />
        <SubmitButton busy={busy}>Send reset link</SubmitButton>
      </form>
      <p className="form-footer">
        Remembered it? <Link to="/login">Back to log in</Link>
      </p>
    </AuthCard>
  );
}

// ------------------------------------------------------------- reset password
export function ResetPassword() {
  useDocumentTitle("Choose a new password");
  const { token } = useParams();
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [linkState, setLinkState] = useState({ status: "checking", message: null });
  const [values, setValues] = useState({ password: "", confirm_password: "" });
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const summaryRef = useFocusWhen(attempt > 0 && Object.keys(errors).length > 0);

  useEffect(() => {
    let cancelled = false;
    api
      .checkResetToken(token)
      .then(() => !cancelled && setLinkState({ status: "valid", message: null }))
      .catch((error) => {
        if (cancelled) return;
        const message =
          error instanceof ApiError && error.status < 500 && error.status !== 0
            ? error.status === 404
              ? "Password reset is not available on this server."
              : error.message
            : "We could not check this link right now. Please try again in a moment.";
        setLinkState({ status: "invalid", message });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (linkState.status === "checking") return <PageLoading label="Checking your reset link…" />;

  if (linkState.status === "invalid") {
    return (
      <AuthCard title="Link not valid">
        <Alert kind="error">{linkState.message}</Alert>
        <div className="form-actions">
          <Link className="btn" to="/forgot-password">
            Request a new link
          </Link>
          <Link className="btn btn--ghost" to="/login">
            Back to log in
          </Link>
        </div>
      </AuthCard>
    );
  }

  const set = (field) => (value) => setValues((current) => ({ ...current, [field]: value }));

  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setGeneral(null);
    const found = {};
    const password = passwordProblem(values.password);
    if (password) found.password = password;
    else if (values.confirm_password !== values.password) found.confirm_password = "The two passwords do not match.";
    setErrors(found);
    setAttempt((count) => count + 1);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      await api.resetPassword(token, values);
      await refresh(); // the server signed this browser out
      navigate("/login", {
        state: {
          notice: { kind: "success", text: "Your password has been changed. Please log in with the new password." },
        },
      });
    } catch (error) {
      if (error instanceof ApiError && error.code === "INVALID_RESET_TOKEN") {
        setLinkState({ status: "invalid", message: error.message });
        return;
      }
      const parsed = readApiError(error);
      setErrors(parsed.fields);
      setGeneral(parsed.general);
      setAttempt((count) => count + 1);
      setBusy(false);
    }
  };

  return (
    <AuthCard
      title="Choose a new password"
      intro="After changing your password you will be logged out everywhere and need to log in again."
    >
      {general ? (
        <Alert kind="error" focusOnMount>
          {general}
        </Alert>
      ) : null}
      <ErrorSummary errors={toErrorList(errors)} summaryRef={summaryRef} />
      <form className="stack" onSubmit={onSubmit} noValidate>
        <TextField
          id="password"
          label="New password"
          type="password"
          value={values.password}
          onChange={set("password")}
          error={errors.password}
          autoComplete="new-password"
          maxLength={128}
          revealable
          hint="At least 8 characters, with at least one letter and one number."
        />
        <TextField
          id="confirm_password"
          label="Confirm new password"
          type="password"
          value={values.confirm_password}
          onChange={set("confirm_password")}
          error={errors.confirm_password}
          autoComplete="new-password"
          maxLength={128}
        />
        <SubmitButton busy={busy}>Change password</SubmitButton>
      </form>
    </AuthCard>
  );
}
