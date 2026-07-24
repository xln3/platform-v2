CREATE DATABASE IF NOT EXISTS geo_analytics;

CREATE TABLE IF NOT EXISTS geo_analytics.answer_fact
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    answer_pub_id String,
    run_pub_id String,
    query_pub_id String,
    event_time DateTime64(6, 'UTC'),
    model LowCardinality(String),
    region LowCardinality(String),
    mode LowCardinality(String),
    channel LowCardinality(String),
    account_dimension_opaque Nullable(String),
    mentioned UInt8,
    rank Nullable(UInt16),
    sentiment LowCardinality(String),
    recommended Nullable(UInt8),
    citation_count UInt16,
    scorer_version LowCardinality(String),
    metric_version LowCardinality(String),
    input_hash FixedString(64),
    event_id String
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, project_pub_id, event_time, answer_pub_id, event_id);

CREATE TABLE IF NOT EXISTS geo_analytics.citation_fact
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    answer_pub_id String,
    citation_pub_id String,
    event_time DateTime64(6, 'UTC'),
    canonical_host LowCardinality(String),
    canonical_url String,
    content_hash Nullable(FixedString(64)),
    own_source UInt8,
    event_id String
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, project_pub_id, event_time, answer_pub_id, citation_pub_id);

CREATE TABLE IF NOT EXISTS geo_analytics.run_event
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    run_pub_id String,
    event_id String,
    event_type LowCardinality(String),
    event_time DateTime64(6, 'UTC'),
    status LowCardinality(String),
    adapter_version LowCardinality(String),
    payload_json String
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, project_pub_id, run_pub_id, event_time, event_id);

CREATE TABLE IF NOT EXISTS geo_analytics.feature_fact
(
    tenant_pub_id LowCardinality(String),
    investigation_pub_id String,
    subject_pub_id String,
    event_id String,
    feature_name LowCardinality(String),
    feature_value Float64,
    rule_version LowCardinality(String),
    model_version LowCardinality(String),
    event_time DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(event_time)
PARTITION BY toYYYYMM(event_time)
ORDER BY (tenant_pub_id, investigation_pub_id, subject_pub_id, feature_name, event_id);

CREATE TABLE IF NOT EXISTS geo_analytics.metric_daily
(
    tenant_pub_id LowCardinality(String),
    project_pub_id String,
    metric_date Date,
    metric_name LowCardinality(String),
    dimensions_hash FixedString(64),
    dimensions_json String,
    value Nullable(Decimal64(6)),
    numerator Nullable(UInt64),
    denominator UInt64,
    state LowCardinality(String),
    metric_version LowCardinality(String),
    scorer_version LowCardinality(String),
    trace_token FixedString(64),
    updated_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(metric_date)
ORDER BY (tenant_pub_id, project_pub_id, metric_date, metric_name, dimensions_hash,
          metric_version, scorer_version);

