"""Offline regression tests for diagnostic candidates, not motion approval."""
import copy
import math
import unittest

from backend.pose_quality import check_pose_plan


def actor(torso=300, *, reversed_legs=False, visible_parts=None, scale=1):
    joints = {"left_shoulder": [-50, 100], "right_shoulder": [50, 100],
              "left_hip": [-40, 100+torso], "right_hip": [40, 100+torso],
              "left_knee": [-40, 100+torso+180], "right_knee": [40, 100+torso+180],
              "left_ankle": [-40, 100+torso+360], "right_ankle": [40, 100+torso+360]}
    if reversed_legs:
        # Frame295-style bug: shoulders y100, hips y400, knees/ankles above shoulders.
        for side in ("left", "right"):
            joints[side+"_knee"][1] = 10
            joints[side+"_ankle"][1] = -40
    return {"id": "silver", "joints": {k: [v[0]*scale, v[1]*scale] for k, v in joints.items()},
            "head": {"ellipse": {"radii": [50*scale, 60*scale]}}, "visible_parts": visible_parts}


def plan(actors, *, first=1, shots=None):
    return {"frames": [{"frame": first+i, "actors": [a]} for i, a in enumerate(actors)],
            "shots": shots or [[first, first+len(actors)-1]]}


class PoseQualityTests(unittest.TestCase):
    def test_295_visible_reversed_legs_are_visual_candidates_not_failure(self):
        source = plan([actor(reversed_legs=True)], first=295)
        before = copy.deepcopy(source)
        report = check_pose_plan(source)
        self.assertEqual(source, before, "diagnostics must not mutate pose data")
        self.assertEqual(report["status"], "NEEDS_VISUAL_REVIEW")
        self.assertEqual(report["frames"], [295])
        self.assertEqual(report["issues"][0]["code"], "leg_reverse_fold_candidate")
        self.assertTrue(report["diagnostic_only"])
        self.assertNotIn("FAIL", str(report))

    def test_523_three_frame_torso_collapse_uses_robust_neighbors(self):
        source = plan([actor(torso=65 if i in (4, 5, 6) else 300) for i in range(11)], first=518)
        report = check_pose_plan(source)
        collapsed = [i["frame"] for i in report["issues"] if i["code"] == "torso_length_collapse_candidate"]
        self.assertEqual(collapsed, [522, 523, 524])

    def test_occluded_legs_with_bad_hidden_landmarks_do_not_warn(self):
        report = check_pose_plan(plan([actor(reversed_legs=True, visible_parts=["torso", "pelvis", "head", "left_arm", "right_arm"])]))
        self.assertFalse(report["needs_visual_review"])

    def test_cut_separates_neighbor_scale_comparisons(self):
        # Same actor/head size across a cut, but a genuinely different projection.
        source = plan([actor(torso=300) for _ in range(6)]+[actor(torso=100) for _ in range(6)], shots=[[1,6],[7,12]])
        report = check_pose_plan(source)
        self.assertEqual(report["issues"], [])

    def test_whole_frame_roll_does_not_make_normal_legs_reversed(self):
        rolled = actor()
        angle = math.radians(175)
        rolled["joints"] = {key: [x*math.cos(angle)-y*math.sin(angle), x*math.sin(angle)+y*math.cos(angle)]
                            for key, (x, y) in rolled["joints"].items()}
        self.assertEqual(check_pose_plan(plan([rolled]))["issues"], [])

    def test_camera_scale_change_with_head_does_not_warn(self):
        source = plan([actor(scale=s) for s in (1,1,1,.85,.7,.5,.7,.85,1,1,1)])
        self.assertEqual(check_pose_plan(source)["issues"], [])

    def test_missing_head_does_not_claim_torso_check(self):
        source = plan([actor(torso=60 if i==5 else 300) for i in range(11)])
        for frame in source["frames"]:
            frame["actors"][0].pop("head")
        report = check_pose_plan(source)
        self.assertEqual(report["status"], "NO_CANDIDATES")
        self.assertTrue(any("无头部尺寸" in limit for limit in report["limits"]))


if __name__ == "__main__":
    unittest.main()
