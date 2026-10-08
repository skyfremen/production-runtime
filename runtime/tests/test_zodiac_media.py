import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
from test_zodiac_flow import fixture

@unittest.skipUnless(importlib.util.find_spec('PIL') and shutil.which('ffmpeg'), 'optional media prerequisites missing')
class CatalogueMediaTests(unittest.TestCase):
    def test_exact_quarter_second_overlap_produces_six_second_audio(self):
        import wave
        from zodiac import media
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir()
            raw=root/'audio.wav';video=root/'video.mp4';video.write_bytes(b'fixture')
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=220:sample_rate=48000','-t','6.25','-ac','2',str(raw)],check=True)
            v={'method':'circular_crossfade','playback_speed':.5,'source_duration_seconds':3.5,'crossfade_seconds':1,'crossfade_curve':'cosine','blend_position':'start','playback_direction':'forward'}
            a={**v,'playback_speed':1,'source_duration_seconds':6.25,'crossfade_seconds':.25}
            for name,defaults in [('background.json',v),('audio.json',a)]:
                (root/'data'/name).write_text(json.dumps({'version':1,'output':{'duration_seconds':6},'loop_defaults':defaults,'assets':[{'id':'fixture','loop':{'source_start_seconds':0,'gain_db':0}}]}))
            original=media.run
            def audio_only(args):
                if args[0]=='ffprobe':return json.dumps({'streams':[{'width':1080,'height':1920}]})
                if '-frames:v' in args:return ''
                return original(args)
            with patch.object(media,'source',side_effect=lambda asset,cache,kind:video if kind=='video' else raw),patch.object(media,'run',side_effect=audio_only):
                _,music,_=media.prepare(root,'za-test',root/'work')
            with wave.open(str(music)) as w:
                self.assertEqual(w.getnframes(),288000)
                self.assertEqual(w.getnchannels(),2)

    def test_catalogue_selection_is_stable_and_approved(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.media'), 'catalogue media module missing')
        from zodiac.media import select_assets
        assets=[{'id':'one'},{'id':'two'}]
        a,b=select_assets({'assets':assets},{'assets':assets},'za-test')
        self.assertIn(a,assets)
        self.assertEqual((a,b),select_assets({'assets':assets},{'assets':assets},'za-test'))

    def test_catalogue_render_contains_moving_background_and_audio(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.media'), 'catalogue media module missing')
        from zodiac.cards import generate
        from zodiac.content import validate
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);source=root/'source.mp4';audio=root/'source.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=1080x1920:rate=30','-t','6','-c:v','libx264','-preset','ultrafast','-threads','2',str(source)],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=220:sample_rate=48000','-t','6','-ac','2',str(audio)],check=True)
            p=root/'validated.json';p.write_text(json.dumps(validate(fixture())))
            with patch('zodiac.media.prepare',return_value=(source,audio,{'background_id':'synthetic','audio_id':'synthetic'})):
                generate(p,root/'artifact',catalogue_root=root)
            m=json.loads((root/'artifact'/'manifest.json').read_text())
            v=m['videos'][0]
            self.assertEqual(v['audio_streams'],1)
            self.assertEqual(v['frames'],180)
            self.assertEqual(v['background_id'],'synthetic')
            self.assertTrue(m['music'])
            self.assertFalse(m['youtube_upload_enabled'])
            mp4=root/'artifact'/v['file']
            def frame(t):
                return subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(mp4),'-frames:v','1','-vf','scale=108:192','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            self.assertNotEqual(frame(0),frame(3),'background must move behind stationary text')

    def test_partial_catalogues_fail_instead_of_silent_fallback(self):
        self.assertIsNotNone(importlib.util.find_spec('zodiac.media'), 'catalogue media module missing')
        from zodiac.media import prepare
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir()
            (root/'data'/'background.json').write_text('{"assets":[]}')
            with self.assertRaises((ValueError,FileNotFoundError)):
                prepare(root,'za-test',root/'work')
