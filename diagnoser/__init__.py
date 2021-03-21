from redbot.core.bot import Red

from .diagnoser import Diagnoser


def setup(bot: Red) -> None:
    cog = Diagnoser(bot)
    bot.add_cog(cog)
