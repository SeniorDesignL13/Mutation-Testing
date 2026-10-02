from typer.testing import CliRunner

from mtlj import __version__
from mtlj.cli.main import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.stdout
