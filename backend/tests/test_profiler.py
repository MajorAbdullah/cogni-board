import asyncio

import cache
import profiler
from schemas import DatasetProfile


class _FakeClient:
    def __init__(self, batch_result):
        self._batch_result = batch_result

    async def query_batch(self, dataset_id, probes, top_k):
        return self._batch_result


def test_profile_dataset_reports_honestly_when_no_chunks_retrieved(monkeypatch):
    monkeypatch.setattr(cache, "get_profile", lambda dataset_id: None)
    monkeypatch.setattr(cache, "set_profile", lambda dataset_id, profile: None)

    async def fail_if_called(system, user, model_cls, **kwargs):
        raise AssertionError("chat_json should not be called when zero chunks were retrieved")

    monkeypatch.setattr(profiler, "chat_json", fail_if_called)

    client = _FakeClient({"results": [{"results": []}, {"results": []}]})
    dataset = {"id": 7679, "name": "Advanced Prompt Engineering Techniques", "knowledge_source_count": 2}

    profile = asyncio.run(profiler.profile_dataset(client, dataset))

    assert isinstance(profile, DatasetProfile)
    assert profile.suggested_queries == []
    assert "no indexed content" in profile.summary.lower()


def test_profile_dataset_calls_llm_when_chunks_exist(monkeypatch):
    monkeypatch.setattr(cache, "get_profile", lambda dataset_id: None)
    monkeypatch.setattr(cache, "set_profile", lambda dataset_id, profile: None)

    calls = []

    async def fake_chat_json(system, user, model_cls, **kwargs):
        calls.append(user)
        return DatasetProfile(summary="real profile", suggested_queries=["a real query"])

    monkeypatch.setattr(profiler, "chat_json", fake_chat_json)

    client = _FakeClient({"results": [{"results": [{"text": "some real passage"}]}]})
    dataset = {"id": 1, "name": "ds", "knowledge_source_count": 5}

    profile = asyncio.run(profiler.profile_dataset(client, dataset))

    assert len(calls) == 1
    assert profile.summary == "real profile"
    assert profile.suggested_queries == ["a real query"]
