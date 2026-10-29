from pathlib import Path

from snm_data.env import load_env_file


def test_env_file_fills_missing_values_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        'export DATABRICKS_TOKEN="from-file"\n'
        "DATABRICKS_WAREHOUSE_ID=\n"
        "SNM_CATALOG='snm'\n"
        "not a setting\n",
        encoding="utf-8",
    )
    environ = {"DATABRICKS_TOKEN": "from-environment"}
    loaded = load_env_file(env_file, environ)
    assert environ["DATABRICKS_TOKEN"] == "from-environment"
    assert environ["DATABRICKS_HOST"] == "https://example.cloud.databricks.com"
    assert environ["SNM_CATALOG"] == "snm"
    assert "DATABRICKS_WAREHOUSE_ID" not in environ
    assert sorted(loaded) == ["DATABRICKS_HOST", "SNM_CATALOG"]


def test_missing_env_file_is_ignored(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / ".env", {}) == []
