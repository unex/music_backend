from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from async_spotify import SpotifyApiClient


class Endpoint:
    def __init__(self, api: "SpotifyApiClient"):
        self._api = api
