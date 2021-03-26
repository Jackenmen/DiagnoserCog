# TODO: Ensure consistency for labels
# TODO: Split IssueDiagnoser in a couple of mixins
# TODO: Replace lists with tuples
# TODO: Add "No further checks have been ran." line
# TODO: Improve resolutions to work well with the rest of the string
from __future__ import annotations

import itertools
from copy import copy
from dataclasses import dataclass
from functools import partial
from typing import Awaitable, Callable, Iterable, List, Optional, Union

import discord
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import bold, format_perms_list, inline

_ = lambda s: s


@dataclass
class CheckResult:
    success: bool
    label: str
    details: Union[List[CheckResult], str] = ""
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

    # reusable methods
    async def _check_until_fail(
        self,
        label: str,
        checks: Iterable[Callable[[], Awaitable[CheckResult]]],
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

    def _format_command_name(self, command: Union[commands.Command, str]) -> str:
        if not isinstance(command, str):
            command = command.qualified_name
        return inline(f"{self._original_ctx.clean_prefix}{command}")

    def _command_error_handler(
        self,
        msg: str,
        label: str,
        failed_with_message: str,
        failed_without_message: str,
    ) -> CheckResult:
        command = self.ctx.command
        details = (
            failed_with_message.format(command=self._format_command_name(command), message=msg)
            if msg
            else failed_without_message.format(command=self._format_command_name(command))
        )
        return CheckResult(
            False,
            label,
            details,
        )

    # all the checks
    async def _check_is_author_bot(self) -> CheckResult:
        label = _("Check if the command caller is not a bot")
        if not self.author.bot:
            return CheckResult(True, label)
        return CheckResult(
            False,
            label,
            _("The user is a bot which prevents them from running any command."),
            _("This cannot be fixed - bots should not be listening to other bots."),
        )

    async def _check_can_bot_send_messages(self) -> CheckResult:
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
    async def _check_ignored_issues(self) -> CheckResult:
        label = _("Check if the channel and the server aren't set to be ignored")
        if await self.bot.ignored_channel_or_guild(self.message):
            return CheckResult(True, label)

        if self.channel.category is None:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel and the server aren't a part of that list."
            ).format(
                command=self._format_command_name("ignore list"),
                channel=self.channel.mention,
            )
        else:
            resolution = _(
                "To fix this issue, check the list returned by the {command} command"
                " and ensure that the {channel} channel,"
                " the channel category it belongs to ({channel_category}),"
                " and the server aren't a part of that list."
            ).format(
                command=self._format_command_name("ignore list"),
                channel=self.channel.mention,
                channel_category=self.channel.category.mention,
            )

        return CheckResult(
            False,
            label,
            _("The bot is set to ignore commands in the given channel or this server."),
            resolution,
        )

    async def _check_whitelist_blacklist_issues(self) -> CheckResult:
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
                    command_1=self._format_command_name("allowlist list"),
                    command_2=self._format_command_name("blocklist list"),
                    user_id=self.author.id,
                ),
            )

        return CheckResult(
            False,
            label,
            _(
                "Local allowlist or blocklist prevents the user"
                " or their roles from running this command."
            ),
            _(
                "To fix this issue, check the lists returned by {command_1}"
                " and {command_2} commands~~, and ensure that the given user's ID ({user_id}) or IDs of their roles"
                " aren't a part of either of them~~ (this resolution is not quite accurate, ask in support)."
            ).format(
                command_1=self._format_command_name("localallowlist list"),
                command_2=self._format_command_name("localblocklist list"),
                user_id=self.author.id,
            ),
        )

        # this code is more granular, but sadly doesn't work due to a bug in Core Red
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
                    command_1=self._format_command_name("localallowlist list"),
                    command_2=self._format_command_name("localblocklist list"),
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
                command_1=self._format_command_name("localallowlist list"),
                command_2=self._format_command_name("localblocklist list"),
                user_id=self.author.id,
            ),
        )

    async def _check_global_checks_issues(self) -> CheckResult:
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

    async def _check_disabled_command_issues(self) -> CheckResult:
        label = _("Check if the command is disabled")
        command = self.ctx.command

        for parent in reversed(command.parents):
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
                    command=self._format_command_name(f"command enable global {parent}"),
                    affected_command=self._format_command_name(parent),
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
                    command=self._format_command_name(f"command enable global {command}"),
                    affected_command=self._format_command_name(command),
                ),
            )

        return CheckResult(True, label)

    async def _check_dpy_can_run(self) -> CheckResult:
        command = self.ctx.command
        label = _("Run all of the checks")
        try:
            if await super(commands.Command, command).can_run(self.ctx):
                return CheckResult(True, label)
        except commands.DisabledCommand:
            details = (
                _("The given command is disabled in this guild.")
                if command is self.command
                else _("One of the parents of the given command is disabled globally.")
            )
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_command} command in this guild."
                ).format(
                    command=self._format_command_name(f"command enable guild {command}"),
                    affected_command=self._format_command_name(command),
                ),
            )
        except commands.CommandError:
            # we want to narrow this down to specific type of checks (bot/cog/command)
            pass

        return await self._check_until_fail(
            label,
            [
                self._check_dpy_can_run_bot,
                self._check_dpy_can_run_cog,
                self._check_dpy_can_run_command,
            ],
            final_check_result=CheckResult(
                False,
                _("Other issues related to the checks"),
                _(
                    "There's an issue related to the checks for {command}"
                    " but we're not able to determine the exact cause."
                ),
                _(
                    "To fix this issue, a manual review of"
                    " the global, cog and command checks is required."
                ),
            ),
        )

    async def _check_dpy_can_run_bot(self) -> CheckResult:
        label = _("Run the global checks")
        msg = ""
        try:
            if await self.bot.can_run(self.ctx):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _(
                "One of the global checks for the command {command} failed with a message:\n"
                "{message}"
            ),
            _("One of the global checks for the command {command} failed without a message."),
        )

    async def _check_dpy_can_run_cog(self) -> CheckResult:
        label = _("Run the cog check")
        cog = self.ctx.command.cog
        if cog is None:
            return CheckResult(True, label)
        local_check = commands.Cog._get_overridden_method(cog.cog_check)
        if local_check is None:
            return CheckResult(True, label)

        msg = ""
        try:
            if await discord.utils.maybe_coroutine(local_check, self.ctx):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _("The cog check for the command {command} failed with a message:\n{message}"),
            _("The cog check for the command {command} failed without a message."),
        )

    async def _check_dpy_can_run_command(self) -> CheckResult:
        label = _("Run the command checks")
        predicates = self.ctx.command.checks
        if not predicates:
            return CheckResult(True, label)

        msg = ""
        try:
            if await discord.utils.async_all(predicate(self.ctx) for predicate in predicates):
                return CheckResult(True, label)
        except commands.CommandError as e:
            msg = str(e)
        return self._command_error_handler(
            msg,
            label,
            _(
                "One of the command checks for the command {command} failed with a message:\n"
                "{message}"
            ),
            _("One of the command checks for the command {command} failed without a message."),
        )

    async def _check_requires(self) -> CheckResult:
        return await self._check_requires_impl(_("Check permissions"), self.ctx.command)

    async def _check_requires_cog(self) -> CheckResult:
        label = _("Check permissions for {cog}").format(cog=inline(self.ctx.cog.qualified_name))
        if self.ctx.cog is None:
            return CheckResult(True, label)
        return await self._check_requires_impl(label, self.ctx.cog)

    async def _check_requires_impl(
        self, label: str, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        original_perm_state = self.ctx.permission_state
        try:
            allowed = await cog_or_command.requires.verify(self.ctx)
        except commands.DisabledCommand:
            return CheckResult(
                False,
                label,
                _("The cog of the given command is disabled in this guild."),
                _(
                    "To fix this issue, you can run {command}"
                    " which will enable the {affected_cog} cog in this guild."
                ).format(
                    command=self._format_command_name(
                        f"command enablecog {self.ctx.cog.qualified_name}"
                    ),
                    affected_cog=inline(self.ctx.cog.qualified_name),
                ),
            )
        except commands.BotMissingPermissions as e:
            # No, go away, "some" can refer to a single permission so plurals are just fine here!
            # Seriously. They are. Don't even question it.
            details = (
                _(
                    "Bot is missing some of the channel permissions ({permissions})"
                    " required by the {cog} cog."
                ).format(
                    permissions=format_perms_list(e.missing),
                    cog=inline(cog_or_command.qualified_name),
                )
                if cog_or_command is self.ctx.cog
                else _(
                    "Bot is missing some of the channel permissions ({permissions})"
                    " required by the {command} command."
                ).format(
                    permissions=format_perms_list(e.missing),
                    command=self._format_command_name(cog_or_command),
                )
            )
            return CheckResult(
                False,
                label,
                details,
                _(
                    "To fix this issue, grant the required permissions to the bot"
                    " through role settings or channel overrides."
                ),
            )
        if allowed:
            return CheckResult(True, label)

        self.ctx.permission_state = original_perm_state
        return await self._check_until_fail(
            label,
            [
                partial(self._check_requires_bot_owner, cog_or_command),
                partial(self._check_requires_permission_hooks, cog_or_command),
            ],
            # TODO: Split the `final_check_result` into parts to be ran by this function
            final_check_result=CheckResult(
                False,
                _("User's discord permissions, privilege level and rules from Permissions cog"),
                _("One of the above is the issue."),
                _(
                    "To fix this issue, verify each of these"
                    " and determine which part is the issue."
                ),
            ),
        )

    async def _check_requires_bot_owner(
        self, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        label = _("Ensure that the command is not bot owner only")
        if cog_or_command.requires.privilege_level is not commands.PrivilegeLevel.BOT_OWNER:
            return CheckResult(True, label)
        # we don't need to check whether the user is bot owner
        # as call to `verify()` would already succeed if that were the case
        return CheckResult(
            False,
            label,
            _("The command is bot owner only and the given user is not a bot owner."),
            _("This cannot be fixed - regular users cannot run bot owner only commands."),
        )

    async def _check_requires_permission_hooks(
        self, cog_or_command: commands.CogCommandMixin
    ) -> CheckResult:
        label = _("Check the result of permission hooks")
        result = await self.bot.verify_permissions_hooks(self.ctx)
        if result is None:
            return CheckResult(True, label)
        if result is True:
            # this situation is abnormal as in this situation,
            # call to `verify()` would already succeed and we wouldn't get to this point
            return CheckResult(
                False,
                label,
                _("Fatal error: the result of permission hooks is inconsistent."),
                _("To fix this issue, a manual review of the installed cogs is required."),
            )
        return CheckResult(
            False,
            label,
            _("The access has been denied by one of the bot's permissions hooks."),
            _("To fix this issue, a manual review of the installed cogs is required."),
        )

    async def _check_checks(self, command: commands.Command) -> CheckResult:
        label = _("Run checks for the command {command}").format(
            command=self._format_command_name(command)
        )

        self.ctx.command = command
        original_perm_state = self.ctx.permission_state
        try:
            can_run = await command.can_run(self.ctx, change_permission_state=True)
        except commands.CommandError:
            can_run = False

        if can_run:
            return CheckResult(True, label)

        self.ctx.permission_state = original_perm_state
        return await self._check_until_fail(
            label,
            [
                self._check_dpy_can_run,
                self._check_requires,
            ],
            final_check_result=CheckResult(
                False,
                _("Other command checks"),
                _("The given command is failing one of the required checks."),
                _("To fix this issue, a manual review of the command's checks is required."),
            ),
        )

    async def _check_can_run_issues(self) -> CheckResult:
        label = _("Command checks")
        ctx = self.ctx
        try:
            can_run = await self.command.can_run(ctx, check_all_parents=True)
        except commands.CommandError:
            # we want to get more specific error by narrowing down the scope,
            # so we just ignore handling this here
            #
            # NOTE: it might be worth storing this information in case we get to
            # `final_check_result`, although that's not very likely
            # Similar exception handlers further down the line could do that
            # as well if I'm gonna implement it here.
            pass
        else:
            if can_run:
                return CheckResult(True, label)

        ctx.permission_state = commands.PermState.NORMAL
        ctx.command = self.command.root_parent or self.command

        # slight discrepancy here - we're doing cog-level verify before top-level can_run
        return await self._check_until_fail(
            label,
            itertools.chain(
                (self._check_requires_cog,),
                (
                    partial(self._check_checks, command)
                    for command in itertools.chain(reversed(self.command.parents), (self.command,))
                ),
            ),
            final_check_result=CheckResult(
                False,
                _("Other command checks"),
                _("The given command is failing one of the required checks."),
                _("To fix this issue, a manual review of the command's checks is required."),
            ),
        )

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
                ).format(
                    user=self.author,
                    command_name=inline(
                        # needs to be _original_ctx - when bot is passed as the author,
                        # Context parser won't go far enough to parse for prefix
                        f"{self._original_ctx.clean_prefix}{self.command.qualified_name}"
                    ),
                    channel=self.channel.mention,
                ),
                # not perfect, but will do...
                escape_formatting=False,
            )
        ]
        result = await self._check_until_fail(
            "",
            [
                self._check_global_checks_issues,
                self._check_disabled_command_issues,
                self._check_can_run_issues,
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
            if result.resolution:
                lines.append(
                    _("The bot has been able to identify the issue.") + f" {result.resolution}"
                )
            else:
                lines.append(
                    _(
                        "The bot has been able to identify the issue."
                        " Read the details above for more information."
                    )
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
        if not channel.permissions_for(member).send_messages:
            # Let's make Flame happy here
            await ctx.send(
                _(
                    "Don't try to fool me, the given member can't access the {channel} channel."
                ).format(channel=channel.mention)
            )
            return
        issue_diagnoser = IssueDiagnoser(self.bot, ctx, channel, member, command)
        await ctx.send(await issue_diagnoser.diagnose())
