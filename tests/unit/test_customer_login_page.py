from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_customer_login_is_a_dependency_free_customer_only_boundary() -> None:
    page = (ROOT / "deploy/production/customer-login.html").read_text(encoding="utf-8")

    assert '<h1 id="customer-login-title">客户登录</h1>' in page
    assert "登录客户中心" in page
    assert "'/api/v2/identity/login'" in page
    assert "'/api/v2/identity/logout'" in page
    assert "session.role !== 'customer'" in page
    assert "credentials: 'same-origin'" in page
    assert "localStorage" not in page
    assert "sessionStorage" not in page
    assert "/platform/operations/login" in page


def test_customer_login_forwards_only_a_valid_project_hint() -> None:
    page = (ROOT / "deploy/production/customer-login.html").read_text(encoding="utf-8")

    assert "new URLSearchParams(window.location.search).get('project')" in page
    assert "^prj_[A-Za-z0-9_-]{1,116}$" in page
    assert "destination.set('project', project)" in page
    assert "destination.set('section', 'services')" in page
    assert "return_to" not in page
