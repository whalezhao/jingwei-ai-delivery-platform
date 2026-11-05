-- 需求类型：requirement（默认，走需求文档/定稿）或 bug（跳过需求文档，开发直接做方案）
-- 执行前先备份：pg_dump -Fc "$VEGA_DATABASE_URL" > vega-$(date +%F).dump
-- 幂等，可重复执行。

BEGIN;

ALTER TABLE requirement ADD COLUMN IF NOT EXISTS req_type VARCHAR(16) NOT NULL DEFAULT 'requirement';

COMMIT;

SELECT req_type, count(*) FROM requirement GROUP BY req_type;