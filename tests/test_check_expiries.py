import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_expiries", ROOT / "scripts/check-expiries.py")
EXP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EXP)


class ExpiryTest(unittest.TestCase):
    def test_valid_until_settings_are_watched_without_listing_them(self):
        sync = {"targets": [{"name": "brain", "settings": {
            "X_PRICE_VALID_UNTIL": "2026-12-25T00:00:00+00:00", "OTHER": "1"}}]}
        items = EXP.watched(sync, {"items": [{"name": "token", "expires": "2027-11-01", "renew": "r"}]})
        self.assertEqual([i["name"] for i in items], ["X_PRICE_VALID_UNTIL on brain", "token"])

    def test_warn_and_urgent_windows(self):
        items = [{"name": "a", "expires": "2026-12-25", "renew": ""},
                 {"name": "b", "expires": "2026-12-08", "renew": ""},
                 {"name": "c", "expires": "2026-11-30", "renew": ""},
                 {"name": "d", "expires": "2027-11-01", "renew": ""}]
        _, warn, urgent = EXP.check(items, date(2026, 12, 4), 21, 7)
        self.assertEqual([w.split(":")[0] for w in warn], ["a"])
        self.assertEqual([u.split(":")[0] for u in urgent], ["c", "b"])

    def test_real_config_parses(self):
        sync = json.loads((ROOT / "config/environment-sync.json").read_text())
        extra = json.loads((ROOT / "config/expiries.json").read_text())
        lines, _, _ = EXP.check(EXP.watched(sync, extra), date(2026, 11, 2), 21, 7)
        self.assertTrue(any("EMBED_PRICE_VALID_UNTIL" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
