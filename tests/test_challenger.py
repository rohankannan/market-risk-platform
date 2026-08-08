"""The promotion rule is mechanical - test it like the policy it is."""

from risk.jobs.challenger import decide_promotion

CHAMP = {"x": 3, "expected": 2.5, "cc_p": 0.60, "zone": "GREEN", "tl_n": 250}


def test_promotes_when_every_criterion_passes():
    chall = {"x": 2, "expected": 2.5, "cc_p": 0.80, "zone": "GREEN", "tl_n": 250}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.10,
                                         unhealthy_fits=[])
    assert promote and failures == []


def test_holds_on_unhealthy_fits_even_with_green_outcomes():
    """Boundary-stuck or non-converged fits gate the verdict before outcomes
    are even considered - no fake-green promotions."""
    chall = {"x": 2, "expected": 2.5, "cc_p": 0.80, "zone": "GREEN", "tl_n": 250}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.10,
                                         unhealthy_fits=["IR.UST.2Y", "FX.EURUSD"])
    assert not promote
    assert any("fit health" in f and "IR.UST.2Y" in f for f in failures)


def test_holds_on_amber_zone():
    chall = {"x": 6, "expected": 2.5, "cc_p": 0.30, "zone": "AMBER", "tl_n": 250}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.10, unhealthy_fits=[])
    assert not promote
    assert any("zone" in f for f in failures)


def test_holds_when_coverage_error_worsens():
    chall = {"x": 5, "expected": 2.5, "cc_p": 0.60, "zone": "GREEN", "tl_n": 250}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.10, unhealthy_fits=[])
    assert not promote
    assert any("coverage error" in f for f in failures)


def test_holds_on_cc_rejection_or_instability():
    chall = {"x": 2, "expected": 2.5, "cc_p": 0.01, "zone": "GREEN", "tl_n": 250}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.30, unhealthy_fits=[])
    assert not promote and len(failures) == 2                    # both criteria fail


def test_holds_when_evaluation_window_is_short():
    """Zone boundaries are calibrated to 250 days; a shorter window cannot
    green-light a promotion no matter how clean it looks."""
    chall = {"x": 0, "expected": 1.0, "cc_p": 0.90, "zone": "GREEN", "tl_n": 100}
    promote, failures = decide_promotion(CHAMP, chall, mean_abs_delta=0.05,
                                         unhealthy_fits=[])
    assert not promote
    assert any("250" in f for f in failures)
