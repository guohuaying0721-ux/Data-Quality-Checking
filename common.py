"""
公共模块：音频 IO + 特征提取 + 跨项目硬规则 + 阈值校准常量

三个 phase 脚本共享本模块，避免 load_audio/extract_features/check_empty
在各文件复制并发散（历史上 phase1 与 phase3 的 empty 定义已经不一致）。

阈值校准全部收口到 CALIB：phase2 只产出"基准数据长什么样"的原始统计量，
phase3 在检测时统一套 CALIB 算出"多严格"。改灵敏度只动这一处。
"""
import json
import os
import numpy as np
from numpy.fft import rfft


# ── 阈值校准（唯一来源）──────────────────────────────────────────
# phase2 存原始统计量(rms_p_low / late_start_median / *_p95 / duration_min...)，
# phase3 检测时 base × 下面的系数 = 生效阈值。
# 2026-06-11 据继峰验证集复核结果标定（修正四类误报）。
CALIB = {
    # too_weak: rms < rms_p_low × 0.5（原 phase2 ×0.8 贴正常下沿误杀，收紧到 ×0.5）
    'too_weak': 0.5,
    # too_loud: rms > rms_p_high × 1.5（新增；同产品同组但显著过响才算）
    'too_loud': 1.5,
    # late_start: ratio < late_start_median × 0.35（原 phase2 ×0.5 再 phase3 ×0.7 = ×0.35，合并成一处）
    'late_start': 0.35,
    # pre/post_silence: p95 × 2.0，再与绝对地板取大（基准首尾近无静音→p95≈0 会塌缩误报）
    'pre_silence': 2.0,
    'post_silence': 2.0,
    'silence_floor_sec': 0.5,
    # too_short / too_long: 比基准 min 再短 / 比 max 再长才算
    'too_short': 0.5,
    'too_long': 1.5,
    # interference: ZCR 经验绝对阈值（待多项目数据后基准化）
    'interference': 0.375,
    # clipped_ratio: 削波率阈值。默认 = HARD_RULES['clipped']['clipping_ratio']，
    # 行为与原硬规则一致；可被 project.json 的 calib_override 按产品覆盖（如 EPS
    # 电机信号天生满量程贴顶、削波率天生偏高，需调高免误判）。
    'clipped_ratio': 0.001,
}

# 跨项目硬规则常量（empty / clipped）
HARD_RULES = {
    # cond_a_energy_floor: cond_a 仅在帧能量确实很低时才判空采，避免稳态实信号
    # （nsk-eps8 正常件 frame_mean≈0.1、peak/mean≈9）被误判为空。0.05 取真空采
    # （实测 frame_mean≈0.008~0.025）与正常稳态信号（≈0.1）之间空档的中点，两侧各留 ~2× 余量。
    'empty': {'frame_mean': 0.01, 'frame_cv': 0.6, 'frame_max': 0.05, 'rms': 0.01,
              'cond_a_energy_floor': 0.05},
    'clipped': {'peak': 0.99, 'clipping_ratio': 0.001, 'rms': 0.15, 'frame_mean': 0.15},
}

SILENCE_AMP_THRESHOLD = 0.001  # 判定某采样点为"静音"的绝对幅值

MIN_BASELINE_SAMPLES = 5  # 单组基准样本数低于此值则告警（统计量不可靠）


def resolve_calib(override=None):
    """全局默认 CALIB 叠加项目级覆盖，返回生效系数 dict（不改全局 CALIB）。

    每个项目可在 project.json 的 calib_override 里只写要改的键，其余继承
    common.CALIB。生效结果由 phase2 固化进该项目 thresholds.json，phase3 以
    文件内 calib 为准——项目间互不牵制。
    """
    calib = dict(CALIB)
    if override:
        calib.update(override)
    return calib


def load_calib_file(path):
    """从 json 文件读 calib 覆盖并叠加到全局默认；path 为空则返回全局默认。"""
    if not path:
        return dict(CALIB)
    with open(path, 'r', encoding='utf-8') as f:
        return resolve_calib(json.load(f))


