import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
import array

from backend.media import assemble, extract_audio, probe, trim_video, MediaError
from backend.provider import WanClient, ProviderError, estimate_cost


class FakeWan(WanClient):
    calls=0
    fail_unknown=False
    def _upload(self,path):
        return 'oss://dashscope-instant/test/'+path.name
    def _request_json(self,method,url,body=None,extra_headers=None):
        if method=='POST':
            self.calls+=1
            assert body['parameters']['watermark'] is False
            if any(m['url'].startswith('oss://') for m in body['input']['media']):
                assert extra_headers['X-DashScope-OssResourceResolve']=='enable'
            if self.fail_unknown:raise ProviderError('网络响应丢失')
            return {'output':{'task_id':'test-task-001','task_status':'PENDING'},'request_id':'test-request'}
        return {'output':{'task_id':'test-task-001','task_status':'RUNNING'}}


class ProviderTests(unittest.TestCase):
    def test_price_includes_input_and_output(self):
        q=estimate_cost([{'type':'reference_video','duration_seconds':14.472792}],14.472792,resolution='1080P')
        self.assertAlmostEqual(q['estimate_yuan'],34.7347,places=4)
        self.assertEqual(q['reserved_yuan'],36)
        self.assertFalse(q['free_quota_assumed'])

    def test_default_480p_quote_request_and_budget_are_consistent(self):
        with tempfile.TemporaryDirectory() as folder:
            client=FakeWan({'api_key':'fake'},Path(folder))
            report=client.preflight('走路',[],{'duration':2})
            self.assertEqual(report['parameters']['resolution'],'480P')
            self.assertEqual(report['estimate']['reserved_yuan'],.6)
            self.assertEqual(estimate_cost([],2)['reserved_yuan'],.6)
            for resolution,amount in (('720P',1.2),('1080P',2.4)):
                report=client.preflight('走路',[],{'duration':2,'resolution':resolution})
                self.assertEqual(report['parameters']['resolution'],resolution)
                self.assertEqual(report['estimate']['reserved_yuan'],amount)
            with self.assertRaises(ProviderError):
                client.submit('走路',[],{'duration':2},approved_budget_yuan=.5,request_key='default-low-budget')
            self.assertEqual(client.calls,0)

    def test_key_stays_out_of_state_and_receipts(self):
        with tempfile.TemporaryDirectory() as d:
            client=FakeWan({'api_key':'secret-test-never-persist'},Path(d))
            media=[{'type':'reference_image','url':'https://example.com/character.png'}]
            a=client.submit('图1走路',media,{'duration':2},approved_budget_yuan=10,request_key='A')
            b=client.submit('图1走路',media,{'duration':2},approved_budget_yuan=10,request_key='A')
            self.assertEqual(client.calls,1)
            self.assertEqual(a,b)
            all_text=''.join(p.read_text() for p in Path(d).glob('*.json'))+json.dumps(a)
            self.assertNotIn('secret-test-never-persist',all_text)
            self.assertEqual(client.poll(a['task_id'])['status'],'RUNNING')

    def test_unknown_never_resubmits_and_budget_is_shared(self):
        with tempfile.TemporaryDirectory() as d:
            c=FakeWan({'api_key':'fake'},Path(d));c.fail_unknown=True
            media=[{'type':'reference_image','url':'https://example.com/a.png'}]
            a=c.submit('图1走路',media,{'duration':2,'resolution':'1080P'},approved_budget_yuan=3,request_key='A')
            self.assertEqual(a['status'],'UNKNOWN')
            c.submit('图1走路',media,{'duration':2,'resolution':'1080P'},approved_budget_yuan=3,request_key='A')
            self.assertEqual(c.calls,1)
            with self.assertRaises(ProviderError):c.submit('图1走路',media,{'duration':2,'resolution':'1080P'},approved_budget_yuan=3,request_key='B')
            self.assertEqual(c.calls,1)

    def test_budget_and_watermark_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            c=FakeWan({'api_key':'fake'},Path(d))
            with self.assertRaises(ProviderError):c.submit('走路',[],{'duration':2,'resolution':'1080P'},approved_budget_yuan=1,request_key='A')
            with self.assertRaises(ProviderError):c.preflight('走路',[],{'watermark':True})
            self.assertEqual(c.calls,0)
            with self.assertRaises(ProviderError):WanClient({'api_key':'fake','base_url':'https://example.com/api/v1'},Path(d))


