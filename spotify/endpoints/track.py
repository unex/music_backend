from typing import AsyncIterator

from .base import Endpoint
from ..models import Track
from ..utils import Chunker


class TrackEndpoint(Endpoint):
    async def get_several(self, track_id_list, **kwargs) -> AsyncIterator[Track]:
        for chunk in Chunker(track_id_list, 50):
            data = await self._api.track.get_several(chunk, **kwargs)

            for track in data["tracks"]:
                yield Track(**track)
