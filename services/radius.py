import requests
from bs4 import BeautifulSoup

from config import D_PASSWORD, D_USERNAME, RADIUS_URL
from logger import logger
from radius.auth import hex_hmac_md5, hex_md5

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

        if login_resp.status_code != 200:
            logger.warning(
                f"Radius login returned HTTP {login_resp.status_code}: "
                f"{login_resp.text[:200]!r}"
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

        if dash.status_code != 200:
            logger.warning(
                f"Radius dashboard returned HTTP {dash.status_code}: "
                f"{dash.text[:200]!r}"
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
