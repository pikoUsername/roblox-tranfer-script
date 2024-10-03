from typing import Sequence, Union, Optional, Dict, Any

from app.services.interfaces import BasicDBConnector


class TokenRepository:
    def __init__(self, conn: BasicDBConnector, model_name: str = "user_tokens") -> None:
        self.conn = conn

        self._model_name = model_name

    async def fetch_selected_tokens(self, limit: int = 10) -> Union[Sequence[str], str]:
        conn = self.conn
        model_name = self._model_name

        # Сначала выбираем токены, где is_selected = true и is_active = true
        sql_selected = f"""
                    SELECT token 
                    FROM {model_name} 
                    WHERE is_selected = true AND is_active = true 
                    LIMIT {limit}
                """

        # Выполняем запрос
        results: Sequence[dict] = await conn.fetchmany(sql_selected)

        tokens = []
        for record in results:
            tokens.append(record.get("token"))
        return tokens

    async def fetch_active_tokens(self, limit: int = 10) -> Union[Sequence[str], str]:
        sql_random = f"""
            SELECT token 
            FROM {self._model_name} 
            WHERE is_active = true 
            ORDER BY RANDOM() 
            LIMIT {limit}
        """

        # Выполняем запрос для случайных активных токенов
        results: Sequence[dict] = await self.conn.fetchmany(sql_random)

        tokens = []
        for record in results:
            tokens.append(record.get("token"))

        return tokens

    async def is_token_selected(self, token: str) -> bool:
        # если token не селектед то выбирается астивные токены
        conn = self.conn
        model_name = self._model_name

        # SQL запрос для проверки is_selected по конкретному токену
        sql = f"""
                SELECT EXISTS (
                    SELECT 1 FROM {model_name}
                    WHERE token = $1 AND is_selected = true
                )
            """

        # Выполняем запрос, используя параметризованный запрос для токена
        result = await conn.fetch(sql, token)

        for key, value in result:
            if key == 'exists' and not value:
                return False
        return True

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

    async def mark_as_selected(self, token: str):
        conn = self.conn
        model_name = self._model_name

        await conn.execute(f"UPDATE {model_name} SET is_selected = true WHERE token = $1", token)

    async def create_tokens_table(self) -> None:
        conn = self.conn
        model_name = self._model_name

        await conn.execute(f"CREATE TABLE IF NOT EXISTS {model_name} ("
                           f"id SERIAL PRIMARY KEY, "
                           f"roblox_name VARCHAR(255), token TEXT,"
                           f"is_active BOOLEAN DEFAULT true, "
                           f"is_selected BOOLEAN default false);")
