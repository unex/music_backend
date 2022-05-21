from typing import AsyncIterator

from .base import Endpoint
from ..models import ListTrack, Album
from ..utils import Paginator, Chunker


class LibraryEndpoint(Endpoint):
    async def get_tracks(self, **kwargs) -> AsyncIterator[ListTrack]:
        async for i in Paginator(self._api.library.get_tracks, **kwargs):
            yield ListTrack(**i)

    async def get_albums(self, **kwargs) -> AsyncIterator[Album]:
        async for i in Paginator(self._api.library.get_albums, **kwargs):
            yield Album(**i["album"])

    async def add_tracks(self, track_id_list, **kwargs):
        for chunk in Chunker(track_id_list, 50):
            await self._api.library.add_tracks(chunk, **kwargs)
