import os
import re
import asyncio
import traceback
import glob
import time
import logging

from datetime import date, datetime, timedelta
from typing import List

import spotify
import pygit2
import aiofiles
from aiohttp import ClientSession
from fake_headers import Headers
from bs4 import BeautifulSoup

from derw import makeLogger

log = makeLogger(__file__)
log.setLevel(logging.DEBUG)

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

RE_MARKDOWN = re.compile(r'\|')

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


def make_csv(d: List[str]):
    ret = []
    for s in d:
        if s is None:
            s = ''
        if set([',', '"']).intersection(s):
            s = s.replace('"', '""')
            ret.append(f'"{s}"')
        else:
            ret.append(s)

    return ','.join(ret)

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

            try:
                if datetime.utcnow().weekday() == 0:
                    await self.update_mimo()
            except:
                traceback.print_exc()

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

        lib_md = await aiofiles.open(os.path.join(_dir,'LIBRARY.md'), 'w')

        await lib_md.writelines("# Library\n\n")

        # ================================
        #    SAVED TRACKS & PLAYLISTS
        # ================================

        data = [{
            "title": track.name,
            "album": track.album.name,
            "artist": ', '.join(artist.name for artist in track.artists),
            "id": track.id,
            "url": track.url
        } for track in self.saved_tracks]

        await self.write_csv(
            os.path.join(_dir,'Saved Songs.csv'),
            data
        )

        log.debug("- Saved Tracks")

        # delete playlist csv files and then re-populate for ez deletion handling
        playlist_dir = os.path.join(_dir, "playlists")
        for filename in glob.glob(f'{playlist_dir}/*.csv'):
            os.remove(filename)

        await lib_md.write("## Playlists\n\n")
        await lib_md.write("|Name|Author|Description||\n")
        await lib_md.write("--- | --- | --- | ---\n")

        playlists = await self.user.get_all_playlists()
        playlists.sort(key=lambda x: x.name)

        for playlist in playlists:
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

            await self.write_csv(
                os.path.join(_dir, f'playlists/{filename}.csv'),
                data
            )

            log.debug(f'- Playlist {playlist.name}')

            name = RE_MARKDOWN.sub(r'\\\g<0>', playlist.name)
            desc = RE_MARKDOWN.sub(r'\\\g<0>', playlist.description)

            await lib_md.write(f"|{name}|{playlist.owner.display_name}|{desc}|[open]({playlist.url})|\n")

        # ================================
        #             ARTISTS
        # ================================

        await lib_md.write("\n")
        await lib_md.write("## Artists\n\n")
        await lib_md.write("||Name||\n")
        await lib_md.write("--- | --- | ---\n")


        artists = [artist async for artist in self.get_follwing_artists()]
        artists.sort(key=lambda x: x.name)

        await lib_md.writelines([f"|<img src='{a.images[-1].url}' height=32>|{a.name}|[open]({a.url})|\n" for a in artists])

        data = [{
            "name": artist.name,
            "id": artist.id,
            "url": artist.url
        } for artist in artists]

        await self.write_csv(
            os.path.join(_dir,'Artists.csv'),
            data
        )

        log.debug("- Artists")

        # ================================
        #             ALBUMS
        # ================================

        await lib_md.write("\n")
        await lib_md.write("## Albums\n\n")
        await lib_md.write("|Name|Artists||\n")
        await lib_md.write("--- | --- | ---\n")

        albums = list(await self.library.get_all_albums())
        albums.sort(key=lambda x: x.name)

        await lib_md.writelines([f"|{a.name}|{', '.join([f'[{ar.name}]({ar.url})' for ar in a.artists])}|[open]({a.url})|\n" for a in albums])

        data = [{
            "name": album.name,
            "artist": ', '.join(artist.name for artist in album.artists),
            "id": album.id,
            "url": album.url
        } for album in albums]

        await self.write_csv(
            os.path.join(_dir,'Albums.csv'),
            data
        )

        log.debug("- Albums")

        await lib_md.close()

    async def write_csv(self, file, data: List, fields: List = None):
        async with aiofiles.open(file, 'w') as f:
            if not fields:
                fields = list(data[0].keys())

            await f.write(make_csv(fields) + '\n')

            await f.writelines([f"{make_csv(list(l.values()))}\n" for l in data])

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

        # we have to set this or the spotify library thinks
        # the playlist only has 100 tracks cuz bad code smh
        # I would fix it myself but I am lazy
        # https://github.com/mental32/spotify.py/blob/25149bc4d100b42f3dc6908746a45e0fb29a0ae7/spotify/models/playlist.py#L181
        pl.total_tracks = None
        pl.tracks = await pl.get_all_tracks()

        trackids = [track.id for track in pl.tracks]

        tracks = []

        mimo = MiMo()

        async for songid in mimo.get_songs():
            track = await self.client.get_track(songid)
            if track.id not in trackids:
                tracks.append(track)
                trackids.append(track.id)

        while tracks:
            await pl.add_tracks(*tracks[-100:])
            tracks = tracks[:len(tracks)-100]

        await mimo.close()

    async def close(self):
        await self.client.close()
        await self.user.http.close()

RE_TRACKLIST_LINK = re.compile(r"onclick=\"window.open\(\'(.*)',")
RE_MEDIA = re.compile(r"new MediaViewer\(this, .*, \{(.*)\} \);")

class MiMo:
    DOMAIN = 'https://www.1001tracklists.com'

    def __init__(self):
        self.http = ClientSession(headers = Headers(browser='firefox', os='win').generate(), raise_for_status=True)

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

                res = soup.find_all('div', class_=['bItm', 'action', 'oItm'])
                res.reverse()

                for t in res:
                    if link := RE_TRACKLIST_LINK.findall(str(t)):
                        yield link[0]

                page -= 1

                if page == 0:
                    break

    async def parse_tracklist(self, url):
        async with self.http.get(f'{self.DOMAIN}{url}') as r:
            for item in reversed(BeautifulSoup(await r.text(), "html.parser").find_all(class_='mediaRow')):
                btn = item.select('.fa-spotify.mAction')
                if not btn:
                    continue

                media = list(filter(None, RE_MEDIA.findall(btn[0]['onclick'])))
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
