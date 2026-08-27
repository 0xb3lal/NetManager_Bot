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
    """Execute command on router and return output.

    NOTE: Tomato /shell.cgi wraps output as `cmdresult = '...';` with JS escapes.
    This function preserves historic raw behavior for callers that already handle
    the wrapper (e.g. firewall wl assoclist regex). For nvram/raw values use
    `parse_shell_cgi_result()` or `run_cmd_output_value()`.
    """
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


def _decode_js_escapes(s: str) -> str:
    r"""Decode Tomato shell.cgi JS-escaped string.

    Handles: \x5c -> \, \x27 -> ', \x22 -> \", \\ -> \, \n/\r/\t,
    \' / \", and generic \xNN hex. Does NOT use eval().
    """
    res = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "x" and i + 3 < n:
                hex_part = s[i + 2:i + 4]
                try:
                    res.append(chr(int(hex_part, 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            if nxt == "n":
                res.append("\n"); i += 2; continue
            if nxt == "r":
                res.append("\r"); i += 2; continue
            if nxt == "t":
                res.append("\t"); i += 2; continue
            if nxt == "\\":
                res.append("\\"); i += 2; continue
            if nxt == "'":
                res.append("'"); i += 2; continue
            if nxt == '"':
                res.append('"'); i += 2; continue
            res.append(c)
            res.append(nxt)
            i += 2
            continue
        res.append(c)
        i += 1
    return "".join(res)


def parse_shell_cgi_result(text: str) -> str:
    r"""Extract and JS-decode the actual command output from shell.cgi response.

    Tomato returns e.g.  cmdresult = 'D6\\x5c:E8...';\n
    This returns the decoded inner value.
    If no wrapper found, returns text JS-decoded as best-effort fallback.
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
    return _decode_js_escapes(text)


def run_cmd_output_value(cmd, timeout=15):
    """Execute command and return decoded actual stdout (wrapper removed)."""
    raw = run_cmd_output(cmd, timeout=timeout)
    if raw is None:
        return None
    return parse_shell_cgi_result(raw)

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

