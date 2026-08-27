import discord
import db

from logger import logger
from state import state

def setup(bot):

    @bot.tree.command(
        name="banned_list",
        description="List currently banned MACs"
    )
    async def list_banned(interaction: discord.Interaction):

        logger.info(
            f"ACTION: /banned_list | User: {interaction.user}"
        )

        try:
            await interaction.response.defer()

        except Exception as e:
            logger.error(f"Failed to defer /banned-list: {e}")
            return

        try:

            if state.banned_macs:

                manual_lines = []
                daily_lines = []

                for m in state.banned_macs:

                    device_name = state.macs_list.get(
                        m,
                        "Unknown Device"
                    )

                    reason = db.get_ban_reason(m)

                    if reason == "daily_limit":
                        daily_lines.append(device_name)
                    else:
                        manual_lines.append(device_name)

                sections = []

                if manual_lines:
                    sections.append("🔒 Manual / Other:")
                    sections += [
                        f"  {i:02d}. {name}"
                        for i, name in enumerate(manual_lines, 1)
                    ]

                if daily_lines:

                    if sections:
                        sections.append("")

                    sections.append("⏱️ Daily Limit:")
                    sections += [
                        f"  {i:02d}. {name}"
                        for i, name in enumerate(daily_lines, 1)
                    ]

                banned_output = (
                    "```\n"
                    + "\n".join(sections)
                    + "```"
                )

                count = len(state.banned_macs)
                embed_color = 0xe67e22

            else:

                banned_output = (
                    "✨ *No devices are currently under lockdown.*"
                )

                count = 0
                embed_color = 0x95a5a6

            embed = discord.Embed(
                title=f"`🚫` Blocked Devices ({count})",
                description=banned_output,
                color=embed_color
            )

            embed.set_footer(
                text="Use /rm to unblock a specific device"
            )

            await interaction.followup.send(
                embed=embed
            )

            logger.info(
                f"SUCCESS: /banned_list | Returned banned list ({count} devices)"
            )

        except Exception as e:

            logger.error(
                f"FAILURE in /banned_list command: {e}"
            )

            try:
                await interaction.followup.send(
                    "`❌` Failed to retrieve the list."
                )
            except Exception:
                pass
