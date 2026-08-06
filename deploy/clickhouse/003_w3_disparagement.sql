-- W3 拉踩检测 fact 表（2026-08-05 改进计划）。
-- 生产注意：本文件需加入 ClickHouse initdb 挂载清单（deploy/production/compose.yaml
-- 目前只挂 001_s02_analytics.sql 与 002_w2_source_audit.sql，compose 改动由协调者
-- 完成）；initdb 只对新数据卷生效，既有生产卷需手动执行本文件（clickhouse-client
-- --queries-file 003_w3_disparagement.sql）。
-- evidence_quote 不投影到 CH（留 PG 供复查，CH 只承载分布维度与判定结果）。

CREATE TABLE IF NOT EXISTS geo_analytics.disparagement_fact
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    run_pub_id String,
    judgment_pub_id String,
    subject_type LowCardinality(String),
    subject_pub_id String,
    platform LowCardinality(String),
    subject_brand String,
    target_brand String,
    attitude LowCardinality(String),
    disparagement UInt8,
    confidence Float32,
    method LowCardinality(String),
    model LowCardinality(String),
    prompt_version LowCardinality(String),
    judgment_status LowCardinality(String),
    event_time DateTime64(6, 'UTC'),
    event_id String
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, project_pub_id, run_pub_id, event_time, subject_pub_id,
          target_brand, event_id);
