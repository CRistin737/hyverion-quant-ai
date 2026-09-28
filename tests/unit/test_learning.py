from decimal import Decimal

from trading_bot.learning.metrics import summarize_evaluations
from trading_bot.learning.optimizer import ChampionChallenger, ExperimentMetrics


def test_challenger_requires_value_without_more_drawdown() -> None:
    champion = ExperimentMetrics(Decimal("1"), Decimal("10"), Decimal("0.2"), 500)
    challenger = ExperimentMetrics(Decimal("1.1"), Decimal("10"), Decimal("0.19"), 100)

    assert ChampionChallenger().ready_for_review(champion, challenger) is True


def test_small_challenger_sample_cannot_be_promoted() -> None:
    champion = ExperimentMetrics(Decimal("1"), Decimal("10"), Decimal("0.2"), 500)
    challenger = ExperimentMetrics(Decimal("2"), Decimal("5"), Decimal("0.1"), 99)

    assert ChampionChallenger().ready_for_review(champion, challenger) is False


def test_learning_metrics_are_cost_aware_and_count_invalid_rows() -> None:
    metrics = summarize_evaluations(
        [
            {
                "realized_net_pnl": "4",
                "mfe_usd": "6",
                "mae_usd": "-1",
                "fees_usd": "0.2",
                "slippage_usd": "0.1",
                "signal_correct": True,
                "agent_incremental_values": {"technical": "1.5"},
            },
            {
                "realized_net_pnl": "-2",
                "mfe_usd": "1",
                "mae_usd": "-3",
                "fees_usd": "0.2",
                "slippage_usd": "0.1",
                "signal_correct": False,
                "agent_incremental_values": {"technical": "-0.5"},
            },
            {"realized_net_pnl": "not-a-number"},
        ]
    )

    assert metrics.sample_size == 2
    assert metrics.invalid_records == 1
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.expectancy_usd == Decimal("1")
    assert metrics.profit_factor == Decimal("2")
    assert metrics.max_drawdown_usd == Decimal("2")
    assert metrics.agent_incremental_value_usd["technical"] == Decimal("1.0")
