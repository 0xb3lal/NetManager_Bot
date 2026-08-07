import asyncio
import requests
import discord

from bs4 import BeautifulSoup

import db

from config import (
    D_USERNAME,
    D_PASSWORD,
    RADIUS_URL,
    CHANNEL_ID,
)

from logger import logger
from state import state, ROUTER_LOCK

from router.firewall import enable_lockdown

from radius.auth import (
    hex_md5,
    hex_hmac_md5,
)

from utils.traffic import parse_traffic_to_gb


RADIUS_SESSION = requests.Session()


def fetch_radius_traffic(verbose=False):
    """
    Fetch available traffic from Radius dashboard.

    verbose=False:
        Silent mode (used by auto traffic checker).

    verbose=True:
        Logs success/failure (used by /balance).
    """

    md5_password = hex_md5(D_PASSWORD)
    md5_final = hex_hmac_md5(
        D_USERNAME,
        md5_password
    )

    payload = {
        "username": D_USERNAME,
        "md5": md5_final,
        "Submit": "Submit"
    }

    try:
        login_resp = RADIUS_SESSION.post(
            f"{RADIUS_URL}/radiusmanager/user.php?cont=login",
            data=payload,
            timeout=30
        )

        if verbose:
            login_resp.raise_for_status()

        RADIUS_SESSION.get(
            f"{RADIUS_URL}/radiusmanager/user.php?cont=change_lang&lang=English",
            timeout=30
        )

        dash = RADIUS_SESSION.get(
            f"{RADIUS_URL}/radiusmanager/user.php",
            timeout=30
        )

        if verbose:
            dash.raise_for_status()

        soup = BeautifulSoup(
            dash.text,
            "html.parser"
        )

        for td in soup.find_all("td"):

            if "Available total traffic" in td.get_text(strip=True):

                balance = td.find_next_sibling("td").get_text(strip=True)

                if verbose:
                    logger.info(
                        f"Successfully fetched balance: {balance}"
                    )

                return balance

        if verbose:
            logger.warning(
                "Balance field not found in dashboard HTML."
            )

        return None

    except requests.exceptions.Timeout:

        if verbose:
            logger.error(
                "Timeout: Radius Dashboard is not responding."
            )

        return None

    except requests.exceptions.ConnectionError:

        if verbose:
            logger.error(
                "Connection Error: Could not connect to Radius."
            )

        return None

    except Exception as e:

        if verbose:
            logger.error(
                f"Unexpected error in fetch_radius_traffic: {e}"
            )

        return None


def apply_lockdown_for_traffic(traffic_value):
    """
    Apply lockdown based on traffic threshold.
    """

    if traffic_value < state.threshold:

        enable_lockdown(
            force_lock=True
        )

        state.lockdown_state = True
        db.set_lockdown_state(True)

        return "`❌` System Lockdown", 0xff4747

    else:

        enable_lockdown(
            force_lock=False
        )

        state.lockdown_state = False
        db.set_lockdown_state(False)

        return "`✅` System Normal", 0x47ff7e


async def async_check_and_lock(bot_instance):
    """
    Check traffic and apply lockdown asynchronously.
    """

    try:

        available_traffic = await asyncio.to_thread(
            fetch_radius_traffic
        )

        if not available_traffic:
            return

        traffic_value = parse_traffic_to_gb(
            available_traffic
        )

        async with ROUTER_LOCK:

            e_title, e_color = await asyncio.to_thread(
                apply_lockdown_for_traffic,
                traffic_value
            )

        status_box = (
            "```\n"
            f"{'Balance:'.ljust(9)} {available_traffic}\n"
            f"{'Limit:'.ljust(9)} {state.threshold} GB\n"
            "```"
        )

        embed = discord.Embed(
            title=e_title,
            description=status_box,
            color=e_color
        )

        try:

            channel = bot_instance.get_channel(
                CHANNEL_ID
            )

            if channel:
                await channel.send(
                    embed=embed
                )

        except Exception:

            logger.error(
                "Failed to push status update to Discord"
            )

    except Exception as e:

        logger.error(
            f"Main Check Error: {e}"
        )