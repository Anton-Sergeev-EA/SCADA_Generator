-- Исходная схема (как её создавала версия 0.x). Идемпотентна:
-- на существующей базе ничего не меняет.
CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(100) NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    UNIQUE (device_id, tag_name)
);

CREATE TABLE IF NOT EXISTS tag_history (
    id BIGSERIAL PRIMARY KEY,
    tag_id INTEGER REFERENCES tags (id) ON DELETE CASCADE,
    value FLOAT,
    quality VARCHAR(20) DEFAULT 'GOOD',
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alarms (
    id BIGSERIAL PRIMARY KEY,
    tag_id INTEGER REFERENCES tags (id) ON DELETE CASCADE,
    alarm_type VARCHAR(20) NOT NULL,
    value FLOAT,
    message TEXT,
    acknowledged BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
