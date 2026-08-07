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

class BotState:
    """Holds all bot-wide mutable state."""

    def __init__(self):
        self.threshold       = 3.0
        self.banned_macs     = set()
        self.macs_list       = {}
        self.allowed_macs    = []
        self.lockdown_state  = False
        self.ip_to_mac_cache = {}

    def reload_from_db(self):
        """(Re)load all cached state from the database.

        Call once at startup, right after db.init_db() has run, and again
        in on_ready() to resync after a reconnect.
        """
        self.banned_macs     = db.get_banned()
        self.macs_list       = db.get_devices()
        self.allowed_macs    = db.get_allowed()
        self.threshold       = db.get_threshold()
        self.lockdown_state  = db.get_lockdown_state()
        self.ip_to_mac_cache = {}
        
# Single shared instance — import this, don't instantiate BotState() again.
state = BotState()

# Shared lock serializing all router HTTP calls — lives here (not in
# bot_core) so command files can import it without importing the bot
# itself.
ROUTER_LOCK = asyncio.Lock()