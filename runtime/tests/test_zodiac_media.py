import importlib.util
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
from test_zodiac_flow import fixture

class ZodiacBackgroundRotationTests(unittest.TestCase):
    def test_scheduled_slots_rotate_categories_and_avoid_15_recent_clips(self):
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        from zodiac.media import select_assets

        slot_config = {'timezone': 'Asia/Singapore',
                       'slots': ['00:30', '08:00', '19:30']}
        amounts = {'flowing_silk': 9, 'gold_particles': 6,
                   'liquid_ink': 9, 'aurora_lights': 6,
                   'space': 4, 'night_sky': 6}
        backgrounds = {'assets': [
            {'id': f'{category}-{i}', 'category': category}
            for category, size in amounts.items() for i in range(size)
        ]}
        audio = {'assets': [{'id': 'a'}, {'id': 'b'}]}
        first_day = datetime(2026, 11, 8, tzinfo=ZoneInfo('Asia/Singapore'))
        results = []
        for day in range(40):
            for local_slot in slot_config['slots']:
                hour, minute = map(int, local_slot.split(':'))
                dt = first_day + timedelta(days=day, hours=hour, minutes=minute)
                publish_at = dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
                cid = f'zodiac-{len(results)}'
                picked = select_assets(backgrounds, audio, cid,
                                       publish_at=publish_at, slot_config=slot_config)
                self.assertEqual(picked, select_assets(
                    backgrounds, audio, cid, publish_at=publish_at,
                    slot_config=slot_config), 'retries must be deterministic')
                bg, track = picked
                self.assertNotIn(bg['id'], [item['id'] for item in results[-15:]])
                if results:
                    self.assertNotEqual(bg['category'], results[-1]['category'])
                self.assertIn(track, audio['assets'])
                results.append(bg)
        self.assertEqual(len(results), 120)
        self.assertEqual(set(amounts), {bg['category'] for bg in results})

    def test_approved_pexels_download_endpoint_and_host_restrictions(self):
        from zodiac.media import source
        import io
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            item = {'id':'px-34554207',
                    'download_url':'https://www.pexels.com/download/video/34554207/'}
            with patch('zodiac.media.urlopen', return_value=io.BytesIO(b'video-fixture')) as mocked:
                path=source(item,cache,'video')
                self.assertEqual(path.read_bytes(),b'video-fixture')
                self.assertEqual(mocked.call_count,1)
            blocked = [
                'http://www.pexels.com/download/video/34554207/',
                'https://www.pexels.com/download/video/999999/',
                'https://www.pexels.com/download/video/34554207/?redirect=https://evil.example',
                'https://untrusted.example/download/video/34554207/',
            ]
            for url in blocked:
                with self.subTest(url=url),self.assertRaisesRegex(ValueError,'UNAPPROVED_MEDIA_HOST'):
                    source({'id':'px-34554207','download_url':url},cache,'video')

    def test_pending_pixabay_backgrounds_are_excluded_from_all_rotation_modes(self):
        from zodiac.media import select_assets
        background = {'assets': [
            {'id':'safe-1','category':'space'},
            {'id':'pb-381078','category':'constellation_lines','enabled':False},
            {'id':'pb-340950','category':'constellation_lines','enabled':False},
        ]}
        audio = {'assets':[{'id':'music'}]}
        config = {'timezone':'Asia/Singapore','slots':['00:30','08:00','19:30']}
        for n in range(200):
            cid=f'za-{n}'
            a,_=select_assets(background,audio,cid)
            b,_=select_assets(background,audio,cid,
                              publish_at='2026-11-08T00:00:00Z',slot_config=config)
            self.assertEqual(a['id'],'safe-1')
            self.assertEqual(b['id'],'safe-1')
        with self.assertRaisesRegex(ValueError,'EMPTY_ZODIAC_CATALOGUE'):
            select_assets({'assets':background['assets'][1:]},audio,'za-0')

    def test_pixabay_download_requires_exact_approved_video_id_and_host(self):
        from zodiac.media import source
        from unittest.mock import patch
        import io
        with tempfile.TemporaryDirectory() as td:
            video={'id':'pb-381078','download_url':
                   'https://pixabay.com/videos/download/video-381078_large.mp4'}
            with patch('zodiac.media.urlopen',return_value=io.BytesIO(b'demo')):
                self.assertEqual(source(video,Path(td),'video').read_bytes(),b'demo')
            for link in (
                'https://pixabay.com/videos/download/video-340950_large.mp4',
                'https://pixabay.com/videos/download/video-381078_tiny.mp4',
                'https://evil.example/videos/download/video-381078_large.mp4',
                'https://pixabay.com/videos/download/video-381078_large.mp4?to=evil',
                'http://pixabay.com/videos/download/video-381078_large.mp4',
            ):
                with self.subTest(url=link),self.assertRaisesRegex(ValueError,'UNAPPROVED_MEDIA_HOST'):
                    source({'id':'pb-381078','download_url':link},Path(td),'video')

    def test_new_category_rotation_across_scheduled_slots(self):
        from datetime import datetime,timedelta,timezone
        from zoneinfo import ZoneInfo
        from zodiac.media import select_assets
        categories = ('moonlit_reflections','underwater_light','gold_particles',
                      'liquid_ink','space','night_sky','aurora_lights')
        backgrounds={'assets':[{'id':f'{cat}-{k}','category':cat}
                               for cat in categories for k in range(4)]}
        audio={'assets':[{'id':'track'}]}
        config={'timezone':'Asia/Singapore','slots':['00:30','08:00','19:30']}
        day=datetime(2026,11,8,tzinfo=ZoneInfo('Asia/Singapore'))
        chosen=[]
        for n in range(56):
            t=day+timedelta(days=n//3)
            hour,minute=map(int,config['slots'][n%3].split(':'))
            slot=(t+timedelta(hours=hour,minutes=minute)).astimezone(timezone.utc).isoformat()
            bg,_=select_assets(backgrounds,audio,'zodiac-'+str(n),
                               publish_at=slot,slot_config=config)
            self.assertNotIn(bg['id'],[x['id'] for x in chosen[-15:]])
            if chosen:self.assertNotEqual(bg['category'],chosen[-1]['category'])
            chosen.append(bg)
        self.assertEqual(set(categories),set(x['category'] for x in chosen))

    def test_legacy_and_unscheduled_selection_are_stable(self):
        from zodiac.media import select_assets
        backgrounds = {'assets': [{'id':'old-one','category':'space'},
                                  {'id':'old-two','category':'night_sky'}]}
        audio = {'assets':[{'id':'track'}]}
        config = {'timezone':'Asia/Singapore','slots':['00:30','08:00','19:30']}
        baseline=select_assets(backgrounds,audio,'za-test')
        self.assertEqual(baseline,select_assets(
            backgrounds,audio,'za-test',publish_at='2026-11-08T02:00:00Z',
            slot_config=config))


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
            for name,defaults in [('backgrounds.json',v),('audio.json',a)]:
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
            (root/'data'/'backgrounds.json').write_text('{"assets":[]}')
            with self.assertRaises((ValueError,FileNotFoundError)):
                prepare(root,'za-test',root/'work')
