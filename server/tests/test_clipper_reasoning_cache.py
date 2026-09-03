"""A reasoning checkpoint is reusable only for the inputs that produced it."""

from __future__ import annotations

import json

import pytest

from services.clipper import reasoning_cache as cache


def _inputs(*, service: str = "atoms_v1", model=None, upstream=None):
    base = cache.base_inputs(
        {"duration": 100.0, "filesize": 1234},
        {"language": "en", "segments": [{"start": 0, "end": 1,
                                            "text": "hello"}]},
    )
    return cache.artifact_inputs(
        base,
        service_version=service,
        model=model,
        upstream=upstream or {"signals": {"rms": [0.1]}},
    )


def test_envelope_round_trips_an_empty_answer():
    inputs = _inputs()
    blob = cache.envelope("atoms", inputs, [])

    read = cache.inspect(blob, "atoms", inputs)

    assert read.hit is True
    assert read.data == []
    assert read.reason == cache.HIT


def test_a_real_json_round_trip_does_not_invalidate_tuple_inputs():
    """The segment-type ranges are tuples in memory and arrays on disk.
    Comparing Python containers instead of their canonical JSON identity made
    every apparently valid segment_types cache miss on its second run."""
    inputs = _inputs()
    inputs["parameters"]["ranges"] = [(0.0, 100.0)]
    blob = json.loads(json.dumps(cache.envelope("segment_types", inputs, [])))

    assert cache.inspect(blob, "segment_types", inputs).hit is True


def test_a_legacy_bare_artifact_is_a_miss_not_a_cache_hit():
    read = cache.inspect([{"i": 0}], "atoms", _inputs())

    assert read.hit is False
    assert read.reason == cache.LEGACY_OR_MALFORMED


def test_an_envelope_for_another_artifact_is_not_reused():
    inputs = _inputs()
    blob = cache.envelope("threads", inputs, [])

    read = cache.inspect(blob, "atoms", inputs)

    assert read.hit is False
    assert read.reason == cache.WRONG_ARTIFACT


def test_a_changed_input_fingerprint_invalidates_only_that_identity():
    inputs = _inputs(upstream={"signals": {"rms": [0.1]}})
    changed = _inputs(upstream={"signals": {"rms": [0.2]}})
    blob = cache.envelope("atoms", inputs, [{"i": 0}])

    assert cache.inspect(blob, "atoms", inputs).hit is True
    read = cache.inspect(blob, "atoms", changed)
    assert read.hit is False
    assert read.reason == cache.INPUT_CHANGED


def test_tampering_with_payload_is_not_a_cache_hit():
    inputs = _inputs()
    blob = cache.envelope("atoms", inputs, [{"i": 0}])
    blob["data"].append({"i": 1})

    read = cache.inspect(blob, "atoms", inputs)

    assert read.hit is False
    assert read.reason == cache.DATA_CHANGED


def test_tampering_with_declared_inputs_is_not_hidden_by_the_old_digest():
    inputs = _inputs()
    blob = cache.envelope("atoms", inputs, [])
    blob["inputs"]["service_version"] = "atoms_v99"

    read = cache.inspect(blob, "atoms", inputs)

    assert read.hit is False
    assert read.reason == cache.MALFORMED_ENVELOPE


def test_fingerprints_do_not_depend_on_dictionary_insertion_order():
    assert cache.fingerprint({"a": 1, "b": 2}) == cache.fingerprint(
        {"b": 2, "a": 1})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1, 2}])
def test_a_fingerprint_refuses_values_that_are_not_strict_json(value):
    with pytest.raises((TypeError, ValueError)):
        cache.fingerprint({"value": value})


def test_source_and_transcript_have_separate_fingerprints():
    one = cache.base_inputs({"filesize": 10}, {"segments": [{"text": "a"}]})
    source_changed = cache.base_inputs(
        {"filesize": 11}, {"segments": [{"text": "a"}]})
    transcript_changed = cache.base_inputs(
        {"filesize": 10}, {"segments": [{"text": "b"}]})

    assert one["source_fingerprint"] != source_changed["source_fingerprint"]
    assert one["transcript_fingerprint"] != transcript_changed["transcript_fingerprint"]


