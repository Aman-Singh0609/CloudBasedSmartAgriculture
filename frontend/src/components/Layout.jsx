import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";
import { useConfig } from "../context/ConfigContext.jsx";
import { Alert, Icon } from "./ui.jsx";

function navClass({ isActive }) {
  return isActive ? "nav-link is-active" : "nav-link";
}

function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const toggleRef = useRef(null);

  // Close the mobile menu whenever the page changes.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        toggleRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const handleLogout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    // Leave any protected page first. Otherwise its guard sees the session end
    // and sends the user to "log in to continue" instead of the home page.
    navigate("/", { state: { notice: { kind: "success", text: "You have been logged out." } } });
    try {
      await logout();
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <header className="site-header">
      <div className="container site-header__inner">
        <Link className="brand" to="/">
          <span className="brand__mark">
            <Icon name="sprout" />
          </span>
          <span>AgricultureCloud</span>
        </Link>

        <button
          ref={toggleRef}
          className="nav-toggle"
          type="button"
          aria-expanded={open}
          aria-controls="site-nav"
          onClick={() => setOpen((current) => !current)}
        >
          <Icon name={open ? "x" : "menu"} />
          <span>Menu</span>
        </button>

        <nav className={`site-nav${open ? " is-open" : ""}`} id="site-nav" aria-label="Main">
          <ul className="site-nav__list">
            <li>
              <NavLink className={navClass} to="/" end>
                Home
              </NavLink>
            </li>
            {user ? (
              <>
                <li>
                  <NavLink className={navClass} to="/advisor">
                    Crop Advisor
                  </NavLink>
                </li>
                <li>
                  <NavLink className={navClass} to="/history">
                    History
                  </NavLink>
                </li>
                <li className="nav-user">
                  <Icon name="user" /> <span className="visually-hidden">Signed in as </span>
                  {user.name}
                </li>
                <li>
                  <button className="nav-link" type="button" onClick={handleLogout} disabled={loggingOut}>
                    <Icon name="logout" /> Log out
                  </button>
                </li>
              </>
            ) : (
              <>
                <li>
                  <NavLink className={navClass} to="/login">
                    Log in
                  </NavLink>
                </li>
                <li>
                  <Link className="nav-link nav-link--cta" to="/signup">
                    Create account
                  </Link>
                </li>
              </>
            )}
          </ul>
        </nav>
      </div>
    </header>
  );
}

function Footer() {
  const { config } = useConfig();
  return (
    <footer className="site-footer">
      <div className="container site-footer__inner">
        <div>
          <div className="site-footer__brand">AgricultureCloud</div>
          <p>Crop decision support from soil and climate data.</p>
        </div>
        <div className="stack">
          <p>
            <strong>Advisory only.</strong> Recommendations come from a statistical model that sees seven readings. It
            cannot account for markets, pests, irrigation, soil texture or future weather, and it does not guarantee
            any outcome. Confirm decisions with a local agronomist or extension officer.
          </p>
          <p>
            &copy; {new Date().getFullYear()} AgricultureCloud{config?.version ? ` · v${config.version}` : ""}
          </p>
        </div>
      </div>
    </footer>
  );
}

export default function Layout() {
  const location = useLocation();
  const mainRef = useRef(null);
  const firstRender = useRef(true);
  const [dismissedKey, setDismissedKey] = useState(null);

  const notice = location.state && location.state.notice ? location.state.notice : null;
  const showNotice = notice && dismissedKey !== location.key;

  // A single-page app does not reload on navigation, so move focus and the
  // scroll position to the new page ourselves (screen readers rely on this).
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    window.scrollTo(0, 0);
    mainRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <Header />
      {showNotice ? (
        <div className="container flash-region">
          <Alert kind={notice.kind || "info"} onDismiss={() => setDismissedKey(location.key)}>
            {notice.text}
          </Alert>
        </div>
      ) : null}
      <main id="main" tabIndex={-1} ref={mainRef}>
        <Outlet />
      </main>
      <Footer />
    </>
  );
}
