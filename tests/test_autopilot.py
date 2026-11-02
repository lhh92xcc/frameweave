"""Offline orchestration evidence only; these fixtures are not model output."""
import json

import pytest
from conftest import SOURCE, offline_visual_qc, plan_for
from PIL import Image

from studio.models import RoutingSettings
from studio.providers import ProviderFailure
from studio.reference_policy import ReferenceGuard
from studio.store import now


def full_project(client):
    r = client.post('/api/projects', json={
        'title': '离线全自动流程验证', 'source': SOURCE, 'episodes': 2, 'duration': 10,
        'production_mode': 'full', 'unlimited_budget': True,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def drive(w, p, maximum=70):
    for _ in range(maximum):
        p = w.store.project(p['id'])
        state = w.automation_state(p)
        if state['state'] == 'completed':
            return p
        await w.advance_safely(p)
        jobs = [j for j in reversed(w.store.jobs(p['id'])) if j['status'] == 'queued']
        if jobs:
            await w.execute(jobs[0])
        else:
            assert w.automation_state(w.store.project(p['id']))['state'] not in {'attention', 'error'}, w.project_view(w.store.project(p['id']))['automation_status']
    pytest.fail('Offline automatic production did not complete')


def install_offline(w, monkeypatch, *, fail_video=False, fail_image=0):
    counts = {'image': 0, 'video': 0, 'vision': 0, 'speech': 0, 'renders': 0}
    prompts = []
    async def generate(kind, job, prompt, refs=(), duration=5, aspect_ratio='9:16', output=None):
        prompts.append((kind, prompt, [p.name for p in refs]))
        assert job['snapshot']['contract']['markdown'] in prompt
        if kind == 'text':
            if job['stage'] == 'requirements':
                return {'text': '离线替身制作设定，测试用，不代表外部生成。' * 20}
            p = w.store.project(job['project_id'])
            plan = plan_for(p)
            if '不要逐镜展开' in prompt:
                return {'text': json.dumps({'assets': plan['assets'], 'episodes': [
                    {k: v for k, v in e.items() if k != 'shots'} for e in plan['episodes']
                ]}, ensure_ascii=False)}
            ep = plan['episodes'][1 if 'ep2' in prompt.split('本集：')[-1].split('素材库：')[0] else 0]
            return {'text': json.dumps({'shots': ep['shots']}, ensure_ascii=False)}
        if kind == 'image':
            counts['image'] += 1
            name = output + '.png'
            Image.new('RGB', (90, 160), (counts['image'] * 20 % 255, 0, 50)).save(w.store.media / name)
            return {'file': name, 'mime': 'image/png', 'metadata': {'aspect_matches': True}, 'route': {'provider': 'offline-fixture'}}
        if kind == 'video':
            counts['video'] += 1
            name = output + '.mp4'
            (w.store.media / name).write_bytes(b'offline-fixture-video')
            return {'file': name, 'mime': 'video/mp4', 'metadata': {'duration_seconds': 10}}
        counts['vision'] += 1
        failing = (job['stage'] == 'video' and fail_video) or (job['stage'] == 'reference' and counts['image'] <= fail_image)
        return {'text': offline_visual_qc(result='fail' if failing else 'pass', correction='保持同一母版修复脸部' if failing else '')}
    async def engine(path, payload):
        if path == '/video-frames':
            Image.new('RGB', (90, 160)).save(w.store.media / 'offline-frame.png')
            return {'frames': [{'file': 'offline-frame.png', 'seconds': 0}, {'file': 'offline-frame.png', 'seconds': 9.9}], 'coverage': 'every_decoded_frame', 'frame_count': 2}
        if path == '/speech':
            counts['speech'] += 1
            (w.store.media / payload['output']).write_bytes(b'offline-fixture-audio')
            return {'file': payload['output'], 'metadata': {'duration_seconds': 3}}
        if path == '/render':
            counts['renders'] += 1
            (w.store.media / payload['output']).write_bytes(b'offline-fixture-render')
            name = payload['output'].replace('.mp4', '.srt')
            (w.store.media / name).write_text('1\n00:00:00,000 --> 00:00:10,000\n一封信改变了她的旅途。\n')
            return {'file': payload['output'], 'subtitle_file': name, 'metadata': {'duration_seconds': 10}}
        if path == '/validate':
            return {'duration_seconds': 3 if payload.get('kind') == 'audio' else 10, 'audio_stream_count': 1}
        raise AssertionError(path)
    monkeypatch.setattr(w.providers, 'generate', generate)
    monkeypatch.setattr(w.providers, 'engine_request', engine)
    return counts, prompts


def test_full_mode_needs_explicit_project_spend_authorization(client, project):
    assert client.post('/api/projects', json={'title': '测试', 'source': SOURCE, 'production_mode': 'full'}).status_code == 422
    assert client.post(f"/api/projects/{project['id']}/automation", json={'mode': 'full'}).status_code == 422
    p = full_project(client)
    assert p['automation'] and p['production_mode'] == 'full'
    assert not client.get('/api/settings').json()['allow_paid']


@pytest.mark.asyncio
async def test_full_pipeline_finishes_all_episodes_without_any_manual_approval(app, client, monkeypatch):
    p = full_project(client)
    w = app.state.workflow
    counts, prompts = install_offline(w, monkeypatch)
    p = await drive(w, p)
    assert p['contract']['approved'] and p['plan_approved']
    assert counts['image'] == 5  # front, side, full body and two episode keyframes
    assert counts['video'] == counts['speech'] == counts['renders'] == 2
    assert w.automation_state(p) == {'state': 'completed'}
    assert all(a['review'] == 'approved' for a in w.store.artifacts(p['id']))
    assert set(p['reference_masters']) == {'lin'}
    master = p['reference_masters']['lin']
    for a in w.store.artifacts(p['id']):
        if a['kind'] == 'reference' and a['id'] != master:
            assert a['dependencies'] == [master]
        if a['kind'] == 'video':
            assert a['metadata']['video_qc']['passed']
            assert len(a['metadata']['video_qc']['frames']) == 2
        if a['kind'] == 'render':
            assert (w.store.media / a['subtitle_file']).is_file()
    assert all('新建定制音色' not in prompt for _, prompt, _ in prompts)
    assert client.get(f"/api/projects/{p['id']}/export.zip").status_code == 200


@pytest.mark.asyncio
async def test_failed_video_consistency_never_reaches_render(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    counts, _ = install_offline(w, monkeypatch, fail_video=True)
    for _ in range(24):
        p = w.store.project(p['id'])
        await w.advance_safely(p)
        queued = [j for j in reversed(w.store.jobs(p['id'])) if j['status'] == 'queued']
        if queued:
            await w.execute(queued[0])
    assert counts['video'] >= 1
    assert counts['renders'] == 0
    assert not [a for a in w.store.artifacts(p['id']) if a['kind'] in {'video', 'render'}]
    failed = next(j for j in w.store.jobs(p['id']) if j['stage'] == 'video' and j['status'] == 'blocked')
    assert failed['error']['code'] == 'VIDEO_QC_FAILED'
    assert failed['runtime']['video_correction']['checks']['identity']['result'] == 'fail'


@pytest.mark.asyncio
async def test_full_image_retries_beyond_bounded_batch_without_replacing_master(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    counts, prompts = install_offline(w, monkeypatch, fail_image=3)
    for _ in range(12):
        p = w.store.project(p['id'])
        await w.advance_safely(p)
        for j in w.store.jobs(p['id']):
            if j['status'] == 'blocked' and j['error']['code'] == 'IMAGE_QC_EXHAUSTED':
                j['finished_at'] = now() - 1000
                w.store.save_job(j)
        queued = [j for j in reversed(w.store.jobs(p['id'])) if j['status'] == 'queued']
        if queued:
            await w.execute(queued[0])
    p = await drive(w, p)
    assert counts['image'] == 8
    assert any('自动返工轮次 1' in prompt and '修复脸部' in prompt for kind, prompt, _ in prompts if kind == 'image')
    assert len([a for a in w.store.artifacts(p['id']) if a['kind'] == 'reference' and not a['dependencies']]) == 1


@pytest.mark.asyncio
async def test_disable_full_mode_stops_further_calls(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    counts, _ = install_offline(w, monkeypatch)
    job = w.enqueue(p['id'], 'requirements')
    client.post(f"/api/projects/{p['id']}/automation", json={'enabled': False})
    await w.execute(job)
    assert w.store.job(job['id'])['error']['code'] == 'AUTOMATION_DISABLED'
    assert not any(counts.values())


def test_full_unknown_submission_is_not_automatically_repeated(app, client):
    w = app.state.workflow
    p = full_project(client)
    job = w.enqueue(p['id'], 'requirements')
    job.update(status='blocked', finished_at=now() - 1000, error={'code': 'SUBMISSION_UNKNOWN', 'message': 'offline'})
    job['runtime']['cloud_submitting'] = True
    w.store.save_job(job)
    assert w.automation_state(w.store.project(p['id']))['state'] == 'attention'
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 422


def test_full_automatic_cast_keeps_recording_policy(app, client):
    w = app.state.workflow
    p = full_project(client)
    plan = plan_for(p)
    plan['assets'][0].update(name='橘猫', speech_source='recording')
    for ep in plan['episodes']:
        ep['shots'][0].update(speaker_id='lin', dialogue='喵', narration='')
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan, 0)
    w.approve_plan(p['id'], 1)
    p = w.automatic_cast(w.store.project(p['id']))
    assert p['audio_cast']['voices']['lin']['source'] == 'recording'
    with pytest.raises(ProviderFailure, match='缺少已确认的原音轨'):
        w.snapshot(p, 'audio', 'ep1_s01')


def test_full_reference_requirements_cover_every_asset_kind(app, client):
    w = app.state.workflow
    p = full_project(client)
    p['plan'] = plan_for(p)
    for kind in ['location', 'prop', 'product']:
        p['plan']['assets'].append({**p['plan']['assets'][0], 'id': kind, 'kind': kind})
    guard = ReferenceGuard(w, p)
    assert set(guard.policy.assets) == {'lin', 'location', 'prop', 'product'}
    assert guard.policy.assets['lin'].required_roles == ['front', 'side', 'full_body']
    assert guard.policy.assets['location'].required_roles == ['wide', 'reverse']
    assert guard.policy.assets['prop'].required_roles == ['general', 'detail']


@pytest.mark.asyncio
async def test_full_technical_audio_check_rejects_too_long_audio(app, client, monkeypatch):
    w = app.state.workflow
    settings = w.settings.get()
    settings.pipeline.audio.post_production = False
    settings.pipeline.render.timeline_overflow = "error"
    settings.audio_mode = "edge"
    w.settings.save(settings)
    p = full_project(client)
    plan = plan_for(p)
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan, 0)
    w.approve_plan(p['id'], 1)
    w.automatic_cast(w.store.project(p['id']))
    async def engine(path, payload):
        return {'file': 'offline.mp3', 'metadata': {'duration_seconds': 25}}
    monkeypatch.setattr(w.providers, 'engine_request', engine)
    job = w.enqueue(p['id'], 'audio', 'ep1_s01')
    await w.execute(job)
    assert w.store.job(job['id'])['error']['code'] == 'AUDIO_DURATION_INVALID'
    assert not w.store.artifacts(p['id'])


def test_full_snapshot_authorizes_project_only(app, client):
    w = app.state.workflow
    p = full_project(client)
    snapshot = w.snapshot(w.store.project(p['id']), 'requirements', '')
    assert snapshot['allow_paid'] and snapshot['unlimited_budget']
    assert snapshot['automation_authorization']['mode'] == 'full'
    assert not w.settings.get().allow_paid
    with pytest.raises(ProviderFailure):
        w.parse_auto_qc({'text': '{}'}, [])


@pytest.mark.asyncio
async def test_full_video_rework_checks_new_bytes_and_reuses_same_references(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    counts, prompts = install_offline(w, monkeypatch, fail_video=True)
    for _ in range(24):
        p = w.store.project(p['id'])
        await w.advance_safely(p)
        queued = [j for j in reversed(w.store.jobs(p['id'])) if j['status'] == 'queued']
        if queued:
            await w.execute(queued[0])
    jobs = [j for j in w.store.jobs(p['id']) if j['stage'] == 'video']
    original_refs = {j['entity_id']: j['snapshot']['dependencies'] for j in jobs}
    for j in jobs:
        j['finished_at'] = now() - 1000
        w.store.save_job(j)
    counts2, prompts2 = install_offline(w, monkeypatch)
    p = await drive(w, p)
    assert counts2['video'] == 2 and counts2['renders'] == 2
    assert all(j['snapshot']['dependencies'] == original_refs[j['entity_id']] for j in w.store.jobs(p['id']) if j['stage'] == 'video' and j['entity_id'] in original_refs)
    assert any('视频轮次 1' in prompt for kind, prompt, _ in prompts2 if kind == 'vision')
    assert counts['renders'] == 0


@pytest.mark.asyncio
async def test_full_pending_upload_is_checked_and_locked_without_human_review(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan_for(p), 0)
    w.approve_plan(p['id'], 1)
    import io
    image = io.BytesIO()
    Image.new('RGB', (90, 160), 'white').save(image, 'PNG')
    uploaded = client.post(f"/api/projects/{p['id']}/upload", data={
        'stage': 'reference', 'entity_id': 'lin', 'reference_role': 'front',
    }, files={'file': ('offline.png', image.getvalue(), 'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()['review'] == 'pending'
    counts, _ = install_offline(w, monkeypatch)
    p = await drive(w, p)
    assert p['reference_masters']['lin'] == uploaded.json()['id']
    assert counts['image'] == 4  # use uploaded master, generate only derivative views and keyframes
    assert w.store.artifact(uploaded.json()['id'])['review_method'] == 'automatic_visual_qc'


@pytest.mark.asyncio
async def test_full_missing_audio_and_changed_reference_bytes_stop_quality(app, client, monkeypatch):
    from test_workflow import add_artifact
    w = app.state.workflow
    p = full_project(client)
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan_for(p), 0)
    w.approve_plan(p['id'], 1)
    p = w.store.project(p['id'])
    artifact = add_artifact(w, p, 'reference', 'lin')
    artifact.setdefault('metadata', {})['reference_role'] = 'front'
    w.store.save_artifact(artifact)
    job = w.enqueue_quality(p, artifact)
    (w.store.media / artifact['file']).write_bytes(b'changed offline fixture')
    install_offline(w, monkeypatch)
    await w.execute(job)
    assert w.store.job(job['id'])['error']['code'] == 'STALE_DEPENDENCY'
    assert w.store.artifact(artifact['id'])['review'] == 'pending'


@pytest.mark.asyncio
async def test_full_plan_edit_rechecks_original_bytes_and_keeps_voices(app, client, monkeypatch):
    from studio.archive_import import sha256
    w = app.state.workflow
    p = full_project(client)
    install_offline(w, monkeypatch)
    p = await drive(w, p)
    original = w.store.artifact(p['reference_masters']['lin'])
    original_sha = sha256(w.store.media / original['file'])
    voices = p['audio_cast']['voices']
    plan = p['plan']
    plan['episodes'][0]['shots'][0]['description'] += '，镜头轻推。'
    edited = w.edit_plan(p['id'], plan, p['plan_revision'])
    assert edited['audio_cast']['voices'] == voices
    counts, _ = install_offline(w, monkeypatch)
    # New deterministic image colours are offset from the old original.
    counts['image'] = 5
    p = await drive(w, p)
    current = w.store.artifact(p['reference_masters']['lin'])
    assert current['id'] != original['id']
    assert sha256(w.store.media / current['file']) == original_sha
    assert current['metadata']['revalidated_from']['artifact_id'] == original['id']
    assert p['audio_cast']['voices'] == voices


@pytest.mark.asyncio
async def test_full_recording_reuses_original_audio_with_snapshot_and_no_tts(app, client, monkeypatch):
    from test_workflow import add_artifact

    from studio.store import uid
    w = app.state.workflow
    settings = w.settings.get()
    settings.pipeline.audio.post_production = False  # isolate recording reuse, ordering tested separately
    w.settings.save(settings)
    p = full_project(client)
    plan = plan_for(p)
    plan['assets'][0].update(name='橘猫', speech_source='recording')
    for ep in plan['episodes']:
        ep['shots'][0].update(speaker_id='lin', dialogue='喵', narration='')
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan, 0)
    w.approve_plan(p['id'], 1)
    p = w.store.project(p['id'])
    recording = add_artifact(w, p, 'audio', 'ep1_s01', review='approved')
    filename = uid('art') + '.mp3'
    raw = b'offline-only-existing-cat-recording'
    (w.store.media / filename).write_bytes(raw)
    recording.update(file=filename, mime='audio/mpeg', metadata={'duration_seconds': 3})
    w.store.save_artifact(recording)
    w.set_audio_cast(p['id'], {'lin': {'voice_id': 'existing', 'source': 'recording', 'recording_artifact_id': recording['id']}},
                     {'ep1_s01': 'lin', 'ep2_s01': 'lin'}, p['version'])
    calls = []
    async def engine(path, payload):
        calls.append(path)
        assert path == '/validate'
        return {'duration_seconds': 3}
    monkeypatch.setattr(w.providers, 'engine_request', engine)
    job = w.enqueue(p['id'], 'audio', 'ep2_s01')
    assert job['snapshot']['dependencies'] == [recording['id']]
    assert job['snapshot']['audio_source_sha256']
    await w.execute(job)
    saved = w.store.job(job['id'])
    assert saved['status'] == 'succeeded', saved.get('error')
    output = w.store.artifact(saved['artifact_id'])
    assert output['review'] == 'approved'
    assert (w.store.media / output['file']).read_bytes() == raw
    assert calls == ['/validate']


@pytest.mark.asyncio
async def test_full_paid_route_without_image_estimate_uses_project_authorization_only(app, client, monkeypatch):
    import httpx
    w = app.state.workflow
    p = full_project(client)
    w.settings.save(RoutingSettings.model_validate({'vision': [{'transport': 'cloud', 'provider': 'cloud', 'model': 'vision',
        'base_url': 'https://offline.example/v1', 'api_key': 'offline-secret'}]}))
    job = w.enqueue(p['id'], 'requirements')
    job['stage'] = 'reference'  # exercise the provider cost guard, no production output
    calls = []
    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'offline fixture'}}]})
    monkeypatch.setattr(w.providers.client, '_transport', httpx.MockTransport(transport))
    result = await w.providers.generate('vision', job, 'offline-only authorization test')
    assert result['text'] == 'offline fixture' and len(calls) == 1
    assert not w.settings.get().allow_paid
    assert not job['runtime'].get('qc_cost_reservations')


