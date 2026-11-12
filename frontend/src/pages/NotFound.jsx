import { Link } from "react-router-dom";
import { Icon, useDocumentTitle } from "../components/ui.jsx";
import { useAuth } from "../context/AuthContext.jsx";

export default function NotFound({
  title = "Page not found",
  message = "The page you are looking for does not exist or may have been moved.",
}) {
  useDocumentTitle(title);
  const { user } = useAuth();
  return (
    <div className="container error-page">
      <div className="error-page__code" aria-hidden="true">
        404
      </div>
      <h1>{title}</h1>
      <p>{message}</p>
      <div className="cluster form-actions error-page__actions">
        <Link className="btn" to="/">
          <Icon name="arrow-left" /> Back to home
        </Link>
        {user ? (
          <Link className="btn btn--secondary" to="/history">
            Your analyses
          </Link>
        ) : null}
      </div>
    </div>
  );
}
