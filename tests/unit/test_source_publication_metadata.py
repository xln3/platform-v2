from datetime import UTC, datetime

from domain.collection.source_metadata import extract_source_metadata


def test_jsonld_and_visible_date_agreement_is_verified() -> None:
    metadata = extract_source_metadata(
        """
        <html lang="zh-CN"><head>
          <title>测试文章</title>
          <link rel="canonical" href="/article">
          <script type="application/ld+json">
            {"@type":"Article","headline":"测试文章","datePublished":"2026-08-01T09:30:00+08:00",
             "dateModified":"2026-08-02T10:00:00+08:00","author":{"name":"张三"}}
          </script>
        </head><body>
          <time itemprop="datePublished" datetime="2026-08-01">2026年8月1日</time>
        </body></html>
        """,
        final_url="https://example.com/article?from=feed",
        observed_at=datetime(2026, 8, 18, tzinfo=UTC),
    )

    assert metadata.published_at is not None
    assert metadata.published_at.isoformat() == "2026-08-01T09:30:00+08:00"
    assert metadata.published_at_source == "jsonld.datePublished"
    assert metadata.published_at_confidence == "verified_structured"
    assert metadata.published_at_precision == "second"
    assert metadata.canonical_url == "https://example.com/article"
    assert metadata.authors == ("张三",)
    assert metadata.modified_at is not None
    assert len(metadata.candidates) >= 3


def test_last_modified_is_never_selected_as_publication_time() -> None:
    metadata = extract_source_metadata(
        "<html><head><title>无发布时间</title></head><body>正文</body></html>",
        final_url="https://example.com/no-date",
        response_headers={"Last-Modified": "Mon, 17 Aug 2026 10:00:00 GMT"},
        observed_at=datetime(2026, 8, 18, tzinfo=UTC),
    )

    assert metadata.published_at is None
    assert metadata.published_at_confidence == "unknown"
    assert metadata.modified_at == datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    modified_candidate = next(
        candidate for candidate in metadata.candidates if candidate.source == "http.last_modified"
    )
    assert modified_candidate.precision == "second"


def test_explicit_meta_date_precedes_time_and_agreement_is_verified() -> None:
    metadata = extract_source_metadata(
        """
        <meta name="publishdate" content="2026-08-04">
        <time datetime="2026-08-04">2026-08-04</time>
        """,
        final_url="https://example.com/2026/08/03/article",
        observed_at=datetime(2026, 8, 18, tzinfo=UTC),
    )

    assert metadata.published_at_raw == "2026-08-04"
    assert metadata.published_at_source == "meta.publishdate"
    assert metadata.published_at_confidence == "verified_structured"
    assert len(metadata.candidates) == 3


def test_url_date_is_low_confidence_and_future_date_is_not_selected() -> None:
    inferred = extract_source_metadata(
        "<html><body>正文</body></html>",
        final_url="https://example.com/2026/08/03/article",
        observed_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    future = extract_source_metadata(
        '<meta property="article:published_time" content="2099-01-01T00:00:00Z">',
        final_url="https://example.com/article",
        observed_at=datetime(2026, 8, 18, tzinfo=UTC),
    )

    assert inferred.published_at_confidence == "inferred_low"
    assert inferred.published_at_precision == "date"
    assert future.published_at is None
    assert future.candidates[0].parsed_at is not None
