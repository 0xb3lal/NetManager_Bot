from requests.exceptions import ReadTimeout, ConnectionError
from logger import logger
from config import *

def run_cmd(cmd, _retry=True):
    """Execute command on router and return success status."""
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        logger.debug(f"Sending Command to Router: {cmd}")
        response = ROUTER_SESSION.post(f"{ROUTER_URL}/shell.cgi", data=data, timeout=30)
        if response.status_code == 200:
            logger.debug(f"Router executed: {cmd} successfully.")
            return True
        else:
            logger.error(f"Router returned error code {response.status_code} for command: {cmd}")
            return False
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        if _retry:
            logger.warning(f"Retrying command once: {cmd}")
            return run_cmd(cmd, _retry=False)
        return False
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd: {e}")
        return False

def run_cmd_output(cmd, timeout=15):
    """Execute command on router and return output."""
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = ROUTER_SESSION.post(f"{ROUTER_URL}/shell.cgi", data=data, timeout=timeout)
        if response.status_code == 200:
            return response.text
        logger.error(f"Router returned error code {response.status_code} for command: {cmd}")
        return None
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd_output: {e}")
        return None

def reboot_router():
    """Send reboot command to router."""
    data = "action=execute&command=reboot\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = ROUTER_SESSION.post(f"{ROUTER_URL}/shell.cgi", data=data, timeout=10)
        if response.status_code == 200:
            logger.warning("Reboot command acknowledged by router (HTTP 200).")
            return True
        logger.error(f"Router responded with unexpected status {response.status_code} for reboot command.")
        return False
    except (ReadTimeout, ConnectionError) as e:
        logger.warning(f"Connection dropped while router was rebooting (expected): {e}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reboot command: {e}")
        return False

def ping_router(timeout=10):
    """Send a lightweight 'true' shell command to check router responsiveness.

    Returns the requests.Response on success. Raises the underlying requests
    exception on failure so each caller can keep its own error handling/logging.
    """
    return ROUTER_SESSION.post(
        f"{ROUTER_URL}/shell.cgi",
        data="action=execute&command=true\n&_http_id=TIDe5b1505eeac7f67f",
        timeout=timeout
    )

def is_router_alive():
    """Check if router is responding."""
    try:
        r = ping_router(timeout=5)
        return r.status_code == 200
    except Exception:
        return False
    