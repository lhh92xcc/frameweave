"""Offline rules tests and synthetic FFmpeg fixtures; no external model output."""
import json
import subprocess

import httpx
import pytest

from studio.pipeline_rules import PipelineRules, default_document
from studio.providers import ProviderFailure
from studio.vidu_policy import select_vidu


def test_defaults_and_edit_reload_conflict(client, app, project):
    state = client.get('/api/settings/pipeline').json()
    assert state['rules'] == state['defaults']
    assert state['rules']['instructions'] == default_document()
    assert state['rules']['audio']['post_production']
    assert not state['rules']['audio']['preserve_vidu_audio']
    assert state['rules']['vidu']['poll_protocol'] == 'post_task_get'
    state['rules']['audio']['bgm_db'] = -12
    response = client.put('/api/settings/pipeline', json={'rules':state['rules'],'expected_revision':state['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['revision'] == 1
    assert app.state.workflow.settings.get().pipeline.audio.bgm_db == -12
    assert client.put('/api/settings/pipeline', json={'rules':state['rules'],'expected_revision':0}).status_code == 409
    invalid = response.json()['rules']
    invalid['vidu']['reference_max_seconds'] = 6
    assert client.put('/api/settings/pipeline', json={'rules':invalid,'expected_revision':1}).status_code == 422


def test_general_settings_cannot_overwrite_rules_or_leak_keys(client, app):
    config = app.state.workflow.settings.get()
    config.audio.api_key = 'offline-private-key'
    config.pipeline.render.crf = 18
    app.state.workflow.settings.save(config)
    public = client.get('/api/settings').json()
    assert 'offline-private-key' not in json.dumps(public)
    public.pop('pipeline')
    public.pop('pipeline_revision')
    for name in ['text','image','vision','video','audio']:
        for route in [public[name]] if name == 'audio' else public[name]:
            route.pop('configured')
    response = client.put('/api/settings', json=public)
    assert response.status_code == 200
    assert response.json()['pipeline']['render']['crf'] == 18
    assert app.state.workflow.settings.get().audio.api_key == 'offline-private-key'


def test_revision_blocks_old_task_and_audio_requires_video(client, app, project):
    from conftest import approve_with_plan

    p = approve_with_plan(client, project)
    w = app.state.workflow
    with pytest.raises(ValueError, match='先完成'):
        w.enqueue(p['id'], 'audio', 'ep1_s01')
    job = w.enqueue(p['id'], 'reference', 'lin')
    assert job['snapshot']['pipeline_rules']['render']['crf'] == 23
    config = w.settings.get()
    config.pipeline.render.crf = 21
    w.settings.save(config)
    with pytest.raises(ProviderFailure, match='制作规则已修改'):
        w.assert_current(job)


def test_configured_defaults_drive_dispatch():
    rules = PipelineRules().vidu.model_dump()
    rules.update(default_model='vidu-video-q2-pro', audio_model='vidu-video-q3-pro', bgm_model='vidu-video-q2', long_model='vidu-video-q3-pro')
    assert select_vidu({}, 5, rules=rules)['model'] == 'vidu-video-q2-pro'
    assert select_vidu({'audio': True}, 5, rules=rules)['model'] == 'vidu-video-q3-pro'
    assert select_vidu({'bgm': True}, 5, rules=rules)['model'] == 'vidu-video-q2'
    assert select_vidu({}, 12, rules=rules)['model'] == 'vidu-video-q3-pro'


@pytest.mark.asyncio
async def test_real_postproduction_ass_mix_and_danmaku(tmp_path, monkeypatch):
    from services import engine_bridge

    monkeypatch.setattr(engine_bridge, 'MEDIA', tmp_path)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=160x288:rate=24','-t','2','-c:v','libx264',str(tmp_path/'fixture.mp4')], check=True, timeout=30)
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1','-c:a','libmp3lame',str(tmp_path/'fixture.mp3')], check=True, timeout=30)
    rules = PipelineRules().model_dump()
    rules['render']['preset'] = 'ultrafast'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine_bridge.app), base_url='http://test') as client:
        response = await client.post('/render',json={
            'clips':[{'file':'fixture.mp4','duration':2,'subtitle':'离线测试字幕','audio':'fixture.mp3','speech_start':0.2,'speech_end':1.4}],
            'output':'art_123456.mp4','pipeline_rules':rules,'preserve_audio':False,
            'bgm':'fixture.mp3','sound_effects':[{'file':'fixture.mp3','time':1.5,'volume':0.5}],
            'danmaku':[{'start':0.1,'duration':1,'text':'离线弹幕 {不能注入标签}'},{'start':4,'text':'超时丢弃'}]
        })
    assert response.status_code == 200, response.text
    out = response.json()
    assert out['metadata']['audio_stream_count'] == 1
    assert out['metadata']['duration_seconds'] == pytest.approx(2, abs=0.15)
    assert any('丢弃' in s for s in out['metadata']['warnings'])
    assert any('截断' in s for s in out['metadata']['warnings'])
    sub = (tmp_path/out['subtitle_file']).read_text()
    assert '0:00:00.20,0:00:01.40' in sub and '离线测试字幕' in sub
    overlay = (tmp_path/out['metadata']['pipeline_files']['danmaku.ass']).read_text()
    assert r'\move(720' in overlay and '｛不能注入标签｝' in overlay
    assert '超时丢弃' not in overlay
    assert all((tmp_path/file).is_file() for file in out['metadata']['pipeline_files'].values())


def test_rule_save_pauses_active_automation_without_recasting(client, app, project):
    stored = app.state.store.project(project['id'])
    stored.update(automation=True, production_mode='full', audio_cast={'voices':{}, 'speakers':{}})
    app.state.store.save_project(stored)
    state = client.get('/api/settings/pipeline').json()
    state['rules']['subtitles']['font_size'] = 32
    response = client.put('/api/settings/pipeline', json={'rules':state['rules'],'expected_revision':state['revision']})
    assert response.status_code == 200
    current = app.state.store.project(project['id'])
    assert current['paused'] and current['audio_cast'] == stored['audio_cast']
    assert not app.state.store.jobs(project['id'])