@pytest.mark.asyncio
async def test_full_same_episode_binds_previous_delivered_video_tail(app, client, monkeypatch):
    w = app.state.workflow
    p = full_project(client)
    plan = plan_for(p)
    shot = plan['episodes'][0]['shots'][0]
    shot['duration'] = 5
    plan['episodes'][0]['shots'].append({**shot, 'id': 'ep1_s02'})
    w.approve_contract(p['id'], p['contract']['hash'])
    w.edit_plan(p['id'], plan, 0)
    w.approve_plan(p['id'], 1)
    counts, prompts = install_offline(w, monkeypatch)
    p = await drive(w, p)
    previous = w.latest(p, 'video', 'ep1_s01')
    following = w.latest(p, 'video', 'ep1_s02')
    assert previous['id'] in following['dependencies']
    assert counts['video'] == 3
    assert any('上一镜实际交付尾帧' in prompt and 'offline-frame.png' in refs for kind, prompt, refs in prompts if kind == 'video')
    # Reviewing an existing clip must use the same contact sheets and delivered tail.
    following['review'] = 'pending'
    following['metadata'].pop('video_qc')
    w.store.save_artifact(following)
    check = w.enqueue_quality(p, following)
    await w.execute(check)
    check = w.store.job(check['id'])
    assert check['status'] == 'succeeded', check.get('error')
    assert any(len(entry['source_artifact_ids']) == 3 for entry in check['runtime']['reference_uploads'])
    check_refs = [refs for kind, _, refs in prompts if kind == 'vision'][-1]
    assert 'offline-frame.png' in check_refs and not any(name.endswith('.mp4') for name in check_refs)


