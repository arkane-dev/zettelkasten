from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zk_sidecar.app import create_app
from zk_sidecar.config import Config

TOKEN = 'test-token'


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Config(vault=tmp_path, token=TOKEN, llm_api_key='sk-secret')))


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.parametrize('headers', [{}, bearer('wrong'), {'Authorization': TOKEN}])
def test_routes_need_the_token(client: TestClient, headers: dict[str, str]):
    assert client.get('/health', headers=headers).status_code == 401
    assert client.get('/config', headers=headers).status_code == 401


def test_health(client: TestClient):
    r = client.get('/health', headers=bearer())
    assert r.status_code == 200
    assert r.json()['ok'] is True


def test_config_hides_the_api_key(client: TestClient, tmp_path: Path):
    body = client.get('/config', headers=bearer()).json()
    assert body['vault'] == str(tmp_path)
    assert body['has_api_key'] is True
    assert 'sk-secret' not in str(body)


def test_no_docs_routes(client: TestClient):
    assert client.get('/docs', headers=bearer()).status_code == 404
    assert client.get('/openapi.json', headers=bearer()).status_code == 404


def test_config_from_env(tmp_path: Path):
    c = Config.from_env(tmp_path, {'ZK_TOKEN': 't', 'ZK_LLM_MODEL': 'qwen3', 'ZK_LLM_BASE_URL': ''})
    assert c.llm_model == 'qwen3'
    assert c.llm_base_url == 'http://127.0.0.1:11434/v1'


def test_config_needs_a_token(tmp_path: Path):
    with pytest.raises(ValueError):
        Config.from_env(tmp_path, {})
