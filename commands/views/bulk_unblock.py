import asyncio
import discord
import db
from logger import logger
from state import state, ROUTER_LOCK
from utils.validators import is_valid_mac
from router.firewall import enable_lockdown


class BulkUnblockSelect(discord.ui.Select):
    """Dropdown for selecting multiple devices to unblock."""
    def __init__(self, options):
        super().__init__(
            placeholder="Select devices to unblock...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # The invoking /rmall was admin-gated, but the posted menu itself is
        # clickable by anyone who can see it — enforce here too.
        user = interaction.user
        if isinstance(user, discord.Member) and user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "`❌` Only administrators can use this.", ephemeral=True
        )
        return False

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.error(f"Failed to defer BulkUnblockSelect: {e}")
            return

        selected_macs = self.values

        def _bulk_unban():
            removed = []
            for mac in selected_macs:
                mac = mac.upper()
                if not is_valid_mac(mac):
                    continue
                if mac in state.banned_macs:
                    state.banned_macs.remove(mac)
                    db.unban_device(mac)
                    removed.append(mac)
                    logger.info(f"Internal: Removed {mac} from banned set.")
            if removed:
                enable_lockdown(force_lock=state.lockdown_state)
            return [state.macs_list.get(m, "Unknown") for m in removed]

        async with ROUTER_LOCK:
            success_list = await asyncio.to_thread(_bulk_unban)

        lines = [
            f"{i:02d}. {state.macs_list.get(m, 'Unknown Device')}"
            for i, m in enumerate(state.banned_macs, 1)
        ]

        current_list = (
            "```\n" + "\n".join(lines) + "```"
            if lines else "No devices currently banned"
        )

        embed = discord.Embed(
            title="`✅` Bulk Unblock Completed",
            description=f"**Unblocked:** {', '.join(success_list)}",
            color=0x47ff47
        )

        embed.add_field(
            name="`📝` Remaining Banned List",
            value=current_list,
            inline=False
        )

        await interaction.followup.send(embed=embed)


class BulkUnblockView(discord.ui.View):
    """View containing BulkUnblockSelect."""
    def __init__(self, options):
        super().__init__(timeout=60)
        self.add_item(BulkUnblockSelect(options))