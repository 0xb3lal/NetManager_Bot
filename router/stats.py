from config import ROUTER_SESSION, ROUTER_URL
from logger import logger


def reset_ip_traffic_stats():
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = (
        "_nextpage=%2F%23admin-iptraffic.asp&_service=cstatsnew-restart&cstats_enable=1"
        "&cstats_path=%2Fjffs%2F&cstats_sshut=1&cstats_bak=0&cstats_all=1&f_cstats_enable=on"
        "&f_loc=%2Fjffs%2F&f_user=%2Fjffs%2F&cstats_stime=1&f_sshut=on&f_new=on&cstats_offset=1"
        "&cstats_exclude=&f_all=on&cstats_labels=0&_http_id=TIDe5b1505eeac7f67f"
    )
    try:
        response = ROUTER_SESSION.post(
            f"{ROUTER_URL}/tomato.cgi", headers=headers, data=data, timeout=30
        )
        if response.status_code == 200:
            logger.info("IP Traffic stats reset successfully.")
            return True
        logger.error(
            f"IP Traffic stats reset failed with status {response.status_code}"
        )
        return False
    except Exception as e:
        logger.error(f"Error resetting IP Traffic stats: {e}")
        return False


def reset_bandwidth_stats():
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = (
        "_nextpage=%2F%23admin-bwm.asp&_service=rstatsnew-restart&rstats_enable=1"
        "&rstats_path=%2Fjffs%2F&rstats_sshut=1&rstats_bak=0&f_rstats_enable=on"
        "&f_loc=%2Fjffs%2F&f_user=%2Fjffs%2F&rstats_stime=1&f_sshut=on&f_new=on"
        "&rstats_offset=1&rstats_exclude=&_http_id=TIDe5b1505eeac7f67f"
    )
    try:
        response = ROUTER_SESSION.post(
            f"{ROUTER_URL}/tomato.cgi", headers=headers, data=data, timeout=30
        )
        if response.status_code == 200:
            logger.info("Bandwidth stats reset successfully.")
            return True
        logger.error(f"Bandwidth stats reset failed with status {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Error resetting Bandwidth stats: {e}")
        return False
