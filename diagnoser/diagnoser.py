from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable, List, Optional, Tuple

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, inline

_ = lambda s: s


@dataclass
class CheckResult:
    success: bool
    label: str
    details: str = ""
    resolution: str = ""


class IssueDiagnoser:
    def __init__(
        self,
        bot: Red,
        original_ctx: commands.Context,
        channel: discord.TextChannel,
        author: discord.Member,
        command: commands.Command,
    ) -> None:
        self.bot = bot
        self._original_ctx = original_ctx
        self.guild = channel.guild
        self.channel = channel
        self.author = author
        self.command = command
        self._prepared = False
        self.message: discord.Message
        self.ctx: commands.Context

    async def _prepare(self) -> None:
        if self._prepared:
            return
        self.message = copy(self._original_ctx.message)
        self.message.author = self.author
        self.message.channel = self.channel
        self.message.content = self._original_ctx.prefix + self.command.qualified_name

        self.ctx = await self.bot.get_context(self.message)

    async def _check_until_fail(
        self,
        label: str,
        checks: Iterable[Callable[[], Awaitable[Tuple[str, bool]]]],
        *,
        final_check_result: Optional[CheckResult] = None,
    ) -> CheckResult:
        details = []
        for check in checks:
            check_result = await check()
            details.append(check_result)
            if not check_result.success:
                return CheckResult(False, label, details, check_result.resolution)
        if final_check_result is not None:
            details.append(final_check_result)
            return CheckResult(
                final_check_result.success,
                label,
                details,
                final_check_result.resolution,
            )
        return CheckResult(True, label, details)

    def _check_is_author_bot(self) -> Optional[str]:
        label = _("Check if the command caller is not a bot")
        if not self.author.bot:
            return CheckResult(True, label)
        return CheckResult(
            False,
            label,
            _("The user is a bot which prevents them from running any command."),
            _("This cannot be fixed - bots should not be listening to other bots."),
        )

    def _check_can_bot_send_messages(self) -> Optional[str]:
        label = _("Check if the bot can send messages in the given channel")
        if self.channel.permissions_for(self.guild.me).send_messages:
            return CheckResult(True, label)
        return CheckResult(
            False,
            label,
            _("Bot doesn't have permission to send messages in the given channel."),
            _(
                "To fix this issue, ensure that the permissions setup allows the bot"
                " to send messages per Discord's role hierarchy:\n"
                "https://support.discord.com/hc/en-us/articles/206141927"
            ),
        )

    # While the following 2 checks could show even more precise error message,
    # it would require a usage of private attribute rather than the public API
    # which increases maintanance burden for not that big of benefit.
    async def _check_ignored_issues(self) -> Optional[str]:
        label = _("Check if the channel and the server aren't set to be ignored")
        if await self.bot.ignored_channel_or_guild(self.message):
            return CheckResult(True, label)

        if self.channel.category is None:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel and the guild aren't a part of that list."
            ).format(
                command=inline(f"{self.ctx.clean_prefix}ignore list"),
                channel=self.channel.mention,
            )
        else:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel,"
                " the channel category it belongs to ({channel_category}),"
                " and the guild aren't a part of that list."
            ).format(
                command=inline(f"{self.ctx.clean_prefix}ignore list"),
                channel=self.channel.mention,
                channel_category=self.channel.category.mention,
            )

        return CheckResult(
            False,
            label,
            _("The bot is set to ignore commands in the given channel or this guild."),
            resolution,
        )

    async def _check_whitelist_blacklist_issues(self) -> Optional[str]:
        # TODO: Okay, so I need a way better error messages here,
        # because if allowlist is non-empty, we want the user to check that the user/role ID
        # is part of the allowlist.
        # And if allowlist is empty, we want the user to check that the user/role ID
        # is NOT a part of the blocklist.
        #
        # Damn, this is complicated...
        label = _("Allowlist and blocklist checks")
        if await self.bot.allowed_by_whitelist_blacklist(self.author):
            return CheckResult(True, label)

        is_global = not await self.bot.allowed_by_whitelist_blacklist(who_id=self.author.id)
        if is_global:
            return CheckResult(
                False,
                label,
                _("Global allowlist or blocklist prevents the user from running this command."),
                _(
                    "To fix this issue, check the lists returned by {command_1}"
                    " and {command_2} commands~~, and ensure that the given user's ID ({user_id})"
                    " isn't a part of either of them~~ (this resolution is not quite accurate, ask in support)."
                ).format(
                    command_1=inline(f"{self.ctx.clean_prefix}allowlist list"),
                    command_2=inline(f"{self.ctx.clean_prefix}blocklist list"),
                    user_id=self.author.id,
                ),
            )

        is_role_related = not await self.bot.allowed_by_whitelist_blacklist(
            who_id=self.author.id, guild_id=self.guild.id
        )
        if is_role_related:
            # Remember when I said, I don't want to touch private attrs?
            # Well, I saw what `localallowlist list` command returns and I've noticed how hard
            # it is to read it...
            # TODO: Let's actually give more helpful resolution here.
            return CheckResult(
                False,
                label,
                _(
                    "Local allowlist or blocklist prevents one of the roles the user has"
                    " from running this command."
                ),
                _(
                    "To fix this issue, check the lists returned by {command_1}"
                    " and {command_2} commands~~, and ensure that none of the IDs of roles"
                    " of the given user's are a part of either of them~~"
                    " (this resolution is not quite accurate, ask in support)."
                ).format(
                    command_1=inline(f"{self.ctx.clean_prefix}localallowlist list"),
                    command_2=inline(f"{self.ctx.clean_prefix}localblocklist list"),
                    user_id=self.author.id,
                ),
            )

        return CheckResult(
            False,
            label,
            _("Local allowlist or blocklist prevents the user from running this command."),
            _(
                "To fix this issue, check the lists returned by {command_1}"
                " and {command_2} commands~~, and ensure that the given user's ID ({user_id})"
                " isn't a part of either of them~~ (this resolution is not quite accurate, ask in support)."
            ).format(
                command_1=inline(f"{self.ctx.clean_prefix}localallowlist list"),
                command_2=inline(f"{self.ctx.clean_prefix}localblocklist list"),
                user_id=self.author.id,
            ),
        )

    async def _check_global_checks_issues(self) -> Optional[str]:
        label = _("Global checks")
        # To avoid running core's global checks twice, we just run them all regularly
        # and if it turns out that invokation would end here, we go back and check each of
        # core's global check individually to give more precise error message.
        try:
            can_run = await self.bot.can_run(self.ctx, call_once=True)
        except commands.CommandError:
            pass
        else:
            if can_run:
                return CheckResult(True, label)

        return await self._check_until_fail(
            label,
            [
                self._check_is_author_bot,
                self._check_can_bot_send_messages,
                self._check_ignored_issues,
                self._check_whitelist_blacklist_issues,
            ],
            final_check_result=CheckResult(
                False,
                _("Other 'global call once checks'"),
                _(
                    "One of the 'global call once checks' implemented by a 3rd-party cog"
                    " prevents this command from being ran."
                ),
                _("To fix this issue, a manual review of the installed cogs is required."),
            ),
        )

    async def _check_disabled_command_issues(self) -> Optional[str]:
        label = _("Check if the command is disabled")
        command = self.ctx.command

        for parent in reversed(parents):
            if parent.enabled:
                continue
            return CheckResult(
                False,
                label,
                _("One of the parents of the given command is disabled globally."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command globally."
                ).format(
                    command=inline(
                        f"{self.ctx.clean_prefix}command enable global {parent.qualified_name}"
                    ),
                    affected_command=inline(f"{self.ctx.clean_prefix}{parent.qualified_name}"),
                ),
            )

        if not command.enabled:
            return CheckResult(
                False,
                label,
                _("The given command is disabled globally."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command globally."
                ).format(
                    command=inline(
                        f"{self.ctx.clean_prefix}command enable global {command.qualified_name}"
                    ),
                    affected_command=inline(f"{self.ctx.clean_prefix}{command.qualified_name}"),
                ),
            )

        return CheckResult(True, label)

    def get_message_from_check_result(self, result: CheckResult, *, prefix: str = "") -> List[str]:
        lines = []
        if not result.details:
            return []
        if isinstance(result.details, str):
            return [result.details]

        for idx, subresult in enumerate(result.details, start=1):
            status = (
                "Passed \N{WHITE HEAVY CHECK MARK}"
                if subresult.success
                else "Failed \N{NO ENTRY}\N{VARIATION SELECTOR-16}"
            )
            lines.append(f"{prefix}{idx}. {subresult.label}: {status}")
            lines.extend(self.get_message_from_check_result(subresult, prefix=f"{prefix}{idx}."))
        return lines

    async def diagnose(self) -> str:
        await self._prepare()
        lines = [
            bold(
                _(
                    "Diagnose results for issues of {user}"
                    " when trying to run {command_name} command in {channel} channel:\n"
                )
            )
        ]
        result = await self._check_until_fail(
            "",
            [
                self._check_global_checks_issues,
                self._check_disabled_command_issues,
            ],
        )
        lines.extend(self.get_message_from_check_result(result))
        lines.append("")
        if result.success:
            lines.append(
                _(
                    "All checks passed and no issues were detected."
                    " Make sure that the given parameters correspond to"
                    " the channel, user, and command name that have been problematic.\n\n"
                    "If you still can't find the issue, it is likely that one of the 3rd-party cogs"
                    " you're using adds a global or cog local before invoke hook that prevents"
                    " the command from getting invoked as this can't be diagnosed with this tool."
                )
            )
        else:
            lines.append(
                _("The bot has been able to identify the issue.") + f" {result.resolution}"
            )

        return "\n".join(lines)


class Diagnoser(commands.Cog):
    """Diagnose issues with command checks with ease!"""

    def __init__(self, bot: Red) -> None:
        super().__init__()
        self.bot = bot

    # You may ask why this command is owner-only,
    # cause after all it could be quite useful to guild owners!
    # Truth to be told, that would require me to make some part of this
    # more end-user friendly rather than just bot owner friendly - terms like
    # 'global call once checks' are not of any use to someone who isn't bot owner.
    @commands.is_owner()
    @commands.command()
    async def diagnoseissues(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        member: discord.Member,
        *,
        command_name: str,
    ) -> None:
        """Diagnose issues with command checks with ease!"""
        command = self.bot.get_command(command_name)
        if command is None:
            await ctx.send("Command not found!")
            return
        issue_diagnoser = IssueDiagnoser(self.bot, ctx, channel, member, command)
        await ctx.send(await issue_diagnoser.diagnose())
