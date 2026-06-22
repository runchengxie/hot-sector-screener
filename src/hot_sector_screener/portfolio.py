"""Portfolio construction — re-exports from a-share-factor-core.

The authoritative implementation lives in factor_core.portfolio.
"""

from factor_core.portfolio import (  # noqa: F401
    build_equal_weight_portfolio,
    optimize_portfolio_weights,
    shrink_covariance_matrix,
)
