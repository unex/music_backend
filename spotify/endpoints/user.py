from .base import Endpoint
from ..models import User


class UserEndpoint(Endpoint):
    async def get_one(self, user_id: str) -> User:
        return User(**(await self._api.user.get_one(user_id)))

    async def me(self):
        return User(**(await self._api.user.me()))
