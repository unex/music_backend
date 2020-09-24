import os
import re
import asyncio
import traceback
import csv
import glob
import time

from datetime import date, datetime, timedelta
from typing import List

import spotify
import pygit2
from aiohttp import ClientSession
from fake_headers import Headers
from bs4 import BeautifulSoup

from derw import log

# SPOTIFY
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI")
SPOTIFY_SCOPES = [
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
    "user-follow-read"
]

SPOTIFY_MIRROR_PLAYLIST = os.environ.get("SPOTIFY_MIRROR_PLAYLIST")

# GIT
GIT_REPO = os.environ.get("GIT_REPO")
GIT_USERNAME = os.environ.get("GIT_USERNAME")
GIT_EMAIL = os.environ.get("GIT_EMAIL")
GIT_PASSWORD = os.environ.get("GIT_PASSWORD")

class Git():
    def __init__(self):
        self.repo = pygit2.Repository(os.path.join(os.path.relpath(GIT_REPO), '.git'))

        creds = pygit2.UserPass(GIT_USERNAME, GIT_PASSWORD)
        self.remote = pygit2.RemoteCallbacks(credentials=creds)
        self.author = pygit2.Signature(GIT_USERNAME, GIT_EMAIL)

    def pull(self):
        self.repo.reset(self.repo.head.target, pygit2.GIT_RESET_HARD)
        self.repo.remotes["origin"].fetch(callbacks=self.remote)
        target = self.repo.references.get('refs/remotes/origin/master').target
        reference = self.repo.references.get('refs/heads/master')
        reference.set_target(target)

        self.repo.checkout_head()

    def commit_and_push(self):
        ref = "refs/heads/master"

        diff = self.repo.diff()

        if not len(diff):
            log.info("No changes, commit not needed")
            return

        index = self.repo.index
        index.add_all()
        index.write()

        tree = index.write_tree()

        message = (
            f'{date.today()}'
        )

        oid = self.repo.create_commit(ref, self.author, self.author, message, tree, [self.repo.revparse_single('HEAD').hex])

        log.info(f'Created commit {oid}')

        self.repo.remotes["origin"].push([ref], callbacks=self.remote)


