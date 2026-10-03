# Scripted *illustrative* conversation used to render docs/*.png and docs/demo.gif.
# Usage: MOCK_FONT_DIR=file:///path/to/fonts python3 render.py scenario ../   (needs Chrome + ffmpeg + Pillow)
from render import *
SPEC=dict(name='SaveIt', icon='📥', c1='#2f8cf0', c2='#7b5cf5', scenarios=[
 dict(steps=[
  U('<span class="lnk">https://www.instagram.com/reel/EXAMPLE123/</span>'),
  B('🔎 <b>Instagram · Reel</b> شناسایی شد.\nکیفیت را انتخاب کن:', id='q',
    buttons=[['⬇️ بهترین کیفیت (حداکثر ۵۰MB)'],['1080p','720p','480p'],['🎵 MP3','M4A']]),
  dict(press='q', label='⬇️ بهترین کیفیت (حداکثر ۵۰MB)'),
  B('⏳ در حال دانلود…', id='p'),
  B('📤 در حال ارسال…', id='p'),
  B(id='p', media=video('#f09433','#bc1888','reel','0:23','12.4 MB','🌅'),
    text='🤖 <span class="lnk">@saveit_downloader_bot</span>'),
 ]),
 dict(steps=[
  U('<span class="lnk">https://vm.tiktok.com/EXAMPLE/</span>'),
  B('🔎 <b>TikTok</b> شناسایی شد · بدون واترمارک ✅', id='q',
    buttons=[['⬇️ ویدیو (حداکثر ۵۰MB)'],['720p','480p'],['🎵 MP3']]),
  dict(press='q', label='⬇️ ویدیو (حداکثر ۵۰MB)'),
  B(media=video('#25f4ee','#fe2c55','tt','0:15','8.1 MB','🎶'), text='✅ آماده شد · <i>no watermark</i>'),
 ]),
 dict(steps=[
  U('https://youtu.be/EXAMPLE'),
  B('🔎 <b>YouTube</b>\nSelect quality / انتخاب کیفیت:', id='q',
    buttons=[['⬇️ Best (max 50 MB)'],['720p','480p','360p ⚠️'],['🎵 MP3','M4A']]),
  dict(press='q', label='🎵 MP3'),
  B(media=audio('#ff5f6d','#ffc371','Example Song','Example Artist','3:42'), text='🎼 title · artist · album · cover (ID3)'),
  B('📝 <b>Lyrics</b> · 🔎 Other results', buttons=[['📝 Lyrics','🔎 Other results']]),
 ]),
])
