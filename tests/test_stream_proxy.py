from __future__ import annotations

from app.dataplane.fireworks.stream_proxy import StreamUsageCollector


def test_stream_usage_collector_captures_response_id_from_created_event() -> None:
    collector = StreamUsageCollector()

    collector.feed(b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_stream_1","object":"response"}}\n\n')

    assert collector.response_id == "resp_stream_1"


def test_stream_usage_collector_captures_response_id_from_top_level_payload() -> None:
    collector = StreamUsageCollector()

    collector.feed(b'data: {"id":"resp_stream_2","object":"response","status":"completed"}\n\n')

    assert collector.response_id == "resp_stream_2"


def test_stream_usage_collector_keeps_first_response_id() -> None:
    collector = StreamUsageCollector()

    collector.feed(b'data: {"type":"response.created","response":{"id":"resp_first"}}\n\n')
    collector.feed(b'data: {"type":"response.completed","response":{"id":"resp_second"}}\n\n')

    assert collector.response_id == "resp_first"