class DewsBeats():
    def __init__(self):
        self.client = spotify.Client(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)

    async def main(self):
        try:
            await self.auth()

            self.git = Git()
            self.git.pull()

            log.info(f'Logged in as {self.user.display_name}')

            self.library = spotify.Library(self.client, self.user)

            await self.purge_idk_playlists()

            self.saved_tracks = list([track async for track in self.get_saved_tracks()])
            self.saved_tracks.sort(key=lambda x: x.added_at)

            await self.update_playlist()

            if datetime.utcnow().weekday() == 0:
                await self.update_mimo()

            await self.update_git()

            self.git.commit_and_push()

        except:
            traceback.print_exc()

        finally:
            await self.close()

    async def purge_idk_playlists(self):
        log.debug("Purging idk playlists")
        for playlist in await self.user.get_all_playlists():
            if not playlist.name.startswith("idk"):
                continue

            tracks: List[spotify.PlaylistTrack] = []

            async for track in self.get_playlist_tracks(playlist.id):
                if track.added_at > datetime.today() - timedelta(weeks=2):
                    continue

                tracks.append(track)

            tracks.sort(key=lambda x: x.added_at)

            for track in tracks:
                await self.library.save_tracks(track)
                await asyncio.sleep(1)

            await self.user.http.remove_playlist_tracks(playlist.id, [track.uri for track in tracks])

            log.debug(f'- {len(tracks)} track(s) from {playlist.name}')

    async def update_git(self):
        log.debug("Updating Git")

        _dir = os.path.relpath(GIT_REPO)

        # ================================
        #    SAVED TRACKS & PLAYLISTS
        # ================================

        fields = ["title", "album", "artist", "id", "url"]

        data = [{
            "title": track.name,
            "album": track.album.name,
            "artist": ', '.join(artist.name for artist in track.artists),
            "id": track.id,
            "url": track.url
        } for track in self.saved_tracks]

        self.write_csv(
            os.path.join(_dir,'Saved Songs.csv'),
            "Saved Songs",
            fields,
            data
        )

        log.debug("- Saved Tracks")

        # delete playlist csv files and then re-populate for ez deletion handling
        playlist_dir = os.path.join(_dir, "playlists")
        for filename in glob.glob(f'{playlist_dir}/*.csv'):
            os.remove(filename)

        for playlist in await self.user.get_all_playlists():
            # Ignore the mirror playlist just cuz its a duplicate of saved tracks
            if playlist.id == SPOTIFY_MIRROR_PLAYLIST:
                continue

            # Ignore playlists that are not mine or spoitfys?
            # if playlist.owner.id not in [self.user.id, "spotify"]:
            #     continue

            if not playlist.public:
                continue

            tracks = list(await playlist.get_all_tracks())

            # Sort by name first so that tracks with the same added_at
            # will always appear in the same order
            tracks.sort(key=lambda x: x.name)
            tracks.sort(key=lambda x: x.added_at)

            data = [{
                "title": track.name,
                "album": track.album.name,
                "artist": ', '.join(artist.name for artist in track.artists),
                "id": track.id,
                "url": track.url
            } for track in tracks]

            filename = re.sub(r"[^\w\d\s-]", "_", playlist.name)

            self.write_csv(
                os.path.join(_dir, f'playlists/{filename}.csv'),
                f'{playlist.name}, By {playlist.owner.display_name}, {playlist.url}',
                fields,
                data
            )

            log.debug(f'- Playlist {playlist.name}')

        # ================================
        #             ARTISTS
        # ================================

        fields = ["name", "id", "url"]

        artists = [artist async for artist in self.get_follwing_artists()]
        artists.sort(key=lambda x: x.name)

        data = [{
            "name": artist.name,
            "id": artist.id,
            "url": artist.url
        } for artist in artists]

        self.write_csv(
            os.path.join(_dir,'Artists.csv'),
            "Artists",
            fields,
            data
        )

        log.debug("- Artists")

        # ================================
        #             ALBUMS
        # ================================

        fields = ["name", "artist", "id", "url"]

        albums = list(await self.library.get_all_albums())
        albums.sort(key=lambda x: x.name)

        data = [{
            "name": album.name,
            "artist": ', '.join(artist.name for artist in album.artists),
            "id": album.id,
            "url": album.url
        } for album in albums]

        self.write_csv(
            os.path.join(_dir,'Albums.csv'),
            "Albums",
            fields,
            data
        )

        log.debug("- Albums")

    def write_csv(self, file, header, fields: List, data: List):
        with open(file, 'w') as csvfile:
            if header:
                csvfile.write(f'# {header}\n')

            writer = csv.DictWriter(csvfile, fieldnames=fields)

            writer.writeheader()
            writer.writerows(data)

    async def get_follwing_artists(self):
        """
        Can't find this method in the spotify library
        """

        has_next = True
        last = 0
        while has_next:
            data = (await self.user.http.request(("GET", f'https://api.spotify.com/v1/me/following?type=artist&limit=50&after={last}'))).get("artists")

            has_next = data.get("next")

            for item in data.get("items"):
                last = item.get("id")

                yield spotify.Artist(self.user.client, item)


    async def get_saved_tracks(self):
        """
        The spotify library.get_saved_tracks() makes an individual call for each track
        No thanks
        """

        has_next = True
        offset = 0
        while has_next:
            data = await self.user.http.saved_tracks(limit=50, offset=offset)

            has_next = data.get("next")
            offset += 50

            for item in data.get("items"):
                track = spotify.Track(self.user.client, item.get("track"))
                track.added_at = datetime.strptime(item["added_at"], "%Y-%m-%dT%H:%M:%SZ")
                yield track

    async def auth(self):
        refresh_token = None

        try:
            with open(".spotify", "r") as f:
                refresh_token = f.read()

        except FileNotFoundError:
            pass

        if refresh_token:
            user = await spotify.User.from_refresh_token(self.client, refresh_token=refresh_token)

        else:
            import urllib.parse as urlparse
            from urllib.parse import parse_qs
            print("Open the following link in browser to auth with Spotify, then paste the URL you are redirected to.")
            print(spotify.OAuth2.url_only(client_id=SPOTIFY_CLIENT_ID, redirect_uri=SPOTIFY_REDIRECT_URI, scopes=SPOTIFY_SCOPES))
            url = input("URL: ")
            code = parse_qs(urlparse.urlparse(url).query)["code"][0]
            user = await spotify.User.from_code(self.client, code, redirect_uri=SPOTIFY_REDIRECT_URI)

            with open(".spotify", "w") as f:
                f.write(user.http.refresh_token)

        self.user = user

    async def get_playlist_tracks(self, _id):
        has_next = True
        offset = 0
        while has_next:
            data = await self.user.http.get_playlist_tracks(_id, limit=50, offset=offset)

            has_next = data.get("next")
            offset += 50

            for track in data.get("items"):
                yield spotify.PlaylistTrack(self.user.client, track)

    async def update_playlist(self):
        data = await self.user.http.get_playlist(SPOTIFY_MIRROR_PLAYLIST)
        playlist = spotify.Playlist(self.client, data, http=self.user.http)

        playlist_tracks: List[str] = [track.id async for track in self.get_playlist_tracks(playlist.id)]
        new_tracks: List[spotify.Track] = []

        for track in self.saved_tracks:
            if track.id not in playlist_tracks:
                new_tracks.append(track)

        log.debug(f'Added {len(new_tracks)} new tracks to mirror playlist')

        for track in new_tracks:
            await playlist.add_tracks(track)
            await asyncio.sleep(1)

    async def update_mimo(self):
        pl = spotify.Playlist(client = self.client, data = await self.client.http.get_playlist('62Wdnd2oq36OIRAQdf77OR'), http = self.user.http)
        trackids = [track.id for track in pl.tracks]

        tracks = []

        mimo = MiMo()

        async for songid in mimo.get_songs():
            track = await self.client.get_track(songid)
            if track.id not in trackids:
                if len(tracks) == 100:
                    await pl.add_tracks(*tracks)
                    tracks = []
                else:
                    tracks.append(track)

                trackids.append(track.id)

        if tracks:
            await pl.add_tracks(*tracks)

        await mimo.close()

    async def close(self):
        await self.client.close()
        await self.user.http.close()

