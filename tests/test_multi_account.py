import unittest

from bot.multi_account.accounts import parse_accounts_json, stable_account_id
from bot.multi_account.profiles import build_strategy_profile


class AccountRegistryTests(unittest.TestCase):
    def test_stable_account_id_is_deterministic_and_opaque(self):
        first = stable_account_id("Example@Email.com")
        second = stable_account_id("example@email.com")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("acct_"))
        self.assertNotIn("example", first)
        self.assertNotIn("email", first)

    def test_credentials_are_hidden_from_repr_and_public_dict(self):
        accounts = parse_accounts_json(
            '[{"email":"friend@example.com","password":"super-secret","display_name":"Bot A"}]'
        )
        account = accounts[0]
        rendered = repr(account)
        self.assertNotIn("friend@example.com", rendered)
        self.assertNotIn("super-secret", rendered)
        public = account.public_dict()
        self.assertNotIn("email", public)
        self.assertNotIn("password", public)
        self.assertEqual(public["display_name"], "Bot A")

    def test_duplicate_emails_are_rejected_case_insensitively(self):
        with self.assertRaises(ValueError):
            parse_accounts_json(
                '[{"email":"a@example.com","password":"one"},'
                '{"email":"A@example.com","password":"two"}]'
            )


class StrategyProfileTests(unittest.TestCase):
    def test_profile_is_deterministic_for_account_and_season(self):
        p1 = build_strategy_profile("acct_123456789abc", season="2026-27")
        p2 = build_strategy_profile("acct_123456789abc", season="2026-27")
        self.assertEqual(p1, p2)

    def test_profiles_are_bounded(self):
        profile = build_strategy_profile("acct_abcdef123456", season="2026-27")
        values = profile.as_dict()
        for key, value in values.items():
            if key in {"name", "archetype", "seed", "fixture_horizon"}:
                continue
            self.assertGreaterEqual(value, 0.05, key)
            self.assertLessEqual(value, 0.95, key)
        self.assertGreaterEqual(profile.fixture_horizon, 3)
        self.assertLessEqual(profile.fixture_horizon, 8)

    def test_requested_archetype_is_respected(self):
        profile = build_strategy_profile(
            "acct_abcdef123456",
            requested_profile="Differential Hunter",
            season="2026-27",
        )
        self.assertEqual(profile.archetype, "Differential Hunter")


if __name__ == "__main__":
    unittest.main()
