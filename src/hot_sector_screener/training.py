"""ML model training pipeline — re-exports from a-share-factor-core.

The authoritative implementation lives in factor_core.training.
"""

from factor_core.training import (  # noqa: F401
    DEFAULT_STRATEGY_PARAMS,
    LinearRankModel,
    build_training_data,
    compute_feature_importance,
    preprocess_inference_features,
    preprocess_training_features,
    train_lightgbm_regression,
    train_linear_rank,
    train_model,
    transform_cross_sectional_target,
)
