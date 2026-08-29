from .moderation import banned_list, blk, blkall, macs, pending, purge, rm, rmall, wl
from .network import active, balance, netstat
from .system import device, limit, quota, reboot, rename, tglink


def setup(bot):
    blk.setup(bot)
    rm.setup(bot)
    blkall.setup(bot)
    rmall.setup(bot)
    macs.setup(bot)
    banned_list.setup(bot)
    balance.setup(bot)
    active.setup(bot)
    netstat.setup(bot)
    limit.setup(bot)
    purge.setup(bot)
    pending.setup(bot)
    reboot.setup(bot)
    wl.setup(bot)
    quota.setup(bot)
    tglink.setup(bot)
    device.setup(bot)
    rename.setup(bot)
