-- W2 信源准确性核对 fact 表（2026-08-05 改进计划）。
-- 生产注意：本文件需加入 ClickHouse initdb 挂载清单（deploy/production/compose.yaml
-- 目前只挂 001_s02_analytics.sql，compose 改动由协调者完成）。

CREATE TABLE IF NOT EXISTS geo_analytics.source_audit_fact
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    run_pub_id String,
    source_document_pub_id String,
    source_audit_pub_id String,
    url String,
    host LowCardinality(String),
    dimension LowCardinality(String),
    verdict LowCardinality(String),
    audit_status LowCardinality(String),
    model LowCardinality(String),
    prompt_version LowCardinality(String),
    event_time DateTime64(6, 'UTC'),
    event_id String
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, project_pub_id, run_pub_id, event_time, source_document_pub_id,
          dimension, event_id);
