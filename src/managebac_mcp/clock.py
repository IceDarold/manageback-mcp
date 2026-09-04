"""The school's wall clock.

Deadlines are scraped as bare local times ("8:40 AM") with no zone attached,
and the server does not run in the school's zone -- so every comparison against
"now" has to be made on the school's clock, or each one skews by the offset.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Nicosia"


def school_now(timezone: str = DEFAULT_TIMEZONE) -> datetime:
    """Now on the school's wall clock, naive, to match how deadlines are stored."""
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # an unknown zone must not take the connector down
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(zone).replace(tzinfo=None)
