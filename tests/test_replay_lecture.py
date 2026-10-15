from tools.replay_lecture import is_interim_stop


def test_replay_does_not_finish_on_an_interim_stop_acknowledgement():
    assert is_interim_stop(
        {"status": "stopping", "awaiting_final_results": True, "segments": []}
    )
    assert not is_interim_stop(
        {"status": "stopping", "awaiting_final_results": False, "segments": []}
    )
    assert not is_interim_stop({"status": "success", "segments": []})
