import asyncio
import discord
import db
from logger import logger
from state import state, ROUTER_LOCK
from utils.validators import is_valid_mac
from router.firewall import enable_lockdown


class BulkBlockSelect(discord.ui.Select):
    """Dropdown for selecting multiple devices to block."""
    def __init__(self, options):
        super().__init__(
            placeholder="Select devices to block...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer BulkBlockSelect: {e}")
            return

        selected_macs = [m.upper() for m in self.values]

        def _bulk_ban():
            added = []
            for mac in selected_macs:
                if not is_valid_mac(mac):
                    continue
                if mac not in state.banned_macs:
                    state.banned_macs.add(mac)
                    db.ban_device(mac, reason="manual")
                    added.append(mac)
                    logger.info(f"Internal: Added {mac} to banned set.")
            if added:
                enable_lockdown(force_lock=state.lockdown_state)
            return [state.macs_list.get(m, "Unknown") for m in added]

        async with ROUTER_LOCK:
            success_list = await asyncio.to_thread(_bulk_ban)

        lines = [
            f"{i:02d}. {state.macs_list.get(m, 'Unknown Device')}"
            for i, m in enumerate(state.banned_macs, 1)
        ]

        current_list = (
            "```\n" + "\n".join(lines) + "```"
            if lines else "No devices currently banned"
        )

        embed = discord.Embed(
            title="`🚫` Bulk Block Completed",
            description=f"**Blocked:** {', '.join(success_list)}",
            color=0xff4747
        )

        embed.add_field(
            name="`📝` Updated Banned List",
            value=current_list,
            inline=False
        )

        await interaction.followup.send(embed=embed)


class BulkBlockView(discord.ui.View):
    """View containing BulkBlockSelect."""
    def __init__(self, options):
        super().__init__(timeout=60)
        self.add_item(BulkBlockSelect(options))