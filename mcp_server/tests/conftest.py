import pytest


@pytest.fixture(autouse=True)
def mock_devices(monkeypatch):
    """The spawned MCP server inherits this environment; set values win over backend/.env (load_dotenv
    never overrides), so these tests use the mock light even when DEVICE_MODE=unoq is configured."""
    monkeypatch.setenv("DEVICE_MODE", "mock")