@pytest.mark.asyncio
@pytest.mark.parametrize('problem,code', [
    ('audio', 'RENDER_AUDIO_MISSING'), ('subtitles', 'SUBTITLES_MISSING'),
    ('text', 'SUBTITLES_INCOMPLETE'),
])
async def test_full_existing_render_cannot_bypass_audio_and_subtitle_checks(app, client, monkeypatch, problem, code):
    w = app.state.workflow
    p = full_project(client)
    install_offline(w, monkeypatch)
    p = await drive(w, p)
    artifact = w.latest(p, 'render', 'ep1')
    artifact['review'] = 'pending'
    if problem == 'subtitles':
        artifact.pop('subtitle_file')
    elif problem == 'text':
        (w.store.media / artifact['subtitle_file']).write_text('offline incomplete subtitle')
    w.store.save_artifact(artifact)
    original_engine = w.providers.engine_request
    async def engine(path, payload):
        if problem == 'audio' and path == '/validate':
            return {'duration_seconds': 10, 'audio_stream_count': 0}
        return await original_engine(path, payload)
    monkeypatch.setattr(w.providers, 'engine_request', engine)
    check = w.enqueue_quality(p, artifact)
    await w.execute(check)
    assert w.store.job(check['id'])['error']['code'] == code
    assert w.store.artifact(artifact['id'])['review'] == 'pending'


