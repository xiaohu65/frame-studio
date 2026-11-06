"""Run with Blender --background --python. Real meshes, projected source joints.
This scene does not claim recovered camera/depth: camera-space XY is calibrated,
Z and hidden anatomy are explicit modeling estimates for the approved view.
"""
import bpy,json,math,sys,hashlib,time
from pathlib import Path
from mathutils import Vector
args=sys.argv[sys.argv.index('--')+1:];planpath=Path(args[0]);out=Path(args[1]);selection=args[2] if len(args)>2 else 'all'
plan=json.loads(planpath.read_text());W=plan['width'];H=plan['height'];S=100.;engine=args[3] if len(args)>3 else 'BLENDER_EEVEE'
out.mkdir(parents=True,exist_ok=True);(out/'transparent').mkdir(exist_ok=True)
frames=[r['frame'] for r in plan['frames']] if selection=='all' else [int(v) for v in selection.split(',')]
bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
scene.render.engine=engine
if engine=='CYCLES':scene.cycles.device='CPU';scene.cycles.samples=12;scene.cycles.use_denoising=True
else:scene.eevee.taa_render_samples=24
scene.render.resolution_x=W;scene.render.resolution_y=H;scene.render.resolution_percentage=100
scene.render.film_transparent=True;scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA';scene.render.image_settings.color_depth='8'
scene.render.threads_mode='FIXED';scene.render.threads=4
scene.view_settings.view_transform='AgX';scene.view_settings.look='AgX - Medium High Contrast';scene.view_settings.exposure=.3
scene.world=bpy.data.worlds.new('Neutral studio environment');scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.25,.28,.35,1);scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.45
n,d=map(int,plan['fps'].split('/'));scene.render.fps=round(n/d);scene.render.fps_base=scene.render.fps/(n/d);scene.frame_start=min(r['frame'] for r in plan['frames']);scene.frame_end=max(r['frame'] for r in plan['frames'])
scene['source_sha256']=plan.get('source_sha256','');scene['method']='Real 3D articulated meshes; source-calibrated screen-space XY, estimated Z and hidden anatomy';scene['quality']='PREVIEW_REQUIRES_MOTION_AND_OCCLUSION_REVIEW';scene['plan_sha256']=hashlib.sha256(planpath.read_bytes()).hexdigest()
bpy.ops.object.camera_add(location=(0,0,60));camera=bpy.context.object;camera.name='Source projection camera';camera.data.type='ORTHO';camera.data.ortho_scale=W/S;camera.data.clip_end=120;scene.camera=camera
# Camera local -Z points directly at the XY plane; the plate retains original camera motion.
def area(name,loc,power,size):
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.name=name;o.data.energy=power;o.data.shape='DISK';o.data.size=size;o.rotation_euler=(Vector((0,0,0))-o.location).to_track_quat('-Z','Y').to_euler()
area('Large soft key',(-12,16,50),20000,22);area('Soft fill',(15,2,42),9500,18);area('Rim',(0,12,-15),3000,12)
collections={};objects={};materials={};clips={}

def xyz(p,z=0):return Vector((float(p[0])/S-W/(2*S),H/(2*S)-float(p[1])/S,z))
def linear(c):return c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4

def mat_for(a,joint=False):
 key=(a['id'],joint)
 if key in materials:return materials[key]
 m=bpy.data.materials.new(a['id']+('_joint' if joint else '_surface'));m.use_nodes=True;nt=m.node_tree;nt.nodes.clear()
 output=nt.nodes.new('ShaderNodeOutputMaterial');p=nt.nodes.new('ShaderNodeBsdfPrincipled')
 color=[linear(v/255)*(.6 if joint else 1) for v in a.get('color',[185,190,196])]
 p.inputs['Base Color'].default_value=(*color,1);p.inputs['Metallic'].default_value=.4 if not joint else .25;p.inputs['Roughness'].default_value=.34 if not joint else .42
 p.inputs['Coat Weight'].default_value=.15
 geo=nt.nodes.new('ShaderNodeNewGeometry');sep=nt.nodes.new('ShaderNodeSeparateXYZ');nt.links.new(geo.outputs['Position'],sep.inputs[0])
 values={};tests=[]
 for name,axis,op in [('xmin','X','GREATER_THAN'),('xmax','X','LESS_THAN'),('ymin','Y','GREATER_THAN'),('ymax','Y','LESS_THAN')]:
  v=nt.nodes.new('ShaderNodeValue');v.name=name;values[name]=v
  test=nt.nodes.new('ShaderNodeMath');test.operation=op;nt.links.new(sep.outputs[axis],test.inputs[0]);nt.links.new(v.outputs[0],test.inputs[1]);tests.append(test.outputs[0])
 mask=tests[0]
 for test in tests[1:]:
  mul=nt.nodes.new('ShaderNodeMath');mul.operation='MULTIPLY';nt.links.new(mask,mul.inputs[0]);nt.links.new(test,mul.inputs[1]);mask=mul.outputs[0]
 trans=nt.nodes.new('ShaderNodeBsdfTransparent');mix=nt.nodes.new('ShaderNodeMixShader');nt.links.new(mask,mix.inputs[0]);nt.links.new(trans.outputs[0],mix.inputs[1]);nt.links.new(p.outputs[0],mix.inputs[2]);nt.links.new(mix.outputs[0],output.inputs['Surface'])
 materials[key]=m;clips[key]=values;return m

