from .moderation import (
    blk,
    rm,
    blkall,
    rmall,
    macs,
    banned_list,
    purge,
    wl
)

from .network import (
    balance,
    active,
    netstat,

)

from .system import (
    limit,
    reboot,
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
    reboot.setup(bot)
    wl.setup(bot)