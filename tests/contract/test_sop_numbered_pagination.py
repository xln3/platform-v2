from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NUMBERED_SOP_GETS = (
    "/api/v2/sop/projects",
    "/api/v2/sop/projects/{project_pub_id}/dashboard",
    "/api/v2/sop/projects/{project_pub_id}/query-sets",
    "/api/v2/sop/query-sets/{query_set_pub_id}/items",
    "/api/v2/sop/projects/{project_pub_id}/baseline-answers",
    "/api/v2/sop/projects/{project_pub_id}/insights",
    "/api/v2/sop/projects/{project_pub_id}/evidence",
    "/api/v2/sop/projects/{project_pub_id}/opportunities",
    "/api/v2/sop/projects/{project_pub_id}/articles",
    "/api/v2/sop/articles/{article_pub_id}/versions",
    "/api/v2/sop/article-versions/{version_pub_id}/checks",
    "/api/v2/sop/projects/{project_pub_id}/publications",
    "/api/v2/sop/publications/{publication_pub_id}/observations",
    "/api/v2/sop/publications/{publication_pub_id}/retest-answers",
    "/api/v2/sop/publications/{publication_pub_id}/comparisons",
    "/api/v2/sop/projects/{project_pub_id}/experiments",
    "/api/v2/sop/projects/{project_pub_id}/work-logs",
)


def _openapi() -> dict[str, object]:
    return json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))


def test_all_unbounded_sop_gets_only_publish_numbered_pagination() -> None:
    schema = _openapi()
    paths = schema["paths"]

    for path in NUMBERED_SOP_GETS:
        operation = paths[path]["get"]
        query_names = {
            parameter["name"] for parameter in operation["parameters"] if parameter["in"] == "query"
        }
        assert {"page", "page_size"} <= query_names, path
        assert "cursor" not in query_names, path
        assert "limit" not in query_names, path


def test_sop_details_do_not_embed_unbounded_legacy_collections() -> None:
    schemas = _openapi()["components"]["schemas"]

    assert "versions" not in schemas["ArticleView"]["properties"]
    assert "checks" not in schemas["ArticleVersionDetail"]["properties"]
    assert "observations" not in schemas["PublicationDetail"]["properties"]
    assert schemas["DashboardView"]["properties"]["articles"] == {
        "$ref": "#/components/schemas/SopPage_DashboardArticle_"
    }