def make(a,name,kind='sphere',joint=False):
 key=(a['id'],name)
 if key in objects:return objects[key]
 if a['id'] not in collections:
  coll=bpy.data.collections.new(a.get('label',a['id']));scene.collection.children.link(coll);collections[a['id']]=coll
 if kind=='rod':
  bpy.ops.mesh.primitive_cone_add(vertices=24,radius1=1,radius2=.86,depth=2)
  obj=bpy.context.object;bev=obj.modifiers.new('Rounded limb edge','BEVEL');bev.width=.18;bev.segments=3
 else:
  bpy.ops.mesh.primitive_uv_sphere_add(segments=40 if name in ['head','chest','pelvis'] else 24,ring_count=28 if name in ['head','chest','pelvis'] else 16,radius=1);obj=bpy.context.object
  if kind=='torso':
   for v in obj.data.vertices:
    v.co.x*=.82+.22*v.co.y;v.co.z*=.68
 obj.name=a['id']+'__'+name;obj['role_id']=a['id'];obj['part_id']=name
 for c in list(obj.users_collection):c.objects.unlink(obj)
 collections[a['id']].objects.link(obj)
 for face in obj.data.polygons:face.use_smooth=True
 obj.data.materials.append(mat_for(a,joint));objects[key]=obj;return obj

def pose_obj(obj,loc,scale,rot=None,visible=True,f=1):
 obj.location=loc;obj.scale=scale;obj.rotation_mode='QUATERNION';obj.rotation_quaternion=rot if rot is not None else (1,0,0,0);obj.hide_render=not visible
 for prop in ['location','scale','rotation_quaternion','hide_render']:obj.keyframe_insert(data_path=prop,frame=f)

def ball(a,name,center,rx,ry,rz,z,angle,f,visible=True,joint=False):
 obj=make(a,name,kind='torso' if name=='chest' else 'sphere',joint=joint);from mathutils import Quaternion
 pose_obj(obj,xyz(center,z),(max(rx,.1)/S,max(ry,.1)/S,max(rz,.1)/S),Quaternion((0,0,1),-angle),visible,f)

def rod(a,name,p0,p1,radius,z,f,visible=True):
 A=xyz(p0,z);B=xyz(p1,z);v=B-A;obj=make(a,name,'rod');pose_obj(obj,(A+B)/2,(max(radius,.1)/S,max(radius,.1)/S,max(v.length/2,.002)),v.to_track_quat('Z','Y'),visible,f)

