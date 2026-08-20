from __future__ import annotations

import pytest

import services.upscaler as upscaler


class _FinishedProcess:
    returncode = 0

    def poll(self):
        return self.returncode


class _HangingProcess:
    pid = 4242
    returncode = None

    def __init__(self):
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        return None


def test_wait_for_process_returns_exit_code_when_process_finishes():
    assert upscaler._wait_for_process(_FinishedProcess(), timeout=0) == 0


def test_wait_for_process_terminates_a_hung_process(monkeypatch):
    process = _HangingProcess()
    terminated = []
    monkeypatch.setattr(
        upscaler,
        "_terminate_process_tree",
        lambda proc: terminated.append(proc),
    )

    with pytest.raises(TimeoutError, match="exceeded its 0-second timeout"):
        upscaler._wait_for_process(process, timeout=0, poll_interval=0)

    assert terminated == [process]
