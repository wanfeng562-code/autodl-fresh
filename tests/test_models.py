from datetime import datetime

import pytest

from autodl_fresh.browser import reset_verified
from autodl_fresh.models import DAY, SHANGHAI, Instance, parse_countdown, schedule_due
from autodl_fresh.storage import Settings, Store


@pytest.mark.parametrize("text, expected", [
    ("2天04小时34分后释放", 2 * DAY + 4 * 3600 + 34 * 60),
    ("13天09小时40分后\n释放\n设置定时关机", 13 * DAY + 9 * 3600 + 40 * 60),
    ("3小时12分钟5秒后释放", 3 * 3600 + 12 * 60 + 5),
    ("关机15天后释放", None),
    ("关机7天后释放", None),
    ("到期15天后释放", None),
    ("2026-11-05 18:00:00", None),
    ("即将释放", None),
    ("未知", None),
])
def test_countdown(text, expected):
    assert parse_countdown(text) == expected


def instance(**kwargs):
    defaults = dict(uuid="abcdef12-12345678", status="已关机", charge="按量计费", remaining_seconds=2 * DAY)
    return Instance(**(defaults | kwargs))


def test_threshold_is_strict_and_running_instances_are_skipped():
    assert instance().eligible(3)
    assert not instance(remaining_seconds=3 * DAY).eligible(3)
    assert not instance(remaining_seconds=0).eligible(3)
    assert not instance(remaining_seconds=None).eligible(3)
    assert not instance(status="运行中").eligible(3)
    assert not instance(charge="包年包月").eligible(3)
    assert not instance(blocked_reason="主机下架").eligible(3)


def test_verification_requires_both_shutdown_and_a_full_reset():
    assert reset_verified(instance(remaining_seconds=15 * DAY - 60), 15, 2 * DAY)
    assert not reset_verified(instance(status="运行中", remaining_seconds=15 * DAY), 15, 2 * DAY)
    assert not reset_verified(instance(remaining_seconds=3 * DAY), 15, 2 * DAY)
    assert not reset_verified(instance(remaining_seconds=None), 15, 2 * DAY)
    assert reset_verified(instance(remaining_seconds=7 * DAY - 60), 7, DAY)


def test_daily_scheduler_catchup_and_no_duplicate_attempts():
    settings = Settings(schedule_enabled=True)
    current = datetime(2026, 10, 27, 10, 0, tzinfo=SHANGHAI)
    assert schedule_due(settings, "", current)
    assert schedule_due(settings, "2026-10-26T10:00:00+08:00", current)
    assert not schedule_due(settings, "2026-10-27T09:01:00+08:00", current)
    assert not schedule_due(settings, "", current.replace(hour=8))
    settings.schedule_enabled = False
    assert not schedule_due(settings, "", current)


def test_atomic_settings_and_pending_journal(tmp_path):
    store = Store(tmp_path)
    store.save_settings(Settings(threshold_days=2, daily_time="10:30"))
    assert store.settings().threshold_days == 2
    store.set_pending({"uuid": "abcdef12-12345678", "phase": "accepted"})
    assert Store(tmp_path).pending()["phase"] == "accepted"
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_journal_does_not_silently_disappear(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "pending.json").write_text("corrupted", encoding="utf-8")
    with pytest.raises(RuntimeError):
        store.pending()


def test_console_url_cannot_send_credentials_to_another_host():
    with pytest.raises(ValueError):
        Settings(console_url="https://example.com/console/instance/list").validate()


def test_visibility_defaults_to_background_and_migrates_existing_choice(tmp_path):
    store = Store(tmp_path)
    assert store.settings().browser_visibility == "background"
    store.write("settings.json", {"headless": False})
    assert store.settings().browser_visibility == "foreground"
    store.save_settings(Settings(browser_visibility="background"))
    assert store.settings().browser_visibility == "background"
