from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from bot.models import FPLPointsPredictor


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "bot" / "models"


class ModelRuntimeSmokeTests(unittest.TestCase):
    def test_committed_models_load_and_predict_with_ci_runtime(self):
        """Catch pickle/runtime drift before it reaches the live orchestrator."""
        predictor = FPLPointsPredictor.load(MODELS_DIR)
        features = json.loads(
            (MODELS_DIR / "minutes.pkl.json").read_text(encoding="utf-8")
        )["features"]

        # A single neutral synthetic row is enough to exercise every persisted
        # estimator and the sklearn/XGBoost/LightGBM wrapper compatibility.
        X = pd.DataFrame([{feature: 0.0 for feature in features}])
        X.loc[0, "now_cost"] = 75.0
        X.loc[0, "pos_mid"] = 1.0
        X.loc[0, "is_home"] = 0.5
        X.loc[0, "ewma_minutes"] = 75.0
        X.loc[0, "ewma_start_rate"] = 0.85
        X.loc[0, "ewma_p60_rate"] = 0.80
        X.loc[0, "ewma_played_any"] = 0.90
        X.loc[0, "games_played"] = 2.0

        pred = predictor.predict(X, [3])
        self.assertEqual(len(pred), 1)
        self.assertTrue(np.isfinite(float(pred.iloc[0]["expected_points"])))
        self.assertGreaterEqual(float(pred.iloc[0]["expected_points"]), -5.0)


if __name__ == "__main__":
    unittest.main()
