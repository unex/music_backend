from typing import AsyncIterator

from .base import Endpoint
from ..models import Playlist, ListTrack
from ..utils import Paginator, Chunker


class PlaylistsEndpoint(Endpoint):
    async def current_get_all(self, **kwargs) -> AsyncIterator[Playlist]:
        async for i in Paginator(self._api.playlists.current_get_all, **kwargs):
            yield Playlist(**i)

    async def get_tracks(self, *args, **kwargs) -> AsyncIterator[ListTrack]:
        async for i in Paginator(self._api.playlists.get_tracks, *args, **kwargs):
            if "track" in i and i["track"]["id"] is None:
                continue

            yield ListTrack(**i)

    async def add_tracks(self, playlist_id, spotify_uris, **kwargs):
        for chunk in Chunker(spotify_uris, 100):
            await self._api.playlists.add_tracks(playlist_id, chunk, **kwargs)

    async def remove_tracks(self, playlist_id, spotify_uris, **kwargs):
        for chunk in Chunker(spotify_uris, 100):
            uris = list(map(lambda x: {"uri": x}, chunk))
            await self._api.playlists.remove_tracks(
                playlist_id, {"tracks": uris}, **kwargs
            )
