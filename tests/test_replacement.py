"""Identity, revision and input-contract tests; no network or model calls."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from backend import replacement, store


class ReplacementContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.override = patch.object(store, "DATA", self.root / "data")
        self.override.start()
        self.p = store.create_project("通用替换测试")
        self.pid = self.p["id"]
        self.source = self.asset("source.mp4", "source")
        self.image = self.asset("role.png", "reference")
        self.mask = self.asset("mask.mp4", "edit_mask")
        self.p = store.get_project(self.pid)
        self.p.update(source=self.source, source_meta={"duration": 4, "fps_rational": "24000/1001"}, timeline_duration=4)
        self.p["segments"] = [self.segment("A", 0, 2), self.segment("B", 2, 4)]
        self.p["roles"] = [{"id": "target_a", "name": "新角色甲", "color": "#8899aa", "images": [self.image], "description": "短发、灰色外衣"}]
        self.p["source_actors"] = [self.actor("person_a", "target_a")]
        store.put_project(self.p)

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def asset(self, name, kind):
        path = self.root / name
        path.write_bytes((name + " local fixture").encode())
        return store.add_asset(self.pid, path, kind)

    def segment(self, sid, start, end):
        return {"id": sid, "name": sid + "段", "start": start, "end": end, "edit_mask": self.mask}

    def actor(self, aid, target=None, scope="person"):
        return {"id": aid, "label": aid, "source_description": "开场左侧穿蓝衣、随后走向右侧的人",
                "target_role_id": target, "scope": scope, "color": "#8899aa",
                "observations": [{"segment_id": "A", "time": 0, "box": [.1, .1, .3, .9], "visibility": "visible", "confirmed": True},
                                 {"segment_id": "B", "time": 2.5, "box": [.6, .1, .8, .9], "visibility": "visible", "confirmed": True}]}

    def test_new_project_has_no_special_case_people_and_cloud_stays_locked(self):
        p = store.create_project("空项目")
        self.assertEqual(p["workflow"], "replacement")
        self.assertEqual(p["roles"], [])
        self.assertEqual(p["source_actors"], [])
        self.assertIsNone(p["settings"]["resolution"])
        self.assertFalse(p["cloud_gate"]["upload_allowed"])
        self.assertFalse(p["cloud_gate"]["generation_allowed"])
        self.assertEqual(p["preservation"], replacement.PRESERVATION)
        self.assertNotIn("球", p["scene"]["description"])

    def test_resolution_default_only_fills_missing_setting(self):
        missing = replacement.normalize_project({"workflow": "replacement", "settings": {}})
        self.assertEqual(missing["settings"]["resolution"], "480P")
        for resolution in ("720P", "1080P"):
            existing = replacement.normalize_project({"workflow": "replacement", "settings": {"resolution": resolution}})
            self.assertEqual(existing["settings"]["resolution"], resolution)

    def test_legacy_read_does_not_migrate_material_or_workflow(self):
        path = store.project_path(self.pid)
        old = copy.deepcopy(self.p)
        old.pop("workflow")
        store.save_json(path, old)
        p = store.get_project(self.pid)
        self.assertEqual(p["workflow"], "legacy")
        self.assertEqual(p["roles"], old["roles"])

    def test_generic_plan_keeps_stable_person_mapping_across_segments(self):
        a = replacement.plan(self.p, "A")
        b = replacement.plan(self.p, "B")
        self.assertTrue(a["ready"], a["issues"])
        self.assertTrue(b["ready"], b["issues"])
        self.assertEqual(a["mappings"][0]["actor_id"], b["mappings"][0]["actor_id"])
        self.assertEqual(a["mappings"][0]["target_role_id"], "target_a")
        self.assertEqual(a["references"][0]["reference"], "图1")
        self.assertEqual(b["mappings"][0]["observations"][0]["time"], 2.5)

    def test_head_only_prompt_uses_source_description_and_segment_relative_time(self):
        self.p["source_actors"][0]["scope"] = "head"
        prompt = replacement.generate_prompt(self.p, self.p["segments"][1])
        self.assertIn("仅头部、面孔和发型，原身体及服装保留", prompt)
        self.assertIn("片段0.500秒", prompt)
        self.assertIn("开场左侧穿蓝衣", prompt)
        self.assertIn("人物交叉、出入画或遮挡前后不得互换身份", prompt)
        self.assertNotIn("乒乓", prompt)

    def test_preserved_person_does_not_require_target_reference(self):
        self.p["source_actors"].append(self.actor("spectator", scope="preserve"))
        report = replacement.plan(self.p)
        self.assertTrue(report["ready"], report["issues"])
        self.assertIsNone(report["mappings"][1]["target_role_id"])
        self.assertEqual(len(report["references"]), 1)

    def test_unconfirmed_occlusion_and_missing_segment_identity_block_plan(self):
        actor = self.p["source_actors"][0]
        actor["observations"] = [{"segment_id": "A", "time": .5, "visibility": "occluded", "confirmed": False}]
        report = replacement.plan(self.p)
        codes = {i["code"] for i in report["issues"] if i["blocking"]}
        self.assertIn("IDENTITY_UNCONFIRMED", codes)
        self.assertIn("ACTOR_NOT_LOCATED", codes)
        self.assertFalse(report["ready"])

    def test_duplicate_target_binding_is_not_silently_accepted(self):
        self.p["source_actors"].append(self.actor("person_b", "target_a"))
        report = replacement.plan(self.p)
        self.assertIn("DUPLICATE_TARGET", {i["code"] for i in report["issues"]})
        self.assertFalse(report["ready"])

    def test_locators_are_not_accepted_as_pixel_masks(self):
        self.p["segments"][0].pop("edit_mask")
        report = replacement.plan(self.p, "A")
        self.assertFalse(report["pixel_masks_ready"])
        self.assertFalse(report["ready"])
        self.assertIn("MASK_REQUIRED", {i["code"] for i in report["issues"] if i["blocking"]})

    def test_head_only_with_preserved_scene_can_be_ready_without_masks(self):
        self.p["source_actors"][0]["scope"] = "head"
        self.p["source_actors"].append(self.actor("spectator", scope="preserve"))
        for segment in self.p["segments"]:
            segment.pop("edit_mask")

        for sid in (None, "A", "B"):
            with self.subTest(segment=sid):
                report = replacement.plan(self.p, sid)
                self.assertTrue(report["ready"], report["issues"])
                self.assertFalse(report["pixel_masks_ready"])
                self.assertEqual(len(report["references"]), 1)

    def test_scene_requires_reference_region_and_explicit_confirmation(self):
        self.p["source_actors"][0]["scope"] = "head"
        self.p["scene"] = {"mode": "replace", "images": [self.image], "description": "将远处建筑外观换成古城，保持原道路与透视", "regions": []}
        first = replacement.plan(self.p, "A")
        self.assertIn("SCENE_REGION_MISSING", {i["code"] for i in first["issues"]})
        self.p["scene"]["regions"] = [{"id": "background", "segment_id": "A", "start": 0, "end": 2, "label": "远处建筑", "box": [0, 0, 1, .4], "confirmed": True}]
        replacement.validate_project(self.p)
        report = replacement.plan(self.p, "A")
        self.assertTrue(report["ready"], report["issues"])
        self.assertEqual(report["references"][-1]["kind"], "scene")
        self.p["segments"][0].pop("edit_mask")
        report = replacement.plan(self.p, "A")
        self.assertFalse(report["ready"])
        self.assertFalse(report["pixel_masks_ready"])
        self.assertIn("MASK_REQUIRED", {i["code"] for i in report["issues"] if i["blocking"]})

    def test_ten_people_have_independent_ids_and_reference_labels(self):
        self.p["roles"] = []
        self.p["source_actors"] = []
        for index in range(10):
            role_id = f"target_{index}"
            image = self.asset(f"role_{index}.png", "reference")
            self.p["roles"].append({"id": role_id, "name": f"角色{index}", "images": [image]})
            self.p["source_actors"].append(self.actor(f"person_{index}", role_id))
        report = replacement.plan(self.p, "A")
        self.assertTrue(report["ready"], report["issues"])
        self.assertEqual(len(report["mappings"]), 10)
        self.assertEqual(report["mappings"][-1]["reference_labels"], ["图10"])
        self.p["roles"][0]["images"].append(self.image)
        report = replacement.plan(self.p, "A")
        self.assertFalse(report["ready"])
        self.assertIn("TOO_MANY_REFERENCES", {i["code"] for i in report["issues"]})

    def test_different_role_names_cannot_hide_identical_person_reference(self):
        self.p["roles"].append({"id": "other_role", "name": "另一个名字", "images": [self.image]})
        self.p["source_actors"].append(self.actor("person_b", "other_role"))
        report = replacement.plan(self.p, "A")
        self.assertFalse(report["ready"])
        self.assertIn("SHARED_PERSON_REFERENCE", {i["code"] for i in report["issues"]})
        shared = next(issue for issue in report["issues"] if issue["code"] == "SHARED_PERSON_REFERENCE")
        self.assertEqual(shared["role_id"], "other_role")
        self.assertEqual(shared["asset_id"], self.image["id"])
        self.assertEqual(shared["reference"], "图2")
        self.assertEqual(shared["reference_kind"], "person")
        self.assertEqual(shared["actor_id"], "person_b")
        self.assertEqual(shared["segment_id"], "A")

    def test_target_issues_expose_real_role_actor_and_segment_without_inventing_assets(self):
        p = copy.deepcopy(self.p)
        p["roles"][0].update(name="", images=[])
        report = replacement.plan(p, "A")
        target = [issue for issue in report["issues"] if issue["code"] in {"TARGET_NAME_MISSING", "TARGET_IMAGE_MISSING"}]
        self.assertEqual(len(target), 2)
        for issue in target:
            self.assertEqual(issue["role_id"], "target_a")
            self.assertEqual(issue["actor_id"], "person_a")
            self.assertEqual(issue["segment_id"], "A")
            self.assertEqual(issue["reference_kind"], "person")
            self.assertNotIn("asset_id", issue)
        p["source_actors"][0]["target_role_id"] = None
        unbound = next(issue for issue in replacement.plan(p, "A")["issues"] if issue["code"] == "TARGET_NOT_BOUND")
        self.assertNotIn("role_id", unbound)
        self.assertEqual(unbound["actor_id"], "person_a")

    def test_reference_issues_expose_exact_person_and_scene_context(self):
        for code in ("REFERENCE_MISSING", "REFERENCE_CHANGED"):
            with self.subTest(code=code):
                p = copy.deepcopy(self.p)
                if code == "REFERENCE_MISSING":
                    p["roles"][0]["images"][0]["path"] = str(self.root/"missing-role.png")
                else:
                    Path(self.image["path"]).write_bytes(b"changed-reference")
                issue = next(issue for issue in replacement.plan(p, "A")["issues"] if issue["code"] == code)
                self.assertEqual({key: issue[key] for key in ("asset_id", "role_id", "reference", "reference_kind", "actor_id", "segment_id")},
                                 {"asset_id": self.image["id"], "role_id": "target_a", "reference": "图1", "reference_kind": "person", "actor_id": "person_a", "segment_id": "A"})
                global_issue = next(issue for issue in replacement.plan(p)["issues"] if issue["code"] == code)
                self.assertNotIn("segment_id", global_issue)
        p = copy.deepcopy(self.p)
        scene_image = copy.deepcopy(self.image)
        scene_image["path"] = str(self.root/"missing-scene.png")
        p["scene"].update(mode="replace", images=[scene_image])
        issue = next(issue for issue in replacement.plan(p, "B")["issues"] if issue["code"] == "REFERENCE_MISSING" and issue["reference_kind"] == "scene")
        self.assertEqual(issue["asset_id"], self.image["id"])
        self.assertEqual(issue["reference"], "图2")
        self.assertEqual(issue["segment_id"], "B")
        self.assertNotIn("role_id", issue)
        self.assertNotIn("actor_id", issue)

    def test_reference_context_does_not_guess_actor_when_role_has_multiple_bindings(self):
        p = copy.deepcopy(self.p)
        p["source_actors"].append(self.actor("person_b", "target_a"))
        p["roles"][0]["images"][0]["path"] = str(self.root/"missing-shared-role.png")
        issue = next(issue for issue in replacement.plan(p, "A")["issues"] if issue["code"] == "REFERENCE_MISSING")
        self.assertEqual(issue["role_id"], "target_a")
        self.assertNotIn("actor_id", issue)

    def test_inspect_mask_issue_preserves_rule_and_adds_registered_asset_context(self):
        from backend import replacement_workflow
        with patch.object(replacement_workflow.replacement_media, "validate_mask", return_value={"issues": [{"code": "FIXTURE", "message": "fixture mask invalid"}]}):
            report = replacement_workflow.inspect_plan(self.p, "A")
        issue = next(issue for issue in report["issues"] if issue["code"] == "EDIT_MASK_FIXTURE")
        self.assertEqual(issue["message"], "fixture mask invalid")
        self.assertTrue(issue["blocking"])
        self.assertEqual(issue["segment_id"], "A")
        self.assertEqual(issue["asset_id"], self.mask["id"])

    def test_fingerprint_ignores_outputs_and_detects_actual_asset_change(self):
        before = replacement.fingerprint(self.p)
        self.p["revision"] += 20
        self.p["assets"].append({"id": "diagnostic_output"})
        self.p["preparation"] = {"asset": "local-preview"}
        self.p["segments"][0]["generated"] = {"path": "output.mp4"}
        self.assertEqual(before, replacement.fingerprint(self.p))
        Path(self.image["path"]).write_bytes(b"modified outside the application")
        self.assertNotEqual(before, replacement.fingerprint(self.p))
        self.assertIn("REFERENCE_CHANGED", {i["code"] for i in replacement.plan(self.p)["issues"]})

    def test_review_and_preparation_are_invalidated_by_mapping_change(self):
        p = store.get_project(self.pid)
        p["generic_review"] = {"fingerprint": replacement.fingerprint(p), "approved": True}
        p["preparation"] = {"fingerprint": replacement.fingerprint(p)}
        store.put_project(p)
        actors = copy.deepcopy(p["source_actors"])
        actors[0]["scope"] = "head"
        result = store.update_project(self.pid, {"source_actors": actors, "expected_revision": p["revision"]})
        self.assertIsNone(result["generic_review"])
        self.assertIsNone(result["preparation"])

    def test_stale_browser_edit_returns_conflict_without_overwriting(self):
        p = store.get_project(self.pid)
        store.update_project(self.pid, {"name": "人工更新", "expected_revision": p["revision"]})
        with self.assertRaises(store.RevisionConflict):
            store.update_project(self.pid, {"name": "旧页面更新", "expected_revision": p["revision"]})
        self.assertEqual(store.get_project(self.pid)["name"], "人工更新")

    def test_put_cannot_inject_review_cloud_gate_or_arbitrary_asset_paths(self):
        p = store.get_project(self.pid)
        result = store.update_project(self.pid, {"cloud_gate": {"upload_allowed": True, "generation_allowed": True}, "generic_review": {"approved": True}})
        self.assertFalse(result["cloud_gate"]["generation_allowed"])
        self.assertIsNone(result["generic_review"])
        changed = copy.deepcopy(p["roles"])
        changed[0]["images"][0]["path"] = "/arbitrary/private/file"
        result = store.update_project(self.pid, {"roles": changed})
        self.assertEqual(result["roles"][0]["images"][0]["path"], self.image["path"])

    def test_segment_put_cannot_inject_output_or_foreground_protection(self):
        p = store.get_project(self.pid)
        segments = copy.deepcopy(p["segments"])
        for field in ("protection_mask", "composited", "composite_preview", "composite_report", "candidate_validation", "candidate_review", "reference_timeline"):
            segments[0][field] = {"path": "/arbitrary/private/file", "approved": True}
        result = store.update_project(self.pid, {"segments": segments})
        for field in ("protection_mask", "composited", "composite_preview", "composite_report", "candidate_validation", "candidate_review", "reference_timeline"):
            self.assertIsNone(result["segments"][0].get(field))

    def test_old_client_segment_save_keeps_server_sample_and_cannot_forge_or_overwrite_it(self):
        legacy_segments = copy.deepcopy(store.get_project(self.pid)["segments"])
        sample = self.asset("offline-sample.mp4", "sample_candidate")
        preview = self.asset("offline-sample-preview.mp4", "sample_preview")
        evidence = {"sample_candidate": sample,
                    "sample_provenance": {"run_id": "offline-run", "task_id": "offline-task", "purpose": "candidate_sample", "resolution": "720P"},
                    "sample_preview": preview,
                    "sample_preview_report": {"input_sample_sha256": sample["sha256"], "final_approved": False}}
        p = store.get_project(self.pid)
        p["segments"][0].update(copy.deepcopy(evidence))
        store.put_project(p)  # Stand in for the worker's server-owned result.
        for mode in ("omitted", "null", "forged"):
            with self.subTest(mode=mode):
                current = store.get_project(self.pid)
                segments = copy.deepcopy(legacy_segments)
                segments[0]["name"] = "旧客户端保存-" + mode
                if mode != "omitted":
                    for segment in segments:
                        for field in evidence:
                            segment[field] = None if mode == "null" else {"id": "client-forged", "path": "/not-a-server-output"}
                saved = store.update_project(self.pid, {"segments": segments, "expected_revision": current["revision"]})
                self.assertEqual(saved["segments"][0]["name"], segments[0]["name"])
                self.assertEqual({field: saved["segments"][0][field] for field in evidence}, evidence)
                self.assertTrue(all(field not in saved["segments"][1] for field in evidence))
                self.assertEqual(saved["source"], current["source"])
                self.assertEqual(saved["segments"][0]["edit_mask"], current["segments"][0]["edit_mask"])
                self.assertEqual(store.digest(sample["path"]), sample["sha256"])
                self.assertEqual(store.digest(preview["path"]), preview["sha256"])

    def test_candidate_review_and_displayed_plan_become_stale_on_binding_edit(self):
        p = store.get_project(self.pid)
        p["replacement_plan"] = replacement.plan(p)
        p["segments"][0]["generated"] = {"path": "generated.mp4"}
        p["segments"][0]["candidate_review"] = {"approved": True}
        store.put_project(p)
        actors = copy.deepcopy(p["source_actors"])
        actors[0]["scope"] = "head"
        result = store.update_project(self.pid, {"source_actors": actors})
        self.assertIsNone(result["replacement_plan"])
        self.assertIsNone(result["segments"][0]["candidate_review"])
        self.assertTrue(result["segments"][0]["candidate_stale"])

    def test_wan_only_and_original_sound_are_enforced(self):
        settings = {**self.p["settings"], "model": "another-provider"}
        with self.assertRaises(ValueError):
            store.update_project(self.pid, {"settings": settings})
        with self.assertRaises(ValueError):
            replacement.validate_project({**self.p, "music": {"mode": "custom"}})

    def test_invalid_bbox_or_dangling_identity_is_rejected(self):
        actors = copy.deepcopy(self.p["source_actors"])
        actors[0]["observations"][0]["box"] = [0, 0, 2, 1]
        with self.assertRaises(ValueError):
            store.update_project(self.pid, {"source_actors": actors})
        actors[0]["observations"][0]["box"] = [0, 0, 1, 1]
        actors[0]["target_role_id"] = "deleted_role"
        with self.assertRaises(ValueError):
            store.update_project(self.pid, {"source_actors": actors})


if __name__ == "__main__":
    unittest.main()
