from typing import AsyncIterator

from .base import Endpoint
from ..models import Artist


class FollowEndpoint(Endpoint):
    async def get_followed_artist(self) -> AsyncIterator[Artist]:
        # why the eff is this one different Spotify???
        # now I cant use Pagiantor...

        after = 0  # this is the only way to pre-set this...

        while True:
            req = await self._api.follow.get_followed_artist(limit=50, after=after)

            req = req["artists"]

            after = req["cursors"]["after"]

            if not "items" in req or not req["items"]:
                break

            for i in req["items"]:
                artist = Artist(**i)

                yield artist

            # break
