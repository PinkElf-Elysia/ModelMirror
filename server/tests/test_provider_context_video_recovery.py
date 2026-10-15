"""A saved upstream video ID permits GET only with its original host source."""
import httpx
import pytest

from server.tests.test_provider_multimodal_r8e import (
    _activated_video_generation_service, _runtime_video_jobs, MODEL_ID,
)
from server.multimodal.stt import MultimodalServiceError


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['origin', 'context', 'call_link', 'run_link', 'connection'])
async def test_video_poll_cannot_reconstruct_or_swap_source(tmp_path, monkeypatch, damage):
    service, _ = await _activated_video_generation_service(tmp_path, monkeypatch)
    requests = []
    def handler(request):
        requests.append(request.method)
        assert request.method == 'POST'
        return httpx.Response(202, json={'id': 'runtime-video-context', 'status': 'pending', 'model': MODEL_ID})
    jobs = _runtime_video_jobs(service, httpx.MockTransport(handler))
    try:
        result = await jobs.create(model_id=MODEL_ID, prompt='synthetic clip', idempotency_key='context-video')
        with service.repository._connect() as db:
            if damage == 'origin':
                db.execute('DELETE FROM provider_workload_run_origins')
            elif damage == 'context':
                db.execute('DELETE FROM provider_call_contexts')
            elif damage == 'call_link':
                db.execute("UPDATE video_jobs SET workload_call_id='wrong-call'")
            elif damage == 'connection':
                db.execute("UPDATE video_jobs SET connection_id=NULL")
            else:
                db.execute("UPDATE video_jobs SET workload_run_id='wrong-run'")
        with pytest.raises(MultimodalServiceError, match='连接已不可用'):
            await jobs.refresh(result.job_id)
        assert requests == ['POST']
    finally:
        service.repository.close()