class MediaTests(unittest.TestCase):
    def test_fractional_fps_trim_preserves_source_rate_and_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'fractional.mp4'
            subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=s=320x240:r=24000/1001','-frames:v','96','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
            result=trim_video(source,root/'trimmed.mp4',24*1001/24000,48*1001/24000,width=320,height=240,fps='24000/1001')
            self.assertEqual(result['fps_rational'],'24000/1001')
            self.assertTrue(result['trim_validation']['original_fps_preserved'])
            self.assertAlmostEqual(result['duration_seconds'],48*1001/24000,delta=.00001)
            count=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(root/'trimmed.mp4')]))
            self.assertEqual(int(count['streams'][0]['nb_read_frames']),48)

    def test_overlap_and_single_continuous_audio_preserve_pulses(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4'
            # Four visible seconds, audio pulses at half-second / integer beats.
            subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=s=320x240:r=30:d=4','-f','lavfi','-i',
                            "aevalsrc=0.5*sin(2*PI*880*t)*between(mod(t\\,1)\\,0.49\\,0.53):s=48000:d=4",'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True)
            original=extract_audio(source,root/'original.wav',start=0,duration=4)
            self.assertFalse(original['music_isolated'])
            self.assertTrue(original['contains_original_dialogue_and_effects'])
            trim_video(source,root/'A.mp4',0,2.5,width=320,height=240)
            trim_video(source,root/'B.mp4',2,2,width=320,height=240)
            output=assemble([{'path':str(root/'A.mp4'),'source_start':0,'source_end':2.5},
                             {'path':str(root/'B.mp4'),'source_start':2,'source_end':4}],root/'complete.mp4',
                            music_path=root/'original.wav',target_duration=4,width=320,height=240)
            self.assertAlmostEqual(output['media']['duration_seconds'],4,delta=.04)
            self.assertEqual(output['plan'][1]['overlap_trimmed_seconds'],.5)
            self.assertFalse(output['audio']['per_clip_audio_joined'])
            self.assertEqual(sum(i['output_frames'] for i in output['plan']),120)
            decoded=root/'decoded.wav'
            subprocess.run(['ffmpeg','-v','error','-n','-i',str(root/'complete.mp4'),'-map','0:a:0','-ac','1','-ar','48000','-c:a','pcm_s16le',str(decoded)],check=True)
            with wave.open(str(decoded),'rb') as w:
                rate=w.getframerate();samples=array.array('h',w.readframes(w.getnframes()))
            for beat in [.5,1.5,2.5,3.5]:
                lo=int((beat-.06)*rate);hi=int((beat+.06)*rate)
                peak=max(range(lo,hi),key=lambda i:abs(samples[i]))/rate
                self.assertLess(abs(peak-beat),.04)
                silent=samples[int((beat-.2)*rate):int((beat-.1)*rate)]
                self.assertLess(max(map(abs,silent)),300)

    def test_anchor_retime_and_audio_video_start_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'offset.mp4'
            subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','color=c=gold:s=320x240:r=30:d=2','-f','lavfi','-i','sine=frequency=600:duration=2.1:sample_rate=48000',
                            '-filter:v','setpts=PTS+0.041/TB','-fps_mode','vfr','-c:v','libx264','-c:a','aac',str(source)],check=True)
            meta=probe(source)
            self.assertGreater(meta['video_start_time'],0)
            a=extract_audio(source,root/'aligned.wav',start=meta['video_start_time'],duration=1.9)
            self.assertAlmostEqual(a['source_start_seconds'],meta['video_start_time'])
            result=assemble([{'path':str(source),'source_start':0,'source_end':2,
                              'anchors':[{'generated_seconds':0,'source_seconds':0},{'generated_seconds':.8,'source_seconds':1},{'generated_seconds':meta['video_duration_seconds'],'source_seconds':2}]}],
                            root/'anchored.mp4',width=320,height=240)
            self.assertEqual(result['beat_sync'],'ANCHORS_APPLIED_REVIEW_REQUIRED')
            self.assertEqual(sum(p['output_frames'] for p in result['plan']),60)
            with self.assertRaises(MediaError):assemble([{'path':str(source),'source_start':0,'source_end':1},{'path':str(source),'source_start':1.2,'source_end':2}],root/'gap.mp4',width=320,height=240)


if __name__=='__main__':unittest.main()
