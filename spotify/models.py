from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, HttpUrl


class SpotifyBase(BaseModel):
    id: str
    uri: str


class Image(BaseModel):
    width: Optional[int]
    height: Optional[int]
    url: HttpUrl


class ExternalUrls(BaseModel):
    spotify: HttpUrl


class Url:
    @property
    def url(self) -> str:
        return self.external_urls.spotify


class User(SpotifyBase):
    display_name: str


class Artist(SpotifyBase, Url):
    name: str
    external_urls: ExternalUrls
    images: List[Image] = []


class Album(SpotifyBase, Url):
    name: str
    artists: List[Artist]
    images: List[Image]
    release_date: date
    total_tracks: int
    external_urls: ExternalUrls


class Playlist(SpotifyBase, Url):
    id: str
    name: str
    public: bool
    description: str
    primary_color: Optional[str]
    owner: User
    images: List[Image]
    external_urls: ExternalUrls


class Track(SpotifyBase, Url):
    name: str
    artists: List[Artist]
    album: Album
    external_urls: ExternalUrls


class ListTrack(BaseModel):
    added_at: datetime
    # added_by: User
    track: Track
