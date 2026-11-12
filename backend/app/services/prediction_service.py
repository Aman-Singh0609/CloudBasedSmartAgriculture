"""The machine-learning recommendation engine.

Wraps the bundled scikit-learn model: a soft-voting blend of ExtraTrees and
logistic regression, trained by ``scripts/train_model.py``, plus a reference
set used to spot unusual input combinations. The model is loaded once per
process, verified, and then reused for every request.

Safety notes
------------
* The model file is a pickle. Unpickling can execute arbitrary code, so the
  file is loaded only from the configured path and only when its SHA-256
  digest matches the expected value.
* After loading, the model is checked for the expected interface (seven
  features in a known order, ``predict_proba``, and exactly the crop labels the
  application has reference data for). A model that fails any check is treated
  as unavailable rather than being used to produce misleading output.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import warnings
from pathlib import Path

import numpy as np

from app.errors import ModelUnavailableError
from app.models.features import FEATURE_KEYS, FEATURES_BY_KEY, MODEL_NAME_TO_KEY
from app.models.schemas import CropProbability, ModelPrediction

logger = logging.getLogger(__name__)

MAX_ALTERNATIVES = 3

# The model was fitted on a pandas DataFrame, so scikit-learn warns when it is
# given a plain array. The column order is verified explicitly in ``load``,
# which makes that warning redundant; it is silenced once, here.
warnings.filterwarnings(
    "ignore",
    message="X does not have valid feature names",
    category=UserWarning,
)


class PredictionService:
    def __init__(self, model_path: str | Path, expected_sha256: str, expected_labels: tuple[str, ...]) -> None:
        self._model_path = Path(model_path)
        self._expected_sha256 = expected_sha256.strip().lower()
        self._expected_labels = frozenset(expected_labels)
        self._lock = threading.Lock()
        self._model = None
        self._labels: tuple[str, ...] = ()
        self._feature_order: tuple[str, ...] = ()
        self._info: dict = {}
        self._load_error: str | None = "Model has not been loaded yet."

    # ------------------------------------------------------------------ load
    def load(self) -> bool:
        """Load and verify the model. Safe to call more than once."""
        with self._lock:
            if self._model is not None:
                return True
            try:
                self._load()
            except Exception as exc:  # noqa: BLE001 - any failure means "unavailable"
                self._model = None
                self._load_error = str(exc)
                logger.error(
                    "model could not be loaded: %s",
                    exc,
                    extra={"event": "model_load_failed", "error_type": type(exc).__name__},
                )
                return False
            self._load_error = None
            logger.info("model loaded", extra={"event": "model_loaded", **self._info})
            return True

    def _load(self) -> None:
        if not self._model_path.is_file():
            raise FileNotFoundError("model file not found at the configured MODEL_PATH")

        digest = hashlib.sha256(self._model_path.read_bytes()).hexdigest()
        if not hmac.compare_digest(digest, self._expected_sha256):
            raise ValueError(
                "model file checksum does not match MODEL_SHA256; refusing to unpickle an unverified file"
            )

        # Imported lazily so a missing ML stack degrades to "model unavailable".
        import joblib
        import sklearn

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bundle = joblib.load(self._model_path)
        for warning in caught:
            logger.warning(
                "warning while loading model: %s",
                str(warning.message)[:300],
                extra={"event": "model_load_warning", "category": warning.category.__name__},
            )

        if not isinstance(bundle, dict) or bundle.get("format") != 1:
            raise TypeError("model file is not a recognised model bundle")
        model = bundle["model"]
        if not hasattr(model, "predict_proba") or not hasattr(model, "classes_"):
            raise TypeError("model does not expose predict_proba/classes_")

        ood = bundle["ood"]
        reference = np.asarray(ood["reference"], dtype=float)
        if reference.ndim != 2 or reference.shape[1] != len(FEATURE_KEYS) or not np.isfinite(reference).all():
            raise ValueError("unusual-input reference data is malformed")
        if not float(ood["threshold"]) > 0:
            raise ValueError("unusual-input threshold is invalid")

        labels = tuple(str(label) for label in model.classes_)
        if set(labels) != self._expected_labels:
            unexpected = sorted(set(labels) ^ self._expected_labels)
            raise ValueError(f"model classes do not match the crop reference data: {unexpected}")

        feature_names = [str(name) for name in bundle.get("feature_names", [])]
        # The blend was fitted on a plain array, so record the column order on it
        # explicitly; the checks below and the tests read it from the model.
        model.feature_names_in_ = np.array(feature_names, dtype=object)
        try:
            feature_order = tuple(MODEL_NAME_TO_KEY[name] for name in feature_names)
        except KeyError as exc:
            raise ValueError(f"model was trained with an unknown feature: {exc}") from exc
        if len(feature_order) != len(FEATURE_KEYS) or set(feature_order) != set(FEATURE_KEYS):
            raise ValueError("model does not use exactly the seven expected input features")
        if getattr(model, "n_features_in_", None) != len(FEATURE_KEYS):
            raise ValueError("model expects a different number of features")

        # Single-row predictions gain nothing from a worker pool and pay for it.
        for part in (model, *getattr(model, "estimators_", [])):
            if hasattr(part, "n_jobs"):
                part.n_jobs = 1

        metadata = bundle.get("metadata") or {}
        self._model = model
        self._labels = labels
        self._feature_order = feature_order
        self._ood_scaler = ood["scaler"]
        self._ood_reference = reference
        self._ood_neighbours = int(ood["neighbours"])
        self._ood_threshold = float(ood["threshold"])
        self._info = {
            "model_type": type(model).__name__,
            "model_version": str(metadata.get("model_version", "")) or None,
            "n_classes": len(labels),
            "sha256_prefix": digest[:12],
            "sklearn_version": sklearn.__version__,
        }

    # ---------------------------------------------------------------- status
    @property
    def is_available(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    @property
    def info(self) -> dict:
        """Non-sensitive description of the loaded model, stored with each prediction."""
        return dict(self._info)

    def feature_importances(self) -> dict[str, float]:
        """Overall importance of each input across the whole model (not per prediction)."""
        if self._model is None:
            raise ModelUnavailableError()
        # Taken from the tree member of the blend; the linear member has no
        # comparable measure.
        trees = self._model.named_estimators_["et"]
        return {
            key: round(float(weight), 4)
            for key, weight in zip(self._feature_order, trees.feature_importances_)
        }

    def is_unusual_combination(self, inputs: dict[str, float]) -> bool:
        """True when the readings, taken together, resemble nothing the model was trained on.

        Each reading may be individually plausible while the combination is not
        (for example very high rainfall with very low humidity). The test is the
        average distance to the five nearest training examples, in standardised
        units, compared with a threshold set from the training data itself.
        """
        if self._model is None:
            raise ModelUnavailableError()
        row = self._row(inputs)
        scaled = self._ood_scaler.transform(row)
        distances = np.sqrt(((self._ood_reference - scaled) ** 2).sum(axis=1))
        nearest = np.sort(distances)[: self._ood_neighbours]
        return bool(nearest.mean() > self._ood_threshold)

    # --------------------------------------------------------------- predict
    def _row(self, inputs: dict[str, float]) -> np.ndarray:
        """Order the readings as the model expects and clamp them to its training range.

        Clamping is explicit because the linear member of the blend would
        otherwise extrapolate without limit; values beyond the range are
        reported to the user separately (``out_of_range_fields``).
        """
        values = [
            min(max(float(inputs[key]), FEATURES_BY_KEY[key].model_min), FEATURES_BY_KEY[key].model_max)
            for key in self._feature_order
        ]
        return np.array([values], dtype=float)

    def predict(self, inputs: dict[str, float]) -> ModelPrediction:
        """Return the recommended crop, its probability and the runner-up crops.

        ``inputs`` must already be validated: a mapping of the seven feature
        keys to finite floats.
        """
        if self._model is None:
            raise ModelUnavailableError()

        row = self._row(inputs)
        probabilities = self._model.predict_proba(row)[0]

        ranked = np.argsort(-probabilities, kind="stable")
        best = int(ranked[0])
        alternatives = tuple(
            CropProbability(crop=self._labels[int(index)], probability=round(float(probabilities[index]), 4))
            for index in ranked[1 : 1 + MAX_ALTERNATIVES]
            if probabilities[index] > 0
        )
        return ModelPrediction(
            recommended_crop=self._labels[best],
            confidence=round(float(probabilities[best]), 4),
            alternatives=alternatives,
        )
