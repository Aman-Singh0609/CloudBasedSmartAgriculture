import { Link } from "react-router-dom";
import { Icon, ShareBar, StatusChip, useDocumentTitle } from "../components/ui.jsx";
import { useAuth } from "../context/AuthContext.jsx";

const FEATURES = [
  ["sprout", "A ranked recommendation", "The best-matching crop out of 22, plus up to three alternatives with their scores."],
  ["chart", "Honest confidence", "Model confidence is shown for what it is: how strongly the model agrees with itself, not a promise of yield."],
  ["flask", "Reading-by-reading check", "Each value is compared with the typical range for the crop, so you can see what fits and what does not."],
  ["cloud", "Weather lookup", "Fetch current temperature and humidity for your town. Every value is labelled with where it came from."],
  ["clock", "History and trends", "Past analyses are saved to your account, with simple charts of how your soil readings change."],
  ["lock", "Minimal personal data", "An account needs only a name, email and password. No government ID, no phone number."],
];

const STEPS = [
  ["Enter soil nutrients", "Type in the nitrogen, phosphorus, potassium and pH values from your soil test report."],
  ["Add climate conditions", "Look up current temperature and humidity for your town, or enter typical values yourself, then add rainfall."],
  ["Review the recommendation", "See the best-matching crop, the alternatives, and a reading-by-reading comparison you can act on."],
];

const NOT_CONSIDERED = [
  "market prices, seed or labour availability, or your budget",
  "irrigation capacity, groundwater or soil texture",
  "pests, diseases or crop rotation history",
  "future weather or local regulations",
];

export default function Landing() {
  useDocumentTitle(null);
  const { user } = useAuth();

  return (
    <>
      <section className="hero" aria-labelledby="hero-title">
        <div className="container hero__inner">
          <div>
            <span className="eyebrow">Crop decision support</span>
            <h1 id="hero-title">Make smarter crop decisions from your soil data</h1>
            <p className="hero__lead">
              Enter your soil test results and local climate. AgricultureCloud suggests crops that fit, shows how
              confident the model is, and explains which of your readings support the choice.
            </p>
            <div className="cluster">
              <Link className="btn btn--lg btn--on-dark" to="/advisor">
                Analyze my soil <Icon name="arrow-right" />
              </Link>
              <a className="btn btn--lg btn--ghost hero__secondary" href="#how-it-works">
                How it works
              </a>
            </div>
            <p className="hero__note">
              Free to use.{" "}
              {user ? null : "You will be asked to log in or create an account so your analyses can be saved."}
            </p>
          </div>

          <div
            className="sample"
            role="img"
            aria-label="Example result: recommended crop rice with 99 percent model confidence. Nitrogen and soil pH within the typical range, phosphorus below the typical range."
          >
            <span className="sample__label">Example result</span>
            <div className="muted small">Recommended crop</div>
            <div className="sample__crop">Rice</div>
            <div className="sample__confidence">
              <div className="sample__confidence-head">
                <span>Model confidence</span>
                <span>99%</span>
              </div>
              <ShareBar value={0.99} />
            </div>
            <div className="sample__rows">
              <div className="sample__row">
                <span>
                  Nitrogen <strong>90</strong>
                </span>
                <StatusChip status="within" label="Within typical range" />
              </div>
              <div className="sample__row">
                <span>
                  Phosphorus <strong>20</strong>
                </span>
                <StatusChip status="below" label="Below typical range" />
              </div>
              <div className="sample__row">
                <span>
                  Soil pH <strong>6.5</strong>
                </span>
                <StatusChip status="within" label="Within typical range" />
              </div>
              <div className="sample__row">
                <span className="muted">Also consider</span>
                <span>
                  <strong>Jute</strong> 1%
                </span>
              </div>
            </div>
          </div>
        </div>
        <svg className="hero__fields" viewBox="0 0 1200 48" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <path d="M0 48V30c150-22 300-22 450-8s300 14 450-2 200-18 300-10v38z" />
        </svg>
      </section>

      <section className="section" id="how-it-works" aria-labelledby="how-title">
        <div className="container">
          <div className="section__header">
            <span className="eyebrow">How it works</span>
            <h2 id="how-title">Three steps from soil report to recommendation</h2>
            <p>It takes about two minutes if you have a soil test report to hand.</p>
          </div>
          <ol className="steps">
            {STEPS.map(([title, text]) => (
              <li className="step" key={title}>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="section section--tint" aria-labelledby="features-title">
        <div className="container">
          <div className="section__header">
            <span className="eyebrow">What you get</span>
            <h2 id="features-title">More than a crop name</h2>
            <p>
              Every analysis is laid out so you can see what the model said, what is a simple rule, and what you could
              change.
            </p>
          </div>
          <div className="feature-grid">
            {FEATURES.map(([icon, title, text]) => (
              <div className="feature" key={title}>
                <span className="feature__icon">
                  <Icon name={icon} className="icon--lg" />
                </span>
                <div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="why-title">
        <div className="container split">
          <div>
            <span className="eyebrow">Why AgricultureCloud</span>
            <h2 id="why-title">Built to be understood, not just believed</h2>
            <ul className="checklist">
              <li>
                <Icon name="check-circle" />
                <span>
                  <strong>Two kinds of result, clearly separated.</strong> The crop recommendation comes from a
                  machine-learning model. The reading comparison is a plain rule. Each is labelled.
                </span>
              </li>
              <li>
                <Icon name="check-circle" />
                <span>
                  <strong>Warnings when the model is guessing.</strong> If a value is outside what the model was
                  trained on, the result says so instead of hiding it.
                </span>
              </li>
              <li>
                <Icon name="check-circle" />
                <span>
                  <strong>Works on a phone.</strong> Every page is designed for small screens and keyboard or
                  screen-reader use.
                </span>
              </li>
            </ul>
          </div>
          <div className="card card--flat" id="limitations">
            <h3 className="card__title">
              <Icon name="info" /> What this tool cannot tell you
            </h3>
            <p>The model sees only seven numbers. It does not know about:</p>
            <ul className="checklist checklist--neutral">
              {NOT_CONSIDERED.map((item) => (
                <li key={item}>
                  <Icon name="x" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
            <p className="small muted">
              Use the recommendation as one input to your decision and confirm it with a local agronomist or
              agricultural extension officer.
            </p>
          </div>
        </div>
      </section>

      <section className="section cta-band" aria-labelledby="cta-title">
        <div className="container">
          <h2 id="cta-title">Ready to check your soil?</h2>
          <p>Have your soil test values ready and get a recommendation in a couple of minutes.</p>
          <Link className="btn btn--lg btn--on-dark" to="/advisor">
            Analyze my soil <Icon name="arrow-right" />
          </Link>
        </div>
      </section>
    </>
  );
}
