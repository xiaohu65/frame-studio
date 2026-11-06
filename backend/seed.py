"""Import approved references without mutating earlier deliverables."""
from pathlib import Path
import json
from . import store
from .store import ROOT,DATA,get_project,put_project,add_asset
from .media import probe
WORKSPACE=ROOT.parent

def seed():
 existing=[x for x in store.list_projects() if x['name']=='第二段 · 金银角乒乓对打']
 if existing:return get_project(existing[0]['id'])
 p=store.create_project('第二段 · 金银角乒乓对打');pid=p['id']
 source=Path('/Users/xiaohu/Documents/Codex/2026-10-30/wo/outputs/first_person_shooter_clips/02_01-07_to_01-37.mp4')
 src=add_asset(pid,source,'source',meta=probe(source))
 source_meta=src['meta'];source_meta['duration']=source_meta['duration_seconds']
 p=get_project(pid);p['source']=src;p['source_meta']=source_meta;p['timeline_duration']=683*1001/24000
 p['roles']=[{'id':i,'name':n,'color':c,'images':[],'description':d} for i,n,c,d in [
 ('gold','金角大王','#be9d5b','古金长大衣、酒红高领、金色半拢长卷发；冠上只有一对金角，身份按原erlang轨迹连续追踪。'),
 ('silver','银角大王','#b9bec4','冰银长外套、炭黑高领、银白半扎长卷发；冠上只有一对银角，身份按原sha_seng轨迹连续追踪。'),
 ('referee','金翅大鹏鸟裁判','#3b3e43','同一黑羽金缘鸟首、金喙、绿宝石金冠、全黑西装三件套；只替换裁判，不参加击球。')]]
 p['segments']=[{'id':'A','name':'A段','start':0,'end':352*1001/24000,'reference':None,'generated':None,'status':'NEEDS_REVIEW','reviewed':False,'review_notes':'旧版剪影仅供对照，待新关节人偶复核'},
 {'id':'B','name':'B段','start':336*1001/24000,'end':683*1001/24000,'reference':None,'generated':None,'status':'NEEDS_REVIEW','reviewed':False,'review_notes':'重叠16帧；新关节人偶需复核后再生成'}]
 p['scene']['description']='沿用原球馆、看台人类观众、灯光和道具，观众不妖怪化。';put_project(p)
 refs=json.loads((WORKSPACE/'output/Wan3百炼_第二段金银角_待生成_v1/04_核验与说明/人物素材引用清单.json').read_text())
 for i,row in enumerate(refs['images']):
  a=add_asset(pid,row['upload_file'],'reference');p=get_project(pid);p['roles'][i//3]['images'].append(a);put_project(p)
 paths={'A':WORKSPACE/'output/第二段A_平滑人偶与造型主色_v2/handoff/A_平滑人偶_暖金银灰炭黑_14.68秒.mp4','B':WORKSPACE/'output/第二段B_平滑人偶与造型主色_v1/handoff/B_平滑人偶_暖金银灰炭黑_14.47秒.mp4'}
 for sid,path in paths.items():
  a=add_asset(pid,path,'legacy_reference',meta=probe(path));p=get_project(pid);s=next(s for s in p['segments'] if s['id']==sid);s['legacy_reference']=a;s['reference']=a
  prompt=path.with_name(f'{sid}_完整提示词_造型主色与服装锁定.txt').read_text().rstrip()
  prompt=prompt.replace('椭圆头、短颈、哑光衣身和无细节表面只是动作及身份占位','椭球头、胸腔、骨盆、胶囊四肢和简化关节只是动作及身份占位')
  prompt+='\n\n人偶关节和体块仅用于识别动作与遮挡；最终角色恢复自然人体与对应定妆图服装。原画面被桌沿、球网、其他人物挡住或裁出画面的部位仍保留该遮挡与裁切，不额外补到前景。不生成音乐，后期接入同一条原片连续音轨。'
  p['prompts'][sid]=prompt;put_project(p)
 return get_project(pid)
if __name__=='__main__':print(json.dumps({'id':seed()['id']},ensure_ascii=False))
