import asyncio
import datetime

import discord
from discord import app_commands

from logger import logger
from utils.embeds import error_embed

MAX_PURGE_AMOUNT = 1000


@app_commands.command(name="purge", description="Delete messages in the channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    action="Delete a specific amount or every message in the channel",
    amount="Number of messages to delete (required when action is Custom Amount)",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Custom Amount", value="amount"),
        app_commands.Choice(name="All Messages", value="all_messages"),
    ]
)
async def purge(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    amount: int = None,
):
    if action.value == "amount" and not amount:
        try:
            await interaction.response.send_message(
                embed=error_embed(
                    "`❌` Provide an amount when action is set to Custom Amount."
                ),
                ephemeral=True,
            )
        except Exception as e:
            logger.warning(f"Failed to respond to invalid /purge args: {e}")
        return

    limit = (
        None
        if action.value == "all_messages"
        else max(1, min(amount, MAX_PURGE_AMOUNT))
    )

    try:
        await interaction.response.defer(ephemeral=True)
    except Exception as e:
        logger.warning(f"Failed to defer /purge (ignoring): {e}")
        return

    label = "the entire channel" if limit is None else f"up to **{limit}** messages"

    try:
        start_embed = discord.Embed(
            title="`🧹` Purge Started",
            description=f"Starting purge of {label}… I'll update this message when done.",
            color=0x3498DB,
        )
        start_embed.set_footer(text=f"Requested by {interaction.user}")
        await interaction.edit_original_response(content=None, embed=start_embed)
    except Exception:
        logger.warning("Could not send initial purge ack (interaction expired).")

    asyncio.create_task(_run_purge(interaction, limit))


async def _run_purge(interaction: discord.Interaction, limit: int | None):
    channel = interaction.channel
    two_weeks_ago = discord.utils.utcnow() - datetime.timedelta(days=14)

    try:
        recent_ids = []
        old_ids = []

        async for msg in channel.history(limit=limit):
            if msg.created_at > two_weeks_ago:
                recent_ids.append(msg.id)
            else:
                old_ids.append(msg.id)

        deleted_count = 0

        for i in range(0, len(recent_ids), 100):
            chunk = recent_ids[i : i + 100]
            if len(chunk) == 1:
                try:
                    await channel.get_partial_message(chunk[0]).delete()
                except Exception:
                    pass
            else:
                await channel.delete_messages([discord.Object(id=mid) for mid in chunk])
            deleted_count += len(chunk)
            await asyncio.sleep(1)

        for mid in old_ids:
            try:
                await channel.get_partial_message(mid).delete()
                deleted_count += 1
                await asyncio.sleep(1)
            except discord.NotFound:
                continue
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, "retry_after", 2))
                else:
                    logger.warning(f"Failed to delete message {mid} in purge: {e}")

        embed = discord.Embed(
            title="`🧹` Purge Completed",
            description=f"`✅` Deleted **{deleted_count}** messages from the channel.",
            color=0x2ECC71,
        )
        embed.set_footer(text=f"Requested by {interaction.user}")

        try:
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception as e:
            logger.warning(f"Failed to edit purge completion message: {e}")

    except Exception as e:
        logger.error(f"Error in purge: {e}")
        try:
            embed = discord.Embed(
                title="`❌` Purge Failed",
                description="Failed to delete messages.",
                color=0xE74C3C,
            )
            embed.set_footer(text=f"Requested by {interaction.user}")
            await interaction.edit_original_response(content=None, embed=embed)
        except Exception:
            pass


def setup(bot):
    bot.tree.add_command(purge)
