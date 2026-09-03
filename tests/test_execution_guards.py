from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from scripts import weekly_orchestrator_core as core
from scripts.weekly_orchestrator_core import (
    _build_planning_news_guard,
    _expected_live_squad,
    _guard_changed,
    _picks_readback_errors,
    _chip_readback_error,
    _candidate_watch_ids,
    _frozen_plan_errors,
    build_picks_payload,
)


class ExecutionGuardTests(unittest.TestCase):
    def test_news_guard_detects_late_official_change(self):
        bootstrap = {
            "elements": [
                {
                    "id": 10,
                    "status": "a",
                    "chance_of_playing_next_round": 100,
                    "news": "",
                }
            ]
        }
        frozen = _build_planning_news_guard(bootstrap, [10])
        changed, _ = _guard_changed(frozen, bootstrap)
        self.assertFalse(changed)

        updated = {
            "elements": [
                {
                    "id": 10,
                    "status": "d",
                    "chance_of_playing_next_round": 50,
                    "news": "Knock - 50% chance of playing",
                }
            ]
        }
        changed, detail = _guard_changed(frozen, updated)
        self.assertTrue(changed)
        self.assertIn("status a→d", detail)

    def test_news_guard_detects_selectability_change_without_news_text(self):
        bootstrap = {
            "elements": [{
                "id": 10,
                "status": "a",
                "chance_of_playing_next_round": 100,
                "news": "",
                "can_select": True,
                "can_transact": True,
                "removed": False,
            }]
        }
        frozen = _build_planning_news_guard(bootstrap, [10])
        updated = {
            "elements": [{
                "id": 10,
                "status": "a",
                "chance_of_playing_next_round": 100,
                "news": "",
                "can_select": False,
                "can_transact": True,
                "removed": False,
            }]
        }
        changed, detail = _guard_changed(frozen, updated)
        self.assertTrue(changed)
        self.assertIn("select True→False", detail)

    def test_expected_squad_accounts_for_checkpointed_transfer(self):
        decision = {
            "source_squad_signature": [1, 2, 3],
            "approved_transfers": [
                {"element_out": 1, "element_in": 4},
                {"element_out": 2, "element_in": 5},
            ],
        }
        self.assertEqual(
            _expected_live_squad(decision, {"1->4"}),
            {2, 3, 4},
        )

    def test_chip_readback_confirms_target_gameweek(self):
        live = {
            "chips": [
                {"name": "3xc", "status_for_entry": "played", "event": 9},
                {"name": "bboost", "status_for_entry": "available", "event": None},
            ]
        }
        self.assertIsNone(_chip_readback_error("3xc", 9, live))
        self.assertIsNotNone(_chip_readback_error("3xc", 10, live))
        self.assertIsNotNone(_chip_readback_error("bboost", 9, live))

    def test_watchlist_collects_only_transfer_in_targets(self):
        state = {
            "signing_ideas": [
                {"action": "transfer_in", "element": 101},
                {"action": "transfer_out", "element": 202},
            ],
            "research_ideas": [
                {"action": "transfer_in", "element": 303},
            ],
            "idea_list": [
                {"action": "hold", "element": 404},
            ],
        }
        self.assertEqual(_candidate_watch_ids(state), [101, 303])

    def test_picks_payload_orders_outfield_bench_by_current_xpts(self):
        starters = [
            {"element": i, "position": 2 if i <= 4 else 3, "cost": 5.0}
            for i in range(1, 12)
        ]
        bench = [
            {"element": 12, "position": 1, "cost": 4.0, "xpts": 3.0},
            {"element": 13, "position": 2, "cost": 7.0, "xpts": 1.5},
            {"element": 14, "position": 3, "cost": 4.5, "xpts": 4.0},
            {"element": 15, "position": 4, "cost": 5.5, "xpts": 2.5},
        ]
        payload = build_picks_payload({
            "captain": {"element": 5},
            "gw_plan": [{
                "starting_xi": starters,
                "bench": bench,
                "vice": {"element": 6},
            }],
        })
        by_position = {row["position"]: row["element"] for row in payload}
        self.assertEqual(by_position[12], 12)
        self.assertEqual(
            [by_position[13], by_position[14], by_position[15]],
            [14, 15, 13],
        )

    def test_two_transfer_execution_is_one_atomic_batch(self):
        source_ids = list(range(1, 16))
        target_ids = list(range(3, 18))
        elements = [
            {
                "id": element,
                "now_cost": 50,
                "status": "a",
                "chance_of_playing_next_round": 100,
                "news": "",
            }
            for element in range(1, 18)
        ]
        bootstrap = {
            "events": [{
                "id": 3,
                "deadline_time": "2099-09-05T11:00:00Z",
                "finished": False,
            }],
            "elements": elements,
        }
        picks_payload = [
            {
                "element": element,
                "position": position,
                "is_captain": position == 1,
                "is_vice_captain": position == 2,
            }
            for position, element in enumerate(target_ids, 1)
        ]
        before_team = {
            "picks": [
                {
                    "element": element,
                    "selling_price": 50,
                    "position": position,
                    "is_captain": False,
                    "is_vice_captain": False,
                }
                for position, element in enumerate(source_ids, 1)
            ],
            "chips": [],
        }
        after_team = {
            "picks": [dict(row, selling_price=50) for row in picks_payload],
            "chips": [],
        }
        decision = {
            "gw": 3,
            "execution_plan_version": 3,
            "model_health": {"loaded": True, "inference_ok": True, "error": None},
            "transfer_plan_kind": "ordinary",
            "approved_transfers": [
                {
                    "element_out": 1,
                    "name_out": "P1",
                    "selling_price": 50,
                    "element_in": 16,
                    "name_in": "P16",
                    "purchase_price": 50,
                },
                {
                    "element_out": 2,
                    "name_out": "P2",
                    "selling_price": 50,
                    "element_in": 17,
                    "name_in": "P17",
                    "purchase_price": 50,
                },
            ],
            "approved_chip": None,
            "picks_payload": picks_payload,
            "source_squad_signature": source_ids,
            "target_squad_signature": target_ids,
            "planning_news_guard": _build_planning_news_guard(
                bootstrap, list(range(1, 18))
            ),
            "expected_net_gain": 5.0,
            "hit_count": 0,
            "reasoning": "test",
        }
        state = {"approved_plan": decision}

        transfer_mock = Mock(return_value={"status": "ok"})
        picks_mock = Mock(return_value={"status": "ok"})
        with (
            patch.object(core, "authenticate", return_value=("token", object())),
            patch.object(core.fpl_api, "me", return_value={"player": {"entry": 42}}),
            patch.object(
                core.fpl_api,
                "my_team",
                side_effect=[before_team, after_team],
            ),
            patch.object(core.fpl_api, "transfer", transfer_mock),
            patch.object(core.fpl_api, "update_picks", picks_mock),
            patch.object(core, "save_state"),
            patch.object(core, "commit_state"),
            patch.object(core.email_alerts, "send_alert"),
            patch.object(core.time, "sleep"),
        ):
            core.stage_execute(bootstrap, state, dry_run=False)

        self.assertEqual(transfer_mock.call_count, 1)
        kwargs = transfer_mock.call_args.kwargs
        self.assertEqual(len(kwargs["transfers"]), 2)
        self.assertEqual(
            {(row["element_out"], row["element_in"]) for row in kwargs["transfers"]},
            {(1, 16), (2, 17)},
        )
        self.assertEqual(picks_mock.call_count, 1)
        self.assertEqual(state["last_executed_gw"], 3)

    def test_legacy_current_plan_is_proactively_replanned_before_execute_window(self):
        deadline = datetime.now(timezone.utc) + timedelta(hours=24)
        bootstrap = {
            "events": [{
                "id": 3,
                "deadline_time": deadline.isoformat().replace("+00:00", "Z"),
                "finished": False,
            }]
        }
        state = {
            "last_simulated_gw": 3,
            "last_executed_gw": 1,
            "approved_plan": {
                "gw": 3,
                "execution_plan_version": 2,
            },
        }
        self.assertEqual(
            core.determine_stage(bootstrap, state),
            core.PRE_DEADLINE_PLAN,
        )

    def test_legacy_frozen_plan_is_rejected(self):
        self.assertTrue(_frozen_plan_errors({"approved_chip": None}))

    def test_legacy_plan_replans_before_authentication(self):
        bootstrap = {
            "events": [{
                "id": 3,
                "deadline_time": "2099-09-05T11:00:00Z",
                "finished": False,
            }]
        }
        state = {"approved_plan": {"gw": 3, "approved_chip": None}}
        with (
            patch.object(
                core, "_replan_stale_execution", return_value={"replanned": True}
            ) as replan,
            patch.object(core, "authenticate") as authenticate,
        ):
            result = core.stage_execute(bootstrap, state, dry_run=False)

        self.assertEqual(result, {"replanned": True})
        replan.assert_called_once()
        authenticate.assert_not_called()

    def test_valid_wildcard_frozen_plan_is_accepted(self):
        self.assertEqual(_frozen_plan_errors({
            "execution_plan_version": 3,
            "model_health": {"loaded": True, "inference_ok": True, "error": None},
            "approved_chip": "wildcard",
            "transfer_plan_kind": "wildcard_rebuild",
            "hit_count": 0,
            "wildcard_validation_errors": [],
            "wildcard_validated": True,
            "approved_transfers": [{"element_out": 1, "element_in": 16}],
            "source_squad_signature": list(range(1, 16)),
            "target_squad_signature": list(range(2, 17)),
        }), [])

    def test_frozen_plan_rejects_target_not_produced_by_transfers(self):
        errors = _frozen_plan_errors({
            "execution_plan_version": 3,
            "model_health": {"loaded": True, "inference_ok": True, "error": None},
            "approved_chip": None,
            "approved_transfers": [],
            "source_squad_signature": list(range(1, 16)),
            "target_squad_signature": list(range(2, 17)),
        })
        self.assertIn("transfer batch does not produce the target squad", errors)

    def test_valid_wildcard_is_attached_to_atomic_transfer_call(self):
        source_ids = list(range(1, 16))
        target_ids = list(range(2, 17))
        bootstrap = {
            "events": [{
                "id": 3,
                "deadline_time": "2099-09-05T11:00:00Z",
                "finished": False,
            }],
            "elements": [
                {
                    "id": element, "now_cost": 50, "status": "a",
                    "chance_of_playing_next_round": 100, "news": "",
                }
                for element in range(1, 17)
            ],
        }
        picks = [
            {
                "element": element, "position": position,
                "is_captain": position == 1,
                "is_vice_captain": position == 2,
            }
            for position, element in enumerate(target_ids, 1)
        ]
        before = {
            "picks": [
                {"element": element, "selling_price": 50}
                for element in source_ids
            ],
            "chips": [{"name": "wildcard", "status_for_entry": "available"}],
        }
        after = {
            "picks": [dict(row, selling_price=50) for row in picks],
            "chips": [{
                "name": "wildcard", "status_for_entry": "played", "event": 3,
            }],
        }
        decision = {
            "gw": 3,
            "execution_plan_version": 3,
            "model_health": {"loaded": True, "inference_ok": True, "error": None},
            "transfer_plan_kind": "wildcard_rebuild",
            "approved_chip": "wildcard",
            "hit_count": 0,
            "wildcard_validation_errors": [],
            "wildcard_validated": True,
            "approved_transfers": [{
                "element_out": 1, "name_out": "P1", "selling_price": 50,
                "element_in": 16, "name_in": "P16", "purchase_price": 50,
            }],
            "picks_payload": picks,
            "source_squad_signature": source_ids,
            "target_squad_signature": target_ids,
            "planning_news_guard": _build_planning_news_guard(
                bootstrap, list(range(1, 17))
            ),
        }
        state = {"approved_plan": decision}
        transfer_mock = Mock(return_value={"status": "ok"})
        with (
            patch.object(core, "authenticate", return_value=("token", object())),
            patch.object(core.fpl_api, "me", return_value={"player": {"entry": 42}}),
            patch.object(core.fpl_api, "my_team", side_effect=[before, after]),
            patch.object(core.fpl_api, "transfer", transfer_mock),
            patch.object(core.fpl_api, "update_picks", return_value={"status": "ok"}),
            patch.object(core, "save_state"),
            patch.object(core, "commit_state"),
            patch.object(core.email_alerts, "send_alert"),
            patch.object(core.time, "sleep"),
        ):
            core.stage_execute(bootstrap, state, dry_run=False)

        self.assertEqual(transfer_mock.call_count, 1)
        self.assertEqual(transfer_mock.call_args.kwargs["chip"], "wildcard")
        self.assertEqual(state["last_executed_gw"], 3)

    def test_exact_picks_readback_checks_positions_captain_and_vice(self):
        expected = [
            {
                "element": 10,
                "position": 1,
                "is_captain": False,
                "is_vice_captain": False,
            },
            {
                "element": 20,
                "position": 2,
                "is_captain": True,
                "is_vice_captain": False,
            },
            {
                "element": 30,
                "position": 3,
                "is_captain": False,
                "is_vice_captain": True,
            },
        ]
        live = {"picks": [dict(row) for row in expected]}
        self.assertEqual(_picks_readback_errors(expected, live), [])

        live["picks"][1]["is_captain"] = False
        errors = _picks_readback_errors(expected, live)
        self.assertTrue(errors)
        self.assertIn("position 2", errors[0])


if __name__ == "__main__":
    unittest.main()
