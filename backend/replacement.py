"""Local contracts for source-video appearance replacement.

Actor IDs belong to this project, not to the Wan API.  Locator keyframes are
review aids and are deliberately never described as tracked pixel masks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re

WAN_MODELS = ("wan3.0-video", "wan3.0-video-prime")
PRESERVATION = {
    "motion": "original", "camera": "original", "timing": "original",
    "audio": "original", "props": "original", "unselected_people": "original",
}
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def normalize_project(project):
    """Fill local metadata without migrating the previous puppet workflow."""
    p = project
    p.setdefault("workflow", "legacy")
    p.setdefault("revision", 0)
    p.setdefault("source_actors", [])
    p.setdefault("cloud_gate", {"upload_allowed": False, "generation_allowed": False})
    p.setdefault("generic_review", None)
    p.setdefault("preparation", None)
    p.setdefault("scene", {"mode": "preserve", "images": [], "description": "", "regions": []})
    p["scene"].setdefault("regions", [])
    if p["workflow"] == "replacement":
        p.setdefault("material_confirmation", None)
        p.setdefault("preservation", copy.deepcopy(PRESERVATION))
        settings = p.setdefault("settings", {})
        for key, value in {"model": WAN_MODELS[0], "resolution": "480P", "ratio": "adaptive",
                           "watermark": False, "audio": False, "prompt_extend": False, "duration": -1}.items():
            settings.setdefault(key, value)
    return p


def material_confirmation_state(project, current_fingerprint=None):
    """Read one material acknowledgement; candidate acceptance remains separate.

    The public fingerprint is always the current input version, even before a
    confirmation exists. Old six-check reviews remain readable without inventing
    or rewriting any of their checks.
    """
    current = current_fingerprint or fingerprint(project)
    result = {"current": False, "fingerprint": current, "confirmed_at": None,
              "source": None, "legacy": False}
    record = project.get("material_confirmation") or {}
    if record.get("confirmed") is True and record.get("fingerprint") == current:
        result.update(current=True, confirmed_at=record.get("confirmed_at"), source=record.get("source"))
        return result
    legacy = project.get("generic_review") or {}
    if legacy.get("approved") is True and legacy.get("fingerprint") == current:
        result.update(current=True, confirmed_at=legacy.get("reviewed_at"), source="legacy_review", legacy=True)
    return result


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def valid_box(box):
    return (isinstance(box, list) and len(box) == 4 and all(_number(v) for v in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


def _identifiers(rows, label):
    if not isinstance(rows, list):
        raise ValueError(f"{label}须为列表")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not _ID.fullmatch(str(row.get("id", ""))) or row["id"] in ids:
            raise ValueError(f"{label}编号须唯一，且仅使用字母、数字、下划线或短横线")
        ids.add(row["id"])
    return ids


def validate_project(p, registered=None):
    """Validate editable structure; incomplete drafts remain editable."""
    if p.get("workflow") != "replacement":
        return p
    roles = p.get("roles", [])
    role_ids = _identifiers(roles, "目标角色")
    actors = p.get("source_actors", [])
    _identifiers(actors, "原片人物")
    segments = {s["id"]: s for s in p.get("segments", [])}
    for role in roles:
        if not isinstance(role.get("images", []), list):
            raise ValueError("角色参考图须为列表")
    for actor in actors:
        label = str(actor.get("label") or actor["id"])
        if actor.get("scope", "person") not in ("head", "person", "preserve"):
            raise ValueError(f"{label}的替换范围无效")
        if actor.get("target_role_id") and actor["target_role_id"] not in role_ids:
            raise ValueError(f"{label}绑定的目标角色不存在，请先取消该绑定")
        observations = actor.get("observations", [])
        if not isinstance(observations, list):
            raise ValueError(f"{label}的定位关键帧须为列表")
        seen = set()
        for observation in observations:
            sid = observation.get("segment_id")
            if sid not in segments:
                raise ValueError(f"{label}的定位关键帧引用了不存在的片段")
            stamp = observation.get("time")
            seg = segments[sid]
            if not _number(stamp) or not seg["start"] - 1e-6 <= stamp <= seg["end"] + 1e-6:
                raise ValueError(f"{label}的定位时间须位于对应片段范围内，使用原片秒数")
            key = (sid, stamp)
            if key in seen:
                raise ValueError(f"{label}在同一片段、同一时间只能保存一个定位状态")
            seen.add(key)
            if observation.get("visibility", "visible") not in ("visible", "occluded", "out"):
                raise ValueError(f"{label}的可见状态无效")
            box = observation.get("box")
            if observation.get("visibility", "visible") == "visible" or box is not None:
                if not valid_box(box):
                    raise ValueError(f"{label}的定位框须为0到1之间的[左,上,右,下]，且有正面积")
            if not isinstance(observation.get("confirmed", False), bool):
                raise ValueError("人物定位的确认状态须为是或否")
    scene = p.get("scene", {})
    if scene.get("mode", "preserve") not in ("preserve", "replace"):
        raise ValueError("场景处理方式无效")
    _identifiers(scene.get("regions", []), "场景区域")
    for region in scene.get("regions", []):
        sid = region.get("segment_id")
        if sid not in segments:
            raise ValueError("场景区域引用了不存在的片段")
        seg = segments[sid]
        start, end = region.get("start"), region.get("end")
        if not all(_number(v) for v in (start, end)) or not seg["start"] - 1e-6 <= start < end <= seg["end"] + 1e-6:
            raise ValueError("场景区域起止时间须位于对应片段内")
        if not valid_box(region.get("box")):
            raise ValueError("场景定位框须为0到1之间的[左,上,右,下]，且有正面积")
        if not isinstance(region.get("confirmed", False), bool):
            raise ValueError("场景区域的确认状态须为是或否")
    settings = p.get("settings", {})
    if settings.get("model", WAN_MODELS[0]) not in WAN_MODELS:
        raise ValueError("仅允许当前已适配的 Wan 视频模型；新增版本须先适配与验证")
    if settings.get("resolution", "480P") not in (None, "480P", "720P", "1080P"):
        raise ValueError("分辨率须为480P、720P或1080P")
    if p.get("preservation", PRESERVATION) != PRESERVATION:
        raise ValueError("本流程固定保留原片动作、镜头、节奏、原声、道具及未选人物")
    if p.get("music", {}).get("mode", "original") != "original":
        raise ValueError("通用替换流程固定使用原片完整声音")
    if registered is not None:
        for asset in [a for role in roles for a in role.get("images", [])] + scene.get("images", []):
            if not isinstance(asset, dict) or asset.get("id") not in registered:
                raise ValueError("参考素材必须先导入本机项目")
    return p


def _digest(path):
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def _asset_snapshot(asset):
    if not asset:
        return None
    return {k: asset.get(k) for k in ("id", "sha256")} | {
        "current_sha256": _digest(asset.get("path"))}


def fingerprint(p, sid=None):
    """Exclude task/UI state; include actual input bytes, prompts and bindings."""
    snapshot = {
        "workflow": p.get("workflow"), "source": _asset_snapshot(p.get("source")),
        "source_meta": p.get("source_meta", {}), "timeline_duration": p.get("timeline_duration"),
        "segments": [{k: s.get(k) for k in ("id", "start", "end")} |
                     {"edit_mask": _asset_snapshot(s.get("edit_mask")),
                      "protection_mask": _asset_snapshot(s.get("protection_mask")),
                      "mask_keyframes": s.get("mask_keyframes", [])} for s in p.get("segments", [])],
        "source_actors": p.get("source_actors", []),
        "roles": [{k: r.get(k) for k in ("id", "name", "description", "color")} |
                  {"images": [_asset_snapshot(a) for a in r.get("images", [])]} for r in p.get("roles", [])],
        "scene": {k: v for k, v in p.get("scene", {}).items() if k != "images"} |
                 {"images": [_asset_snapshot(a) for a in p.get("scene", {}).get("images", [])]},
        "settings": p.get("settings", {}), "prompts": p.get("prompts", {}),
        "preservation": p.get("preservation", PRESERVATION), "segment_id": sid,
    }
    if p.get('series_link'):
        snapshot['series_link'] = p['series_link']
    if p.get('native_720p_source_id'):
        snapshot['native_720p_source_id'] = p['native_720p_source_id']
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def edit_mask_optional(project):
    scopes = {actor.get("scope", "person") for actor in project.get("source_actors", [])}
    return (project.get("workflow") == "replacement"
            and project.get("scene", {}).get("mode", "preserve") == "preserve"
            and scopes - {"preserve"} == {"head"})


def plan(p, sid=None, preserved_evidence=None):
    """Return a reviewable local plan, never an upload or inference request."""
    issues, mappings, references = [], [], []
    def issue(code, message, blocking=True, **extra):
        issues.append({"code": code, "message": message, "blocking": blocking, **extra})
    try:
        validate_project(p)
    except (ValueError, KeyError, TypeError) as error:
        issue("INVALID_PLAN", str(error))
    segments = [s for s in p.get("segments", []) if sid is None or s["id"] == sid]
    if not p.get("source"):
        issue("NO_SOURCE", "请先导入原片")
    elif not _digest(p["source"].get("path")):
        issue("SOURCE_MISSING", "原片文件不存在或无法读取，请重新导入")
    elif p["source"].get("sha256") and _digest(p["source"].get("path")) != p["source"]["sha256"]:
        issue("SOURCE_CHANGED", "原片文件内容已改变，请重新导入并核对人物")
    if not segments:
        issue("NO_SEGMENTS", "请先设置有效片段")
    mask_optional = edit_mask_optional(p)
    if mask_optional and any(not s.get("edit_mask") for s in segments):
        issue("MASK_OPTIONAL", "保留原场景且仅换头时，编辑蒙版可选。请逐镜核对人物位置及手部等前景遮挡。", False)
    for segment in segments:
        if not segment.get("edit_mask"):
            if not mask_optional:
                issue("MASK_REQUIRED", f"{segment.get('name', segment['id'])}尚无精确编辑蒙版；请导入白色编辑、黑色保留的逐帧蒙版，定位框不能代替蒙版", segment_id=segment["id"])
        elif not _digest(segment["edit_mask"].get("path")):
            issue("MASK_MISSING", f"{segment.get('name', segment['id'])}的编辑蒙版无法读取", segment_id=segment["id"])
        elif segment["edit_mask"].get("sha256") and _digest(segment["edit_mask"].get("path")) != segment["edit_mask"]["sha256"]:
            issue("MASK_CHANGED", f"{segment.get('name', segment['id'])}的编辑蒙版文件内容已改变，请重新导入", segment_id=segment["id"])
    roles = {r["id"]: r for r in p.get("roles", [])}
    used_roles, targets = [], {}
    actors = p.get("source_actors", [])
    from . import identity_evidence, shot_analysis
    evidence = preserved_evidence or {}
    current = fingerprint(p) if evidence else None
    evidence = {key: value for key, value in evidence.items() if value and value.get('fingerprint') == current}
    for actor in actors:
        scope = actor.get("scope", "person")
        actor_id = actor["id"]
        label = actor.get("label") or actor_id
        selected_observations = [o for o in actor.get("observations", []) if any(s["id"] == o.get("segment_id") for s in segments)]
        active = False
        for segment in segments:
            observations = [o for o in selected_observations if o.get("segment_id") == segment["id"]]
            preserved = evidence.get(segment['id'])
            if not observations and not (preserved and identity_evidence.covers(preserved, preserved['first_frame']-1, preserved['last_frame'])):
                issue("ACTOR_NOT_LOCATED", f"{label}在{segment.get('name', segment['id'])}尚无人物位置确认；有编辑区域的镜头需对照原片确认", actor_id=actor_id, segment_id=segment["id"])
            for observation in observations:
                index = shot_analysis.frame_boundary(observation['time'], p['source_meta']['fps_rational']) if preserved else None
                if observation.get("confirmed") is not True and not (preserved and identity_evidence.covers(preserved, index, index+1)):
                    issue("IDENTITY_UNCONFIRMED", f"请确认{label}在原片{observation.get('time', 0):.3f}秒的身份与可见状态", actor_id=actor_id, segment_id=segment["id"])
                if observation.get("visibility", "visible") != "out":
                    active = True
            if any(o.get("visibility") == "occluded" for o in observations):
                issue("OCCLUSION_REVIEW", f"{label}在{segment.get('name', segment['id'])}有遮挡记录，请核对遮挡前后身份及前景物体", False, actor_id=actor_id, segment_id=segment["id"])
        role = roles.get(actor.get("target_role_id"))
        if scope != "preserve":
            target_context = {"actor_id": actor_id, "reference_kind": "person"}
            if role:
                target_context["role_id"] = role["id"]
            if sid is not None and segments:
                target_context["segment_id"] = sid
            if not str(actor.get("source_description", "")).strip():
                issue("ACTOR_DESCRIPTION_MISSING", f"请填写{label}在原片中的外观与位置特征", actor_id=actor_id)
            if not role:
                issue("TARGET_NOT_BOUND", f"请为{label}绑定目标角色", **target_context)
            else:
                if not str(role.get("name", "")).strip():
                    issue("TARGET_NAME_MISSING", f"{label}绑定的目标角色尚未命名", **target_context)
                if not role.get("images"):
                    issue("TARGET_IMAGE_MISSING", f"请为目标角色{role.get('name') or role['id']}导入参考图", **target_context)
                other = targets.get(role["id"])
                if other and other != actor_id:
                    issue("DUPLICATE_TARGET", f"{label}与另一原片人物绑定了同一目标角色，请核对人物对应", **target_context)
                targets[role["id"]] = actor_id
                if active and role["id"] not in used_roles:
                    used_roles.append(role["id"])
        mappings.append({"actor_id": actor_id, "label": label, "source_description": actor.get("source_description", ""),
                         "scope": scope, "target_role_id": role["id"] if role else None,
                         "target_name": role.get("name") if role else None, "active": active,
                         "observations": selected_observations, "reference_labels": []})
    # Simultaneous overlapping locators are evidence for extra occlusion review,
    # not proof that either locator is a segmentation mask.
    overlaps = set()
    for i, actor in enumerate(actors):
        for other in actors[i + 1:]:
            for a in actor.get("observations", []):
                if a.get("visibility", "visible") != "visible" or not valid_box(a.get("box")):
                    continue
                for b in other.get("observations", []):
                    if (a.get("segment_id") == b.get("segment_id") and a.get("segment_id") in {s["id"] for s in segments}
                        and abs(a.get("time", 0) - b.get("time", 0)) < .05 and b.get("visibility", "visible") == "visible"
                        and valid_box(b.get("box")) and _overlap(a["box"], b["box"])):
                        overlaps.add((a["segment_id"], actor["id"], other["id"]))
    for segment_id, first, second in sorted(overlaps):
        issue("ACTOR_OVERLAP", f"{first}与{second}的定位框重叠，请逐帧核对交叉与遮挡；定位框不代表像素蒙版", False, segment_id=segment_id)
    scene = p.get("scene", {})
    if scene.get("mode") == "replace":
        if not scene.get("images"):
            issue("SCENE_IMAGE_MISSING", "请导入新场景参考图")
        if not str(scene.get("description", "")).strip():
            issue("SCENE_DESCRIPTION_MISSING", "请说明场景替换范围及原布局、接触位置的保留要求")
        for segment in segments:
            regions = [r for r in scene.get("regions", []) if r.get("segment_id") == segment["id"]]
            if not regions:
                issue("SCENE_REGION_MISSING", f"请标记{segment.get('name', segment['id'])}的场景替换区域", segment_id=segment["id"])
            elif any(r.get("confirmed") is not True for r in regions):
                issue("SCENE_REGION_UNCONFIRMED", "请确认场景区域及前景人物、道具保护范围", segment_id=segment["id"])
    if not any(m["scope"] != "preserve" and m["active"] for m in mappings) and scene.get("mode") != "replace":
        issue("NOTHING_SELECTED", "当前片段未选择需要替换的人物或场景")
    for role_id in used_roles:
        role = roles[role_id]
        for asset in role.get("images", []):
            references.append({"reference": f"图{len(references) + 1}", "kind": "person", "role_id": role_id,
                               "role_name": role.get("name"), "asset_id": asset.get("id"), "name": asset.get("name"),
                               "path": asset.get("path"), "sha256": asset.get("sha256"), "url": asset.get("url")})
    if scene.get("mode") == "replace":
        for asset in scene.get("images", []):
            references.append({"reference": f"图{len(references) + 1}", "kind": "scene", "role_id": None,
                               "role_name": "场景", "asset_id": asset.get("id"), "name": asset.get("name"),
                               "path": asset.get("path"), "sha256": asset.get("sha256"), "url": asset.get("url")})
    def reference_context(item):
        context = {"asset_id": item.get("asset_id"), "role_id": item.get("role_id"),
                   "reference": item["reference"], "reference_kind": item["kind"]}
        if sid is not None and segments:
            context["segment_id"] = sid
        owners = {mapping["actor_id"] for mapping in mappings if mapping["scope"] != "preserve"
                  and mapping["active"] and mapping["target_role_id"] == item.get("role_id")}
        if item["kind"] == "person" and len(owners) == 1:
            context["actor_id"] = next(iter(owners))
        return {key: value for key, value in context.items() if value is not None}

    for item in references:
        actual = _digest(item.get("path"))
        if not actual:
            issue("REFERENCE_MISSING", f"参考素材{item['reference']}无法读取，请重新导入", **reference_context(item))
        elif item.get("sha256") and actual != item["sha256"]:
            issue("REFERENCE_CHANGED", f"参考素材{item['reference']}文件内容已改变，请重新导入并核对", **reference_context(item))
    owners = {}
    for item in references:
        if item["kind"] != "person":
            continue
        identity = item.get("sha256") or item.get("asset_id")
        if identity and identity in owners and owners[identity] != item["role_id"]:
            issue("SHARED_PERSON_REFERENCE", f"{item['reference']}与另一目标角色使用了相同的人物图，请核对角色素材归属", **reference_context(item))
        owners[identity] = item["role_id"]
    for mapping in mappings:
        mapping["reference_labels"] = [r["reference"] for r in references if r["role_id"] == mapping["target_role_id"] and r["kind"] == "person"]
    if len(references) > 10:
        issue("TOO_MANY_REFERENCES", f"当前计划有{len(references)}张参考图，当前 Wan 接口最多10张；请减少每人的参考图或拆分编辑计划")
    if p.get('series_link'):
        from . import series
        issues.extend(series.project_issues(p))
    issue("LOCATORS_ONLY", "人物和场景框用于身份定位与人工复核，尚不等同逐帧像素蒙版；Wan不提供原片人物ID硬绑定", False)
    return {"schema_version": 1, "workflow": "replacement", "project_id": p.get("id"), "revision": p.get("revision", 0),
            "segment_id": sid, "segments": [{k: s.get(k) for k in ("id", "name", "start", "end")} for s in segments],
            "issues": issues, "mappings": mappings, "references": references,
            "fingerprint": fingerprint(p, sid), "ready": not any(i["blocking"] for i in issues),
            "preservation": copy.deepcopy(PRESERVATION), "positioning_method": "manual_keyframes",
            "pixel_masks_ready": bool(segments) and all(s.get("edit_mask") for s in segments)
            and not any(i["code"] in ("MASK_MISSING", "MASK_CHANGED") for i in issues),
            "cloud_gate": copy.deepcopy(p.get("cloud_gate", {})),
            "generation_submitted": False}


def generate_prompt(p, s):
    report = plan(p, s["id"])
    lines = [f"编辑视频1。仅替换下列指定人物及明确指定的场景外观。保持原片{s['end']-s['start']:.3f}秒的动作、镜头和节奏。",
             "人物对应以原片外观、位置、行为及以下时间定位共同判断；项目编号仅为本地核对标签，不作为可见文字加入画面。"]
    for mapping in report["mappings"]:
        actor = next(a for a in p.get("source_actors", []) if a["id"] == mapping["actor_id"])
        if mapping["scope"] == "preserve":
            lines.append(f"保留人物{mapping['label']}：{mapping['source_description']}，外观和动作均沿用原片。")
            continue
        if not mapping["active"]:
            continue
        anchors = []
        for observation in mapping["observations"]:
            stamp = max(0, observation["time"] - s["start"])
            visibility = {"visible": "可见", "occluded": "被遮挡", "out": "已出画"}.get(observation.get("visibility", "visible"), "待核对")
            box = observation.get("box")
            detail = f"，画面归一化位置[{','.join(f'{v:.3f}' for v in box)}]" if box else ""
            anchors.append(f"片段{stamp:.3f}秒{visibility}{detail}")
        role = next((r for r in p.get("roles", []) if r["id"] == mapping["target_role_id"]), {})
        area = "仅头部、面孔和发型，原身体及服装保留" if mapping["scope"] == "head" else "人物外观，保持原身体比例、姿态和全部动作"
        refs = "、".join(mapping["reference_labels"]) or "尚未绑定参考图"
        lines.append(f"{mapping['label']}（{mapping['source_description']}）→{mapping['target_name'] or '待绑定角色'}，使用{refs}；替换范围：{area}。定位：{'；'.join(anchors)}。{role.get('description', '')}")
    scene = p.get("scene", {})
    if scene.get("mode") == "replace":
        labels = "、".join(r["reference"] for r in report["references"] if r["kind"] == "scene")
        lines.append(f"场景外观参考{labels}：{scene.get('description', '')}。保持原机位、透视、运动空间、地面接触及互动位置；前景人物和道具按原片遮挡顺序保护。")
    else:
        lines.append("场景、背景、观众、照明和未指定人物保持原片，参考图背景不进入视频。")
    lines.extend([
        "严格保留每个人的身份对应、骨架运动、步法、出招顺序、速度、力量方向、头部朝向、表情及口型时序；人物交叉、出入画或遮挡前后不得互换身份。",
        "严格保留原镜头构图、焦距观感、机位、摄影机运动、切镜、帧内相对位置、接触点和接触时点。原武器、道具及其轨迹不变；手、武器与其他人物在头部前方经过时，保持原前后遮挡关系。",
        "不增加、删除或重排动作、人物、道具、镜头，不补全原本出画或被遮挡的身体，不改变时长和播放速度，不增加慢动作、停顿或片尾展示。",
        "输出不添加音乐、音效、对白、字幕、角色标签或水印。最终成片由本地按原片时间轴接回一条连续原声音轨。",
    ])
    return "\n\n".join(lines)
