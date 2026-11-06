"""Local, review-gated screen-space articulated mannequin workflow.

This is 2.5D projection reconstruction, not exact monocular 3D recovery. MediaPipe
suggestions are always editable and never mark themselves reviewed. Masks are
removal/occlusion supports, never used as the delivered body geometry.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable
import hashlib, importlib.util, json, math, subprocess

PALETTE={'gold':[190,157,91],'silver':[185,190,196],'referee':[59,62,67]}
NAMES={11:'left_shoulder',12:'right_shoulder',13:'left_elbow',14:'right_elbow',15:'left_wrist',16:'right_wrist',23:'left_hip',24:'right_hip',25:'left_knee',26:'right_knee',27:'left_ankle',28:'right_ankle'}

def capabilities():
    return {'available':all(importlib.util.find_spec(x) for x in ['cv2','numpy','PIL','mediapipe']), 'mode':'editable_2_5D_articulated_proxy','automatic_quality_approval':False,'message':'姿态识别后需复核；暗部、遮挡及小远景可人工校准。'}

def _probe(p):
    j=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)]))
    v=next(s for s in j['streams'] if s['codec_type']=='video'); fps=v.get('avg_frame_rate','24/1')
    a,b=map(int,fps.split('/')); n=int(v.get('nb_frames') or round(float(j['format']['duration'])*a/b))
    return {'width':v['width'],'height':v['height'],'fps':fps,'frame_count':n,'duration':float(j['format']['duration'])}

def _atomic(p,j):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n');t.replace(p)

def analyze_pose(source,output_dir,role_regions=None,progress:Callable|None=None):
    """Suggest poses; return a plan that must be reviewed before rendering.

    role_regions is a list of {id,label,color,box:[x0,y0,x1,y1],first,last}.
    Optional per-frame mask_path/head lets trusted project adapters provide source
    evidence. A missing person is flagged, never rendered as an accepted result.
    """
    import cv2, numpy as np, mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    source=Path(source).resolve();out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    info=_probe(source);model=Path(__file__).resolve().parents[1]/'data/models/pose_landmarker_full.task'
    if not model.exists():raise RuntimeError('缺少本地姿态模型，请运行安装脚本下载官方模型。')
    options=vision.PoseLandmarkerOptions(base_options=python.BaseOptions(model_asset_path=str(model)),running_mode=vision.RunningMode.IMAGE,num_poses=1,min_pose_detection_confidence=.2,min_pose_presence_confidence=.2,output_segmentation_masks=True)
    cap=cv2.VideoCapture(str(source));rows=[]
    regions=role_regions or [{'id':'actor1','label':'人物1','color':PALETTE['gold'],'box':[0,0,info['width'],info['height']],'first':1,'last':info['frame_count']}]
    with vision.PoseLandmarker.create_from_options(options) as detector:
        for f in range(1,info['frame_count']+1):
            ok,bgr=cap.read()
            if not ok:break
            rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB);actors=[]
            for r in regions:
                if not r.get('first',1)<=f<=r.get('last',info['frame_count']):continue
                box=r['box'];x0,y0,x1,y1=[int(v) for v in box];x0=max(0,x0);y0=max(0,y0);x1=min(info['width'],x1);y1=min(info['height'],y1)
                crop=rgb[y0:y1,x0:x1]
                if crop.size==0:continue
                lifted=np.uint8(np.clip((crop.astype(np.float32)/255)**.55*255,0,255))
                det=detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(lifted)))
                joints={};confidence=0;removal_mask=None
                if det.pose_landmarks:
                    lm=det.pose_landmarks[0]
                    joints={name:[float(lm[i].x*(x1-x0)+x0),float(lm[i].y*(y1-y0)+y0)] for i,name in NAMES.items()}
                    joints['head']=[float(lm[0].x*(x1-x0)+x0),float(lm[0].y*(y1-y0)+y0)]
                    confidence=min(float(lm[i].visibility) for i in [11,12,13,14,15,16])
                    if det.segmentation_masks:
                        from PIL import Image
                        local=det.segmentation_masks[0].numpy_view()
                        local=cv2.resize(local,(x1-x0,y1-y0),interpolation=cv2.INTER_LINEAR)
                        matte=np.zeros((info['height'],info['width']),np.uint8)
                        matte[y0:y1,x0:x1]=np.uint8(np.clip((local-.20)/.60,0,1)*255)
                        maskdir=out/'masks'/r['id'];maskdir.mkdir(parents=True,exist_ok=True)
                        removal_mask=str(maskdir/f'{f:05d}.png');Image.fromarray(matte).save(removal_mask)
                if r.get('removal_mask') or r.get('remove_mask_path'):
                    removal_mask=r.get('removal_mask',r.get('remove_mask_path'))
                actors.append({'id':r['id'],'label':r.get('label',r['id']),'color':r.get('color',PALETTE['gold']),'box':[x0,y0,x1,y1],'removal_mask':removal_mask,'remove_mask_path':removal_mask,'joints':joints,'confidence':confidence,'reviewed':False,'estimated':True,'status':'NEEDS_REVIEW' if joints else 'MISSING_POSE','depth_order':['right_leg','left_leg','right_arm','torso','pelvis','left_arm','neck','head']})
            rows.append({'frame':f,'time':(f-1)*int(info['fps'].split('/')[1])/int(info['fps'].split('/')[0]),'actors':actors})
            if progress and f%12==0:progress(f/info['frame_count'],f'分析姿态 {f}/{info["frame_count"]}')
    cap.release()
    plan={'schema':'articulated-puppet/1','source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),**info,'frames':rows,'reviewed':False,'status':'NEEDS_REVIEW','method':'MediaPipe pose suggestions; editable screen-space joints; no exact depth recovery'}
    path=out/'poses.json';_atomic(path,plan)
    return {'pose_path':str(path),'video_path':None,'status':'NEEDS_REVIEW','message':'姿态初稿已生成。请校准关节、角色对应及遮挡，确认后制作人偶。'}

def update_plan(plan_path,updates):
    plan=json.loads(Path(plan_path).read_text())
    if isinstance(updates,dict) and 'frames' in updates:
        for row in updates['frames']:
            target=next((r for r in plan['frames'] if r['frame']==row['frame']),None)
            if target is None:raise ValueError('帧号不存在')
            for actor in row.get('actors',[]):
                old=next((a for a in target['actors'] if a['id']==actor['id']),None)
                if old is None:raise ValueError('角色不存在')
                for k in ['joints','reviewed','depth_order','color','box','head','clip_y_max','radii','estimated','note','removal_mask','remove_mask_path','palms']:
                    if k in actor:old[k]=actor[k]
    if 'reviewed' in updates:plan['reviewed']=bool(updates['reviewed'])
    plan['status']='READY' if plan['reviewed'] else 'NEEDS_REVIEW'
    _atomic(plan_path,plan);return plan

def _field_capsule(yy,xx,a,b,ra,rb):
    import numpy as np
    a=np.array(a);b=np.array(b);v=b-a;u=np.clip(((xx-a[0])*v[0]+(yy-a[1])*v[1])/(v@v+1e-6),0,1)
    dx=xx-a[0]-u*v[0];dy=yy-a[1]-u*v[1];dist=np.sqrt(dx*dx+dy*dy);r=ra+(rb-ra)*u
    alpha=np.clip(r-dist+.5,0,1);z=np.sqrt(np.clip(1-(dist/np.maximum(r,1))**2,0,1))
    light=.78+.19*z-.035*dx/np.maximum(r,1)-.025*dy/np.maximum(r,1)
    return alpha.astype(np.float32),light.astype(np.float32)

def _field_ellipse(yy,xx,center,radii,angle=0):
    import numpy as np
    rx,ry=np.maximum(np.array(radii),1);dx=xx-center[0];dy=yy-center[1];c=math.cos(angle);s=math.sin(angle)
    u=(c*dx+s*dy)/rx;v=(-s*dx+c*dy)/ry;q=np.sqrt(u*u+v*v)
    alpha=np.clip((1-q)*min(rx,ry)+.5,0,1);z=np.sqrt(np.clip(1-u*u-v*v,0,1))
    light=np.clip(.77+.20*z-.055*u-.06*v,.64,1.03)
    return alpha.astype(np.float32),light.astype(np.float32)

def clean_plate(rgb,remove_masks,actors=None):
    """Remove source actors, without compositing original props.

    Return (plate, removal_alpha). For final compositing, restore original
    foreground props once, after 3D alpha-over. This function never applies P.
    Newly revealed surfaces are estimates. A small source-row reconstruction
    around heads avoids Telea dragging bright LED lines into horn-like spikes.
    """
    import cv2,numpy as np
    actors=actors or [];H,W=rgb.shape[:2];remove=np.zeros((H,W),np.uint8)
    for m in remove_masks:remove=np.maximum(remove,np.asarray(m,np.uint8))
    rim=max(2,min(15,round(max((a.get('head',{}).get('ellipse',a.get('head',{})).get('radii',[20,20])[0] for a in actors),default=20)*.10)))
    support=cv2.dilate((remove>0).astype(np.uint8)*255,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(rim*2+1,rim*2+1)))
    donors=cv2.dilate(support,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(15,15)))
    filled=cv2.inpaint(rgb,donors,7,cv2.INPAINT_TELEA).astype(np.float32)
    # Reconstruct source-hidden body background from source pixels on either
    # side of each scanline, inside the registered removal footprint only.
    # Telea alone often drags black-clothing boundary colors into a human-shaped
    # dark ghost. This low-detail fill is an explicit background estimate.
    for y in np.where(np.any(support>0,axis=1))[0]:
        active=np.where(support[y]>0)[0]
        if not len(active):continue
        # Keep separated actors independent; never bridge a wide arena gap.
        split=np.where(np.diff(active)>32)[0]+1
        for group in np.split(active,split):
            left=int(group[0]);right=int(group[-1]);dl=left-1;dr=right+1
            while dl>=0 and donors[y,dl]>0:dl-=1
            while dr<W and donors[y,dr]>0:dr+=1
            if dl<0 or dr>=W:
                # Explicitly registered border-only source actor: its hidden
                # background has only one observable horizontal neighbor. Do
                # not let Telea drag the clipped white shirt back into frame.
                if any(a.get('edge_background_fill')=='one_sided_source_scanline' for a in actors):
                    if dl>=0:filled[y,left:right+1]=np.median(rgb[y,max(0,dl-16):dl+1],axis=0)
                    elif dr<W:filled[y,left:right+1]=np.median(rgb[y,dr:min(W,dr+17)],axis=0)
                continue
            lc=np.median(rgb[y,max(0,dl-16):dl+1],axis=0);rc=np.median(rgb[y,dr:min(W,dr+17)],axis=0)
            u=np.linspace(0,1,right-left+1)[:,None]
            filled[y,left:right+1]=lc*(1-u)+rc*u
    for actor in actors:
        head=actor.get('head',{});e=head.get('ellipse',head)
        if not e.get('center') or ('head' not in actor.get('visible_parts',['head'])):continue
        cx,cy=e['center'];rx,ry=e.get('radii',[10,15])
        x0=max(0,int(cx-rx*1.9));x1=min(W,int(cx+rx*1.9+1));y0=max(0,int(cy-ry*1.6));y1=min(H,int(cy+ry*.65+1))
        if x1<=x0 or y1<=y0:continue
        for y in range(y0,y1):
            active=np.where(support[y,x0:x1]>0)[0]
            if not len(active):continue
            left=x0+int(active.min());right=x0+int(active.max())
            dl=left-1
            while dl>=0 and donors[y,dl]>0:dl-=1
            dr=right+1
            while dr<W and donors[y,dr]>0:dr+=1
            if dl<0 or dr>=W:continue
            # Source background samples at the same scanline continue LED
            # bands horizontally instead of inventing radial head protrusions.
            lc=rgb[y,max(0,dl-2):dl+1].mean(axis=0);rc=rgb[y,dr:min(W,dr+3)].mean(axis=0)
            u=np.linspace(0,1,right-left+1)[:,None]
            filled[y,left:right+1]=lc*(1-u)+rc*u
    alpha=cv2.GaussianBlur(support.astype(np.float32)/255,(0,0),.6)
    plate=np.rint(np.clip(rgb*(1-alpha[...,None])+filled*alpha[...,None],0,255)).astype(np.uint8)
    return plate,alpha

def render_frame(rgb,actors,*,remove_masks=None,prop_mask=None,_crop=True):
    """Render smooth geometric anatomy, preserving original foreground props.

    Removal mattes only reconstruct newly revealed background. The emitted body
    is exclusively ellipsoids/capsules, not the old mask boundary.
    """
    import cv2,numpy as np
    H,W=rgb.shape[:2]
    if _crop and remove_masks and actors:
        import copy
        points=[]
        for a in actors:
            pts=list(a.get('joints',{}).values());hh=a.get('head',{});e=hh.get('ellipse',hh)
            if e.get('center'):
                c=e['center'];rx,ry=e.get('radii',[20,30]);pts += [[c[0]-rx,c[1]-ry],[c[0]+rx,c[1]+ry]]
            points.extend(pts)
        for m in remove_masks:
            ys,xs=np.where(np.asarray(m)>0)
            if len(xs):points.extend([[int(xs.min()),int(ys.min())],[int(xs.max()),int(ys.max())]])
        if points:
            pts=np.asarray(points);pad=max(24,max((a.get('head',{}).get('ellipse',a.get('head',{})).get('radii',[10,10])[0] for a in actors),default=10)*2.5)
            x0=max(0,int(pts[:,0].min()-pad));x1=min(W,int(pts[:,0].max()+pad+1));y0=max(0,int(pts[:,1].min()-pad));y1=min(H,int(pts[:,1].max()+pad+1))
            if (x1-x0)*(y1-y0)<H*W*.70 and x1>x0 and y1>y0:
                ac=copy.deepcopy(actors)
                for a in ac:
                    a['joints']={n:[p[0]-x0,p[1]-y0] for n,p in a.get('joints',{}).items()}
                    h=a.get('head',{});e=h.get('ellipse',h)
                    if e.get('center'):e['center']=[e['center'][0]-x0,e['center'][1]-y0]
                    for n in ['base','end','top','start']:
                        if (h.get('neck') or {}).get(n):h['neck'][n]=[h['neck'][n][0]-x0,h['neck'][n][1]-y0]
                    for p in a.get('palms',{}).values():p['center']=[p['center'][0]-x0,p['center'][1]-y0]
                    if a.get('clip_y_max') is not None:a['clip_y_max']-=y0
                    if a.get('clip_box'):a['clip_box']=[a['clip_box'][0]-x0,a['clip_box'][1]-y0,a['clip_box'][2]-x0,a['clip_box'][3]-y0]
                sub,qa=render_frame(rgb[y0:y1,x0:x1],ac,remove_masks=[np.asarray(m)[y0:y1,x0:x1] for m in remove_masks],prop_mask=None if prop_mask is None else np.asarray(prop_mask)[y0:y1,x0:x1],_crop=False)
                result=rgb.copy();result[y0:y1,x0:x1]=sub
                return result,qa
    yy,xx=np.mgrid[:H,:W];old=rgb.astype(np.float32)
    remove=np.zeros((H,W),np.uint8)
    if remove_masks:
        for m in remove_masks:remove=np.maximum(remove,np.uint8(m))
    else:
        # Generic workflows need a reviewed source-removal mask. Returning raw
        # source outside proxy is safer than silently fabricating that matte.
        raise ValueError('人偶制作需要已复核的人物移除蒙版，避免残留原人物或误改观众。')
    P=np.zeros((H,W),np.float32) if prop_mask is None else np.asarray(prop_mask,np.float32)/255
    plate,erased=clean_plate(rgb,remove_masks,actors)
    base=plate.astype(np.float32)
    combined=np.zeros((H,W),np.float32)
    fg_premult=np.zeros((H,W,3),np.float32);fg_alpha=np.zeros((H,W),np.float32)
    for actor in actors:
        j=actor.get('joints',{})
        if not j:continue
        color=np.array(actor.get('color',PALETTE['gold']),np.float32);parts={}
        ls,rs=[np.array(j[n],float) for n in ['left_shoulder','right_shoulder']]
        lh,rh=[np.array(j[n],float) for n in ['left_hip','right_hip']]
        head_r=actor.get('head',{}).get('ellipse',actor.get('head',{})).get('radii',[0,0])[0]
        shoulder_width=max(np.linalg.norm(ls-rs),head_r*2.05,2);hip_width=max(np.linalg.norm(lh-rh),shoulder_width*.62)
        s=(ls+rs)/2;h=(lh+rh)/2;axis=h-s;length=max(np.linalg.norm(axis),shoulder_width*.75);angle=math.atan2(axis[1],axis[0])-math.pi/2
        r=actor.get('radii',{}); arm_r=r.get('upper_arm',max(shoulder_width*.16,head_r*.60));fore_r=r.get('forearm',max(shoulder_width*.12,head_r*.45));wrist_r=r.get('wrist',max(shoulder_width*.075,head_r*.30))
        for side in ['right','left']:
            pts=[j[f'{side}_{n}'] for n in ['shoulder','elbow','wrist']]
            af,lf=_field_capsule(yy,xx,pts[0],pts[1],arm_r,fore_r)
            a2,l2=_field_capsule(yy,xx,pts[1],pts[2],fore_r,wrist_r);take=a2>af;af=np.maximum(af,a2);lf[take]=l2[take]
            # Palm mass remains small and connected to the source grip.
            a3,l3=_field_ellipse(yy,xx,pts[2],[wrist_r*1.05,wrist_r*1.4],angle)
            take=a3>af;af=np.maximum(af,a3);lf[take]=l3[take]
            palm=actor.get('palms',{}).get(side)
            if palm:
                a3,l3=_field_ellipse(yy,xx,palm['center'],palm['radii'],math.radians(palm.get('angle_degrees',0)))
                a4,l4=_field_capsule(yy,xx,pts[2],palm['center'],wrist_r,min(palm['radii'])*.8)
                for ah,lh in [(a3,l3),(a4,l4)]:
                    take=ah>af;af=np.maximum(af,ah);lf[take]=lh[take]
            parts[f'{side}_arm']=(af,lf,color)
            if all(f'{side}_{n}' in j for n in ['hip','knee','ankle']):
                p=[j[f'{side}_{n}'] for n in ['hip','knee','ankle']]
                a1,l1=_field_capsule(yy,xx,p[0],p[1],max(hip_width*.24,head_r*.8),max(hip_width*.18,head_r*.6));a2,l2=_field_capsule(yy,xx,p[1],p[2],max(hip_width*.18,head_r*.6),max(hip_width*.13,head_r*.45))
                take=a2>a1;a1=np.maximum(a1,a2);l1[take]=l2[take];parts[f'{side}_leg']=(a1,l1,color)
        # Two separate body volumes make bending readable. Rounded torso axes
        # cover shoulder and hip joint roots without a flat cloth silhouette.
        chest=s+axis*.34
        parts['torso']=(*_field_ellipse(yy,xx,chest,[max(shoulder_width*.62,head_r*1.9,3),length*.57],angle),color)
        parts['pelvis']=(*_field_ellipse(yy,xx,h,[max(hip_width*.7,head_r*1.5,3),max(length*.24,hip_width*.32)],angle),color*.94)
        head=actor.get('head',{})
        if head:
            e=head.get('ellipse',head);center=e.get('center',j.get('head'))
            radii=e.get('radii',[shoulder_width*.22,shoulder_width*.30]);hang=math.radians(e.get('angle_degrees',0))
        else:center=j.get('head',(s-axis*.3).tolist());radii=[shoulder_width*.23,shoulder_width*.31];hang=angle
        necktop=np.array(center)+np.array([0,radii[1]*.85]);neckinfo=(head.get('neck') or {}) if head else {};neckbottom=neckinfo.get('base',neckinfo.get('end',(s+axis*.03).tolist()))
        headcolor=color if actor.get('id') in ['dapeng','referee'] else np.array([177,177,177])
        parts['neck']=(*_field_capsule(yy,xx,necktop,neckbottom,radii[0]*.43,radii[0]*.5),headcolor)
        parts['head']=(*_field_ellipse(yy,xx,center,radii,hang),headcolor)
        for name in actor.get('depth_order',['right_leg','left_leg','right_arm','torso','pelvis','left_arm','neck','head']):
            if name not in parts or (actor.get('visible_parts') is not None and name not in actor['visible_parts']):continue
            a,shade,c=parts[name]
            if actor.get('clip_y_max') is not None:a[max(0,int(actor['clip_y_max'])):]=0
            if actor.get('clip_box'):
                bx0,by0,bx1,by1=map(int,actor['clip_box']);a[:max(0,by0)]=0;a[max(0,by1):]=0;a[:,:max(0,bx0)]=0;a[:,max(0,bx1):]=0
            if actor.get('visibility_mask'):
                from PIL import Image
                a*=np.asarray(Image.open(actor['visibility_mask']).convert('L'),np.float32)/255
            base=base*(1-a[...,None])+shade[...,None]*c*a[...,None]
            fg_premult=fg_premult*(1-a[...,None])+shade[...,None]*c*a[...,None]
            fg_alpha=fg_alpha*(1-a)+a
            combined=np.maximum(combined,a)
    # Single prop reconstruction uses the same incremental compositor as the
    # transparent Blender renderer. Do not alpha-over source RGB as a prop.
    straight=fg_premult/np.maximum(fg_alpha[...,None],1e-6)
    rgba=np.concatenate([straight,fg_alpha[...,None]*255],axis=2)
    result,prop_qa=compose_with_props(rgb,plate,rgba,P,change_support=erased)
    allowed=(erased>0)|(combined>0)
    assert not np.any(np.any(result!=rgb,axis=2)&~allowed)
    return result,{'outside_registered_support_changed_pixels':0,'opaque_props_changed_pixels':int(np.count_nonzero(np.any(result!=rgb,axis=2)&(P>=.999))),'support_pixels':int(allowed.sum())}

def render_puppet(source,pose_json_path,output_dir,progress=None,preview=True):
    import cv2,numpy as np
    from PIL import Image
    plan=json.loads(Path(pose_json_path).read_text());out=Path(output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
    if not preview and not plan.get('reviewed'):return {'pose_path':str(pose_json_path),'video_path':None,'status':'NEEDS_REVIEW','message':'正式导出前请先复核姿态与移除蒙版；可先制作预览。'}
    if hashlib.sha256(Path(source).read_bytes()).hexdigest()!=plan['source_sha256']:raise ValueError('原片已改变，不能沿用旧姿态')
    frames=out/'frames';frames.mkdir(exist_ok=True);cap=cv2.VideoCapture(str(source));records=[]
    for row in plan['frames']:
        f=row['frame'];cap.set(cv2.CAP_PROP_POS_FRAMES,f-1);ok,bgr=cap.read()
        if not ok:raise ValueError(f'无法读取第{f}帧')
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        valid_actors=[a for a in row['actors'] if a.get('joints') and a.get('removal_mask',a.get('remove_mask_path'))]
        skipped=[a['id'] for a in row['actors'] if a not in valid_actors]
        if valid_actors:
            masks=[]
            for actor in valid_actors:
                if not actor.get('removal_mask',actor.get('remove_mask_path')):raise ValueError(f'第{f}帧角色{actor["id"]}缺少已复核移除蒙版')
                masks.append(np.asarray(Image.open(actor.get('removal_mask',actor.get('remove_mask_path'))).convert('L')))
            pm=np.asarray(Image.open(row['prop_mask']).convert('L')) if row.get('prop_mask') else None
            result,qa=render_frame(rgb,valid_actors,remove_masks=masks,prop_mask=pm)
        else:result=rgb;qa={'outside_registered_support_changed_pixels':0,'opaque_props_changed_pixels':0}
        Image.fromarray(result).save(frames/f'{f:05d}.png',compress_level=2);records.append({'frame':f,'missing_actor_ids_source_preserved':skipped,**qa})
        if progress and f%12==0:progress(f/len(plan['frames']),f'制作关节人偶 {f}/{len(plan["frames"])}')
    cap.release();video=out/'关节人偶参考.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-framerate',plan['fps'],'-start_number',str(plan['frames'][0]['frame']),'-i',str(frames/'%05d.png'),'-frames:v',str(len(plan['frames'])),'-an','-c:v','libx264','-preset','medium','-crf','16','-pix_fmt','yuv420p','-movflags','+faststart',str(video)],check=True)
    report={'status':'ENCODING_PASS_VISUAL_REVIEW_REQUIRED','frames':len(records),'reviewed':bool(plan.get('reviewed')),'preview':bool(preview),'frames_with_missing_actors':sum(bool(r['missing_actor_ids_source_preserved']) for r in records),'video_path':str(video),'records':records,'exact_3D_recovery':False,'generation_quality':'UNVERIFIED'}
    _atomic(out/'puppet_validation.json',report)
    return {'pose_path':str(pose_json_path),'video_path':str(video),'validation_path':str(out/'puppet_validation.json'),'frames_with_missing_actors':report['frames_with_missing_actors'],'status':'VISUAL_REVIEW_REQUIRED','message':'关节人偶已制作，请检查动作、握拍和遮挡后再生成新视频。'}

def prop_layers(source_rgb,protection,effect_polygon=None):
    """Estimate prop coverage and source scene behind it; no compositing yet.

    Ordinary protection is a soft coverage estimate. White streaks need their
    separate source polygon and color-line unmix, not another copy of source RGB.
    """
    import cv2,numpy as np
    src=np.asarray(source_rgb,np.float32);H,W=src.shape[:2]
    P=np.asarray(protection,np.float32)
    if P.max(initial=0)>1:P=P/255
    p=np.clip(P,0,1);erase=(p>.001).astype(np.uint8)*255
    E=np.zeros((H,W),np.uint8);white_p=np.zeros((H,W),np.float32)
    if effect_polygon is not None:
        cv2.fillPoly(E,[np.asarray(effect_polygon,np.int32)],255)
        B=cv2.inpaint(np.uint8(src),E,5,cv2.INPAINT_TELEA).astype(np.float32);vec=255-B
        white_p=np.clip(np.sum((src-B)*vec,axis=2)/(np.sum(vec*vec,axis=2)+1e-5),0,1);white_p[E==0]=0
        p=np.maximum(p,white_p);erase=np.maximum(erase,E)
    B0=cv2.inpaint(np.uint8(src),erase,5,cv2.INPAINT_TELEA).astype(np.float32)
    B0[p<.001]=src[p<.001]
    if effect_polygon is not None:
        use=(E>0)&(white_p<.92)
        unmix=(src-white_p[...,None]*255)/np.maximum(1-white_p[...,None],.03)
        B0[use]=np.clip(unmix[use],0,255)
    return {'coverage':p,'old_background':B0,'erase_props_mask':erase,'white_coverage':white_p}

def compose_with_props(source_rgb,clean_plate_rgb,rgba,protection,*,frame=None,effect_polygon=None,change_support=None):
    """Composite transparent 3D once behind source props, without alpha².

    Rnew = Rsource + support * (1-p) * (new_background - old_background).
    p=1 retains the exact original source pixel. No prop coordinates are moved.
    Source RGB is never AlphaOver'ed as if it were an isolated prop texture.
    """
    import cv2,numpy as np
    src=np.asarray(source_rgb,np.float32);clean=np.asarray(clean_plate_rgb,np.uint8);fg=np.asarray(rgba,np.float32)
    if fg.shape[-1]!=4:raise ValueError('人物渲染必须为透明RGBA')
    if fg.max(initial=0)<=1:fg=fg*255
    a=np.clip(fg[...,3]/255,0,1)
    layers=prop_layers(source_rgb,protection,effect_polygon)
    p=layers['coverage'];B0=layers['old_background'];erase=layers['erase_props_mask']
    # Clean plate still contains source props where actor removal was not active.
    # Remove them only from the background used beneath the new 3D alpha layer.
    C=cv2.inpaint(clean,erase,5,cv2.INPAINT_TELEA).astype(np.float32)
    T=C*(1-a[...,None])+fg[...,:3]*a[...,None]
    if change_support is None:
        support=(np.any(clean!=np.uint8(src),axis=2)|(a>0)).astype(np.float32)
    else:
        support=((np.asarray(change_support)>0)|(a>0)).astype(np.float32)
    delta=(1-p[...,None])*support[...,None]*(T-B0)
    result=np.rint(np.clip(src+delta,0,255)).astype(np.uint8)
    result[p>=.999]=np.uint8(src[p>=.999]);result[support<=0]=np.uint8(src[support<=0])
    return result,{'frame':frame,'method':'incremental_single_prop_composite','formula':'Rsource + support*(1-p)*(T-B0)','opaque_props_changed_pixels':int(np.count_nonzero(np.any(result!=np.uint8(src),axis=2)&(p>=.999))),'outside_registered_support_changed_pixels':int(np.count_nonzero(np.any(result!=np.uint8(src),axis=2)&(support<=0))),'source_prop_pixels_translated':0,'white_effect_unmixed':effect_polygon is not None}
