from itertools import product

import numpy as np
from sklearn.linear_model import QuantileRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURES

QUANTILES = [0.1, 0.5, 0.9]


def candidates(horizon):
    configs = [{"family": "empirical"}, {"family": "scaled"}]
    configs += [{"family": "linear", "alpha": alpha} for alpha in (0.1, 1, 10)]
    if horizon == "weekly":
        configs += [dict(family="xgb", max_depth=d, n_estimators=n, reg_lambda=l, min_child_weight=30)
                    for d, n, l in product([1, 2], [100, 200], [10, 50])]
    else:
        configs += [dict(family="xgb", max_depth=1, n_estimators=50, reg_lambda=50, min_child_weight=20)]
    return configs


def ordered(raw):
    raw = np.asarray(raw, dtype=float)
    return np.sort(raw, axis=-1), np.any(np.diff(raw, axis=-1) < 0, axis=-1)


class QuantileModel:
    def __init__(self, config, seed=42):
        self.config, self.seed = config, seed

    def fit(self, train):
        if train.empty or train.target.isna().any():
            raise ValueError("Model needs observed training labels")
        family = self.config["family"]
        if family in ("empirical", "scaled"):
            values = train.target.to_numpy()
            if family == "scaled":
                if not train.scale.gt(0).all():
                    raise ValueError("Volatility scales must be positive")
                values = values / train.scale.to_numpy()
            self.quantiles = np.quantile(values, QUANTILES)
        else:
            self.estimators = []
            for quantile in QUANTILES:
                if family == "linear":
                    estimator = make_pipeline(StandardScaler(), QuantileRegressor(
                        quantile=quantile, alpha=self.config["alpha"], solver="highs"))
                elif family == "xgb":
                    from xgboost import XGBRegressor
                    params = {k: v for k, v in self.config.items() if k != "family"}
                    estimator = XGBRegressor(objective="reg:quantileerror", quantile_alpha=quantile,
                                             tree_method="hist", device="cpu", learning_rate=0.03,
                                             subsample=1.0, colsample_bytree=1.0, reg_alpha=0,
                                             random_state=self.seed, n_jobs=1, **params)
                else:
                    raise ValueError(f"Unknown model {family}")
                estimator.fit(train[FEATURES].astype(float), train.target.astype(float))
                self.estimators.append(estimator)
        return self

    def predict(self, frame):
        if self.config["family"] in ("empirical", "scaled"):
            raw = np.tile(self.quantiles, (len(frame), 1))
            if self.config["family"] == "scaled":
                raw *= frame.scale.to_numpy(dtype=float)[:, None]
        else:
            raw = np.column_stack([e.predict(frame[FEATURES].astype(float)) for e in self.estimators])
        return ordered(raw)
