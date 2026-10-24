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


# Sources through the API. The LLM points at a closed port, so summaries fail fast.


@pytest.fixture
def api(tmp_path: Path) -> TestClient:
    config = Config(vault=tmp_path, token=TOKEN, llm_base_url='http://127.0.0.1:9/v1')
    return TestClient(create_app(config))


def wait(api: TestClient, job: dict) -> dict:
    import time

    for _ in range(200):
        job = api.get(f"/jobs/{job['id']}", headers=bearer()).json()
        if job['state'] in ('done', 'error'):
            return job
        time.sleep(0.05)
    raise AssertionError(f'job never finished: {job}')


def test_upload_a_file(api: TestClient, tmp_path: Path):
    r = api.post('/sources/file', params={'name': 'Field notes.txt'}, content=b'Leiden is fast.', headers=bearer())
    assert r.status_code == 202
    job = wait(api, r.json())
    assert job['state'] == 'done'
    assert job['note'] == 'Sources/field-notes/Field notes.md'
    assert 'Summary failed' in job['warning']
    assert (tmp_path / job['note']).exists()
    listed = api.get('/sources', headers=bearer()).json()
    assert listed == [{'slug': 'field-notes', 'title': 'Field notes', 'kind': 'text', 'note': job['note']}]


def test_upload_rejects_unknown_types_and_empty_files(api: TestClient):
    assert api.post('/sources/file', params={'name': 'run.exe'}, content=b'x', headers=bearer()).status_code == 400
    assert api.post('/sources/file', params={'name': 'a.pdf'}, content=b'', headers=bearer()).status_code == 400


def test_upload_name_cannot_escape_the_vault(api: TestClient, tmp_path: Path):
    r = api.post('/sources/file', params={'name': '../../evil.txt'}, content=b'x', headers=bearer())
    job = wait(api, r.json())
    assert job['note'] == 'Sources/evil/evil.md'


def test_bad_url_is_refused(api: TestClient):
    assert api.post('/sources/url', json={'url': 'file:///etc/passwd'}, headers=bearer()).status_code == 400


def test_recapture_and_summarise_need_a_source(api: TestClient):
    assert api.post('/sources/nope/recapture', headers=bearer()).status_code == 404
    assert api.post('/sources/nope/summarise', headers=bearer()).status_code == 404
    job = wait(api, api.post('/sources/file', params={'name': 'a.txt'}, content=b'x', headers=bearer()).json())
    assert api.post('/sources/a/recapture', headers=bearer()).status_code == 400
    assert api.post('/sources/a/summarise', headers=bearer()).status_code == 202
    assert job['state'] == 'done'


def test_unknown_job(api: TestClient):
    assert api.get('/jobs/nope', headers=bearer()).status_code == 404


def test_models_reports_an_unreachable_api(tmp_path: Path):
    app = create_app(Config(vault=tmp_path, token=TOKEN, llm_base_url='http://127.0.0.1:9/v1', graph_schedule='off'))
    body = TestClient(app).get('/models', headers=bearer()).json()
    assert body['ok'] is False and 'did not answer' in body['error'] and body['found'] is False


def test_models_lists_what_the_api_has(tmp_path: Path, monkeypatch):
    import zk_sidecar.app as app_mod
    monkeypatch.setattr(app_mod, 'list_models', lambda url, key: ['gemma3:27b', 'qwen3:30b'])
    app = create_app(Config(vault=tmp_path, token=TOKEN, llm_model='qwen3:30b', graph_schedule='off'))
    body = TestClient(app).get('/models', headers=bearer()).json()
    assert body == {'ok': True, 'error': '', 'models': ['gemma3:27b', 'qwen3:30b'], 'model': 'qwen3:30b', 'found': True}
