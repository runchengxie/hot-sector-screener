"""Technical feature computation — re-exports from a-share-factor-core.

The authoritative implementation lives in factor_core.features.
"""

from factor_core.features import (  # noqa: F401
    ALL_FEATURE_COLUMNS,
    SMALL_POOL_FEATURE_COLUMNS,
    calculate_technical_features,
    extract_feature_dict,
    resolve_feature_columns,
)
