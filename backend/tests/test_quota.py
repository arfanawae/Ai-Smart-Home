"""The LLM usage counter is bookkeeping: a file it cannot write must never stop a turn."""

from nexus.quota import QuotaTracker


def test_unwritable_counter_file_is_not_fatal(tmp_path, caplog):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")  # a file where the folder should be: every save raises OSError
    quota = QuotaTracker(blocker / "llm_usage.json", 5)
    quota.record()
    quota.record()
    quota.mark_exhausted()
    assert quota.used == 2 and quota.exhausted and not quota.can_request()  # still counts in memory
    assert len([r for r in caplog.records if "cannot save" in r.message]) == 1  # warned once


def test_counter_survives_a_restart(tmp_path):
    QuotaTracker(tmp_path / "q.json", None).record()
    assert QuotaTracker(tmp_path / "q.json", None).used == 1
