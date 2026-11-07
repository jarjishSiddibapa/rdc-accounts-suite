"""How many web workers, processing workers and Excel-writing helpers to run, sized from this server.

Worked out once, when the suite starts, from the number of CPU cores and the memory that is free at that moment
(other programs on the server included), so the same code suits a small host and a big one. Anything set by hand in
``.env`` (``API_WORKERS``, ``JOB_WORKER_PROCESSES``, ``CPU_JOB_POOL_WORKERS``) still wins; leaving it out, or writing
``auto``, means "decide for me".

The sizing is deliberately cautious: one core always stays free for the database and everything else, a processing
worker is assumed to need about 1.5 GB at its busiest, and there are never fewer than 2 web workers and 2
processing workers (so one heavy report cannot keep the others waiting) nor more than 4 of either."""

import os
from dataclasses import dataclass

from app import memory

_MIN_WORKERS = 2
_MAX_WORKERS = 4
_MAX_HELPERS = 4
_RESERVE_MB = 1500                 # always left for the operating system, the database and other programs
_API_WORKER_MB = 300
_PROCESSING_WORKER_MB = 1500       # a processing worker at its busiest (a big report held in memory)
_HELPER_MB = 1000                  # one Excel-writing helper at its busiest (a large workbook held in memory)


@dataclass(frozen=True)
class Plan:
    api: int          # web workers (uvicorn --workers)
    jobs: int         # processing workers that run the reports
    helpers: int      # Excel-writing helper processes inside each processing worker
    cores: int
    free_mb: int | None


def make_plan(cores: int, free_mb: int | None, total_mb: int | None = None) -> Plan:
    cores = max(1, cores)
    usable = max(1, cores - 1)                       # one core stays free for the database and everything else
    if free_mb is None:                              # memory unreadable: the documented 4-core / 8 GB host's figures
        return Plan(_MIN_WORKERS, _MIN_WORKERS, 1, cores, None)
    reserve = max(_RESERVE_MB, (total_mb or free_mb) // 10)
    budget = max(0, free_mb - reserve)
    by_cores = max(_MIN_WORKERS, min(_MAX_WORKERS, usable // 2))
    api = min(by_cores, max(_MIN_WORKERS, budget // _API_WORKER_MB))
    jobs = min(by_cores, max(_MIN_WORKERS, budget // _PROCESSING_WORKER_MB))
    helpers = min(usable, max(2, usable // jobs), _MAX_HELPERS)
    helpers = min(helpers, max(1, budget // (jobs * _HELPER_MB)))
    return Plan(api, jobs, max(1, helpers), cores, free_mb)


def auto_plan() -> Plan:
    return make_plan(os.cpu_count() or 1, memory.available_mb(), memory.total_mb())


def configured(name: str) -> int | None:
    """The number set by hand for ``name`` in the environment, or None when it is left to the automatic sizing
    (not set, empty, ``auto``, or not a number)."""
    value = os.environ.get(name, "").strip().lower()
    if value in ("", "auto"):
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def setting(name: str, automatic: int) -> int:
    chosen = configured(name)
    return automatic if chosen is None else chosen


def describe(plan: Plan, api: int, jobs: int, helpers: int) -> str:
    free = f"{plan.free_mb:,} MB free" if plan.free_mb is not None else "memory unknown"
    return (
        f"Sizing for this server ({plan.cores} CPU cores, {free}): {api} web workers, {jobs} processing workers, "
        f"{helpers} Excel helpers each. Anything set in .env is used as written."
    )
