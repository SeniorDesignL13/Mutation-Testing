"""Smoke tests confirming the project shell is wired up correctly.

These exist to catch dependency/packaging problems early - they are not
feature tests. Add real tests alongside the code they cover.
"""

from typer.testing import CliRunner

runner = CliRunner()


def test_package_imports() -> None:
    import mtlj

    assert mtlj.__version__ == "0.1.0"


def test_cli_version_command() -> None:
    from mtlj.cli.main import app

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_service_health_endpoint() -> None:
    from fastapi.testclient import TestClient

    from mtlj.service.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
