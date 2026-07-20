#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成橙色/黄色数据报表图标 dashboard_icon.ico (32x32, 32位含alpha)。"""
import struct

W = H = 32
RADIUS = 7


def in_rounded(x, y, w, h, r):
    if x < r and y < r:
        return (x - r) ** 2 + (y - r) ** 2 <= r * r
    if x >= w - r and y < r:
        return (x - (w - r)) ** 2 + (y - r) ** 2 <= r * r
    if x < r and y >= h - r:
        return (x - r) ** 2 + (y - (h - r)) ** 2 <= r * r
    if x >= w - r and y >= h - r:
        return (x - (w - r)) ** 2 + (y - (h - r)) ** 2 <= r * r
    return True


# 柱状图：3根白色柱子，高度递增
BARS = [((6, 10), 21), ((14, 18), 15), ((22, 26), 10)]  # (x范围, 顶部y)
BAR_BASE = 26

pixels = []  # (b, g, r, a)
for y in range(H):
    for x in range(W):
        if in_rounded(x, y, W, H, RADIUS):
            t = y / (H - 1)
            r = 255
            g = int(196 + (140 - 196) * t)  # 顶部琥珀 -> 底部橙
            b = 0
            in_bar = False
            for (bx0, bx1), top in BARS:
                if bx0 <= x <= bx1 and top <= y <= BAR_BASE:
                    in_bar = True
            if in_bar:
                r, g, b = 255, 255, 255
            a = 255
        else:
            r = g = b = a = 0
        pixels.append((b, g, r, a))

# XOR 位图（自下而上）
xor = b""
for y in range(H - 1, -1, -1):
    for (b, g, r, a) in pixels[y * W:(y + 1) * W]:
        xor += struct.pack("<BBBB", b, g, r, a)

# AND 掩码（全0 = 不额外透明，alpha 决定透明）
and_row = b"\x00\x00\x00\x00"  # 32位宽一行 = 4字节
and_mask = and_row * H

bmp_header = struct.pack("<IiiHHIIiiII", 40, W, 2 * H, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
img = bmp_header + xor + and_mask

icon_dir = struct.pack("<HHH", 0, 1, 1)
icon_entry = struct.pack("<BBBBHHII", W, H, 0, 0, 1, 32, len(img), 6 + 16)
ico = icon_dir + icon_entry + img

out = r"C:\Users\Administrator\WorkBuddy\2026-07-16-10-31-12\my-dashboard\dashboard_icon.ico"
with open(out, "wb") as f:
    f.write(ico)
print("图标已生成:", out, len(ico), "bytes")
