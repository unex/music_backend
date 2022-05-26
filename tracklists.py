import os
import re
import asyncio
import logging
import random

from collections import namedtuple
from datetime import datetime
from typing import AsyncIterator

from aiohttp import ClientSession
from fake_headers import Headers
from bs4 import BeautifulSoup

from derw import makeLogger

import spotify

log = makeLogger(__file__)
log.setLevel(logging.DEBUG)


SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI")
SPOTIFY_SCOPES = [
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
    "user-follow-read",
]
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN")


RE_MEDIA = re.compile(r"new MediaViewer\(this, .*, \{(.*)\} \);")


DJ = namedtuple("DJ", ("name", "playlist_id"))

DJs = (DJ("missmonique", "62Wdnd2oq36OIRAQdf77OR"),)


class Session(ClientSession):
    def __init__(self, *args, **kwargs) -> None:
        fake_headers = Headers(
            browser="chrome",  # Generate only Chrome UA
            os="win",  # Generate ony Windows platform
            headers=False,  # generate misc headers
        )

        super().__init__(
            *args,
            headers={
                "Host": "www.1001tracklists.com",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Sec-GPC": "1",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                **fake_headers.generate(),
            },
            **kwargs,
        )

        self._last_request_at = datetime.fromtimestamp(0)

    async def _request(self, *args, **kwargs):
        seconds_since_last = (datetime.utcnow() - self._last_request_at).total_seconds()
        rand_next = random.uniform(1.2, 1.6)

        if seconds_since_last < rand_next:
            wait_for = rand_next - seconds_since_last
            await asyncio.sleep(wait_for)

        r = await super()._request(*args, **kwargs)

        self._last_request_at = datetime.utcnow()

        return r


class Tracklists:
    BASE_URI = "https://www.1001tracklists.com"

    def __init__(self):
        self._http: Session

    async def init(self):
        self._http = Session(
            raise_for_status=True,
        )

        # this will set the session cookie for later requests
        await self._http.get(self.BASE_URI)

    async def get_songs(self, name: str) -> AsyncIterator[str]:
        async for tl in self.get_tracklists(name):
            log.info(f"Pulling list {tl}")
            async for media in self.parse_tracklist(tl):
                yield await self.get_medialink(media)

    async def get_tracklists(self, name: str) -> AsyncIterator[str]:
        dj_id = None

        async with self._http.get(f"{self.BASE_URI}/dj/{name}/") as r:
            soup = BeautifulSoup(await r.text(), "html.parser")

            # scrape DJ artist ID
            for link in soup.find(id="left").find_all("a"):
                if link["href"].startswith("https://1001.tl/"):
                    dj_id = link["href"].replace("https://1001.tl/", "")

            soup = soup.find(id="middle")

            count = 0
            last_id = None

            while True:
                if last_id != None:
                    async with self._http.post(
                        f"{self.BASE_URI}/ajax/get_data.php",
                        data={
                            "type": "overview",
                            "dj": dj_id,
                            "pos": count,
                            "id": last_id,
                            "count": 100,
                        },
                    ) as r:
                        data = await r.json()

                        if data.get("end"):
                            break

                        soup = BeautifulSoup(data["data"], "html.parser")

                for item in soup.find_all(class_=["bItm", "action", "oItm"]):
                    last_id = item["data-id"]
                    count += 1

                    yield item.find(class_=["bTitle"]).find("a")["href"]

                await asyncio.sleep(1)

    async def parse_tracklist(self, url) -> AsyncIterator[dict]:
        async with self._http.get(f"{self.BASE_URI}{url}") as r:
            for item in reversed(
                BeautifulSoup(await r.text(), "html.parser").find_all(class_="mediaRow")
            ):
                btn = item.select(".fa-spotify.mAction")
                if not btn:
                    continue

                media = list(filter(None, RE_MEDIA.findall(btn[0]["onclick"])))
                if not media:
                    continue

                yield {
                    m[0].strip(): m[1].strip()
                    for m in [
                        l.split(":") for l in media[0].replace("'", "").split(",")
                    ]
                }

    async def get_medialink(self, params) -> str:
        async with self._http.get(
            f"{self.BASE_URI}/ajax/get_medialink.php", params=params
        ) as r:
            data = await r.json()
            return data["data"][0]["playerId"]

    async def close(self) -> None:
        await self._http.close()


class Core:
    def __init__(self) -> None:
        self.spotify = spotify.SpotifyClient(
            SPOTIFY_CLIENT_ID,
            SPOTIFY_CLIENT_SECRET,
            SPOTIFY_SCOPES,
            SPOTIFY_REDIRECT_URI,
            SPOTIFY_REFRESH_TOKEN,
        )

        self.tracklists = Tracklists()

    async def init(self):
        await self.spotify.refresh_token()
        await self.tracklists.init()

    async def run(self):
        await self.init()

        me = await self.spotify.user.me()

        log.info(f"Logged in as {me.display_name} ({me.id})")

        for dj in DJs:
            log.info(f"Scraping {dj.name}")

            new_track_ids = set()

            tracks: list[spotify.Track] = [
                track.track
                async for track in self.spotify.playlists.get_tracks(dj.playlist_id)
            ]

            async for trackid in self.tracklists.get_songs(dj.name):
                if list(filter(lambda x: x.id == trackid, tracks)):
                    continue

                new_track_ids.add(trackid)

            new_track_uris = [
                track.uri
                async for track in self.spotify.track.get_several(list(new_track_ids))
            ]

            await self.spotify.playlists.add_tracks(dj.playlist_id, new_track_uris)

            log.info(f"Added {len(new_track_uris)} tracks for {dj.name}")

    async def close(self):
        await self.spotify.api.close_client()
        await self.tracklists.close()


if __name__ == "__main__":
    app = Core()
    loop = asyncio.new_event_loop()

    try:
        loop.run_until_complete(app.run())
    finally:
        loop.run_until_complete(app.close())
