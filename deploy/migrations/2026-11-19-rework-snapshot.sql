-- 运行快照 + 方案返工回路
-- 幂等，可重复执行。执行前按惯例先备份。

BEGIN;

-- P3：本次执行用的模型/prompt/参数快照
ALTER TABLE dev_task ADD COLUMN IF NOT EXISTS snapshot JSON;

-- P2：返工轮次。0 = 首轮；>0 = 第几次返工派发的任务，工作台据此分两段画流程
ALTER TABLE dev_task ADD COLUMN IF NOT EXISTS rework_round INTEGER NOT NULL DEFAULT 0;

-- P2：CRG 初审不可通过时，由方案阶段 AM 专家出修订稿 → EGM 复审 → 重新派发
ALTER TABLE requirement ADD COLUMN IF NOT EXISTS rework_round INTEGER NOT NULL DEFAULT 0;
ALTER TABLE requirement ADD COLUMN IF NOT EXISTS rework_state VARCHAR(16);
ALTER TABLE requirement ADD COLUMN IF NOT EXISTS rework_team_id VARCHAR(64);
ALTER TABLE requirement ADD COLUMN IF NOT EXISTS rework_agent_id VARCHAR(64);

COMMIT;

SELECT count(*) FILTER (WHERE snapshot IS NOT NULL) AS tasks_with_snapshot FROM dev_task;
SELECT count(*) FILTER (WHERE rework_state IS NOT NULL) AS requirements_in_rework FROM requirement;