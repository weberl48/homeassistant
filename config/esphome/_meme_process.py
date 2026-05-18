from PIL import Image, ImageSequence
import os

sources = ['meme_raw.gif', 'meme2_raw.gif', 'meme3_raw.gif', 'meme4_raw.gif', 'meme5_raw.gif', 'nyan_raw.gif']
outs    = ['meme1.png',    'meme2.png',     'meme3.png',     'meme4.png',     'meme5.png',    'nyan.png']

for src_name, out_name in zip(sources, outs):
    src = Image.open('/config/esphome/' + src_name)
    frames = list(ImageSequence.Iterator(src))
    # Choose the middle frame for the most representative still
    f = frames[len(frames) // 2].convert('RGBA')
    w, h = f.size
    s = min(w, h)
    f = f.crop(((w-s)//2, (h-s)//2, (w+s)//2, (h+s)//2))
    f = f.resize((240, 240), Image.LANCZOS)
    bg = Image.new('RGB', f.size, (0, 0, 0))
    bg.paste(f, mask=f.split()[3] if f.mode == 'RGBA' else None)
    out_path = '/config/esphome/' + out_name
    bg.save(out_path)
    print(out_name, ':', os.path.getsize(out_path), 'bytes;', len(frames), 'source frames')
