from .moderation import (
    blk,
    rm,
    blkall,
    rmall,
    macs,
    banned_list,
    purge,
    pending,
    wl
)

from .network import (
    balance,
    active,
    netstat,

)

from .system import (
    limit,
    quota,
    reboot,
    tglink,
    device,
    rename
)

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
