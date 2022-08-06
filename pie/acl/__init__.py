from __future__ import annotations

from typing import Callable, Set, Optional, TypeVar

import ring

import discord
from discord.ext import commands

import pie._tracing
from pie import i18n

from pie.acl.database import ACDefault, ACLevel, ACLevelMappping
from pie.exceptions import (
    NegativeUserOverwrite,
    NegativeChannelOverwrite,
    NegativeRoleOverwrite,
    InsufficientACLevel,
)
from pie.acl.database import UserOverwrite, ChannelOverwrite, RoleOverwrite

_trace: Callable = pie._tracing.register("pie_acl")

_ = i18n.Translator(__file__).translate
T = TypeVar("T")


@ring.lru(expire=10)
def map_member_to_ACLevel(
    *,
    bot: commands.Bot,
    member: discord.Member,
):
    """Map member to their ACLevel."""

    _acl_trace = lambda message: _trace(f"[acl(mapping)] {message}")  # noqa: E731

    # Gather information

    # NOTE This relies on pumpkin.py:update_app_info()
    bot_owner_ids: Set = getattr(bot, "owner_ids", {*()})
    guild_owner_id: int = member.guild.owner.id

    is_bot_owner: bool = False
    is_guild_owner: bool = False

    if member.id in bot_owner_ids:
        _acl_trace(f"'{member}' is bot owner.")
        is_bot_owner = True

    elif member.id == guild_owner_id:
        _acl_trace(f"'{member}' is guild owner.")
        is_guild_owner = True

    # Perform the mapping

    if is_bot_owner:
        member_level = ACLevel.BOT_OWNER
    elif is_guild_owner:
        member_level = ACLevel.GUILD_OWNER
    else:
        member_level = ACLevel.EVERYONE
        for role in member.roles[::-1]:
            mapping = ACLevelMappping.get(member.guild.id, role.id)
            if mapping is not None:
                _acl_trace(
                    f"'{member}' is mapped via '{role.name}' to '{mapping.level.name}'."
                )
                member_level = mapping.level
                break

    return member_level


def acl2(level: ACLevel) -> Callable[[T], T]:
    """A decorator that adds ACL2 check to a command.

    Each command has its preferred ACL group set in the decorator. Bot owner
    can add user and channel overwrites to these decorators, to allow detailed
    controll over the system with sane defaults provided by the system itself.

    Usage:

    . code-block:: python
        :linenos:

        from core import check

        ...

        @check.acl2(check.ACLevel.SUBMOD)
        @commands.command()
        async def repeat(self, ctx, *, input: str):
            await ctx.reply(utils.text.sanitise(input, escape=False))
    """

    def predicate(ctx: commands.Context) -> bool:
        return acl2_function(ctx, level)

    return commands.check(predicate)


# TODO Make cachable as well?
def acl2_function(
    ctx: commands.Context, level: ACLevel, *, for_command: Optional[str] = None
) -> bool:
    """Check function based on Access Control.

    Set `for_command` to perform the check for other command
    then the one being invoked.
    """
    if for_command:
        command = for_command
    else:
        command: str = ctx.command.qualified_name
    _acl_trace = lambda message: _trace(f"[{command}] {message}")  # noqa: E731

    # Allow invocations in DM.
    # Wrap the function in `@commands.guild_only()` to change this behavior.
    if ctx.guild is None:
        _acl_trace("Non-guild context is always allowed.")
        return True

    member_level = map_member_to_ACLevel(bot=ctx.bot, member=ctx.author)
    if member_level == ACLevel.BOT_OWNER:
        _acl_trace("Bot owner is always allowed.")
        return True

    custom_level = ACDefault.get(ctx.guild.id, command)
    if custom_level:
        level = custom_level.level

    _acl_trace(f"Required level '{level.name}'.")

    uo = UserOverwrite.get(ctx.guild.id, ctx.author.id, command)
    if uo is not None:
        _acl_trace(f"User overwrite for '{ctx.author}' exists: '{uo.allow}'.")
        if uo.allow:
            return True
        raise NegativeUserOverwrite()

    co = ChannelOverwrite.get(ctx.guild.id, ctx.channel.id, command)
    if co is not None:
        _acl_trace(f"Channel overwrite for '#{ctx.channel.name}' exists: '{co.allow}'.")
        if co.allow:
            return True
        raise NegativeChannelOverwrite(channel=ctx.channel)

    for role in ctx.author.roles:
        ro = RoleOverwrite.get(ctx.guild.id, role.id, command)
        if ro is not None:
            _acl_trace(f"Role overwrite for '{role.name}' exists: '{ro.allow}'.")
            if ro.allow:
                return True
            raise NegativeRoleOverwrite(role=role)

    if member_level >= level:
        _acl_trace(
            f"Member's level '{member_level.name}' "
            f"higher than required '{level.name}'."
        )
        return True

    _acl_trace(
        f"Member's level '{member_level.name}' lower than required '{level.name}'."
    )
    raise InsufficientACLevel(required=level, actual=member_level)
