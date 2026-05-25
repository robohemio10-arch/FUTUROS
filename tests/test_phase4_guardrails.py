import pandas as pd

from smartcrypto.ml.model_guardrails import evaluate_training_guardrails


def test_guardrail_blocks_insufficient_trades():
    frame = pd.DataFrame({"target_win": [1], "feature": [0.1]})

    decision = evaluate_training_guardrails(
        frame=frame,
        target_column="target_win",
        min_trades_for_training=50,
        min_trades_for_walk_forward=100,
    )

    assert decision.status == "blocked"
    assert decision.trainable is False
    assert decision.reason == "insufficient_trades_for_training"
