-- 方案消息是否由 EGM 设计技能接管：工作台「技术方案」卡片据此显示 EGM 标记
-- 执行前先备份：pg_dump -Fc "$VEGA_DATABASE_URL" > vega-$(date +%F).dump
-- 幂等，可重复执行。

BEGIN;

ALTER TABLE plan_message ADD COLUMN IF NOT EXISTS egm BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;

SELECT egm, count(*) FROM plan_message GROUP BY egm;
