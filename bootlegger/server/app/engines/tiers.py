"""Positional tiers: 1-D Gaussian mixture with BIC-chosen component count —
the Boris Chen method (design doc §4)."""
from __future__ import annotations

import numpy as np
from sklearn.mixture import GaussianMixture


def fit_tiers(points: list[float], max_components: int = 9) -> list[int]:
    """Tier (1 = best) for each input point. Deterministic across runs."""
    n = len(points)
    if n == 0:
        return []
    if n < 4:
        return [1] * n
    x = np.asarray(points, dtype=float).reshape(-1, 1)
    upper = min(max_components, max(2, n // 3))
    best_gmm, best_bic = None, np.inf
    for k in range(1, upper + 1):
        gmm = GaussianMixture(n_components=k, random_state=7, n_init=3)
        gmm.fit(x)
        bic = gmm.bic(x)
        if bic < best_bic - 1e-9:
            best_gmm, best_bic = gmm, bic
    labels = best_gmm.predict(x)
    # Order components by mean desc so tier 1 is the top cluster.
    order = np.argsort(-best_gmm.means_.ravel())
    rank = {int(comp): i + 1 for i, comp in enumerate(order)}
    return [rank[int(l)] for l in labels]
