"""
Загрузчик конфигурации из YAML
"""
import os
import yaml
import logging
from typing import Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

class ConfigLoader:
    def __init__(self, config_path: str = "configs/config.yaml"):
        self.config_path = Path(config_path)
        self._config = None
    
    def load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Конфиг не найден: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
        
        logger.info(f"Конфигурация загружена из {self.config_path}")
        return self._config
    
    def get_devices(self) -> List[Dict[str, Any]]:
        """Возвращает список устройств"""
        if not self._config:
            self.load()
        devices = self._config.get('devices', [])
        return [d for d in devices if d.get('enabled', True)]
    
    def get_database_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию БД"""
        password = os.getenv('DB_PASSWORD')
        if not password:
            logger.warning(
                "DB_PASSWORD не задан в переменных окружения — "
                "скопируйте .env.example в .env и укажите реальный пароль."
            )
        return {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 5432)),
            'name': os.getenv('DB_NAME', 'scada_generator'),
            'user': os.getenv('DB_USER', 'avser'),
            'password': password
        }

_config_loader = None

def get_config() -> ConfigLoader:
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
        _config_loader.load()
    return _config_loader
