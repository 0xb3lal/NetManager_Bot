from requests.exceptions import ConnectionError, ReadTimeout

from config import *
from logger import logger


def run_cmd(cmd, _retry=True):
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        logger.debug(f"Sending Command to Router: {cmd}")
        response = ROUTER_SESSION.post(f"{ROUTER_URL}/shell.cgi", data=data, timeout=30)
        if response.status_code == 200:
            logger.debug(f"Router executed: {cmd} successfully.")
            return True
        else:
            logger.error(
                f"Router returned error code {response.status_code} for command: {cmd}"
            )
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
    """Execute command on router and return raw shell.cgi output (JS-wrapped)."""
    data = f"action=execute&command={cmd}\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = ROUTER_SESSION.post(
            f"{ROUTER_URL}/shell.cgi", data=data, timeout=timeout
        )
        if response.status_code == 200:
            return response.text
        logger.error(
            f"Router returned error code {response.status_code} for command: {cmd}"
        )
        return None
    except (ReadTimeout, ConnectionError) as e:
        logger.error(f"Router Connection Error while executing '{cmd}': {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in run_cmd_output: {e}")
        return None


def _decode_js_escapes(s: str) -> str:
    """Decode Tomato shell.cgi JS escapes (\\xNN, \\n, etc.)."""
    res = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "x" and i + 3 < n:
                hex_part = s[i + 2 : i + 4]
                try:
                    res.append(chr(int(hex_part, 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if nxt == "n":
                res.append("\n")
                i += 2
                continue
            if nxt == "r":
                res.append("\r")
                i += 2
                continue
            if nxt == "t":
                res.append("\t")
                i += 2
                continue
            if nxt == "\\":
                res.append("\\")
                i += 2
                continue
            if nxt == "'":
                res.append("'")
                i += 2
                continue
            if nxt == '"':
                res.append('"')
                i += 2
                continue
            res.append(c)
            res.append(nxt)
            i += 2
            continue
        res.append(c)
        i += 1
    return "".join(res)


def parse_shell_cgi_result(text: str) -> str:
    """Extract and decode cmdresult payload from shell.cgi response.

    Returns None when the response is not a genuine cmdresult= payload
    (e.g. a login page, an HTML error page, or an empty body). Callers
    must treat None as "read failed" — never as an empty command result.
    A genuine shell.cgi response always carries the cmdresult wrapper,
    even when the command output is empty.
    """
    if text is None:
        return None
    import re

    m = re.search(r"cmdresult\s*=\s*'(.*)'\s*;?\s*\Z", text, re.DOTALL)
    if m:
        inner = m.group(1)
        return _decode_js_escapes(inner)
    m2 = re.search(r'cmdresult\s*=\s*"(.*)"\s*;?\s*\Z', text, re.DOTALL)
    if m2:
        inner = m2.group(1)
        return _decode_js_escapes(inner)
    logger.warning(
        "shell.cgi response missing cmdresult marker — treating as read failure"
    )
    return None


def run_cmd_output_value(cmd, timeout=15):
    raw = run_cmd_output(cmd, timeout=timeout)
    if raw is None:
        return None
    return parse_shell_cgi_result(raw)


def reboot_router():
    data = "action=execute&command=reboot\n&_http_id=TIDe5b1505eeac7f67f"
    try:
        response = ROUTER_SESSION.post(f"{ROUTER_URL}/shell.cgi", data=data, timeout=10)
        if response.status_code == 200:
            logger.warning("Reboot command acknowledged by router (HTTP 200).")
            return True
        logger.error(
            f"Router responded with unexpected status {response.status_code} for reboot command."
        )
        return False
    except (ReadTimeout, ConnectionError) as e:
        logger.warning(f"Connection dropped while router was rebooting (expected): {e}")
        return True
    except Exception as e:
        logger.error(f"Failed to send reboot command: {e}")
        return False


def ping_router(timeout=10):
    """Probe router liveness via a lightweight 'true' command."""
    return ROUTER_SESSION.post(
        f"{ROUTER_URL}/shell.cgi",
        data="action=execute&command=true\n&_http_id=TIDe5b1505eeac7f67f",
        timeout=timeout,
    )


def is_router_alive():
    try:
        r = ping_router(timeout=5)
        return r.status_code == 200
    except Exception:
        return False
