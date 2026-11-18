-- 部署触发测试：部署事件表 + 任务合入时间 + 系统的环境映射配置
-- 执行前先备份：pg_dump -Fc "$VEGA_DATABASE_URL" > vega-$(date +%F).dump
-- 幂等，可重复执行。

BEGIN;

CREATE TABLE IF NOT EXISTS deploy_event (
    id SERIAL PRIMARY KEY,
    system_id INTEGER NOT NULL REFERENCES dev_system(id) ON DELETE CASCADE,
    gitlab_project_id INTEGER NOT NULL,
    environment VARCHAR(64) NOT NULL,
    ref VARCHAR(256),
    sha VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    deployed_at TIMESTAMPTZ NOT NULL,
    -- 原始事件，排查字段差异用；result 回填本次匹配的触发/跳过明细
    payload JSON,
    handled_at TIMESTAMPTZ,
    result JSON,
    CONSTRAINT uq_deploy_event_env_sha UNIQUE (system_id, environment, sha)
);
CREATE INDEX IF NOT EXISTS ix_deploy_event_system_id ON deploy_event (system_id);
CREATE INDEX IF NOT EXISTS ix_deploy_event_system_time ON deploy_event (system_id, deployed_at);

ALTER TABLE dev_task ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;
ALTER TABLE dev_system ADD COLUMN IF NOT EXISTS deploy_env_map JSON;
ALTER TABLE dev_system ADD COLUMN IF NOT EXISTS deploy_refs JSON;

-- 老数据回填：已合入但没记时间的任务用 updated_at 兜底，避免上线即漏测
UPDATE dev_task SET merged_at = updated_at WHERE status = 'done' AND merged_at IS NULL;

COMMIT;

SELECT s.path, s.deploy_env_map::text AS deploy_env_map,
       count(t.id) FILTER (WHERE t.merged_at IS NOT NULL) AS merged_tasks
FROM dev_system s
LEFT JOIN dev_task t ON t.requirement_id IN (SELECT id FROM requirement WHERE system_id = s.id)
GROUP BY s.id, s.path, CAST(s.deploy_env_map AS text)
ORDER BY s.id;