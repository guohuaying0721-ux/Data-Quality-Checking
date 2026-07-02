"""零依赖频谱图生成：scipy 算谱 → numpy 上色 → 手写 PNG 编码 → base64 data-URI。

不引入 matplotlib/PIL，沿用项目"纯 numpy/scipy"的依赖约束。供 customer_report.py
在异常样本明细里给每条异常 wav 内嵌一张频谱图（自包含离线）。
"""
import base64
import struct
import zlib

import numpy as np
from scipy import signal

import common

# magma 风格色表锚点（低能量→高能量：近黑→紫→橙→浅黄），插值成 256 级 LUT
_ANCHORS = np.array([
    (0, 0, 4), (40, 11, 84), (101, 21, 110), (159, 42, 99),
    (212, 72, 66), (245, 125, 21), (250, 193, 39), (252, 255, 164),
], dtype=np.float64)
_xp = np.linspace(0, 255, len(_ANCHORS))
_LUT = np.stack([np.interp(np.arange(256), _xp, _ANCHORS[:, c]) for c in range(3)],
                axis=1).astype(np.uint8)


def _resize_mean(M, out_h, out_w):
    """把 (F, T) 谱阵按块平均缩到至多 (out_h, out_w)，不放大。"""
    f, t = M.shape
    if f > out_h:
        idx = np.linspace(0, f, out_h + 1).astype(int)
        M = np.stack([M[idx[i]:idx[i + 1]].mean(0) for i in range(out_h)])
    if t > out_w:
        idx = np.linspace(0, M.shape[1], out_w + 1).astype(int)
        M = np.stack([M[:, idx[i]:idx[i + 1]].mean(1) for i in range(out_w)], axis=1)
    return M


def _png_bytes(rgb):
    """把 (H, W, 3) uint8 数组编码为 PNG 字节（truecolor 8-bit，无第三方库）。"""
    h, w, _ = rgb.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)                 # 每行 filter type 0
        raw.extend(rgb[y].tobytes())

    def chunk(typ, data):
        return (struct.pack('>I', len(data)) + typ + data
                + struct.pack('>I', zlib.crc32(typ + data) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def spectrogram_datauri(wav_path, out_h=192, out_w=420, db_range=80):
    """返回 (data_uri, caption)；失败返回 ('', '')。
    caption 给出时间跨度与频率上限，便于客户读图。"""
    samples, sr, err = common.load_audio(wav_path)
    if err or samples is None or len(samples) < 256:
        return '', ''
    nper = 1024 if len(samples) >= 1024 else 256
    f, t, Sxx = signal.spectrogram(samples, fs=sr, nperseg=nper,
                                   noverlap=nper // 2, scaling='spectrum')
    if Sxx.size == 0:
        return '', ''
    db = 10.0 * np.log10(Sxx + 1e-12)
    vmax = float(db.max())
    norm = np.clip((db - (vmax - db_range)) / db_range, 0.0, 1.0)  # 0..1
    norm = _resize_mean(norm, out_h, out_w)
    img = _LUT[(norm * 255).astype(np.uint8)]          # (H, W, 3)
    img = np.flipud(img)                                # 低频在下、高频在上
    uri = 'data:image/png;base64,' + base64.b64encode(_png_bytes(np.ascontiguousarray(img))).decode()
    cap = f'时间 0–{len(samples) / sr:.1f}s · 频率 0–{sr / 2000:.1f}kHz · 颜色=能量(dB)'
    return uri, cap
