import re
import db
from logger import logger
from state import state
from config import *
from utils.validators import is_valid_mac
from router.client import run_cmd, run_cmd_output


def _kick_non_allowed_devices():
    """Deauthenticate non-whitelisted devices during force lockdown."""
    output = run_cmd_output(f"wl -i {WIFI_IFACE} assoclist")
    if not output:
        return
    connected_macs = re.findall(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", output)
    for mac in connected_macs:
        mac = mac.upper()
        if mac not in state.allowed_macs:
            logger.info(f"Kicking non-allowed device during lockdown: {mac}")
            run_cmd(f"wl -i {WIFI_IFACE} deauthenticate {mac}")


def enable_lockdown(force_lock=False):
    """Apply firewall lockdown rules via iptables."""
    mode = "FORCE (Whitelist only)" if force_lock else "NORMAL (Banning list)"
    logger.info(f"Applying Firewall Lockdown: Mode={mode}")

    run_cmd("iptables -F LOCKDOWN 2>/dev/null")
    run_cmd("iptables -X LOCKDOWN 2>/dev/null")
    run_cmd("iptables -N LOCKDOWN 2>/dev/null")

    if force_lock:
        for mac in state.allowed_macs:
            if is_valid_mac(mac):
                run_cmd(f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd("iptables -A LOCKDOWN -j DROP")
        logger.debug(f"Whitelist applied: {len(state.allowed_macs)} devices allowed, others dropped.")
    else:
        # Order matters: banned first, then PENDING (unreviewed) devices are
        # dropped unconditionally — fail-safe default until onboarding is done.
        for mac in state.banned_macs:
            if is_valid_mac(mac):
                run_cmd(f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j DROP")
        for mac in state.pending_macs:
            if is_valid_mac(mac):
                run_cmd(f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j DROP")
        for mac in state.allowed_macs:
            if is_valid_mac(mac):
                run_cmd(f"iptables -A LOCKDOWN -m mac --mac-source {mac} -j ACCEPT")
        run_cmd("iptables -A LOCKDOWN -j ACCEPT")
        logger.debug(
            f"Banned list applied: {len(state.banned_macs)} dropped, "
            f"{len(state.pending_macs)} pending-onboarding dropped."
        )

    run_cmd("iptables -D FORWARD -i br0 -j LOCKDOWN 2>/dev/null")
    run_cmd("iptables -I FORWARD 1 -i br0 -j LOCKDOWN")
    logger.info("Firewall rules synchronized successfully.")

    if force_lock:
        _kick_non_allowed_devices()


def reapply_firewall_state():
    """Reapply firewall rules during bot startup."""
    enable_lockdown(force_lock=state.lockdown_state)


def ban_mac(mac, reason="manual"):
    """Add MAC to banned list and update firewall."""
    mac = mac.upper()
    if not is_valid_mac(mac):
        logger.error(f"Invalid MAC format attempt: {mac}")
        return
    if mac not in state.banned_macs:
        state.banned_macs.add(mac)
        db.ban_device(mac, reason=reason)
        logger.info(f"Internal: Added {mac} to banned set (reason={reason}).")
        enable_lockdown(force_lock=state.lockdown_state)
    elif db.ban_device(mac, reason=reason):
        logger.warning(
            f"Internal: {mac} was already banned — ban reason overwritten to '{reason}'."
        )
    else:
        logger.debug(f"Internal: {mac} already banned with reason '{reason}', nothing to change.")


def unban_mac(mac):
    """Remove MAC from banned list and update firewall."""
    mac = mac.upper()
    if not is_valid_mac(mac):
        return
    if mac in state.banned_macs:
        state.banned_macs.remove(mac)
        db.unban_device(mac)
        logger.info(f"Internal: Removed {mac} from banned set.")
        enable_lockdown(force_lock=state.lockdown_state)
    else:
        logger.warning(f"Internal: Attempted to unban {mac} but it wasn't in the list.")