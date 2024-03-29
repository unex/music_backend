import os
import re
import asyncio
import traceback
import glob
import logging

from datetime import date, datetime, timedelta, timezone
from typing import List
from urllib.parse import urlparse, urlunparse

import aiofiles

from derw import makeLogger

import spotify

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
    "user-follow-read",
]
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN")

SPOTIFY_MIRROR_PLAYLIST = os.environ.get("SPOTIFY_MIRROR_PLAYLIST")

# GIT
GIT_REPO = os.environ.get("GIT_REPO")
GIT_COMMITTER_NAME = os.environ.get("GIT_COMMITTER_NAME")
GIT_COMMITTER_EMAIL = os.environ.get("GIT_COMMITTER_EMAIL")
GIT_PASSWORD = os.environ.get("GIT_PASSWORD")

RE_MARKDOWN = re.compile(r"\|")


class Git:
    def __init__(self):
        self.dir = os.path.abspath(GIT_REPO)

    async def _run_command(self, command):
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.dir,
        )

        stdout, stderr = await proc.communicate()

        if stderr and proc.returncode != 0:
            raise Exception(f'Command "{command}" failed: {stderr.decode().strip()}')

        return stdout.decode().strip()

    async def pull(self):
        await self._run_command("git reset HEAD --hard")
        await self._run_command("git pull --quiet")

    async def commit_and_push(self):
        # check if we need to make a commit
        diff = await self._run_command("git diff --numstat")

        if not len(diff):
            log.info("No changes, commit not needed")
            return

        # Add all files
        await self._run_command("git add -A .")

        # Create commit
        await self._run_command(
            f"git commit --message='{date.today()}' --author='{GIT_COMMITTER_NAME} <{GIT_COMMITTER_EMAIL}>' --no-gpg-sign"
        )
        commit_id = await self._run_command("git rev-parse --verify HEAD")
        log.info(f"Created commit {commit_id}")

        # Create push
        origin = await self._run_command("git remote get-url origin")

        parts = urlparse(origin)
        parts = parts._replace(
            netloc=f"{GIT_COMMITTER_NAME}:{GIT_PASSWORD}@{parts.netloc}"
        )

        origin = urlunparse(parts)

        await self._run_command(f"git push {origin} master --porcelain")


def make_csv(d: List[str]):
    ret = []
    for s in d:
        if s is None:
            s = ""
        if set([",", '"']).intersection(s):
            s = s.replace('"', '""')
            ret.append(f'"{s}"')
        else:
            ret.append(s)

    return ",".join(ret)


