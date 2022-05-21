from typing import List

from urllib.parse import urlparse, parse_qs

from async_spotify import SpotifyApiClient
from async_spotify.authentification.authorization_flows import AuthorizationCodeFlow
from async_spotify.authentification.spotify_authorization_token import (
    SpotifyAuthorisationToken,
)
from async_spotify.spotify_errors import SpotifyError

from .endpoints.user import UserEndpoint
from .endpoints.library import LibraryEndpoint
from .endpoints.playlists import PlaylistsEndpoint
from .endpoints.follow import FollowEndpoint


class SpotifyClient:
    def __init__(
        self,
        application_id: str,
        application_secret: str,
        scopes: List[str],
        redirect_uri: str,
        refresh_token: str = None,
    ) -> None:
        self._refresh_token = refresh_token

        auth = AuthorizationCodeFlow(
            application_id=application_id,
            application_secret=application_secret,
            scopes=scopes,
            redirect_url=redirect_uri,
        )

        self.api = SpotifyApiClient(auth, hold_authentication=True)

        self.user = UserEndpoint(self.api)
        self.library = LibraryEndpoint(self.api)
        self.playlists = PlaylistsEndpoint(self.api)
        self.follow = FollowEndpoint(self.api)

    async def _update_refresh_token(self):
        authorization_url: str = self.api.build_authorization_url(show_dialog=True)
        print(authorization_url)

        url = input("URL: ")
        code = parse_qs(urlparse(url).query)["code"][0]

        auth = await self.api.get_auth_token_with_code(code)

        self._refresh_token = auth.refresh_token

        print(f"NEW REFRESH TOKEN: {auth.refresh_token}")

    async def refresh_token(self):
        if not self._refresh_token:
            await self._update_refresh_token()

        else:
            try:
                await self.api.refresh_token(
                    SpotifyAuthorisationToken(refresh_token=self._refresh_token)
                )
            except SpotifyError:
                await self._update_refresh_token()

        await self.api.create_new_client()

    async def __aenter__(self):
        await self.refresh_token()
        return self.api

    async def __aexit__(self, exc_type, exc, tb):
        await self.api.close_client()
