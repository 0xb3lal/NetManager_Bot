import asyncio

import discord
import requests
from bs4 import BeautifulSoup

import db
from config import CHANNEL_ID, D_PASSWORD, D_USERNAME, RADIUS_URL
from logger import logger
from radius.auth import hex_hmac_md5, hex_md5
from router.firewall import enable_lockdown
from state import ROUTER_LOCK, state
from utils.traffic import format_data_size, parse_traffic_to_gb

RADIUS_LOGIN_TIMEOUT = 30


def fetch_radius_traffic(verbose=False):
    """Fetch remaining traffic from Radius dashboard (verbose logs or silent)."""

    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(D_USERNAME, md5_password)

    payload = {"username": D_USERNAME, "md5": md5_final, "Submit": "Submit"}

    # several concurrent entry points (hourly check, /balance, /limit), and
    # requests.Session is not safe for cross-thread sharing.
    session = requests.Session()

    try:
        login_resp = session.post(
            f"{RADIUS_URL}/radiusmanager/user.php?cont=login",
            data=payload,
            timeout=RADIUS_LOGIN_TIMEOUT,
        )

        if verbose:
            login_resp.raise_for_status()

        session.get(
            f"{RADIUS_URL}/radiusmanager/user.php?cont=change_lang&lang=English",
            timeout=RADIUS_LOGIN_TIMEOUT,
        )

        dash = session.get(
            f"{RADIUS_URL}/radiusmanager/user.php", timeout=RADIUS_LOGIN_TIMEOUT
        )

        if verbose:
            dash.raise_for_status()

        soup = BeautifulSoup(dash.text, "html.parser")

        for td in soup.find_all("td"):

            if "Available total traffic" in td.get_text(strip=True):

                balance = td.find_next_sibling("td").get_text(strip=True)

                if verbose:
                    logger.info(f"Successfully fetched balance: {balance}")

                return balance

        logger.warning("Balance field not found in dashboard HTML.")

        return None

    except requests.exceptions.Timeout:

        logger.error("Timeout: Radius Dashboard is not responding.")

        return None

    except requests.exceptions.ConnectionError:

        logger.error("Connection Error: Could not connect to Radius.")

        return None

    except Exception as e:

        logger.error(f"Unexpected error in fetch_radius_traffic: {e}")

        return None

    finally:
        session.close()


def apply_lockdown_for_traffic(traffic_value):
    """Apply firewall lockdown based on remaining traffic threshold."""

    if traffic_value < state.threshold:

        enable_lockdown(force_lock=True)

        state.lockdown_state = True
        db.set_lockdown_state(True)

        return "`❌` System Lockdown", 0xFF4747

    else:

        enable_lockdown(force_lock=False)

        state.lockdown_state = False
        db.set_lockdown_state(False)

        return "`✅` System Normal", 0x47FF7E


async def async_check_and_lock(bot_instance):
    """Async wrapper for traffic check and lockdown."""

    try:

        available_traffic = await asyncio.to_thread(fetch_radius_traffic)

        if not available_traffic:
            return

        traffic_value = parse_traffic_to_gb(available_traffic)

        async with ROUTER_LOCK:

            e_title, e_color = await asyncio.to_thread(
                apply_lockdown_for_traffic, traffic_value
            )

        status_box = (
            "```\n"
            f"{'Balance:'.ljust(9)} {available_traffic}\n"
            f"{'Limit:'.ljust(9)} {format_data_size(state.threshold)}\n"
            "```"
        )

        embed = discord.Embed(title=e_title, description=status_box, color=e_color)

        try:

            channel = bot_instance.get_channel(CHANNEL_ID)

            if channel:
                await channel.send(embed=embed)

        except Exception:

            logger.error("Failed to push status update to Discord")

    except Exception as e:

        logger.error(f"Main Check Error: {e}")