class DewsBeats:
    def __init__(self):
        self.saved_tracks: List[spotify.ListTrack]
        self.playlists: List[spotify.Playlist]

        self.spotify = spotify.SpotifyClient(
            SPOTIFY_CLIENT_ID,
            SPOTIFY_CLIENT_SECRET,
            SPOTIFY_SCOPES,
            SPOTIFY_REDIRECT_URI,
            SPOTIFY_REFRESH_TOKEN,
        )

    async def main(self):
        try:
            await self.spotify.refresh_token()

            self.git = Git()
            await self.git.pull()

            me = await self.spotify.user.me()

            log.info(f"Logged in as {me.display_name} ({me.id})")

            self.saved_tracks = list(
                [track async for track in self.spotify.library.get_tracks()]
            )
            self.saved_tracks.sort(key=lambda x: x.added_at)

            self.playlists = list(
                [pl async for pl in self.spotify.playlists.current_get_all()]
            )
            self.playlists.sort(key=lambda x: x.name)

            await self.purge_idk_playlists()

            await self.update_playlist()

            await self.update_git()

            await self.git.commit_and_push()

        except:
            traceback.print_exc()

        finally:
            await self.close()

    async def purge_idk_playlists(self):
        log.debug("Purging idk playlists")

        for playlist in self.playlists:
            if not playlist.name.startswith("idk"):
                continue

            tracks: List[str] = []

            async for track in self.spotify.playlists.get_tracks(playlist.id):
                if track.added_at > datetime.now(timezone.utc) - timedelta(weeks=2):
                    continue

                tracks.append(track)

            if not tracks:
                continue

            tracks.sort(key=lambda x: x.added_at)

            ids = list(map(lambda x: x.track.id, tracks))
            await self.spotify.library.add_tracks(ids)

            uris = list(map(lambda x: x.track.uri, tracks))
            await self.spotify.playlists.remove_tracks(playlist.id, uris)

            log.debug(f"- {len(tracks)} track(s) from {playlist.name}")

    async def update_git(self):
        log.debug("Updating Git")

        _dir = os.path.relpath(GIT_REPO)

        lib_md = await aiofiles.open(os.path.join(_dir, "LIBRARY.md"), "w")

        await lib_md.writelines("# Library\n\n")

        # ================================
        #    SAVED TRACKS & PLAYLISTS
        # ================================

        log.debug("- Saved Tracks")

        data = [
            {
                "title": track.track.name,
                "album": track.track.album.name,
                "artist": ", ".join(artist.name for artist in track.track.artists),
                "id": track.track.id,
                "url": track.track.url,
            }
            for track in self.saved_tracks
        ]

        await self.write_csv(os.path.join(_dir, "Saved Songs.csv"), data)

        # delete playlist csv files and then re-populate for ez deletion handling
        playlist_dir = os.path.join(_dir, "playlists")
        for filename in glob.glob(f"{playlist_dir}/*.csv"):
            os.remove(filename)

        await lib_md.write("## Playlists\n\n")
        await lib_md.write("|Name|Author|Description||\n")
        await lib_md.write("--- | --- | --- | ---\n")

        for playlist in self.playlists:
            log.debug(f"- Playlist {playlist.name}")

            # Ignore the mirror playlist just cuz its a duplicate of saved tracks
            if playlist.id == SPOTIFY_MIRROR_PLAYLIST:
                continue

            # Ignore playlists that are not mine or spoitfys?
            # if playlist.owner.id not in [self.user.id, "spotify"]:
            #     continue

            if not playlist.public:
                continue

            tracks = list(
                [
                    track
                    async for track in self.spotify.playlists.get_tracks(playlist.id)
                ]
            )

            # Sort by name first so that tracks with the same added_at
            # will always appear in the same order
            tracks.sort(key=lambda x: x.track.name)
            tracks.sort(key=lambda x: x.added_at)

            data = [
                {
                    "title": track.track.name,
                    "album": track.track.album.name,
                    "artist": ", ".join(artist.name for artist in track.track.artists),
                    "id": track.track.id,
                    "url": track.track.url,
                }
                for track in tracks
            ]

            filename = re.sub(r"[^\w\d\s-]", "_", playlist.name)

            await self.write_csv(os.path.join(_dir, f"playlists/{filename}.csv"), data, fields = ["title", "album", "artist", "id", "url"])

            name = RE_MARKDOWN.sub(r"\\\g<0>", playlist.name)
            desc = RE_MARKDOWN.sub(r"\\\g<0>", playlist.description)

            await lib_md.write(
                f"|{name}|{playlist.owner.display_name}|{desc}|[open]({playlist.url})|\n"
            )

        # ================================
        #             ARTISTS
        # ================================

        log.debug("- Artists")

        await lib_md.write("\n")
        await lib_md.write("## Artists\n\n")
        await lib_md.write("||Name||\n")
        await lib_md.write("--- | --- | ---\n")

        artists = [artist async for artist in self.spotify.follow.get_followed_artist()]
        artists.sort(key=lambda x: x.name)

        await lib_md.writelines(
            [
                f"|<img src='{a.images[-1].url}' height=32>|{a.name}|[open]({a.url})|\n"
                for a in artists
            ]
        )

        data = [
            {"name": artist.name, "id": artist.id, "url": artist.url}
            for artist in artists
        ]

        await self.write_csv(os.path.join(_dir, "Artists.csv"), data)

        # ================================
        #             ALBUMS
        # ================================

        log.debug("- Albums")

        await lib_md.write("\n")
        await lib_md.write("## Albums\n\n")
        await lib_md.write("||Name|Artists||\n")
        await lib_md.write("--- | --- | --- | ---\n")

        albums = list([a async for a in self.spotify.library.get_albums()])
        albums.sort(key=lambda x: x.name)

        await lib_md.writelines(
            [
                f"|<img src='{a.images[-1].url}' height=32>|{a.name}|{', '.join([f'[{ar.name}]({ar.url})' for ar in a.artists])}|[open]({a.url})|\n"
                for a in albums
            ]
        )

        data = [
            {
                "name": album.name,
                "artist": ", ".join(artist.name for artist in album.artists),
                "id": album.id,
                "url": album.url,
            }
            for album in albums
        ]

        await self.write_csv(os.path.join(_dir, "Albums.csv"), data)

        await lib_md.close()

    async def write_csv(self, file, data: List, fields: List = None):
        async with aiofiles.open(file, "w") as f:
            if not fields:
                fields = list(data[0].keys())

            await f.write(make_csv(fields) + "\n")

            await f.writelines([f"{make_csv(list(l.values()))}\n" for l in data])

    async def update_playlist(self):
        playlist_tracks: List[str] = [
            track.track.uri
            async for track in self.spotify.playlists.get_tracks(
                SPOTIFY_MIRROR_PLAYLIST  # , fields="items(track(uri))
            )
        ]

        new_tracks: List[str] = []

        for track in self.saved_tracks:
            if track.track.uri not in playlist_tracks:
                new_tracks.append(track.track.uri)

        await self.spotify.playlists.add_tracks(SPOTIFY_MIRROR_PLAYLIST, new_tracks)

        log.debug(f"Added {len(new_tracks)} new tracks to mirror playlist")

    async def close(self):
        pass
        # await self.client.close()
        # await self.user.http.close()


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    d = DewsBeats()
    loop.run_until_complete(d.main())