# Build exactly one stable mesh per role/part, keyframed for every source frame.
for row in plan['frames']:
 f=row['frame'];active=set()
 for a in row['actors']:
  j=a.get('joints',{})
  if not all(k in j for k in ['left_shoulder','right_shoulder','left_hip','right_hip']):continue
  active.add(a['id']);ls=Vector(j['left_shoulder']);rs=Vector(j['right_shoulder']);lh=Vector(j['left_hip']);rh=Vector(j['right_hip']);s=(ls+rs)/2;h=(lh+rh)/2;axis=h-s
  head=a.get('head') or {};e=head.get('ellipse',head);hr=float((e.get('radii') or [0,0])[0]);sw=max((ls-rs).length,hr*2.05,2);hw=max((lh-rh).length,sw*.62);length=max(axis.length,sw*.75);angle=math.atan2(axis.y,axis.x)-math.pi/2
  actor_z=float(a.get('actor_depth_rank_far_to_near',0))*5
  depth={name:actor_z+i*.42 for i,name in enumerate(a.get('depth_order',['right_leg','left_leg','right_arm','torso','pelvis','left_arm','neck','head']))}
  visible=a.get('visible_parts');vis=lambda part:visible is None or part in visible
  box=a.get('clip_box',[0,0,W,H]);clipmax=min(box[3],a.get('clip_y_max',H)) if a.get('clip_y_max') is not None else box[3]
  for joint in [False,True]:
   mat_for(a,joint);values=clips[(a['id'],joint)]
   for key,val in {'xmin':box[0]/S-W/(2*S),'xmax':box[2]/S-W/(2*S),'ymin':H/(2*S)-clipmax/S,'ymax':H/(2*S)-box[1]/S}.items():
    values[key].outputs[0].default_value=val;values[key].outputs[0].keyframe_insert(data_path='default_value',frame=f)
  z=depth.get('torso',1.2)
  # Smooth chest and pelvis volumes; separate waist joint reads bending clearly.
  ball(a,'chest',s+axis*.34,max(sw*.60,hr*1.85,2),length*.55,sw*.40,z,angle,f,vis('torso'))
  ball(a,'waist',s+axis*.77,hw*.50,length*.20,hw*.35,depth.get('pelvis',1.6)-.1,angle,f,vis('torso'),True)
  ball(a,'pelvis',h,max(hw*.67,hr*1.45,2),max(length*.22,hw*.32),hw*.43,depth.get('pelvis',1.6),angle,f,vis('pelvis'))
  radii=a.get('radii',{});ar=radii.get('upper_arm',max(sw*.16,hr*.60));fr=radii.get('forearm',max(sw*.12,hr*.45));wr=radii.get('wrist',max(sw*.075,hr*.30))
  for side in ['right','left']:
   arm=side+'_arm';az=depth.get(arm,2.0);ok=vis(arm)
   if all(side+'_'+n in j for n in ['shoulder','elbow','wrist']):
    sh,el,wi=[j[side+'_'+n] for n in ['shoulder','elbow','wrist']]
    rod(a,side+'_upper_arm',sh,el,ar,az,f,ok);rod(a,side+'_forearm',el,wi,fr,az+.03,f,ok)
    for name,p,r in [('shoulder',sh,ar*.92),('elbow',el,fr*.94),('wrist',wi,wr*.92)]:ball(a,side+'_'+name,p,r,r,r,az+.035,0,f,ok,True)
    palm=a.get('palms',{}).get(side);pc=palm['center'] if palm else wi;pr=palm['radii'] if palm else [wr*1.15,wr*1.55];pa=math.radians(palm.get('angle_degrees',0)) if palm else math.atan2(wi[1]-el[1],wi[0]-el[0])-math.pi/2
    rod(a,side+'_palm_link',wi,pc,wr,az+.04,f,ok);ball(a,side+'_palm',pc,pr[0],pr[1],pr[0]*.50,az+.08,pa,f,ok)
    # Fine, shallow finger separations, kept inside the simple palm envelope.
    for finger in range(4):
     off=(finger-1.5)*pr[0]*.39;cx=pc[0]+math.cos(pa)*off;cy=pc[1]-math.sin(pa)*off
     ball(a,side+f'_finger{finger}',[cx,cy],pr[0]*.20,pr[1]*.70,pr[0]*.25,az+.08+pr[0]/S*.30,pa,f,ok)
    thumb=[pc[0]+math.cos(pa)*pr[0]*.85,pc[1]-math.sin(pa)*pr[0]*.85]
    ball(a,side+'_thumb',thumb,pr[0]*.27,pr[1]*.46,pr[0]*.28,az+.08+pr[0]/S*.26,pa+.6,f,ok)
   leg=side+'_leg';lz=depth.get(leg,0);ok=vis(leg)
   if all(side+'_'+n in j for n in ['hip','knee','ankle']):
    hp,kn,an=[j[side+'_'+n] for n in ['hip','knee','ankle']];th=max(hw*.24,hr*.8);kr=max(hw*.18,hr*.6);ank=max(hw*.13,hr*.45)
    rod(a,side+'_thigh',hp,kn,th,lz,f,ok);rod(a,side+'_shin',kn,an,kr,lz,f,ok)
    for name,p,r in [('hip',hp,th*.9),('knee',kn,kr*.94),('ankle',an,ank*.9)]:ball(a,side+'_'+name,p,r,r,r,lz+.04,0,f,ok,True)
    foot=a.get('feet',{}).get(side);fc=foot['center'] if foot else [an[0]+(ank*.40 if side=='left' else -ank*.40),an[1]-ank*.15];rr=foot.get('radii',[ank*1.65,ank*.65]) if foot else [ank*1.65,ank*.65]
    rod(a,side+'_foot_bridge',an,fc,ank*.82,lz+.06,f,ok)
    ball(a,side+'_foot',fc,rr[0],rr[1],ank*.65,lz+.08,math.radians(foot.get('angle_degrees',0)) if foot else 0,f,ok)
  hc=e.get('center',j.get('head',(s-axis*.3)));r=e.get('radii',[sw*.23,sw*.31]);hang=math.radians(e.get('angle_degrees',0));nb=(head.get('neck') or {}).get('base',(head.get('neck') or {}).get('end',(s+axis*.03)))
  rod(a,'neck',[hc[0],hc[1]+r[1]*.72],nb,r[0]*.43,depth.get('neck',2.4),f,vis('neck'))
  ball(a,'head',hc,r[0],r[1],r[0]*.85,depth.get('head',2.8),hang,f,vis('head'))
 for (aid,name),obj in list(objects.items()):
  if aid not in active:obj.hide_render=True;obj.keyframe_insert(data_path='hide_render',frame=f)
 # For actors not created yet, existing future frames handled with explicit first-frame hidden key below.
