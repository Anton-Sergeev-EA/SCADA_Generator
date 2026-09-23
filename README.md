# SCADA Generator — асинхронный Modbus TCP → PostgreSQL поллер

Headless-сервис на Python/asyncio: опрашивает устройства Modbus TCP по
расписанию из YAML-конфига, пишет историю тегов и алармы в PostgreSQL.
Никакого GUI и REST API в текущей версии нет — это фоновый процесс.

## Возможности

- **Асинхронный опрос** нескольких устройств Modbus TCP (`DataPoller`,
  `scada_core/engine/`).
- **Декларативная конфигурация** устройств и тегов в `configs/config.yaml`:
  адрес регистра, коэффициент масштабирования, пороги алармов
  (`alarm_high`/`alarm_low`).
- **Автоматическое создание схемы** PostgreSQL при первом запуске: таблицы
  `tags`, `tag_history`, `alarms`.
- **Modbus TCP эмулятор** (`modbus_emulator_new.py`) для локальной разработки
  и тестов без реального ПЛК.

## Структура проекта

```
SCADA_generator/
├── run.py                        # точка входа — запускает поллер как сервис
├── modbus_emulator_new.py        # эмулятор Modbus TCP для тестов
├── configs/config.yaml           # список устройств и тегов для опроса
├── .env.example                  # шаблон переменных окружения (БД)
├── scada_core/
│   ├── engine/
│   │   ├── modbus_client.py      # асинхронный Modbus TCP клиент
│   │   └── data_poller.py        # цикл опроса + коллбэки данных/алармов
│   ├── database/repository.py    # asyncpg-репозиторий, схема PostgreSQL
│   └── config/loader.py          # загрузка config.yaml и .env
├── test_poller.py                # ручная проверка DataPoller + БД
├── test_simple.py                # ручная проверка Modbus-клиента
├── test_sync.py                  # ручная синхронная проверка через pyModbusTCP
├── requirements.txt
└── LICENSE.md
```

Тесты `test_*.py` — это ручные проверочные скрипты, а не набор для
`pytest` (запускаются напрямую `python test_*.py`, а не через test discovery).

## Быстрый старт

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
2. Настройте переменные окружения для PostgreSQL:
   ```bash
   cp .env.example .env
   # отредактируйте .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
   ```
3. Опишите свои устройства и теги в `configs/config.yaml` (адреса регистров,
   пороги алармов).
4. (Опционально, для теста без реального ПЛК) запустите эмулятор Modbus TCP:
   ```bash
   python modbus_emulator_new.py
   ```
5. Запустите поллер:
   ```bash
   python run.py
   ```
   Таблицы в PostgreSQL создаются автоматически при первом подключении.

## Схема базы данных

| Таблица | Описание |
|---|---|
| `tags` | Реестр тегов: `device_id` + `tag_name`, уникальная пара |
| `tag_history` | История значений тега: `value`, `quality`, `timestamp` |
| `alarms` | Сработавшие алармы: тип, значение, сообщение, `acknowledged` |

## Зависимости

| Библиотека | Назначение |
|---|---|
| asyncpg | Асинхронный драйвер PostgreSQL |
| pyModbusTCP | Клиент Modbus TCP (используется в `test_sync.py`) |
| python-dotenv | Загрузка `.env` |
| PyYAML | Парсинг `configs/config.yaml` |
| cryptography | Требуется asyncpg для SCRAM-SHA-256 на некоторых серверах PostgreSQL |
| pytest | Используется в части `test_*.py` как обвязка, не как полноценный сьют |

## Лицензия

MIT — см. [LICENSE.md](LICENSE.md).

## Контакты

Сергеев Антон Валентинович
Эл. почта: kavery@mail.ru
