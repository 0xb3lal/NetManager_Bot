"""
Shared mutable bot state, in one place.

Every file that needs to read or write shared data (banned MACs, allowed MACs, threshold, etc.)

does: from state import state, ROUTER_LOCK

and then reads/writes attributes on `state` directly, e.g.:

    state.banned_macs.add(mac)
    state.threshold = 5.0

Never do `state = something_else` (that only rebinds the name inside your
own file). Always mutate attributes on the existing `state` object, so the
change is visible from every other file that imported the same instance.
"""

import asyncio
import db
from logger import logger

class BotState:

    def __init__(self):
        self.threshold       = 3.0
        self.banned_macs     = set()
        self.macs_list       = {}
        self.allowed_macs    = []
        self.pending_macs    = set()   # MACs still awaiting onboarding (firewall-dropped)
        self.lockdown_state  = False
        self.ip_to_mac_cache = {}

    def reload_from_db(self):
        """Reload cached state from DB (call at startup and on reconnect)."""
        self.banned_macs     = db.get_banned()
        self.macs_list       = db.get_devices()
        self.allowed_macs    = db.get_allowed()
        self.pending_macs    = set(db.get_pending_devices())
        self.threshold       = db.get_threshold()
        self.lockdown_state  = db.get_lockdown_state()
        self.ip_to_mac_cache = {}

state = BotState()

# bot_core) so command files can import it without importing the bot
# itself.
ROUTER_LOCK = asyncio.Lock()

# quota (usage_db.extra_quota table). Without this, two near-simultaneous
# /quota calls (e.g. an add racing an edit, or a double-fired interaction)
# can both read the same starting value and one update silently overwrites
# the other. Always hold this around any get_extra_quota() + set_extra_quota()
# pair that depends on the value just read.
QUOTA_LOCK = asyncio.Lock()

ROUTER_LOCK_WAIT_TIMEOUT = 30

async def acquire_router_lock_bounded(caller: str) -> bool:
    """Wait up to 30s for ROUTER_LOCK; return True if acquired."""
    try:
        await asyncio.wait_for(ROUTER_LOCK.acquire(), timeout=ROUTER_LOCK_WAIT_TIMEOUT)
        return True
    except asyncio.TimeoutError:
        logger.warning(
            f"{caller} skipped this cycle: the router lock stayed busy for "
            f"longer than the {ROUTER_LOCK_WAIT_TIMEOUT}s wait cap (expected occasional "
            f"contention with device discovery / reporting / manual commands), so this "
            f"check was aborted instead of blocking. It will retry on the next cycle."
        )
        return False
