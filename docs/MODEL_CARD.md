# Model card: crop recommendation (v2.0)

## Purpose
Suggest which of 22 crops best matches seven readings (N, P, K, temperature, humidity, pH, rainfall), and say how strongly the model supports the choice. It is decision support, not an agronomic prescription.

## Data
- **Training table:** the public *Crop Recommendation* dataset (2,200 rows, exactly 100 per crop, 22 crops). Stored at `backend/data/training/crop_recommendation.csv`; its SHA-256 is pinned in `scripts/train_model.py`. Check the dataset's licence and attribution terms at its original source before redistributing the project.
- **What it is:** a curated table with very tight, almost non-overlapping clusters per crop, no duplicates and no missing values. That is why any reasonable model scores about 99% on it.
- **What it is not:** field observations. Rainfall values (20–299 mm) are not annual totals, and N, P and K carry no documented unit.

## Method
1. Stratified 5-fold cross-validation, comparing the previous 100-tree RandomForest with the final model.
2. Because real readings are noisy, the held-out fold is also scored after adding noise of 10%, 20% and 30% of each feature's standard deviation.
3. Final model: soft-voting blend of ExtraTrees (200 trees, minimum leaf 3) and logistic regression, trained on the data plus four noise-jittered copies (10%), clipped to the observed range.
4. An *unusual input* detector: mean distance to the five nearest training rows in standardised units, thresholded at the 99th percentile of the training data's own distances.

## Results (cross-validated, from `scripts/train_model.py`)
| Input noise | Previous RandomForest | Final blend |
|---|---|---|
| none | 99.6% | 98.6% |
| 10% | 96.6% | 97.9% |
| 20% | 86.4% | 93.4% |
| 30% | 73.9% | 85.4% |

Calibration error of the top probability at 20% noise fell from 0.17 to 0.10. The detector flags about 98% of uniformly random inputs and about 1% of real rows (tests in `tests/unit/test_services.py`).

The blend gives up about one point on perfectly clean data in exchange for much better behaviour when readings are imprecise, which they always are.

## Independent agronomic check
Temperature and pH of every result are compared with FAO EcoCrop absolute limits for the recommended crop. 122 of the 2,200 training rows (5.5%, mostly mothbeans, blackgram, grapes, pomegranate and orange) lie outside those limits for their own label, which is evidence the table has noisy rows; the check therefore only warns and lowers the displayed reliability, it never silently changes the recommendation.

End-to-end through the service, with readings jittered by realistic amounts, accuracy was 99.4% (small error), 98.5% and 92.7% (larger error), and results shown at 80% confidence or higher were right 98.8-100% of the time. These are again measured on the same table.

## What the numbers do **not** show
- The 99% figures are accuracy at reproducing a curated table, not the chance that a crop will succeed on a farm. No public dataset pairs soil and climate readings with verified field outcomes at this scale, so that claim cannot be measured here.
- Ranges of the readings themselves (for example units for N, P, K) are not validated against laboratory standards.
- Only seven inputs are modelled; prices, irrigation, pests, soil texture, rotation, region and season are not.

## Intended use and limits
Use as one input alongside local expertise. Treat results with weak or moderate model agreement, or flagged as an unusual combination, as unreliable. Retrain with `python scripts/train_model.py` and pin the printed SHA-256 in `app/config.py` when the data or the library version changes.

## Recommended next steps for higher real-world accuracy
- Collect or license regional data with verified outcomes (district yield records joined to measured soil and weather) and evaluate with a geographic hold-out.
- Extend the FAO EcoCrop guardrail (now live for temperature and pH) to rainfall once the dataset's rainfall period is known.
