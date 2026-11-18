-- 系统测试负责人：测试执行开始/结果在研发组 octo 群 @ 的人
-- 执行前先备份：pg_dump -Fc "$VEGA_DATABASE_URL" > vega-$(date +%F).dump
-- 幂等，可重复执行。

BEGIN;

CREATE TABLE IF NOT EXISTS system_qa_owner (
    id SERIAL PRIMARY KEY,
    system_id INTEGER NOT NULL REFERENCES dev_system(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    UNIQUE (system_id, person_id)
);
CREATE INDEX IF NOT EXISTS ix_system_qa_owner_system_id ON system_qa_owner (system_id);
CREATE INDEX IF NOT EXISTS ix_system_qa_owner_person_id ON system_qa_owner (person_id);

COMMIT;

SELECT s.path, p.name
FROM system_qa_owner o
JOIN dev_system s ON s.id = o.system_id
JOIN person p ON p.id = o.person_id
ORDER BY s.id;