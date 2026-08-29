import db
from logger import logger
from state import state

ONBOARDING_STEP_TIMEOUT = 600  # seconds each question waits for an answer


def _device_name(mac: str) -> str:
    return state.macs_list.get(mac, "unknown")


class OnboardingSession:
    def __init__(
        self,
        mac: str,
        hostname: str,
        ip: str = None,
        rssi_dbm: int | None = None,
        distance_m: float | None = None,
        quality_pct: int | None = None,
    ):
        self.mac = mac
        self.hostname = hostname
        self.ip = ip
        self.rssi_dbm = rssi_dbm
        self.distance_m = distance_m
        self.quality_pct = quality_pct
        self.step = "q1"  # q1 -> q2/q3 -> done
        self.context = None  # "allowed" | "blocked" once Q1 answered
        self.whitelisted = False
        self.named = None  # custom name if one was assigned
        self.message = None  # the Discord message carrying the current question
        self.busy: bool = False
        self.retry_count: int = 0
        self.max_retries: int = 3


_sessions: dict[str, OnboardingSession] = {}


def drop_session(mac: str):
    """Forget any active session for this MAC (used on completion, timeout,
    and stale-device cleanup so no dangling reference survives deletion)."""
    _sessions.pop(mac.upper(), None)


def _device_gone(session: OnboardingSession) -> bool:
    """True when the underlying devices row disappeared (e.g. purged by the
    stale-device cleanup while this session was waiting)."""
    return not db.device_exists(session.mac)


async def _gone_notice(session: OnboardingSession):
    try:
        await session.message.edit(
            content=(
                f"`⚠️` Device record for `{session.mac}` no longer exists — "
                f"onboarding cancelled."
            ),
            embed=None,
            view=None,
        )
    except Exception as e:
        logger.warning(
            f"Could not update deleted-device message for {session.mac}: {e}"
        )
    drop_session(session.mac)
