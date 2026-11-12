import { Component } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import { PageError, PageLoading } from "./components/ui.jsx";
import { useAuth } from "./context/AuthContext.jsx";
import Advisor from "./pages/Advisor.jsx";
import { ForgotPassword, Login, ResetPassword, Signup } from "./pages/Auth.jsx";
import History from "./pages/History.jsx";
import Landing from "./pages/Landing.jsx";
import NotFound from "./pages/NotFound.jsx";
import Result from "./pages/Result.jsx";

/** Catches rendering errors so a bug shows a recoverable message, not a blank page. */
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    console.error("Unexpected rendering error", error);
  }

  render() {
    if (this.state.failed) {
      return (
        <PageError
          title="Something went wrong"
          message="This page could not be displayed. Reloading usually fixes it."
          onRetry={() => window.location.reload()}
        />
      );
    }
    return this.props.children;
  }
}

/** Only render children for a signed-in user; otherwise send them to log in and back. */
export function RequireAuth({ children }) {
  const { status, user, error, refresh } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <PageLoading label="Checking your session…" />;
  }
  if (status === "error") {
    // status 0 = the request never got an answer; anything else is the
    // server explaining its own problem (for example, database unavailable).
    const answered = error && error.status > 0;
    return (
      <PageError
        title={answered ? "Service temporarily unavailable" : "We could not reach the server"}
        message={
          answered
            ? error.message
            : "Your session could not be checked. Please check your connection and try again."
        }
        onRetry={refresh}
      />
    );
  }
  if (!user) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  return children;
}

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Landing />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/login" element={<Login />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password/:token" element={<ResetPassword />} />
          <Route
            path="/advisor"
            element={
              <RequireAuth>
                <Advisor />
              </RequireAuth>
            }
          />
          <Route
            path="/history"
            element={
              <RequireAuth>
                <History />
              </RequireAuth>
            }
          />
          <Route
            path="/history/:id"
            element={
              <RequireAuth>
                <Result />
              </RequireAuth>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
