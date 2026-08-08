"""The nightly trigger fires when the design says it does."""

import datetime as dt
from zoneinfo import ZoneInfo

from risk.jobs import scheduler
from risk.jobs.scheduler import EOD_HOUR, EOD_MINUTE, build_scheduler

NY = ZoneInfo("America/New_York")


def test_nightly_job_fires_weekday_evenings_new_york():
    job = build_scheduler().get_job("eod_nightly")
    assert job is not None

    midday = dt.datetime(2026, 8, 5, 12, 0, tzinfo=NY)          # a Wednesday
    assert midday.weekday() == 2
    nxt = job.trigger.get_next_fire_time(None, midday)
    assert (nxt.hour, nxt.minute) == (EOD_HOUR, EOD_MINUTE)
    assert nxt.date() == midday.date()                          # same trading day
    assert nxt.utcoffset() == dt.timedelta(hours=-4)            # EDT in August

    friday_night = dt.datetime(2026, 8, 7, 20, 0, tzinfo=NY)
    assert friday_night.weekday() == 4
    over_weekend = job.trigger.get_next_fire_time(None, friday_night)
    assert over_weekend.weekday() == 0                          # rolls to Monday


def test_run_eod_pins_date_to_new_york_calendar(monkeypatch):
    """A misfire recovered after the UTC date roll must still run the NY date."""
    seen = []
    monkeypatch.setattr(scheduler.eod, "main", lambda argv: seen.append(argv) or 0)
    scheduler.run_eod()
    expected = dt.datetime.now(NY).date().isoformat()
    assert seen == [["run", "--date", expected]]
