# -*- coding: utf-8 -*-
"""Готовит растры для WXQSetupUI: фон шапки (арт игры, затемнён, сглажен, с дизерингом против бэндинга) и знак.
Запуск: python prep_assets.py  -> ../assets/header.png, ../assets/mark.png
"""
import os
import random

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = os.path.normpath(os.path.join(HERE, "..", "..", "brand"))
OUT = os.path.normpath(os.path.join(HERE, "..", "assets"))
BG = (14, 14, 15)  # graphite-900
W, H, S = 880, 128, 2  # размер шапки в DIP и масштаб растра


def smooth(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def header():
    src = Image.open(os.path.join(BRAND, "source", "game-art-sun-1050006114_814406544a14a246.png")).convert("RGBA")
    flat = Image.new("RGBA", src.size, BG + (255,))
    flat.alpha_composite(src)
    flat = flat.convert("RGB")
    # окно арта: кольцо справа, широкая полоса с мягким переходом
    cw = 1700
    ch = int(cw * (H * S) / (W * S))
    cx, cy = 1000, 880
    box = (cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2)
    art = flat.crop(box).resize((W * S, H * S), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.9))
    px = art.load()
    random.seed(7)
    out = Image.new("RGB", (W * S, H * S))
    po = out.load()
    for y in range(H * S):
        fy = smooth(0.35 * H * S, H * S, y) ** 1.3  # низ растворяется в фон окна
        for x in range(W * S):
            r, g, b = px[x, y]
            lum = (0.3 * r + 0.59 * g + 0.11 * b) / 255.0
            lum = min(1.0, max(0.0, (lum - 0.25) * 1.5))
            r, g, b = (int(0.75 * lum * c + 0.25 * v) for c, v in zip((214, 178, 100), (r, g, b)))
            k = 0.55 * smooth(0.30 * W * S, 0.98 * W * S, x) * (1 - fy)  # левая часть чистая: текст читается
            n = random.random() - 0.5
            po[x, y] = tuple(max(0, min(255, int(round(BG[i] + (c - BG[i]) * k + n)))) for i, c in enumerate((r, g, b)))
    os.makedirs(OUT, exist_ok=True)
    out.save(os.path.join(OUT, "header.png"), optimize=True)


def mark():
    m = Image.open(os.path.join(BRAND, "mark-knight-frame.png")).convert("RGBA")
    m.save(os.path.join(OUT, "mark.png"), optimize=True)


if __name__ == "__main__":
    header()
    mark()
    print("ok", OUT)
