-- 1. Время с часовым поясом. Старые значения записывались как
--    CURRENT_TIMESTAMP в часовом поясе сессии; приведение TIMESTAMP ->
--    TIMESTAMPTZ использует тот же пояс сессии, поэтому моменты времени
--    сохраняются точно.
ALTER TABLE tag_history ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ;
ALTER TABLE tag_history ALTER COLUMN "timestamp" SET DEFAULT now();
ALTER TABLE alarms ALTER COLUMN created_at TYPE TIMESTAMPTZ;
ALTER TABLE alarms ALTER COLUMN created_at SET DEFAULT now();

-- 2. Индекс под основной запрос «история тега за период».
--    Без него каждый запрос тренда — полный просмотр таблицы.
CREATE INDEX IF NOT EXISTS idx_tag_history_tag_ts ON tag_history (tag_id, "timestamp" DESC);

-- 3. Метаданные тегов для отчётов.
ALTER TABLE tags ADD COLUMN IF NOT EXISTS unit VARCHAR(32);
ALTER TABLE tags ADD COLUMN IF NOT EXISTS label JSONB;

-- 4. Жизненный цикл аларма (ISA-18.2): одна строка на активацию,
--    а не на каждый цикл опроса.
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS device_id VARCHAR(100);
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS priority VARCHAR(10);
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS limit_value FLOAT;
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS params JSONB;
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS cleared_at TIMESTAMPTZ;
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS acked_at TIMESTAMPTZ;
ALTER TABLE alarms ADD COLUMN IF NOT EXISTS acked_by VARCHAR(100);
CREATE INDEX IF NOT EXISTS idx_alarms_created ON alarms (created_at DESC);

-- 5. Журнал ML-инсайтов.
CREATE TABLE IF NOT EXISTS ml_insights (
    id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(100) NOT NULL,
    tag_name VARCHAR(100),
    kind VARCHAR(20) NOT NULL,
    severity VARCHAR(10) NOT NULL,
    code VARCHAR(50) NOT NULL,
    params JSONB,
    peak FLOAT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ml_insights_started ON ml_insights (started_at DESC);
