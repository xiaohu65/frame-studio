"""Read-only, screen-space pose diagnostics; candidates always need visual review.

These measurements cannot recover depth, determine a ball contact, or distinguish
every valid crouch/jump from a tracking failure. They never repair a plan, mark a
render approved, or turn a landmark anomaly into a definitive motion failure.
"""
from __future__ import annotations

from collections import defaultdict
import math
from statistics import median
from typing import Any


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (ValueError, TypeError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def _center(joints: dict, first: str, second: str) -> tuple[float, float] | None:
    a, b = _point(joints.get(first)), _point(joints.get(second))
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) if a and b else None


def _visible(actor: dict, part: str) -> bool:
    parts = actor.get("visible_parts")
    # Missing/None means all parts, matching the renderer's contract. An empty
    # list means nothing is visible and must not generate an invisible-leg alarm.
    return parts is None or (isinstance(parts, (list, tuple, set)) and part in parts)


def _head_scale(actor: dict) -> float | None:
    head = actor.get("head") or {}
    ellipse = head.get("ellipse") or {} if isinstance(head, dict) else {}
    radii = _point(ellipse.get("radii")) if isinstance(ellipse, dict) else None
    if not radii or min(radii) <= 0:
        return None
    return math.sqrt(radii[0] * radii[1])


def _shot_ranges(plan: dict) -> list[tuple[int, int]]:
    ranges = []
    for shot in plan.get("shots") or []:
        try:
            if isinstance(shot, dict):
                start = shot.get("start_frame", shot.get("start"))
                end = shot.get("end_frame", shot.get("end"))
            else:
                start, end = shot[:2]
            start, end = int(start), int(end)
            if start <= end:
                ranges.append((start, end))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(ranges)


