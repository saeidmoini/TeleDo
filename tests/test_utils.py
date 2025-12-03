from datetime import datetime

from utils import date_utils
from utils.texts import t


def test_date_utils_round_trip():
    now = datetime(2024, 1, 2, 3, 4, 5)
    jalali = date_utils.gregorian_to_jalali(now)
    back = date_utils.jalali_to_gregorian(jalali)
    assert back is not None
    assert back.date() == now.date()

    # Invalid inputs should return fallbacks
    assert date_utils.gregorian_to_jalali(None) == "N/A"
    assert date_utils.jalali_to_gregorian("bad-date") is None


def test_texts_lookup_and_formatting():
    assert t("cmd_user_tasks_desc").startswith("مشاهده")
    assert t("cmd_admin_tasks_desc")
    assert t("cmd_admin_users_desc")
    assert t("missing_key") == "missing_key"
    assert t("deadline_prompt", title="X")  # ensures formatting does not crash