for (aid,name),obj in objects.items():
 first=min(r['frame'] for r in plan['frames'] if any(a['id']==aid and a.get('joints') for a in r['actors']))
 if first>scene.frame_start:obj.hide_render=True;obj.keyframe_insert(data_path='hide_render',frame=scene.frame_start)
 # Integer source frames are authoritative. Constant transforms prevent interpolated invented poses.
 if obj.animation_data and obj.animation_data.action:
  for layer in obj.animation_data.action.layers:
   for strip in layer.strips:
    for bag in strip.channelbags:
     for fc in bag.fcurves:
      for kp in fc.keyframe_points:kp.interpolation='CONSTANT'
scene.frame_set(frames[0]);bpy.ops.wm.save_as_mainfile(filepath=str(out/'关节人偶_真实3D可编辑.blend'))
receipt={'engine':scene.render.engine,'device':'CPU' if engine=='CYCLES' else 'EEVEE_RUNTIME_BACKEND_NOT_CLAIMED','samples':scene.cycles.samples if engine=='CYCLES' else scene.eevee.taa_render_samples,'resolution':[W,H],'film_transparent':True,'view_transform':scene.view_settings.view_transform,'mesh_objects':len(objects),'role_collections':list(collections),'source_sha256':plan.get('source_sha256'),'plan_sha256':scene['plan_sha256'],'depth':'estimated camera-space depth ordering','exact_3D_recovery':False,'frame_hashes':{}}
from bpy_extras.object_utils import world_to_camera_view
projection_errors=[]
for row in plan['frames']:
 if row['frame'] not in frames:continue
 scene.frame_set(row['frame'])
 for actor in row['actors']:
  obj=objects.get((actor['id'],'head'));e=(actor.get('head') or {}).get('ellipse',actor.get('head') or {})
  if obj is None or not e.get('center'):continue
  uv=world_to_camera_view(scene,camera,obj.location);actual=Vector((uv.x*W,(1-uv.y)*H));projection_errors.append((actual-Vector(e['center'])).length)
receipt['head_projection_max_error_px']=max(projection_errors,default=0)
assert receipt['head_projection_max_error_px']<.03,'Camera projection no longer matches source pixel coordinates'
start=time.time()
for f in frames:
 scene.frame_set(f);path=out/'transparent'/f'{f:05d}.png'
 if not path.exists():scene.render.filepath=str(path);bpy.ops.render.render(write_still=True)
 receipt['frame_hashes'][str(f)]=hashlib.sha256(path.read_bytes()).hexdigest()
 print(f'PUPPET_3D_FRAME {f} elapsed {time.time()-start:.1f}',flush=True)
(out/'blender_render_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
