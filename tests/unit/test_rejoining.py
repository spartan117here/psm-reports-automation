"""Unit tests for closed member and rejoining calculations."""

from app.processing.rejoining import RejoiningCalculator, RejoiningRow


def test_rejoining_row_calculation():
    """Verify single showroom rejoining formulas."""
    # Example historical row: CLOSED = 6,178, REJOIN SS = 1,247, REJOIN SV = 131
    # TOTAL REJOIN = 1,378, REJOIN % = 22.30%
    row = RejoiningCalculator.calculate_row(
        showroom="CHROMEPET",
        closed=6178,
        rejoin_ss=1247,
        rejoin_sv=131,
    )

    assert row.total_rejoin == 1378
    assert row.rejoin_percentage == 22.30


def test_rejoining_compute_summary():
    """Verify summary grand total row across showrooms."""
    rows = [
        RejoiningRow(showroom="CPT", closed_count=100, rejoin_ss=20, rejoin_sv=5, total_rejoin=25, rejoin_percentage=25.0),
        RejoiningRow(showroom="TVL", closed_count=100, rejoin_ss=10, rejoin_sv=5, total_rejoin=15, rejoin_percentage=15.0),
    ]

    summary = RejoiningCalculator.compute_summary(rows)
    assert summary.showroom == "TOTAL"
    assert summary.closed_count == 200
    assert summary.rejoin_ss == 30
    assert summary.rejoin_sv == 10
    assert summary.total_rejoin == 40
    assert summary.rejoin_percentage == 20.0
