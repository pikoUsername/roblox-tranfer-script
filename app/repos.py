from typing import Sequence, Union, Optional, Dict, Any

from app.services.interfaces import BasicDBConnector


class TokenRepository:
    def __init__(self, conn: BasicDBConnector, model_name: str) -> None:
        self.conn = conn

        self._model_name = model_name

    async def fetch_active_tokens(self, limit: int = 10) -> Union[Sequence[str], str]:
        conn = self.conn
        model_name = self._model_name

        sql = f"SELECT token FROM {model_name} WHERE is_active = true LIMIT {limit}"

        results: Sequence[dict] = await conn.fetchmany(sql)
        tokens = []
        for record in results:
            tokens.append(record.get("token"))
        return tokens

    async def fetch_tokens_with_user(self, limit: int = 10) -> list[Dict[str, Any]]:
        conn = self.conn
        model_name = self._model_name

        sql = f"SELECT token, roblox_name FROM {model_name} WHERE is_active = true LIMIT {limit}"

        results: Sequence[dict] = await conn.fetchmany(sql)
        tokens: list[dict] = []
        for record in results:
            tokens.append({
                'token': record.get("token"),
                'roblox_name': record.get('roblox_name'),
            })
        return tokens

    async def fetch_token(self) -> Optional[str]:
        """
        Выбирает рандомный свободный токен

        :return:
        """
        tokens = await self.fetch_active_tokens()
        if not tokens:
            return ""
        return tokens[0]

    async def mark_as_inactive(self, token: str) -> None:
        conn = self.conn
        model_name = self._model_name

        await conn.execute(f"UPDATE {model_name} SET is_active = false WHERE token = $1", token)

    async def create_tokens_table(self) -> None:
        conn = self.conn
        model_name = self._model_name

        await conn.execute(f"CREATE TABLE IF NOT EXISTS {model_name} ("
                           f"id SERIAL PRIMARY KEY, "
                           f"roblox_name VARCHAR(255), token TEXT, is_active BOOLEAN DEFAULT true);")
