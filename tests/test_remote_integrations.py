"""Offline HTTP fixtures only: no external generation or billing evidence."""
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from studio.models import ModerationSettings, Route, RoutingSettings
from studio.providers import ProviderFailure
from studio.settings import SettingsStore
from studio.tencent_moderation import check
from studio.volcengine_tts import synthesize


def test_private_settings_and_official_origins(tmp_path):
    store = SettingsStore(tmp_path)
    config = RoutingSettings(moderation=ModerationSettings(secret_id="offline-id", secret_key="offline-secret"))
    public = store.save(config)
    assert "offline-secret" not in json.dumps(public)
    assert "offline-id" not in json.dumps(public)
    store.save(RoutingSettings.model_validate(public | {k: ([{a:b for a,b in r.items() if a != 'configured'} for r in public[k]] if k != 'audio' else {a:b for a,b in public[k].items() if a != 'configured'}) for k in ['text','image','vision','video','audio']}))
    assert store.get().moderation.secret_key == "offline-secret"
    with pytest.raises(ValueError):
        RoutingSettings(text=[Route(provider="volcengine-ark", transport="cloud", base_url="https://wrong.example")])


@pytest.mark.asyncio
async def test_tts_stream_and_voice_policy():
    calls = []
    def handle(req):
        calls.append(req)
        assert str(req.url) == 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
        assert req.headers['x-api-key'] == 'offline-key'
        assert 'authorization' not in req.headers
        return httpx.Response(200, content=(json.dumps({'code': 0, 'data': base64.b64encode(b'offline audio fixture').decode()})+'\n'+json.dumps({'code':20000000})).encode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await synthesize(client, 'offline-key', '台词', 'zh_female_vv_uranus_bigtts') == b'offline audio fixture'
        with pytest.raises(ProviderFailure, match='系统音色'):
            await synthesize(client, 'offline-key', '台词', 'unknown-custom')
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('suggestion', ['Pass', 'Review', 'Block'])
async def test_moderation_fail_closed_and_cached(suggestion):
    calls = []
    def handle(req):
        calls.append(req)
        assert req.url.host == 'tms.tencentcloudapi.com'
        assert req.headers['x-tc-action'] == 'TextModeration'
        return httpx.Response(200, json={'Response':{'Suggestion':suggestion}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        config = RoutingSettings(moderation=ModerationSettings(enabled=True, secret_id='offline-id', secret_key='offline-key'))
        p = SimpleNamespace(client=client, settings=SimpleNamespace(get=lambda:config), checkpoint=lambda j, **v:j['runtime'].update(v))
        job = {'runtime':{}}
        if suggestion == 'Pass':
            await check(p, job, 'text', '审核文本')
            await check(p, job, 'text', '审核文本')
            assert len(calls) == 1
        else:
            with pytest.raises(ProviderFailure):
                await check(p, job, 'text', '审核文本')
        assert 'offline-key' not in json.dumps(job)


@pytest.mark.asyncio
async def test_video_moderation_resume(tmp_path, monkeypatch):
    import studio.cloud_storage
    calls = []
    class Cloud:
        def __init__(self, root):
            pass
        def upload(self, path, mime):
            return {'key':'offline-fixture'}
        def signed_url(self, ref):
            return 'https://offline.cos.example/video.mp4'
    monkeypatch.setattr(studio.cloud_storage, 'CosStorage', Cloud)
    def handle(req):
        calls.append(req.headers['x-tc-action'])
        assert req.url.host == 'vm.tencentcloudapi.com'
        return httpx.Response(200, json={'Response': {'Results':[{'TaskId':'offline-task'}]} if calls[-1] == 'CreateVideoModerationTask' else {'Status':'FINISH','Suggestion':'Pass'}})
    config = RoutingSettings(moderation=ModerationSettings(enabled=True, secret_id='offline-id', secret_key='offline-key'))
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'offline fixture, not a generated video')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        p = SimpleNamespace(client=client, store=SimpleNamespace(root=tmp_path), settings=SimpleNamespace(get=lambda:config), checkpoint=lambda j, **v:j['runtime'].update(v))
        job = {'runtime':{}}
        await check(p, job, 'video', path)
        await check(p, job, 'video', path)
        assert calls == ['CreateVideoModerationTask', 'DescribeTaskDetail']
        assert 'offline-task' in json.dumps(job)


@pytest.mark.asyncio
async def test_ark_official_chat_payload_and_receipt(app, monkeypatch):
    # Adapter-level fixture, with checkpoint persistence substituted; no remote model output.
    p = app.state.providers if hasattr(app.state, 'providers') else app.state.workflow.providers
    config = Route(transport='cloud', provider='volcengine-ark', model='offline-endpoint', base_url='https://ark.cn-beijing.volces.com/api/v3', api_key='offline-ark')
    calls = []
    def handle(req):
        calls.append(req)
        assert str(req.url) == config.base_url + '/chat/completions'
        assert req.headers['authorization'] == 'Bearer offline-ark'
        assert json.loads(req.content)['messages'][0]['content'] == '完整规范与参考绑定'
        return httpx.Response(200, json={'choices':[{'message':{'content':'离线模拟台词'}}]})
    monkeypatch.setattr(p, 'checkpoint', lambda j, **v:j['runtime'].update(v))
    job = {'id':'offline-ark-test', 'runtime':{'active_call':'text'}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(p, 'client', client)
        for _ in range(2):
            assert (await p.cloud_generate('text', job, config, '完整规范与参考绑定', [], '9:16'))['text'] == '离线模拟台词'
    assert len(calls) == 1