re_media = re.compile(r"new MediaViewer\(this, 'tlp_\d*', \{(.*)\} \);")
class MiMo:
    DOMAIN = 'https://www.1001tracklists.com'

    def __init__(self):
        self.http = ClientSession(headers = Headers(browser='firefox', os='win').generate())

    async def get_songs(self):
        async for tl in self.get_tracklists():
            print(f'Pulling list {tl}')
            async for media in self.parse_tracklist(tl):
                yield await self.get_medialink(media)

                await asyncio.sleep(1)

    async def get_tracklists(self):
        page = 0
        while page >= 0:
            print(f'Page {page}')
            index = f'index{page}.html' if page > 1 else 'index.html'
            async with self.http.get(f'{self.DOMAIN}/dj/missmonique/{index}') as r:
                soup = BeautifulSoup(await r.text(), "html.parser")

                if page == 0:
                    page = len(soup.select('.pagination li')) - 2
                    continue

                for a in reversed(soup.find_all('div', class_='tlLink')):
                    yield a.find('a', href=True)['href']

                page -= 1

                if page == 0:
                    break

    async def parse_tracklist(self, url):
        async with self.http.get(f'{self.DOMAIN}{url}') as r:
            for item in reversed(BeautifulSoup(await r.text(), "html.parser").find_all(class_='tlpItem')):
                btn = item.select('.fa-spotify.mediaAction')
                if not btn:
                    continue

                media = list(filter(None, re_media.findall(btn[0]['onclick'])))
                if not media:
                    continue

                yield {m[0].strip(): m[1].strip() for m in [l.split(':') for l in media[0].replace('\'', '').split(',')]}

    async def get_medialink(self, params):
        async with self.http.get(f'{self.DOMAIN}/ajax/get_medialink.php', params = params) as r:
            data = await r.json()
            return data['data'][0]['playerId']

    async def close(self):
        await self.http.close()


loop = asyncio.get_event_loop()
d = DewsBeats()
loop.run_until_complete(d.main())
