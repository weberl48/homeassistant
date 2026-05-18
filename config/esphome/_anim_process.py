"""Extract per-frame PNGs from the bopping cat + Nyan source GIFs.

PIL's multi-frame GIF save is unreliable across mode conversions (RGB→P often
collapses to 1 frame). Instead we emit one PNG per frame and reference each
separately in the ESPHome `image:` block, then cycle in the display lambda.

Each frame is 200x200 RGB. 4 frames per source. Output to /config/esphome/:
  meme1_f0.png meme1_f1.png meme1_f2.png meme1_f3.png
  nyan_f0.png  nyan_f1.png  nyan_f2.png  nyan_f3.png
"""
from PIL import Image, ImageSequence
import os

sources = ['meme_raw.gif', 'nyan_raw.gif']
prefixes = ['meme1',       'nyan']
target_frames = 4
target_size = 200

for src_name, prefix in zip(sources, prefixes):
    src = Image.open('/config/esphome/' + src_name)
    # IMPORTANT: explicitly seek + copy each frame. ImageSequence.Iterator
    # returns wrappers that all share the source's seek pointer, so collecting
    # them into a list yields N pointers to the same (final) frame.
    frames = []
    for i in range(src.n_frames):
        src.seek(i)
        frames.append(src.copy())
    step = max(1, len(frames) // target_frames)
    kept = frames[::step][:target_frames]
    for i, f in enumerate(kept):
        f = f.convert('RGBA')
        w, h = f.size
        s = min(w, h)
        f = f.crop(((w-s)//2, (h-s)//2, (w+s)//2, (h+s)//2))
        f = f.resize((target_size, target_size), Image.LANCZOS)
        bg = Image.new('RGB', f.size, (0, 0, 0))
        bg.paste(f, mask=f.split()[3] if f.mode == 'RGBA' else None)
        out_path = f'/config/esphome/{prefix}_f{i}.png'
        bg.save(out_path)
        print(f'{prefix}_f{i}.png : {os.path.getsize(out_path)} bytes')
    print(f'  -> {prefix}: {len(kept)} frames at {target_size}x{target_size} (source had {len(frames)} frames)')
