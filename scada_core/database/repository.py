"""
Асинхронный репозиторий для работы с PostgreSQL
"""
import asyncpg
import logging
from typing import Optional, List, Dict, Any
from scada_core.config.loader import get_config

logger = logging.getLogger(__name__)

class DatabaseRepository:
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
    
    async def initialize(self):
        if self._pool is not None:
            return
        
        config = get_config().get_database_config()
        try:
            self._pool = await asyncpg.create_pool(
                host=config['host'],
                port=config['port'],
                database=config['name'],
                user=config['user'],
                password=config['password'],
                min_size=2,
                max_size=10
            )
            await self._create_tables()
            logger.info("✅ PostgreSQL подключен")
        except Exception as e:
            logger.error(f"Ошибка подключения к PostgreSQL: {e}")
            raise
    
    async def _create_tables(self):
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id SERIAL PRIMARY KEY,
                    device_id VARCHAR(100) NOT NULL,
                    tag_name VARCHAR(100) NOT NULL,
                    UNIQUE(device_id, tag_name)
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tag_history (
                    id BIGSERIAL PRIMARY KEY,
                    tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                    value FLOAT,
                    quality VARCHAR(20) DEFAULT 'GOOD',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS alarms (
                    id BIGSERIAL PRIMARY KEY,
                    tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                    alarm_type VARCHAR(20) NOT NULL,
                    value FLOAT,
                    message TEXT,
                    acknowledged BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            logger.info("✅ Таблицы созданы")
    
    async def save_tag_value(self, device_id: str, tag_name: str, value: float, quality: str = 'GOOD'):
        async with self._pool.acquire() as conn:
            tag_id = await conn.fetchval("""
                INSERT INTO tags (device_id, tag_name) 
                VALUES ($1, $2)
                ON CONFLICT (device_id, tag_name) 
                DO UPDATE SET tag_name = EXCLUDED.tag_name
                RETURNING id
            """, device_id, tag_name)
            
            await conn.execute("""
                INSERT INTO tag_history (tag_id, value, quality)
                VALUES ($1, $2, $3)
            """, tag_id, value, quality)
    
    async def get_tag_history(self, device_id: str, tag_name: str, limit: int = 100) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT th.value, th.quality, th.timestamp
                FROM tag_history th
                JOIN tags t ON th.tag_id = t.id
                WHERE t.device_id = $1 AND t.tag_name = $2
                ORDER BY th.timestamp DESC
                LIMIT $3
            """, device_id, tag_name, limit)
            return [dict(row) for row in rows]
    
    async def save_alarm(self, device_id: str, tag_name: str, alarm_type: str, value: float, message: str):
        async with self._pool.acquire() as conn:
            tag_id = await conn.fetchval("""
                SELECT id FROM tags WHERE device_id = $1 AND tag_name = $2
            """, device_id, tag_name)
            if tag_id:
                await conn.execute("""
                    INSERT INTO alarms (tag_id, alarm_type, value, message)
                    VALUES ($1, $2, $3, $4)
                """, tag_id, alarm_type, value, message)
    
    async def close(self):
        if self._pool:
            await self._pool.close()
            logger.info("PostgreSQL отключен")

_repository = None

def get_repository() -> DatabaseRepository:
    global _repository
    if _repository is None:
        _repository = DatabaseRepository()
    return _repository

