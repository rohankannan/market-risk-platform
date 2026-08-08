"""Nightly scheduler for the local compose stack.

Runs the same EOD entrypoint the GitHub Actions cron runs in deploy, at
18:30 America/New_York on weekdays: after the NYSE close and the Fed's
~16:15 ET H.15 yield publication, and DST-safe (the Actions cron is UTC-only,
pinned to 23:30 UTC instead).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from risk.jobs import eod

EOD_TZ = ZoneInfo("America/New_York")
EOD_HOUR = 18
EOD_MINUTE = 30
MISFIRE_GRACE_S = 3600


def run_eod() -> None:
    # pin the run date to the New York calendar: a misfire recovered within the
    # grace window can start after the UTC date rolls, and the container clock
    # is UTC. failures raise and are logged by the scheduler, which keeps the
    # next night's run scheduled
    run_date = dt.datetime.now(EOD_TZ).date().isoformat()
    eod.main(["run", "--date", run_date])


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=EOD_TZ)
    sched.add_job(run_eod,
                  CronTrigger(day_of_week="mon-fri", hour=EOD_HOUR, minute=EOD_MINUTE,
                              timezone=EOD_TZ),
                  id="eod_nightly", coalesce=True, misfire_grace_time=MISFIRE_GRACE_S)
    return sched


def main() -> int:
    print(f"[scheduler] eod_nightly registered: {EOD_HOUR:02d}:{EOD_MINUTE:02d} "
          "America/New_York, Mon-Fri")
    build_scheduler().start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