def test_base_identity_uses_the_source_not_derived_analysis_settings(monkeypatch):
    from workers import clipper_cache as worker_cache

    meta = {
        "analysis_version": "1",
        "source": {"duration": 10.0, "filesize": 100},
        "proxy": {"width": 480},
        "frames_sampled": 10,
    }
    monkeypatch.setattr(worker_cache.storage, "read_artifact",
                        lambda _pid, _name: meta)
    transcript = {"segments": [{"text": "same"}]}
    original = worker_cache._base_stamp("p", transcript)

    meta["proxy"] = {"width": 720}
    meta["frames_sampled"] = 99
    derived_changed = worker_cache._base_stamp("p", transcript)
    meta["source"] = {"duration": 10.0, "filesize": 101}
    source_changed = worker_cache._base_stamp("p", transcript)

    assert original == derived_changed
    assert original["source_fingerprint"] != source_changed["source_fingerprint"]


def test_model_change_does_not_change_a_deterministic_artifact_identity():
    """Targeted invalidation is expressed by each artifact's own inputs.

    A judge model belongs only to the judge identity. It must not be smuggled
    into the common base and invalidate atoms or threads.
    """
    base = cache.base_inputs({"filesize": 10}, {"segments": []})
    atoms_a = cache.artifact_inputs(base, service_version="atoms_v1",
                                     upstream={"signals": {}})
    atoms_b = cache.artifact_inputs(base, service_version="atoms_v1",
                                     upstream={"signals": {}})
    judge_a = cache.artifact_inputs(base, service_version="judge_v1",
                                     model="model-a", upstream={"pool": []})
    judge_b = cache.artifact_inputs(base, service_version="judge_v1",
                                     model="model-b", upstream={"pool": []})

    assert atoms_a == atoms_b
    assert judge_a != judge_b


def test_self_validated_payload_can_be_read_by_an_offline_audit():
    inputs = _inputs()
    blob = cache.envelope("atoms", inputs, [{"i": 0}])

    read = cache.inspect(blob, "atoms")

    assert read.hit is True and read.data == [{"i": 0}]


def test_transcript_change_invalidates_every_reasoning_identity():
    from workers import clipper_cache as worker_cache

    source = {"filesize": 10}
    old = cache.base_inputs(source, {"segments": [{"text": "old"}]})
    new = cache.base_inputs(source, {"segments": [{"text": "new"}]})
    atoms = [{"i": 0}]
    threads = [{"id": "thread-0"}]

    old_stamps = (
        worker_cache._atoms_stamp(old, {}),
        worker_cache._promises_stamp(old, 100.0),
        worker_cache._threads_stamp(old, atoms),
        worker_cache._episodes_stamp(old, atoms, threads),
        worker_cache._anchor_stamp({}, 100.0, base=old, atoms=atoms,
                                   threads=threads),
    )
    new_stamps = (
        worker_cache._atoms_stamp(new, {}),
        worker_cache._promises_stamp(new, 100.0),
        worker_cache._threads_stamp(new, atoms),
        worker_cache._episodes_stamp(new, atoms, threads),
        worker_cache._anchor_stamp({}, 100.0, base=new, atoms=atoms,
                                   threads=threads),
    )

    assert all(a != b for a, b in zip(old_stamps, new_stamps))


def test_signal_change_does_not_invalidate_the_model_only_promises_pass():
    from workers import clipper_cache as worker_cache

    base = cache.base_inputs({"filesize": 10}, {"segments": []})
    promises_a = worker_cache._promises_stamp(base, 100.0)
    promises_b = worker_cache._promises_stamp(base, 100.0)
    atoms_a = worker_cache._atoms_stamp(base, {"rms": [0.1]})
    atoms_b = worker_cache._atoms_stamp(base, {"rms": [0.2]})

    assert promises_a == promises_b
    assert atoms_a != atoms_b