def check_pose_plan(plan: dict) -> dict:
    """Return JSON-safe diagnostic candidates without mutating ``plan``.

    Input uses the articulated-puppet plan contract: 1-based ``frames`` with
    ``actors[id, joints, head.ellipse.radii, visible_parts]`` and optional
    inclusive ``shots`` ranges. For each actor, torso length is compared with
    at least four neighbours within five frames, in the same shot and same
    uninterrupted actor track. Heads must be within 25% of the current size;
    torso lengths are normalized to the current head size before comparison.

    A torso below 60% of that robust median is a collapse candidate. A visible
    leg whose knee and ankle fold far back beyond the hip along the torso axis
    is a reversal candidate. The torso axis makes this rule invariant to an
    in-plane camera roll; genuine tucked jumps can still be candidates.
    """
    if not isinstance(plan, dict):
        raise ValueError("pose plan must be an object")
    shots = _shot_ranges(plan)
    cuts = []
    for cut in plan.get("cuts") or []:
        try:
            cuts.append(int(cut))
        except (ValueError, TypeError):
            continue
    tracks: dict[tuple, list[dict]] = defaultdict(list)
    last_seen: dict[str, tuple] = {}
    skipped = defaultdict(int)
    rows = []
    for entry in plan.get("frames") or []:
        if not isinstance(entry, dict):
            skipped["malformed_frames"] += 1
            continue
        try:
            number = int(entry["frame"])
        except (KeyError, TypeError, ValueError):
            skipped["malformed_frames"] += 1
            continue
        rows.append((number, entry))
    rows.sort(key=lambda row: row[0])
    for number, entry in rows:
        # A gap between supplied shot ranges is its own context. Explicit
        # frame shot IDs / cuts also split tracks when ranges are unavailable.
        shot = next((i for i, (a, b) in enumerate(shots) if a <= number <= b), None)
        if shot is None:
            shot = ("unlisted", sum(number >= c for c in cuts), entry.get("shot_id", entry.get("shot")))
            if isinstance(shot[-1], (list, dict)):
                shot = shot[:2] + (repr(shot[-1]),)
        for actor in entry.get("actors") or []:
            if not isinstance(actor, dict) or actor.get("id") is None:
                skipped["malformed_actors"] += 1
                continue
            actor_id = str(actor["id"])
            joints = actor.get("joints") or {}
            if not isinstance(joints, dict):
                skipped["missing_joints"] += 1
                continue
            shoulders = _center(joints, "left_shoulder", "right_shoulder")
            hips = _center(joints, "left_hip", "right_hip")
            if not shoulders or not hips or math.dist(shoulders, hips) <= 1e-6:
                skipped["missing_or_degenerate_torso"] += 1
                continue
            previous = last_seen.get(actor_id)
            run = previous[2] if previous and previous[:2] == (number - 1, shot) else (previous[2] + 1 if previous else 0)
            last_seen[actor_id] = (number, shot, run)
            tracks[(actor_id, shot, run)].append({
                "frame": number, "actor": actor, "joints": joints,
                "shoulders": shoulders, "hips": hips,
                "torso": math.dist(shoulders, hips), "head": _head_scale(actor),
            })
    issues = []
    instances = 0
    for (actor_id, _shot, _run), track in tracks.items():
        for current in track:
            instances += 1
            actor, torso = current["actor"], current["torso"]
            frame = current["frame"]
            if _visible(actor, "torso") and _visible(actor, "head") and current["head"]:
                references = [row for row in track
                              if 0 < abs(row["frame"] - frame) <= 5
                              and _visible(row["actor"], "torso") and _visible(row["actor"], "head")
                              and row["head"] and 0.8 <= row["head"] / current["head"] <= 1.25]
                if len(references) >= 4:
                    baseline = median(row["torso"] * current["head"] / row["head"] for row in references)
                    ratio = torso / baseline
                    if ratio < 0.6:
                        issues.append({
                            "code": "torso_length_collapse_candidate", "frame": frame,
                            "actor_id": actor_id, "needs_visual_review": True,
                            "metrics": {"torso_px": round(torso, 4), "head_normalized_neighbor_median_px": round(baseline, 4),
                                        "ratio": round(ratio, 4), "neighbor_frames": [r["frame"] for r in references]},
                            "message": "头部尺寸相近时躯干明显缩短；请对照源片，排除真实俯身和透视缩短。",
                        })
            # Project on the current shoulder-to-hip axis rather than image Y.
            # A whole frame rotated 90/180 degrees preserves these dot products.
            sx, sy = current["shoulders"]
            hx, hy = current["hips"]
            axis = ((hx - sx) / torso, (hy - sy) / torso)
            reversals = []
            for side in ("left", "right"):
                if not _visible(actor, side + "_leg"):
                    continue
                knee = _point(current["joints"].get(side + "_knee"))
                ankle = _point(current["joints"].get(side + "_ankle"))
                if not knee or not ankle:
                    continue
                kp = ((knee[0] - hx) * axis[0] + (knee[1] - hy) * axis[1]) / torso
                ap = ((ankle[0] - hx) * axis[0] + (ankle[1] - hy) * axis[1]) / torso
                if kp < -0.35 and ap < -0.6:
                    reversals.append({"side": side, "knee_from_hip_torso_units": round(kp, 4),
                                      "ankle_from_hip_torso_units": round(ap, 4)})
            if reversals:
                issues.append({
                    "code": "leg_reverse_fold_candidate", "frame": frame,
                    "actor_id": actor_id, "needs_visual_review": True,
                    "metrics": {"legs": reversals},
                    "message": "可见腿的膝踝沿躯干轴反折至髋上方；请目视区分跟踪翻转、真实腾空收腿和遮挡。",
                })
    issues.sort(key=lambda issue: (issue["frame"], issue["actor_id"], issue["code"]))
    return {
        "schema": "pose-plan-diagnostics/1", "diagnostic_only": True,
        "status": "NEEDS_VISUAL_REVIEW" if issues else "NO_CANDIDATES",
        "needs_visual_review": bool(issues), "frames": sorted({issue["frame"] for issue in issues}),
        "issues": issues, "analyzed_frame_count": len(rows), "analyzed_actor_instances": instances,
        "skipped": dict(skipped),
        "limits": [
            "没有候选不代表通过运动或生成复核；本报告不授权生成。",
            "只检查屏幕空间躯干坍缩和可见腿反折，不检测所有多肢、握拍断连、手腕跳侧、角色交换或背景残留。",
            "尊重镜头边界和visible_parts；未提供镜头切点时不能保证排除切镜误报。",
            "真实俯身、透视缩短或腾空收腿可能产生候选，必须对照源片后判断。",
            "无头部尺寸、邻帧不足或头部尺寸快速变化时，躯干坍缩规则不会运行。",
        ],
    }
