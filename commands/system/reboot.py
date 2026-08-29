import asyncio

import discord
from discord import app_commands

from logger import logger
from router.client import is_router_alive
from router.client import reboot_router as send_reboot_command
from state import ROUTER_LOCK


async def _wait_for_router_and_notify(channel, user_mention):
    await asyncio.sleep(15)

    max_wait = 300
    interval = 10
    waited = 0

    logger.info("Started watching for router recovery after manual reboot.")

    while waited < max_wait:
        try:
            async with ROUTER_LOCK:
                alive = await asyncio.to_thread(is_router_alive)

        except Exception as e:
            logger.error(f"Error while checking router recovery: {e}")
            alive = False

        if alive:
            embed = discord.Embed(
                title="`✅` Router is Back Online",
                description=(
                    f"{user_mention} I'm up! The router has rebooted "
                    "successfully and is responding again."
                ),
                color=0x2ECC71,
            )

            try:
                await channel.send(embed=embed)
            except Exception as e:
                logger.error(f"Failed to send router recovery notice: {e}")

            logger.info("Router recovery confirmed and notified.")
            return

        await asyncio.sleep(interval)
        waited += interval

    try:
        embed = discord.Embed(
            title="`⚠️` Router Still Unreachable",
            description=(
                f"{user_mention} The router hasn't come back online "
                f"{max_wait // 60} minutes after the reboot. "
                "Please check it manually."
            ),
            color=0xE67E22,
        )

        await channel.send(embed=embed)

    except Exception as e:
        logger.error(f"Failed to send router recovery timeout notice: {e}")

    logger.warning("Router did not come back online within the expected window.")


class RebootConfirmView(discord.ui.View):

    def __init__(self, requester_id):
        super().__init__(timeout=30)
        self.requester_id = requester_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "`⚠️` Only the user who issued this command " "can confirm it.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(
        label="Confirm",
        style=discord.ButtonStyle.danger,
        emoji="✅",
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        for child in self.children:
            child.disabled = True

        try:
            await interaction.response.edit_message(
                content=(
                    "`🔄` Reboot confirmed. Sending the command " "to the router now..."
                ),
                view=self,
            )

        except Exception as e:
            logger.error(f"Failed to edit reboot confirmation message: {e}")

        logger.warning(f"Router reboot CONFIRMED by {interaction.user}")

        logger.info("Waiting 5 seconds before sending the actual " "reboot command...")

        await asyncio.sleep(5)

        logger.info("Done waiting. Sending the reboot command " "to the router now.")

        try:
            async with ROUTER_LOCK:
                success = await asyncio.to_thread(send_reboot_command)

        except Exception as e:
            logger.exception(f"Unexpected error while sending reboot command: {e}")
            success = False

        try:
            if success:
                logger.info(
                    "Sending 'reboot command sent successfully' " "message to channel."
                )

                await interaction.channel.send(
                    f"{interaction.user.mention} "
                    "`✅` Reboot command was sent successfully."
                )

            else:
                logger.info("Sending 'reboot command failed' " "message to channel.")

                await interaction.channel.send(
                    f"{interaction.user.mention} "
                    "`❌` Failed to send the reboot command. "
                    "Check logs."
                )

        except Exception as e:
            logger.error(f"Failed to send reboot result message: {e}")

        if success:
            try:
                asyncio.create_task(
                    _wait_for_router_and_notify(
                        interaction.channel,
                        interaction.user.mention,
                    )
                )

                logger.info("Router recovery watcher scheduled.")

            except Exception as e:
                logger.exception(f"Failed to schedule router recovery watcher: {e}")

        else:
            logger.warning(
                "Skipping recovery watcher because the reboot "
                "command was not confirmed as sent."
            )

        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="`✖️` Reboot cancelled.",
            view=self,
        )

        self.stop()


@app_commands.checks.has_permissions(administrator=True)
async def reboot(
    interaction: discord.Interaction,
):

    try:
        await interaction.response.send_message(
            (
                "`⚠️` **Are you sure you want to reboot the router?**\n"
                "It will be unreachable for a minute or two."
            ),
            view=RebootConfirmView(interaction.user.id),
        )

        logger.info(
            f"Reboot requested by {interaction.user} " "(awaiting confirmation)"
        )

    except Exception as e:
        logger.error(f"Error showing reboot confirmation: {e}")


def setup(bot):
    bot.tree.add_command(
        app_commands.Command(
            name="reboot",
            description="Reboot the router (requires confirmation)",
            callback=reboot,
        )
    )
