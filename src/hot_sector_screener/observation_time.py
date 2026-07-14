from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TIMEZONE_NAME = "Asia/Shanghai"
MARKET_TIMEZONE = ZoneInfo(MARKET_TIMEZONE_NAME)
OBSERVATION_COMPLETE_TIME = time(16, 0)
DEFAULT_OBSERVATION_SOURCES = (
    "dc_concept",
    "dc_concept_cons",
    "kpl_concept_cons",
    "kpl_list",
    "limit_step",
    "limit_cpt_list",
    "limit_list_ths",
)


def shanghai_now() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def parse_calendar_date(value: object) -> date:
    """Parse an exact YYYYMMDD or YYYY-MM-DD calendar date."""
    raw = str(value).strip()
    formats = []
    if len(raw) == 8 and raw.isdigit():
        formats.append("%Y%m%d")
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        formats.append("%Y-%m-%d")
    for date_format in formats:
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid calendar date: {value!r}; expected YYYYMMDD or YYYY-MM-DD")


def date_key(value: object) -> str:
    return parse_calendar_date(value).strftime("%Y%m%d")


def _as_shanghai(now: datetime | None) -> datetime:
    if now is None:
        return shanghai_now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(MARKET_TIMEZONE)


def latest_completed_date(now: datetime | None = None) -> date:
    current = _as_shanghai(now)
    if current.timetz().replace(tzinfo=None) >= OBSERVATION_COMPLETE_TIME:
        return current.date()
    return current.date() - timedelta(days=1)


def _latest_common_date(sources: tuple[str, ...], not_after: str) -> str | None:
    from .data_sources.platform import latest_common_date

    return latest_common_date(sources, not_after=not_after)


def resolve_observation_date(
    requested: str | None,
    *,
    sources: tuple[str, ...] = DEFAULT_OBSERVATION_SOURCES,
    now: datetime | None = None,
) -> str:
    """Resolve a completed, non-future Shanghai-market observation date."""
    current = _as_shanghai(now)
    completed_through = latest_completed_date(current)

    if requested is None:
        resolved = _latest_common_date(
            sources,
            completed_through.strftime("%Y%m%d"),
        )
        if resolved is None:
            raise RuntimeError(
                "No completed common observation date for sources: " + ",".join(sources)
            )
        observation = parse_calendar_date(resolved)
    else:
        observation = parse_calendar_date(requested)

    if observation > current.date():
        raise ValueError(
            f"Observation date {observation} is in the future in {MARKET_TIMEZONE_NAME}"
        )
    if observation > completed_through:
        raise ValueError(
            f"Observation date {observation} is not complete until "
            f"{OBSERVATION_COMPLETE_TIME.isoformat(timespec='minutes')} "
            f"{MARKET_TIMEZONE_NAME}"
        )
    return observation.strftime("%Y%m%d")
