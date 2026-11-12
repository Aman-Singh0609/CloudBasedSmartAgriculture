"""Train, evaluate and package the crop recommendation model.

    python scripts/train_model.py            # evaluate, then write the model
    python scripts/train_model.py --check    # evaluate only, write nothing

What it does
------------
1. Verifies the training CSV against a pinned SHA-256, so the result is
   reproducible and a silently edited dataset is noticed.
2. Cross-validates (stratified, 5 folds) on clean data and on data with
   realistic measurement error added, comparing against the previous
   plain RandomForest baseline.
3. Fits the final model: a soft-voting blend of ExtraTrees and logistic
   regression, trained on the data plus noise-jittered copies. The noise
   teaches the model that a soil test or a weather reading is never exact.
4. Records a reference set of training points and a distance threshold, used at
   run time to detect input combinations unlike anything the model has seen.
5. Writes one ``joblib`` bundle containing only scikit-learn / numpy objects
   (no custom classes), plus provenance and metrics, and prints its SHA-256 to
   pin in ``app/config.py``.

The dataset is the public "Crop Recommendation" table (2,200 rows, 22 crops).
It is a curated, nearly noise-free table, so accuracy on it overstates field
accuracy; ``docs/MODEL_CARD.md`` explains what that means.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, VotingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

BACKEND = Path(__file__).resolve().parent.parent
DATASET = BACKEND / "data" / "training" / "crop_recommendation.csv"
DATASET_SHA256 = "54a5a6e5408668e668667efc50de2fc867c1b875e0431b4f54dd331b0a109a4e"
OUTPUT = BACKEND / "crop_recommendation_model.pkl"
REPORT = BACKEND / "data" / "training" / "evaluation.json"

FEATURES = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
SEED = 42
AUGMENT_COPIES = 4
AUGMENT_NOISE = 0.10  # standard deviation, as a fraction of each feature's spread
OOD_NEIGHBOURS = 5
OOD_PERCENTILE = 99.0
FORMAT_VERSION = 1


def load_dataset() -> tuple[np.ndarray, np.ndarray]:
    digest = hashlib.sha256(DATASET.read_bytes()).hexdigest()
    if digest != DATASET_SHA256:
        sys.exit(f"Training data checksum mismatch ({digest}); refusing to train on an unverified file.")
    with DATASET.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header[:7] != FEATURES or header[7] != "label":
            sys.exit(f"Unexpected columns: {header}")
        rows = list(reader)
    X = np.array([[float(v) for v in row[:7]] for row in rows], dtype=float)
    y = np.array([row[7].strip().lower() for row in rows])
    if not np.isfinite(X).all():
        sys.exit("Training data contains non-finite values.")
    return X, y


def jitter(X: np.ndarray, spread: np.ndarray, low: np.ndarray, high: np.ndarray, fraction: float, rng) -> np.ndarray:
    """Add measurement-like noise, then keep values inside the observed range."""
    return np.clip(X + rng.normal(0.0, 1.0, X.shape) * spread * fraction, low, high)


def augment(X, y, spread, low, high, rng, copies=AUGMENT_COPIES, fraction=AUGMENT_NOISE):
    blocks = [X] + [jitter(X, spread, low, high, fraction, rng) for _ in range(copies)]
    return np.vstack(blocks), np.tile(y, copies + 1)


def build_blend() -> VotingClassifier:
    return VotingClassifier(
        [
            ("et", ExtraTreesClassifier(n_estimators=200, min_samples_leaf=3, random_state=SEED, n_jobs=-1)),
            ("lr", make_pipeline(StandardScaler(), LogisticRegression(C=10, max_iter=2000))),
        ],
        voting="soft",
    )


def expected_calibration_error(probabilities: np.ndarray, truth: np.ndarray, classes: np.ndarray) -> float:
    top = probabilities.max(axis=1)
    hit = classes[probabilities.argmax(axis=1)] == truth
    error = 0.0
    for low in np.linspace(0, 0.9, 10):
        in_bin = (top >= low) & (top < low + 0.1 + (1e-9 if low > 0.85 else 0))
        if in_bin.any():
            error += in_bin.mean() * abs(top[in_bin].mean() - hit[in_bin].mean())
    return float(error)


def evaluate(X, y, spread, low, high) -> dict:
    """5-fold cross-validation; noise is added to the held-out fold only."""
    classes = np.unique(y)
    folds = StratifiedKFold(5, shuffle=True, random_state=SEED)
    noise_levels = (0.0, 0.1, 0.2, 0.3)
    candidates = {
        "baseline_random_forest": (lambda: RandomForestClassifier(100, random_state=SEED, n_jobs=-1), False),
        "final_blend": (build_blend, True),
    }
    report: dict = {}
    for name, (make, use_augmentation) in candidates.items():
        rng = np.random.default_rng(SEED)
        accuracy = {level: [] for level in noise_levels}
        calibration = []
        for train, test in folds.split(X, y):
            if use_augmentation:
                X_train, y_train = augment(X[train], y[train], spread, low, high, rng)
            else:
                X_train, y_train = X[train], y[train]
            model = make().fit(X_train, y_train)
            for level in noise_levels:
                X_test = jitter(X[test], spread, low, high, level, rng) if level else X[test]
                accuracy[level].append(float(model.score(X_test, y[test])))
            noisy = jitter(X[test], spread, low, high, 0.2, rng)
            calibration.append(expected_calibration_error(model.predict_proba(noisy), y[test], classes))
        report[name] = {
            "accuracy_by_input_noise": {f"{int(level * 100)}%": round(float(np.mean(v)), 4) for level, v in accuracy.items()},
            "calibration_error_at_20pct_noise": round(float(np.mean(calibration)), 4),
        }
    return report


def knn_mean_distance(reference: np.ndarray, points: np.ndarray, k: int, skip_self: bool) -> np.ndarray:
    distances = np.sqrt(((points[:, None, :] - reference[None, :, :]) ** 2).sum(axis=2))
    distances.sort(axis=1)
    start = 1 if skip_self else 0
    return distances[:, start : start + k].mean(axis=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="evaluate only; write no files")
    args = parser.parse_args()

    X, y = load_dataset()
    classes = np.unique(y)
    low, high = X.min(axis=0), X.max(axis=0)
    spread = X.std(axis=0)
    print(f"Dataset: {len(y)} rows, {len(classes)} crops, {len(y) // len(classes)} per crop")

    print("Evaluating (5-fold cross-validation)...")
    report = evaluate(X, y, spread, low, high)
    for name, result in report.items():
        print(f"  {name}: {result['accuracy_by_input_noise']}  calibration error {result['calibration_error_at_20pct_noise']}")

    # Out-of-distribution reference, in standardised units.
    scaler = StandardScaler().fit(X)
    reference = scaler.transform(X)
    own = knn_mean_distance(reference, reference, OOD_NEIGHBOURS, skip_self=True)
    threshold = float(np.percentile(own, OOD_PERCENTILE))
    rng = np.random.default_rng(SEED + 1)
    random_inputs = rng.uniform(low, high, (2000, 7))
    flagged = float((knn_mean_distance(reference, scaler.transform(random_inputs), OOD_NEIGHBOURS, False) > threshold).mean())
    report["unusual_input_detector"] = {
        "neighbours": OOD_NEIGHBOURS,
        "threshold_percentile_of_training_data": OOD_PERCENTILE,
        "share_of_real_rows_flagged": round(1 - OOD_PERCENTILE / 100, 4),
        "share_of_uniform_random_inputs_flagged": round(flagged, 4),
    }
    print(f"  unusual-input detector flags {flagged:.0%} of uniformly random inputs "
          f"(and about {100 - OOD_PERCENTILE:.0f}% of real rows)")

    if args.check:
        return

    print("Fitting the final model on all data...")
    rng = np.random.default_rng(SEED)
    X_train, y_train = augment(X, y, spread, low, high, rng)
    model = build_blend().fit(X_train, y_train)
    if list(model.classes_) != list(classes):
        sys.exit("Class order changed unexpectedly.")

    bundle = {
        "format": FORMAT_VERSION,
        "model": model,
        "feature_names": FEATURES,
        "ood": {
            "scaler": scaler,
            "reference": reference,
            "neighbours": OOD_NEIGHBOURS,
            "threshold": threshold,
        },
        "metadata": {
            "model_version": "2.0",
            "algorithm": "soft-voting blend: ExtraTrees(200, min leaf 3) + logistic regression, noise-augmented",
            "dataset": "Crop Recommendation (public table, 2,200 rows, 22 crops)",
            "dataset_sha256": DATASET_SHA256,
            "sklearn_version": sklearn.__version__,
            "augmentation": {"copies": AUGMENT_COPIES, "noise_fraction_of_sd": AUGMENT_NOISE},
            "evaluation": report,
        },
    }
    joblib.dump(bundle, OUTPUT, compress=3)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"\nWrote {OUTPUT.name} ({OUTPUT.stat().st_size // 1024} KiB)")
    print(f"SHA-256 to pin as BUNDLED_MODEL_SHA256 in app/config.py:\n{digest}")


if __name__ == "__main__":
    main()
