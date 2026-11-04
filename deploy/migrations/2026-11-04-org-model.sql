-- Vega 组织模型迁移：系统（原「项目」）→ 研发组 → 项目（新顶层）
-- 执行前必须先备份：pg_dump -Fc "$VEGA_DATABASE_URL" > vega-$(date +%F).dump
-- 执行：psql "$VEGA_DATABASE_URL" -v ON_ERROR_STOP=1 -f deploy/migrations/2026-11-04-org-model.sql
-- 幂等：表/列改名与建表都带存在性判断，可重复执行；推断只在 project_id 为空时做。

BEGIN;

-- ---------- 1. 旧表改名：dev_project → dev_system ----------
DO $$
BEGIN
    IF to_regclass('public.dev_project') IS NOT NULL AND to_regclass('public.dev_system') IS NULL THEN
        ALTER TABLE dev_project RENAME TO dev_system;
    END IF;
    IF to_regclass('public.project_pm') IS NOT NULL AND to_regclass('public.system_pm') IS NULL THEN
        ALTER TABLE project_pm RENAME TO system_pm;
    END IF;
    IF to_regclass('public.project_owner') IS NOT NULL AND to_regclass('public.system_owner') IS NULL THEN
        ALTER TABLE project_owner RENAME TO system_owner;
    END IF;
END $$;

-- ---------- 2. 列改名：project_id → system_id ----------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'system_pm', 'system_owner', 'requirement', 'mr_review',
        'egm_report', 'daily_stat', 'file_stat', 'commit_file'
    ]
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = t AND column_name = 'project_id'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = t AND column_name = 'system_id'
        ) THEN
            EXECUTE format('ALTER TABLE %I RENAME COLUMN project_id TO system_id', t);
        END IF;
    END LOOP;
END $$;

-- ---------- 3. 新表：项目 / 项目总负责人 / 个人 GitLab 账号 ----------
CREATE TABLE IF NOT EXISTS project (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(64) NOT NULL UNIQUE,
    code          VARCHAR(16),
    octo_group_no VARCHAR(64),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_leader (
    id         SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(id),
    person_id  INTEGER NOT NULL REFERENCES person(id),
    CONSTRAINT uq_project_leader_project_person UNIQUE (project_id, person_id)
);
CREATE INDEX IF NOT EXISTS ix_project_leader_project_id ON project_leader (project_id);
CREATE INDEX IF NOT EXISTS ix_project_leader_person_id ON project_leader (person_id);

CREATE TABLE IF NOT EXISTS person_gitlab_account (
    id             SERIAL PRIMARY KEY,
    person_id      INTEGER NOT NULL REFERENCES person(id),
    base_url       VARCHAR(256) NOT NULL,
    gitlab_user_id INTEGER,
    username       VARCHAR(190) NOT NULL,
    name           VARCHAR(190) NOT NULL DEFAULT '',
    email          VARCHAR(190) NOT NULL DEFAULT '',
    token_enc      TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_person_gitlab_account_base_user UNIQUE (base_url, username)
);
CREATE INDEX IF NOT EXISTS ix_person_gitlab_account_person_id ON person_gitlab_account (person_id);

-- ---------- 4. 旧表补列 ----------
ALTER TABLE person ADD COLUMN IF NOT EXISTS username VARCHAR(64);
UPDATE person SET username = COALESCE(NULLIF(gitlab_username, ''), 'u' || id) WHERE username IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ix_person_username ON person (username);

ALTER TABLE dev_group ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES project(id);
ALTER TABLE dev_group ADD COLUMN IF NOT EXISTS gitlab_bot_username VARCHAR(190);
CREATE INDEX IF NOT EXISTS ix_dev_group_project_id ON dev_group (project_id);

ALTER TABLE group_member ADD COLUMN IF NOT EXISTS gitlab_account_id INTEGER REFERENCES person_gitlab_account(id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_group_member_gitlab_account_id ON group_member (gitlab_account_id);

ALTER TABLE notify_log ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES project(id);
CREATE INDEX IF NOT EXISTS ix_notify_log_project_id ON notify_log (project_id);

-- ---------- 5. 从系统路径的顶层命名空间推断项目 ----------
-- 例：path = team-ledger/core/repo → 顶层命名空间 team-ledger
INSERT INTO project (name)
SELECT DISTINCT split_part(path, '/', 1)
FROM dev_system
WHERE path IS NOT NULL AND path <> '' AND split_part(path, '/', 1) <> ''
ON CONFLICT (name) DO NOTHING;

-- 研发组按「系统数最多的命名空间」挂项目；并列时取字典序最小，保证结果可复现
WITH counts AS (
    SELECT group_id, split_part(path, '/', 1) AS ns, count(*) AS n
    FROM dev_system
    WHERE path IS NOT NULL AND path <> ''
    GROUP BY 1, 2
), best AS (
    SELECT DISTINCT ON (group_id) group_id, ns
    FROM counts
    ORDER BY group_id, n DESC, ns
)
UPDATE dev_group g
SET project_id = p.id
FROM best b
JOIN project p ON p.name = b.ns
WHERE g.id = b.group_id AND g.project_id IS NULL;

-- 跨多个顶层命名空间的组：按多数挂靠，人工确认归属（输出到 psql 控制台）
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT g.id, g.name,
               string_agg(DISTINCT split_part(s.path, '/', 1), ', ') AS namespaces
        FROM dev_group g
        JOIN dev_system s ON s.group_id = g.id
        WHERE s.path IS NOT NULL AND s.path <> ''
        GROUP BY g.id, g.name
        HAVING count(DISTINCT split_part(s.path, '/', 1)) > 1
    LOOP
        RAISE NOTICE '研发组 %(%) 跨多个顶层命名空间：% —— 已按多数原则挂靠，请人工确认', r.name, r.id, r.namespaces;
    END LOOP;
END $$;

-- ---------- 6. 把旧的 person.gitlab_username 落成账号并绑到组员 ----------
INSERT INTO person_gitlab_account (person_id, base_url, username, name, email)
SELECT DISTINCT gm.person_id, rtrim(g.gitlab_base_url, '/'), p.gitlab_username,
       p.name, COALESCE(p.gitlab_email, '')
FROM group_member gm
JOIN dev_group g ON g.id = gm.group_id
JOIN person p ON p.id = gm.person_id
WHERE p.gitlab_username IS NOT NULL
  AND g.gitlab_base_url IS NOT NULL AND g.gitlab_base_url <> ''
ON CONFLICT (base_url, username) DO NOTHING;

-- 一个账号只能绑一条组员记录；历史数据里同一人在多个组共用账号时只保留第一条
UPDATE group_member gm
SET gitlab_account_id = x.account_id
FROM (
    SELECT DISTINCT ON (a.id) gm.id AS gm_id, a.id AS account_id
    FROM group_member gm
    JOIN dev_group g ON g.id = gm.group_id
    JOIN person p ON p.id = gm.person_id
    JOIN person_gitlab_account a
      ON a.person_id = p.id
     AND a.base_url = rtrim(g.gitlab_base_url, '/')
     AND a.username = p.gitlab_username
    WHERE gm.gitlab_account_id IS NULL
      AND g.gitlab_base_url IS NOT NULL AND g.gitlab_base_url <> ''
    ORDER BY a.id, gm.id
) x
WHERE gm.id = x.gm_id;

COMMIT;

-- 迁移报告：研发组 → 项目 的推断结果
SELECT g.id AS group_id, g.name AS group_name, p.name AS project_name, g.gitlab_bot_username
FROM dev_group g
LEFT JOIN project p ON p.id = g.project_id
ORDER BY g.id;