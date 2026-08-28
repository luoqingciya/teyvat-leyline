"""绘制 Teyvat Leyline 应用图标（Pillow，代码原生、可复现）。

用法：``uv run --with pillow python tools/make_icon.py``

输出：
  * assets/app.png   1024x1024 高清图
  * assets/app.ico   多尺寸（256/128/64/48/32/16）图标
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
ROOT = Path(__file__).resolve().parents[1]

# 与前端一致的原神风配色
BG_TOP = (19, 26, 48)
BG_BOT = (9, 12, 24)
TEAL = (114, 229, 207)
GOLD = (245, 210, 131)
VIOL = (184, 144, 240)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(c1, c2, w=SIZE, h=SIZE):
    """对角线线性渐变（左上 -> 右下）。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    denom = w + h
    for y in range(h):
        for x in range(w):
            px[x, y] = lerp(c1, c2, (x + y) / denom)
    return img


def tri_gradient(c1, c2, c3, w=SIZE, h=SIZE):
    """三段对角线渐变：c1(上左) -> c2(中) -> c3(下右)，饱和清晰。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    denom = w + h
    for y in range(h):
        for x in range(w):
            t = (x + y) / denom
            col = lerp(c1, c2, t / 0.5) if t < 0.5 else lerp(c2, c3, (t - 0.5) / 0.5)
            px[x, y] = col
    return img


def star_pts(cx, cy, outer, inner, n=8, rot=-math.pi / 2):
    pts = []
    for i in range(n * 2):
        r = outer if i % 2 == 0 else inner
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def main() -> None:
    cx, cy = SIZE / 2, SIZE / 2

    # --- 底层：深蓝地脉渐变背景 + 中心柔光 --------------------------------
    bg = gradient(BG_TOP, BG_BOT).convert("RGBA")

    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    dg = ImageDraw.Draw(glow)
    dg.ellipse((cx - 260, cy - 260, cx + 260, cy + 260), fill=(114, 229, 207, 60))
    dg.ellipse((cx - 200, cy - 180, cx + 180, cy + 240), fill=(245, 210, 131, 42))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    bg.alpha_composite(glow)

    # --- 外环（金色，带光晕）--------------------------------------------
    ring = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ring)
    ring_r = 350
    dr.arc((cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r), 0, 360, fill=GOLD + (255,), width=26)
    ring_glow = ring.filter(ImageFilter.GaussianBlur(22))
    bg.alpha_composite(ring_glow)
    bg.alpha_composite(ring)

    # --- 八芒星，用 teal->gold->violet 渐变填充，并加柔光 ---------------
    outer, inner = 285, 112
    pts = star_pts(cx, cy, outer, inner)

    star_mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(star_mask).polygon(pts, fill=255)

    gl = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(gl).polygon(pts, fill=(127, 232, 212, 255))
    gl = gl.filter(ImageFilter.GaussianBlur(58))
    bg.alpha_composite(gl)

    grad_fill = tri_gradient(TEAL, GOLD, VIOL).convert("RGBA")
    bg.paste(grad_fill, (0, 0), star_mask)

    # --- 顶部中心高光 -----------------------------------------------
    hlt = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(hlt).polygon(pts, fill=(255, 255, 255, 26))
    hlt = hlt.filter(ImageFilter.GaussianBlur(12))
    bg.alpha_composite(hlt)
    # 细小的发光核心
    core_glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(core_glow).ellipse((cx - 40, cy - 40, cx + 40, cy + 40), fill=(214, 246, 236, 140))
    core_glow = core_glow.filter(ImageFilter.GaussianBlur(20))
    bg.alpha_composite(core_glow)
    ImageDraw.Draw(bg).ellipse((cx - 30, cy - 30, cx + 30, cy + 30), fill=(255, 255, 255, 235))

    # --- 装饰星点 ---------------------------------------------------
    sparkle = ImageDraw.Draw(bg)
    for (sx, sy, r, c) in [
        (cx + 250, cy - 210, 9, GOLD),
        (cx - 250, cy - 150, 7, TEAL),
        (cx - 230, cy + 220, 8, VIOL),
        (cx + 240, cy + 180, 6, TEAL),
    ]:
        sparkle.ellipse((sx - r, sy - r, sx + r, sy + r), fill=c + (220,))

    out = ROOT / "assets"
    out.mkdir(parents=True, exist_ok=True)
    bg.save(out / "app.png")

    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    bg.save(out / "app.ico", sizes=ico_sizes)
    print("done:", out / "app.png", out / "app.ico")


if __name__ == "__main__":
    main()