@pytest.mark.asyncio
async def test_full_provider_checks_pause_before_request(app, client, monkeypatch):
    import httpx
    w = app.state.workflow
    p = full_project(client)
    w.settings.save(RoutingSettings.model_validate({'text': [{'transport': 'cloud', 'provider': 'cloud', 'model': 'text',
        'base_url': 'https://offline.example/v1', 'api_key': 'offline-secret'}]}))
    job = w.enqueue(p['id'], 'requirements')
    client.post(f"/api/projects/{p['id']}/pause")
    called = []
    def transport(request):
        called.append(True)
        return httpx.Response(500)
    monkeypatch.setattr(w.providers.client, '_transport', httpx.MockTransport(transport))
    with pytest.raises(ProviderFailure, match='暂停或关闭'):
        await w.providers.generate('text', job, 'offline pause verification')
    assert not called


@pytest.mark.asyncio
async def test_full_mode_checks_existing_general_reference_after_migration(app, client, project, monkeypatch):
    from conftest import approve_with_plan
    from test_workflow import add_artifact
    w = app.state.workflow
    p = approve_with_plan(client, project)
    original = add_artifact(w, p, 'reference', 'lin')
    response = client.post(f"/api/projects/{p['id']}/automation", json={'enabled': True, 'mode': 'full', 'unlimited_budget': True})
    assert response.status_code == 200
    p = w.store.project(p['id'])
    guard = ReferenceGuard(w, p)
    assert guard.current(original)
    install_offline(w, monkeypatch)
    await w.advance_safely(p)
    job = next(j for j in w.store.jobs(p['id']) if j['stage'] == 'quality')
    await w.execute(job)
    assert w.store.artifact(original['id'])['review'] == 'approved'
