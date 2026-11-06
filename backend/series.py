"""Local series cast catalogue. Versions are pinned, never face recognition.

Publishing freezes already imported reference images. An episode binds its own
observed person to that catalogue explicitly; this does not approve a shot,
motion, occlusion or a paid generation request.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil

from . import store


def _id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(value or '')):
        raise ValueError('无效剧集或角色编号')
    return str(value)


def _path(sid):
    return store.DATA / 'series' / _id(sid) / 'series.json'


def get_series(sid):
    with store.LOCK:
        return json.loads(_path(sid).read_text())


def _save(s):
    path = _path(s['id'])
    old = json.loads(path.read_text()) if path.exists() else {}
    s['revision'] = old.get('revision', 0) + 1
    s['updated_at'] = store.now()
    store.save_json(path, s)
    return s


def _revision(s, expected):
    if expected is not None and expected != s.get('revision', 0):
        raise store.RevisionConflict(s.get('revision', 0))


def _project(pid, expected=None):
    p = store.get_project(pid)
    _revision(p, expected)
    if p.get('workflow') != 'replacement':
        raise ValueError('全剧角色库仅关联通用替换项目，历史人偶项目请保留')
    return p


def create_series(name):
    with store.LOCK:
        return _save({'id': store.uid(), 'name': str(name or '未命名剧集')[:100],
                      'created_at': store.now(), 'characters': [], 'episodes': []})


def list_series():
    result = []
    for path in (store.DATA / 'series').glob('*/series.json'):
        s = json.loads(path.read_text())
        result.append({k: s.get(k) for k in ('id', 'name', 'revision', 'updated_at')} |
                      {'character_count': len(s['characters']), 'episode_count': len(s['episodes'])})
    return sorted(result, key=lambda s: s.get('updated_at', ''), reverse=True)


def update_series(sid, body):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, body.get('expected_revision'))
        if 'name' in body:
            s['name'] = str(body['name']).strip()[:100] or s['name']
        # Metadata edits cannot replace frozen versions, files or episode links.
        for change in body.get('characters', []):
            c = next((c for c in s['characters'] if c['id'] == change.get('id')), None)
            if c is None:
                raise ValueError('原剧角色不存在，请从已绑定人物发布角色素材')
            for key in ('name', 'source_description'):
                if key in change:
                    c[key] = str(change[key])[:2000]
            if 'aliases' in change:
                c['aliases'] = _aliases(change['aliases'])
        return _save(s)


def _aliases(value):
    if not isinstance(value, list) or len(value) > 50 or any(not isinstance(v, str) for v in value):
        raise ValueError('角色别名须为不超过50项的文字列表')
    return list(dict.fromkeys(v.strip()[:100] for v in value if v.strip()))


def _episode_number(number):
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 10000:
        raise ValueError('集数须为1到10000之间的整数')
    return number


def _attach(s, p, number, title):
    number = _episode_number(number)
    prior = p.get('series_link') or {}
    if prior.get('series_id') not in (None, s['id']):
        raise ValueError('此项目已属于另一部剧，请使用独立项目，避免串用角色库')
    occupied = next((e for e in s['episodes'] if e['episode_number'] == number and e['project_id'] != p['id']), None)
    if occupied:
        raise ValueError('此集数已有项目，请选择现有项目或另一集数')
    title = str(title or p['name'])[:100]
    s['episodes'] = [e for e in s['episodes'] if e['project_id'] != p['id']]
    s['episodes'].append({'project_id': p['id'], 'episode_number': number, 'title': title})
    s['episodes'].sort(key=lambda e: e['episode_number'])
    p['series_link'] = {'series_id': s['id'], 'episode_number': number, 'title': title}
    store.put_project(p)


def attach_episode(sid, pid, episode_number, title=None, expected_project_revision=None, expected_revision=None):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, expected_revision)
        p = _project(pid, expected_project_revision)
        _attach(s, p, episode_number, title)
        return {'series': _save(s), 'project': store.get_project(pid), 'message': '项目已关联到本剧；人物仍需逐集确认'}


def create_episodes(sid, start, count, expected_revision=None):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, expected_revision)
        start = _episode_number(start)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError('每次创建1到100集空白项目')
        if start + count - 1 > 10000:
            raise ValueError('集数超出范围')
        wanted = set(range(start, start + count))
        if any(e['episode_number'] in wanted for e in s['episodes']):
            raise ValueError('待创建集数包含已有项目，未重复创建')
        created = []
        for number in sorted(wanted):
            name = f'{s["name"]} · 第{number:03d}集'
            p = store.create_project(name)
            _attach(s, p, number, name)
            created.append({'id': p['id'], 'name': name, 'episode_number': number})
        return {'series': _save(s), 'projects': created, 'message': f'已创建{count}集本地空白项目，尚未导入视频或识别人物'}


def _character(s, cid):
    c = next((c for c in s['characters'] if c['id'] == cid), None)
    if not c:
        raise ValueError('全剧角色不存在')
    return c


def _version(c, version):
    if isinstance(version, bool) or not isinstance(version, int):
        raise ValueError('请明确选择角色素材版本')
    v = next((v for v in c['versions'] if v['version'] == version), None)
    if not v:
        raise ValueError('角色素材版本不存在')
    return v


def _version_fingerprint(version):
    value = {k: version.get(k) for k in ('name', 'description', 'color')}
    value['images'] = [a['sha256'] for a in version['images']]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _frozen_images(s, p, images):
    from PIL import Image
    registered = {a['id']: a for a in p.get('assets', [])}
    result = []
    if not images:
        raise ValueError('请先为此人物绑定目标角色并导入定妆图')
    for item in images:
        a = registered.get(item.get('id'))
        if not a or a.get('kind') != 'reference':
            raise ValueError('仅可发布此项目中已登记的人物参考图')
        path = Path(a['path'])
        if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            raise ValueError('角色素材须为支持的图片格式')
        digest = store.digest(path)
        if digest != a.get('sha256'):
            raise ValueError('参考图文件已经改变，请重新导入后再发布版本')
        with Image.open(path) as image:
            image.verify()
        aid = digest
        frozen = _path(s['id']).parent / 'assets' / (digest + path.suffix.lower())
        frozen.parent.mkdir(parents=True, exist_ok=True)
        if frozen.exists() and store.digest(frozen) != digest:
            raise ValueError('冻结角色图片已被外部改写，请检查素材库；不会覆盖旧版本')
        if not frozen.exists():
            shutil.copy2(path, frozen)
        result.append({'id': aid, 'name': a['name'], 'path': str(frozen.resolve()), 'kind': 'reference',
                       'sha256': digest, 'bytes': frozen.stat().st_size, 'meta': a.get('meta', {}),
                       'url': f'/api/series-media/{s["id"]}/{aid}'})
    return result


def publish_character(sid, pid, actor_id, character_id=None, name=None, aliases=None, expected_project_revision=None, expected_revision=None):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, expected_revision)
        p = _project(pid, expected_project_revision)
        if (p.get('series_link') or {}).get('series_id') != sid:
            raise ValueError('请先把项目关联到本剧')
        actor = next((a for a in p['source_actors'] if a['id'] == actor_id), None)
        if actor is None or actor.get('scope') == 'preserve':
            raise ValueError('请先选择需要替换的原片人物')
        role = next((r for r in p['roles'] if r['id'] == actor.get('target_role_id')), None)
        if not role:
            raise ValueError('请先为原片人物绑定目标角色')
        images = _frozen_images(s, p, role.get('images', []))
        if character_id:
            c = _character(s, character_id)
        else:
            c = {'id': store.uid(), 'name': str(name or actor.get('label') or actor_id)[:100],
                 'source_description': actor.get('source_description', ''), 'aliases': _aliases(aliases or []), 'versions': []}
            s['characters'].append(c)
        version = {'version': max((v['version'] for v in c['versions']), default=0)+1,
                   'name': role['name'], 'description': role.get('description', ''), 'color': role.get('color', '#8999aa'),
                   'images': images, 'created_at': store.now(), 'source_project_id': pid, 'source_actor_id': actor_id}
        version['fingerprint'] = _version_fingerprint(version)
        c['versions'].append(version)
        return {'series': _save(s), 'character': c, 'version': version,
                'message': f'角色素材 v{version["version"]} 已冻结；既有剧集继续使用原版本，需明确绑定新版本'}


def bind_actor(sid, pid, actor_id, character_id, version, expected_project_revision=None, expected_revision=None):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, expected_revision)
        c = _character(s, character_id)
        v = _version(c, version)
        p = _project(pid, expected_project_revision)
        if (p.get('series_link') or {}).get('series_id') != sid:
            raise ValueError('此项目尚未关联到本剧')
        actor = next((a for a in p['source_actors'] if a['id'] == actor_id), None)
        if not actor or actor.get('scope') == 'preserve':
            raise ValueError('请选择需要替换的原片人物')
        if any(a['id'] != actor_id and (a.get('series_binding') or {}).get('character_id') == character_id
               for a in p['source_actors'] if a.get('scope') != 'preserve'):
            raise ValueError('本集已有另一人物绑定此原剧角色，请核对身份，避免两人串用')
        registered = []
        for a in v['images']:
            if store.digest(a['path']) != a['sha256']:
                raise ValueError('冻结角色图片已被改写，不能绑定')
            asset = copy.deepcopy(a)
            asset['id'] = store.uid()
            asset['url'] = f'/api/media/{pid}/{asset["id"]}'
            p.setdefault('assets', []).append(asset)
            registered.append(asset)
        prior_rid = actor.get('target_role_id')
        if prior_rid and not any(a['id'] != actor_id and a.get('target_role_id') == prior_rid for a in p['source_actors']):
            rid = prior_rid
        else:
            rid = 'series_' + c['id']
        role = {'id': rid, 'name': v['name'], 'description': v.get('description', ''),
                'color': v.get('color', '#8999aa'), 'images': registered}
        p['roles'] = [r for r in p['roles'] if r['id'] != rid] + [role]
        actor['target_role_id'] = rid
        actor['series_binding'] = {'character_id': c['id'], 'version': v['version'],
                                   'target_fingerprint': v['fingerprint'], 'confirmed': False}
        store.put_project(p)
        return {'series': s, 'project': store.get_project(pid), 'message': '已绑定固定角色版本，请对照本集原片确认身份；镜头定位仍须单独检查'}


def confirm_binding(sid, pid, actor_id, approved, expected_project_revision=None, expected_revision=None):
    with store.LOCK:
        s = get_series(sid)
        _revision(s, expected_revision)
        p = _project(pid, expected_project_revision)
        if (p.get('series_link') or {}).get('series_id') != sid:
            raise ValueError('项目不属于此剧')
        actor = next((a for a in p['source_actors'] if a['id'] == actor_id), None)
        if actor is None or not actor.get('series_binding'):
            raise ValueError('请先绑定原剧角色和素材版本')
        problems = [i for i in project_issues(p) if i.get('actor_id') == actor_id and i['blocking'] and i['code'] != 'SERIES_BINDING_UNCONFIRMED']
        if approved is True and problems:
            raise ValueError(problems[0]['message'])
        actor['series_binding']['confirmed'] = approved is True
        actor['series_binding']['confirmed_at'] = store.now() if approved is True else None
        store.put_project(p)
        return {'series': s, 'project': store.get_project(pid), 'message': '已记录本集角色对应确认；未自动通过定位、遮挡或生成审核'}


def series_asset(sid, aid):
    return next((a for c in get_series(sid)['characters'] for v in c['versions'] for a in v['images'] if a['id'] == aid), None)


def project_issues(p, check_files=True):
    link = p.get('series_link') or {}
    if not link:
        return []
    issues = []
    def issue(code, message, blocking=True, actor_id=None):
        issues.append({'code': code, 'message': message, 'blocking': blocking, **({'actor_id': actor_id} if actor_id else {})})
    try:
        s = get_series(link['series_id'])
    except (OSError, ValueError, KeyError):
        issue('SERIES_MISSING', '关联的全剧角色库无法读取，请重新检查剧集关联')
        return issues
    if not any(e['project_id'] == p['id'] and e['episode_number'] == link.get('episode_number') for e in s['episodes']):
        issue('SERIES_EPISODE_MISMATCH', '本集项目与剧集登记不一致，请重新关联')
    roles = {r['id']: r for r in p.get('roles', [])}
    for actor in p.get('source_actors', []):
        if actor.get('scope') == 'preserve':
            continue
        aid, label = actor['id'], actor.get('label', actor['id'])
        b = actor.get('series_binding') or {}
        if not b:
            issue('SERIES_CHARACTER_NOT_BOUND', f'{label}尚未绑定全剧原角色与固定素材版本', actor_id=aid)
            continue
        try:
            c = _character(s, b.get('character_id'))
            v = _version(c, b.get('version'))
        except ValueError:
            issue('SERIES_VERSION_MISSING', f'{label}绑定的全剧角色版本不存在', actor_id=aid)
            continue
        role = roles.get(actor.get('target_role_id'))
        expected = v['fingerprint']
        if (b.get('target_fingerprint') != expected or not role or _version_fingerprint(role) != expected):
            issue('SERIES_TARGET_MISMATCH', f'{label}的本集人物素材与固定全剧版本不一致，请重新绑定并确认', actor_id=aid)
        if check_files:
            for a in v['images']:
                try: actual = store.digest(a['path'])
                except OSError: actual = None
                if actual != a['sha256']:
                    issue('SERIES_ASSET_CHANGED', f'{label}的冻结角色图片已被外部改写或丢失，请修复角色库', actor_id=aid)
                    break
        if b.get('confirmed') is not True:
            issue('SERIES_BINDING_UNCONFIRMED', f'请确认本集{label}确实对应全剧角色「{c["name"]}」v{v["version"]}', actor_id=aid)
        if c['versions'][-1]['version'] != b['version']:
            issue('SERIES_NEW_VERSION_AVAILABLE', f'{label}仍固定使用v{b["version"]}，角色库已有v{c["versions"][-1]["version"]}；不会自动替换本集素材', False, aid)
    return issues


def dashboard(sid):
    s = get_series(sid)
    episodes = []
    counts = {'episodes': len(s['episodes']), 'without_source': 0, 'needs_mapping': 0, 'needs_confirmation': 0, 'version_updates': 0, 'local_reviewed': 0}
    for e in sorted(s['episodes'], key=lambda e: e['episode_number']):
        try:
            p = store.get_project(e['project_id'])
        except OSError:
            episodes.append({**e, 'status': 'MISSING', 'issues': [{'message': '项目文件不存在', 'blocking': True}]})
            continue
        issues = project_issues(p, check_files=False)
        mappings = [{'actor_id': a['id'], 'label': a.get('label', a['id']), 'scope': a.get('scope'),
                     'binding': a.get('series_binding'), 'target_role_id': a.get('target_role_id')} for a in p.get('source_actors', [])]
        mapped = all(a.get('scope') == 'preserve' or a.get('series_binding') for a in p.get('source_actors', [])) and bool(p.get('source_actors'))
        confirmed = mapped and not any(i['blocking'] for i in issues)
        if not p.get('source'): status = 'NEEDS_SOURCE';counts['without_source'] += 1
        elif not mapped: status = 'NEEDS_MAPPING';counts['needs_mapping'] += 1
        elif not confirmed: status = 'NEEDS_IDENTITY_REVIEW';counts['needs_confirmation'] += 1
        elif (p.get('generic_review') or {}).get('approved'): status = 'LOCAL_REVIEWED';counts['local_reviewed'] += 1
        else: status = 'NEEDS_LOCAL_CHECK'
        if any(i['code'] == 'SERIES_NEW_VERSION_AVAILABLE' for i in issues):counts['version_updates'] += 1
        episodes.append({**e, 'project_name': p['name'], 'project_revision': p.get('revision', 0), 'status': status,
                         'has_source': bool(p.get('source')), 'actor_count': len(mappings), 'segment_count': len(p.get('segments', [])),
                         'mappings': mappings, 'issues': issues, 'has_final': bool(p.get('final'))})
    return {'series': s, 'episodes': episodes, 'counts': counts,
            'identity_method': 'EXPLICIT_EPISODE_BINDING_NOT_AUTOMATIC_FACE_RECOGNITION', 'cloud_generation_authorized': False}


def action(sid, body):
    action_name = body.get('action')
    common = {'expected_revision': body.get('expected_revision')}
    if action_name == 'create_episodes':
        return create_episodes(sid, body.get('start', 1), body.get('count', 1), **common)
    args = {'expected_project_revision': body.get('expected_project_revision'), **common}
    pid = body.get('project_id')
    if action_name == 'attach_episode':
        return attach_episode(sid, pid, body.get('episode_number'), body.get('title'), **args)
    if action_name == 'publish_character':
        return publish_character(sid, pid, body.get('actor_id'), body.get('character_id'), body.get('name'), body.get('aliases'), **args)
    if action_name == 'bind_actor':
        return bind_actor(sid, pid, body.get('actor_id'), body.get('character_id'), body.get('version'), **args)
    if action_name == 'confirm_binding':
        return confirm_binding(sid, pid, body.get('actor_id'), body.get('approved') is True, **args)
    raise ValueError('未知剧集操作')
