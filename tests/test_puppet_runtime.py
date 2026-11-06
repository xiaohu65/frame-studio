import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from PIL import Image
import numpy as np
from backend.puppet import render_puppet,render_frame

class PuppetRuntimeTests(unittest.TestCase):
 def test_missing_pose_preserves_source_and_requires_review(self):
  with tempfile.TemporaryDirectory() as td:
   r=Path(td);src=r/'source.mp4'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=320x240:r=24:d=0.125','-c:v','libx264','-pix_fmt','yuv420p',str(src)],check=True)
   p={'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'fps':'24/1','frames':[{'frame':i,'actors':[{'id':'target','joints':{},'reviewed':False}]} for i in range(1,4)],'reviewed':False}
   plan=r/'plan.json';plan.write_text(json.dumps(p))
   result=render_puppet(src,plan,r/'preview')
   self.assertEqual(result['status'],'VISUAL_REVIEW_REQUIRED');self.assertTrue(Path(result['video_path']).exists())
   qa=json.loads((r/'preview/puppet_validation.json').read_text());self.assertFalse(qa['reviewed']);self.assertEqual(qa['frames_with_missing_actors'],3)
   import cv2
   cap=cv2.VideoCapture(str(src));ok,bgr=cap.read();cap.release();self.assertTrue(ok)
   actual=np.asarray(Image.open(r/'preview/frames/00001.png').convert('RGB'))
   self.assertTrue(np.array_equal(actual,cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)))
 def test_foreground_prop_stays_exact_when_new_proxy_overlaps(self):
  rgb=np.full((180,240,3),45,np.uint8);rgb[50:75,65:95]=[220,40,50]
  mask=np.zeros((180,240),np.uint8);mask[10:165,30:160]=255
  prop=np.zeros_like(mask);prop[50:75,65:95]=255
  j={'left_shoulder':[75,45],'right_shoulder':[120,45],'left_elbow':[70,65],'right_elbow':[130,70],'left_wrist':[80,60],'right_wrist':[135,95],'left_hip':[82,105],'right_hip':[115,105],'left_knee':[80,135],'right_knee':[120,135],'left_ankle':[75,170],'right_ankle':[125,170],'head':[95,25]}
  result,qa=render_frame(rgb,[{'id':'gold','color':[190,157,91],'joints':j}],remove_masks=[mask],prop_mask=prop)
  self.assertTrue(np.array_equal(result[prop==255],rgb[prop==255]));self.assertEqual(qa['opaque_props_changed_pixels'],0)
  self.assertTrue(np.array_equal(result[:,220:],rgb[:,220:]))
if __name__=='__main__':unittest.main()
