"""Statistical validation tools — re-exports from a-share-factor-core.

The authoritative implementation lives in factor_core.validation.
"""

from factor_core.validation import (  # noqa: F401
    TemporalSplit,
    WalkForwardWindow,
    build_temporal_split,
    build_walk_forward_windows,
    compute_daily_rank_ic,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)