def load_audio(filepath):
    """加载 wav，归一化到 [-1, 1]，多通道取均值。返回 (samples, sr, error)。"""
    try:
        from scipy.io import wavfile
        sr, samples = wavfile.read(filepath)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if np.issubdtype(samples.dtype, np.integer):
            info = np.iinfo(samples.dtype)
            samples = samples.astype(np.float64) / max(abs(info.min), abs(info.max))
        else:
            samples = samples.astype(np.float64)
        return samples, sr, None
    except Exception as e:
        return None, 0, str(e)


def _frame_rms(samples, frame_size):
    """向量化帧 RMS（100ms 无重叠帧）。"""
    n = len(samples) // frame_size
    if n == 0:
        return np.array([np.sqrt(np.mean(samples ** 2))]) if len(samples) else np.array([0.0])
    trimmed = samples[:n * frame_size].reshape(n, frame_size)
    return np.sqrt(np.mean(trimmed ** 2, axis=1))


def _edge_silence(abs_data, sr):
    """向量化首尾静音时长（秒）。"""
    active = abs_data >= SILENCE_AMP_THRESHOLD
    if not active.any():
        dur = len(abs_data) / sr
        return dur, dur
    first = int(np.argmax(active))                 # 第一个非静音
    last = len(active) - 1 - int(np.argmax(active[::-1]))  # 最后一个非静音
    return first / sr, (len(active) - 1 - last) / sr


def _spectral_flatness(samples, n_fft=1024, hop=512):
    """平均频谱平坦度（区分空采/噪声 vs 正常音频）。"""
    if len(samples) <= 256:
        return 0.0
    vals = []
    for start in range(0, len(samples) - n_fft + 1, hop):
        spectrum = np.maximum(np.abs(rfft(samples[start:start + n_fft])), 1e-12)
        am = np.mean(spectrum)
        if am > 1e-12:
            vals.append(np.exp(np.mean(np.log(spectrum))) / am)
    return float(np.mean(vals)) if vals else 0.0


def extract_features(samples, sr):
    """提取全部特征（三个 phase 统一用这一份超集）。"""
    if samples is None or len(samples) == 0:
        return None

    abs_data = np.abs(samples)
    duration_sec = float(len(samples) / sr)
    rms = float(np.sqrt(np.mean(samples ** 2)))
    peak_abs = float(np.max(abs_data))
    mean_abs = float(np.mean(abs_data))
    clipping_ratio = float(np.mean(abs_data >= 0.99))

    frames = _frame_rms(samples, max(1, int(sr * 0.1)))
    frame_rms_mean = float(np.mean(frames))
    frame_rms_cv = float(np.std(frames) / (frame_rms_mean + 1e-12))
    frame_rms_max = float(np.max(frames))
    frame_rms_p50 = float(np.percentile(frames, 50))
    frame_rms_p99 = float(np.percentile(frames, 99))

    pre_silence_sec, post_silence_sec = _edge_silence(abs_data, sr)

    split_idx = max(1, int(len(frames) * 0.2))
    front_rms = float(np.mean(frames[:split_idx]))
    back_rms = float(np.mean(frames[split_idx:])) if len(frames) > split_idx else front_rms
    late_start_ratio = front_rms / (back_rms + 1e-12)

    if len(samples) > 1:
        signs = np.sign(samples)
        signs[signs == 0] = 1
        zcr = float(np.mean(signs[:-1] != signs[1:]))
    else:
        zcr = 0.0

    return {
        'duration_sec': duration_sec,
        'rms': rms,
        'peak_abs': peak_abs,
        'mean_abs': mean_abs,
        'clipping_ratio': clipping_ratio,
        'frame_rms_mean': frame_rms_mean,
        'frame_rms_cv': frame_rms_cv,
        'frame_rms_max': frame_rms_max,
        'frame_rms_p50': frame_rms_p50,
        'frame_rms_p99': frame_rms_p99,
        'pre_silence_sec': pre_silence_sec,
        'post_silence_sec': post_silence_sec,
        'late_start_ratio': late_start_ratio,
        'zcr': zcr,
        'spectral_flatness': _spectral_flatness(samples),
    }


