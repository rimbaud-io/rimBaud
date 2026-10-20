#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Paint rimBaud wallpaper / greeter / logo from the live desk palette. Exact text."""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BG = os.path.join(HERE, "overlay/usr/share/backgrounds/rimbaud")
OUT_PIX = os.path.join(HERE, "overlay/usr/share/pixmaps")
OUT_GRUB = os.path.join(HERE, "overlay/usr/share/grub")

BG = (2, 8, 4)
NAVY = (3, 22, 10)
PANEL = (4, 18, 8)
BORDER = (13, 59, 31)
GREEN = (0, 255, 106)
TEXT = (157, 255, 192)
MUTED = (61, 122, 85)
GOLD = (240, 185, 11)
CYAN = (42, 159, 214)
GRID = (0, 22, 10)


def font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    cands = []
    if mono:
        cands += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]
    cands += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    if not mono:
        cands = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ] + cands
    for p in cands:
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def paint_field(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    step = max(24, w // 80)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=GRID)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=GRID)
    scan = Image.new("RGB", (w, h), BG)
    sd = ImageDraw.Draw(scan)
    for y in range(0, h, 4):
        sd.line([(0, y), (w, y)], fill=(0, 0, 0))
    img = Image.blend(img, scan, 0.18)
    return img


def paint_nav(img: Image.Image, title: str, sub: str) -> None:
    w, h = img.size
    nav_h = max(64, h // 14)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, nav_h], fill=NAVY)
    d.line([(0, nav_h - 1), (w, nav_h - 1)], fill=BORDER, width=2)
    lamp = max(10, nav_h // 6)
    cx, cy = nav_h // 2, nav_h // 2
    d.ellipse([cx - lamp, cy - lamp, cx + lamp, cy + lamp], fill=GREEN)
    title_f = font(max(22, nav_h // 3), mono=True)
    sub_f = font(max(14, nav_h // 5), mono=True)
    d.text((cx + lamp * 2 + 12, cy - nav_h // 5), title, fill=CYAN, font=title_f)
    tw = d.textlength(title, font=title_f) if hasattr(d, "textlength") else 280
    d.text((cx + lamp * 2 + 12 + tw + 24, cy - nav_h // 6), sub, fill=GOLD, font=sub_f)


def paint_mark(img: Image.Image, mark: str, tag: str) -> None:
    w, h = img.size
    d = ImageDraw.Draw(img)
    big = font(max(72, w // 12))
    small = font(max(22, w // 48), mono=True)
    bbox = big.getbbox(mark) if hasattr(big, "getbbox") else (0, 0, 800, 120)
    tw = bbox[2] - bbox[0]
    x = (w - tw) // 2
    y = int(h * 0.42)
    glow = Image.new("RGB", img.size, BG)
    gd = ImageDraw.Draw(glow)
    gd.text((x, y), mark, fill=(0, 40, 20), font=big)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(8, w // 200)))
    img.paste(Image.blend(img, glow, 0.35))
    d = ImageDraw.Draw(img)
    d.text((x, y), mark, fill=TEXT, font=big)
    gap = max(20, h // 70)
    ly = y + bbox[3] + gap
    d.line([(x, ly), (x + tw, ly)], fill=GOLD, width=max(2, w // 900))
    tb = small.getbbox(tag) if hasattr(small, "getbbox") else (0, 0, 400, 24)
    ttw = tb[2] - tb[0]
    tx = max(24, (w - ttw) // 2)
    d.text((tx, ly + max(12, h // 90)), tag, fill=MUTED, font=small)


def hsv_rgb(h: float) -> tuple[int, int, int]:
    """Same 24-step wheel as the mothership card in rimbaud-meta."""
    h = h % 360.0
    x = 1.0 - abs((h / 60.0) % 2.0 - 1.0)
    if h < 60:
        rf, gf, bf = 1.0, x, 0.0
    elif h < 120:
        rf, gf, bf = x, 1.0, 0.0
    elif h < 180:
        rf, gf, bf = 0.0, 1.0, x
    elif h < 240:
        rf, gf, bf = 0.0, x, 1.0
    elif h < 300:
        rf, gf, bf = x, 0.0, 1.0
    else:
        rf, gf, bf = 1.0, 0.0, x
    return (int(round(rf * 255)), int(round(gf * 255)), int(round(bf * 255)))


def paint_rainbow_frame(img: Image.Image, thickness: int = 8) -> None:
    """Static frame of the mothership HOME halo. GRUB cannot animate it."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    step = 10
    t = thickness

    def band(x0: int, y0: int, x1: int, y1: int, along: int) -> None:
        d.rectangle([x0, y0, x1, y1], fill=hsv_rgb(((along % 24) + 24) % 24 * 15.0))

    for x in range(0, w, step):
        band(x, 0, min(w, x + step) - 1, t - 1, x // 2)
    for x in range(0, w, step):
        band(x, h - t, min(w, x + step) - 1, h - 1, (w + h + x) // 2)
    for y in range(0, h, step):
        band(0, y, t - 1, min(h, y + step) - 1, (w + y) // 2)
    for y in range(0, h, step):
        band(w - t, y, w - 1, min(h, y + step) - 1, y // 2)


def wallpaper(path: str, w: int, h: int, title: str, sub: str, tag: str, rainbow: bool = False) -> None:
    img = paint_field(w, h)
    paint_nav(img, title, sub)
    paint_mark(img, "rimBaud", tag)
    d = ImageDraw.Draw(img)
    d.rectangle([0, h - 4, w, h], fill=BORDER)
    if rainbow:
        paint_rainbow_frame(img)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    print("wrote", path, img.size)


def logo(path: str, size: int = 256) -> None:
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    m = max(6, size // 24)
    d.rectangle([m, m, size - m - 1, size - m - 1], outline=BORDER, width=max(2, size // 64))
    d.rectangle([m * 2, m * 2, size - m * 2, size // 4], fill=NAVY)
    lamp = size // 16
    d.ellipse([size // 8, size // 10, size // 8 + lamp * 2, size // 10 + lamp * 2], fill=GREEN)
    f = font(max(28, size // 5), mono=True)
    d.text((size // 8 + lamp * 3, size // 12), "rB", fill=CYAN, font=f)
    f2 = font(max(18, size // 8), mono=True)
    d.text((size // 8, size // 2), "rimBaud", fill=TEXT, font=f2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG", optimize=True)
    print("wrote", path)


def main() -> None:
    os.makedirs(OUT_BG, exist_ok=True)
    os.makedirs(OUT_PIX, exist_ok=True)
    os.makedirs(OUT_GRUB, exist_ok=True)
    wallpaper(
        os.path.join(OUT_BG, "desktop.png"),
        3840,
        2160,
        "rimBaud  //  MATRIX",
        "internal appliance",
        "GET only   ·   unique wallets",
    )
    wallpaper(
        os.path.join(OUT_BG, "greeter.png"),
        1920,
        1080,
        "rimBaud  //  MATRIX",
        "sign in",
        "first boot expires the install password",
    )
    wallpaper(
        os.path.join(OUT_GRUB, "rimbaud.png"),
        1024,
        768,
        "rimBaud",
        "boot",
        "live  ·  install unattended",
        rainbow=True,
    )
    logo(os.path.join(OUT_PIX, "rimbaud.png"), 256)
    logo(os.path.join(OUT_PIX, "rimbaud-48.png"), 48)


if __name__ == "__main__":
    main()