def test_a_provider_default_model_is_part_of_the_llm_identity(monkeypatch):
    from services import descriptions
    from workers import clipper_cache as worker_cache

    base = cache.base_inputs({"filesize": 10}, {"segments": []})
    before = worker_cache._promises_stamp(base, 100.0)
    monkeypatch.setattr(descriptions, "DEFAULT_OPENAI_MODEL", "new-model")
    after = worker_cache._promises_stamp(base, 100.0)

    assert before != after
    assert before["model"]["resolved_by_engine"]["openai"] != "new-model"
    assert after["model"]["resolved_by_engine"]["openai"] == "new-model"


def test_an_empty_model_name_resolves_like_the_provider_client():
    from services import descriptions
    from workers import clipper_cache as worker_cache

    identity = worker_cache._model_inputs(("openai",), model="")

    assert identity["model"] == {
        "requested": None,
        "resolved_by_engine": {"openai": descriptions.DEFAULT_OPENAI_MODEL},
    }


def test_execution_settings_that_can_change_provider_output_are_in_identity(
        monkeypatch):
    from services.clipper import llm_engine
    from workers import clipper_cache as worker_cache

    base = cache.base_inputs({"filesize": 10}, {"segments": []})
    original = worker_cache._promises_stamp(base, 100.0, timeout=180.0)
    changed_timeout = worker_cache._promises_stamp(base, 100.0, timeout=30.0)
    monkeypatch.setattr(llm_engine, "NUM_CTX", llm_engine.NUM_CTX * 2)
    changed_context = worker_cache._promises_stamp(base, 100.0,
                                                    timeout=180.0)

    assert original != changed_timeout
    assert original != changed_context
    assert original["parameters"]["timeout_s"] == 180.0
    assert original["parameters"]["num_ctx"] == 32768


def test_anchor_identity_changes_when_any_rendered_upstream_changes():
    from workers import clipper_cache as worker_cache

    base = cache.base_inputs({"filesize": 10}, {"segments": []})
    common = {"base": base, "atoms": [{"i": 0}],
              "threads": [{"id": "t"}], "episodes": [],
              "promises": []}
    original = worker_cache._anchor_stamp({}, 100.0, **common)

    for name, changed in (
        ("atoms", [{"i": 1}]),
        ("threads", [{"id": "other"}]),
        ("episodes", [{"start": 0, "end": 10}]),
        ("promises", [{"t": 1, "text": "later"}]),
    ):
        args = dict(common)
        args[name] = changed
        assert worker_cache._anchor_stamp({}, 100.0, **args) != original


def test_segment_types_reuse_only_the_same_analysis_inputs(tmp_path, monkeypatch):
    from services.clipper import segment_type
    from workers import clipper_cache as worker_cache

    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "000.jpg").write_bytes(b"frame")
    disk = {
        "faces": {"times": [0.0]},
        "signals": {"motion": {"hop_s": 1.0, "motion": [0.1]}},
        # A pre-S7 list exists but has no identity and must not be trusted.
        "segment_types": [{"content_type": "stale"}],
    }
    writes = []
    calls = []

    monkeypatch.setattr(worker_cache.storage, "paths",
                        lambda _pid: {"frames_dir": frames})
    monkeypatch.setattr(worker_cache.storage, "read_artifact",
                        lambda _pid, name: disk.get(name))

    def write(_pid, name, data):
        writes.append(name)
        disk[name] = data

    monkeypatch.setattr(worker_cache.storage, "write_artifact", write)
    monkeypatch.setattr(segment_type, "classify_ranges",
                        lambda *_a, **_k: calls.append(1) or [{
                            "start": 0.0, "end": 100.0,
                            "content_type": "talking_head"}])
    base = cache.base_inputs({"filesize": 10}, {"segments": []})

    first = worker_cache._segment_types("p", 100.0, {"segments": []},
                                        base=base)
    second = worker_cache._segment_types("p", 100.0, {"segments": []},
                                         base=base)
    disk["signals"] = {"motion": {"hop_s": 1.0, "motion": [0.9]}}
    third = worker_cache._segment_types("p", 100.0, {"segments": []},
                                        base=base)

    assert first == second == third
    assert calls == [1, 1], "legacy miss, then one hit, then targeted invalidation"
    assert writes == ["segment_types", "segment_types"]