# ── 跨项目硬规则（empty / clipped）唯一定义 ───────────────────────
def check_empty(features, has_baseline=True):
    """空采检测（统一版，基于信号结构特征而非绝对能量，跨项目通用）。

    has_baseline=False（无匹配基准/快筛）时只用与项目无关的 cond_b/c/d，
    避免跨项目误判；有基准时额外启用 cond_a（依赖 cv / peak_mean 经验阈值）。
    """
    frame_mean = features['frame_rms_mean']
    frame_cv = features['frame_rms_cv']
    frame_p50 = features.get('frame_rms_p50', 0)
    frame_p99 = features.get('frame_rms_p99', 0)
    rms = features['rms']
    peak = features['peak_abs']
    peak_mean_ratio = peak / (frame_mean + 1e-12)

    cond_a = (frame_cv < 0.6 and peak_mean_ratio < 10.0
              and frame_mean < HARD_RULES['empty']['cond_a_energy_floor'])
    cond_b = frame_p50 < 0.005 and (frame_p99 / (frame_p50 + 1e-12)) > 20 and frame_p99 > 0.1
    cond_c = peak < 0.08 and rms < 0.01 and frame_mean < 0.01
    cond_d = frame_mean < 0.02 and frame_cv > 2.0 and peak_mean_ratio > 10 and rms < 0.03

    if has_baseline and cond_a:
        return True, f'empty: cond_a=True (cv<0.6 & peak/mean<10 & frame_mean<{HARD_RULES["empty"]["cond_a_energy_floor"]})'
    if cond_b or cond_c or cond_d:
        return True, f'empty: cond_b={cond_b}, cond_c={cond_c}, cond_d={cond_d}'
    return False, None


def check_clipped(features, clip_ratio_thr=None):
    """削波检测：峰值贴顶 + 削波率达标 + 能量足够（排除多次低能量冲击）。

    clip_ratio_thr 为 None 时用跨产品默认 HARD_RULES['clipped']['clipping_ratio']；
    phase3 传入项目级 CALIB['clipped_ratio']，使 EPS 等天生满量程的产品可调高免误判。
    """
    r = HARD_RULES['clipped']
    ratio_thr = r['clipping_ratio'] if clip_ratio_thr is None else clip_ratio_thr
    if features['peak_abs'] < r['peak']:
        return False, None
    if features['clipping_ratio'] <= ratio_thr:
        return False, None
    if features['rms'] <= r['rms'] and features['frame_rms_mean'] <= r['frame_mean']:
        return False, 'peak_touch_but_low_energy'
    return True, (f'clipped: peak={features["peak_abs"]:.3f}, '
                  f'ratio={features["clipping_ratio"]:.4f}, rms={features["rms"]:.5f}')


def find_wavs(input_dir):
    """递归找 wav（glob 跟随符号链接，与 baseline_input 的软链一致）。"""
    import glob
    return sorted(glob.glob(os.path.join(input_dir, '**', '*.wav'), recursive=True))


def parse_sidecar_key(filepath):
    """读同名 .json 边车，拼出 {detectPointName}_stage{stage}_ch{channel}。无则返回 None。"""
    for jp in (filepath + '.json', filepath[:-4] + '.json' if filepath.endswith('.wav') else filepath + '.json'):
        if os.path.exists(jp):
            try:
                with open(jp, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                dp = str(meta.get('detectPointName', 'unknown')).replace('/', '-').replace('\\', '-')
                return f"{dp}_stage{meta.get('stage', '0')}_ch{meta.get('channel', '0')}"
            except Exception:
                continue
    return None
