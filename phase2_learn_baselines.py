"""
阶段二：基准学习

输入: 人工确认过的干净录音（按 detectPointName_stage_ch 分组）
输出: baselines.json（每组详细统计，供查看/兼容性判断）
      thresholds.json（每组原始统计量，供 phase3 套 CALIB 检测）

注意：本阶段只产出"基准数据长什么样"的原始统计量，不再把"多严格"的
倍率写死进来。所有灵敏度系数收口在 common.CALIB，phase3 检测时统一应用。
"""
import json
import os
import argparse
from collections import defaultdict

import numpy as np

import common


def compute_stats(features_list):
    """从一组干净样本算原始统计量。"""
    durations = [f['duration_sec'] for f in features_list]
    rms_values = [f['rms'] for f in features_list]
    pre_sil = [f['pre_silence_sec'] for f in features_list]
    post_sil = [f['post_silence_sec'] for f in features_list]
    late = [f['late_start_ratio'] for f in features_list]
    zcrs = [f['zcr'] for f in features_list]

    return {
        'n_samples': len(features_list),
        'duration': {
            'min': float(np.min(durations)), 'max': float(np.max(durations)),
            'mean': float(np.mean(durations)), 'median': float(np.median(durations)),
            'std': float(np.std(durations)),
            'p_low': float(np.percentile(durations, 5)),
            'p_high': float(np.percentile(durations, 95)),
        },
        'rms': {
            'mean': float(np.mean(rms_values)), 'median': float(np.median(rms_values)),
            'std': float(np.std(rms_values)),
            'p_low': float(np.percentile(rms_values, 5)),
            'p_high': float(np.percentile(rms_values, 95)),
        },
        'pre_silence': {'mean': float(np.mean(pre_sil)), 'p95': float(np.percentile(pre_sil, 95))},
        'post_silence': {'mean': float(np.mean(post_sil)), 'p95': float(np.percentile(post_sil, 95))},
        'late_start': {
            'mean': float(np.mean(late)), 'median': float(np.median(late)),
            'min': float(np.min(late)), 'p5': float(np.percentile(late, 5)),
        },
        'interference': {'mean': float(np.mean(zcrs)), 'p95': float(np.percentile(zcrs, 95))},
    }


def baseline_key(filepath):
    """优先读边车 JSON；降级用文件名里的 ch 段。

    返回 (key, source)：source='json' 表示来自完整边车；'fallback' 表示
    无边车/字段缺失走了降级路径（这类文件分组不可靠，需告警）。
    """
    key = common.parse_sidecar_key(filepath)
    if key and 'unknown' not in key and '_stage0_' not in key and not key.endswith('_ch0'):
        return key, 'json'
    if key:  # 边车在但字段缺失（unknown/stage0/ch0）
        return key, 'fallback'
    stem = os.path.splitext(os.path.basename(filepath))[0]
    parts = stem.split('_')
    if len(parts) >= 3 and parts[2].startswith('ch'):
        return parts[2], 'fallback'
    return stem, 'fallback'


def validate_groups(grouped, fallback_files):
    """学完基准后的健康校验，返回告警列表。"""
    warnings = []
    for key in sorted(grouped):
        n = len(grouped[key])
        if n < common.MIN_BASELINE_SAMPLES:
            warnings.append(f'样本不足: 组 [{key}] 仅 {n} 个样本 (< {common.MIN_BASELINE_SAMPLES})，统计量不可靠')
    if fallback_files:
        warnings.append(f'元数据降级: {len(fallback_files)} 个文件无完整边车JSON，落到降级分组键（如 {fallback_files[0][1]}）')
    return warnings


def main():
    parser = argparse.ArgumentParser(description='阶段二：基准学习')
    parser.add_argument('input_dir', help='基准音频目录')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--calib-file', default=None,
                        help='项目级 calib 覆盖 json（只写要改的键，其余继承 common.CALIB）')
    args = parser.parse_args()

    calib = common.load_calib_file(args.calib_file)

    print('=' * 80)
    print('阶段二：基准学习')
    print('=' * 80)
    print(f'输入目录: {args.input_dir}')
    print(f'输出目录: {args.output_dir}\n')

    wav_files = common.find_wavs(args.input_dir)
    print(f'找到 {len(wav_files)} 个基准音频文件\n')

    grouped = defaultdict(list)
    fallback_files = []  # [(filepath, key), ...] 走了降级分组的文件
    for filepath in wav_files:
        samples, sr, error = common.load_audio(filepath)
        if error:
            print(f'[SKIP] 无法读取: {filepath}')
            continue
        features = common.extract_features(samples, sr)
        if features:
            key, source = baseline_key(filepath)
            if source == 'fallback':
                fallback_files.append((filepath, key))
            grouped[key].append(features)

    # 健康校验：样本量不足 / 元数据降级
    warnings = validate_groups(grouped, fallback_files)
    if warnings:
        print('\n' + '!' * 80)
        print('⚠ 基准健康告警:')
        for w in warnings:
            print(f'  - {w}')
        print('!' * 80 + '\n')

    baselines = {}
    for key, flist in grouped.items():
        print(f'计算基准: {key} (n={len(flist)})')
        baselines[key] = compute_stats(flist)

    # thresholds.json：每组原始统计量（phase3 套 CALIB 用）
    thresholds = {
        'meta': {
            'version': '2.0',
            'note': '存原始统计量；生效阈值 = 统计量 × common.CALIB 系数，检测时计算',
            'n_baselines': len(baselines),
            'n_total_samples': sum(b['n_samples'] for b in baselines.values()),
            'min_baseline_samples': common.MIN_BASELINE_SAMPLES,
            'warnings': warnings,
        },
        'hard_rules': common.HARD_RULES,
        'calib': calib,
        'baselines': {},
    }
    for key, b in baselines.items():
        thresholds['baselines'][key] = {
            'n_samples': b['n_samples'],
            'duration_min': b['duration']['min'],
            'duration_max': b['duration']['max'],
            'rms_p_low': b['rms']['p_low'],
            'rms_p_high': b['rms']['p_high'],
            'rms_mean': b['rms']['mean'],
            'rms_std': b['rms']['std'],
            'pre_silence_p95': b['pre_silence']['p95'],
            'post_silence_p95': b['post_silence']['p95'],
            'late_start_median': b['late_start']['median'],
            'interference_p95': b['interference']['p95'],
        }

    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, 'baselines.json'), 'w', encoding='utf-8') as f:
        json.dump(baselines, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, 'thresholds.json'), 'w', encoding='utf-8') as f:
        json.dump(thresholds, f, ensure_ascii=False, indent=2)

    print('\n' + '=' * 80)
    print('基准学习完成')
    print('=' * 80)
    print(f'\n基准组数: {len(baselines)}   总样本数: {thresholds["meta"]["n_total_samples"]}')
    print('输出: baselines.json（详细统计） + thresholds.json（原始统计量 + CALIB）\n')
    for key, t in thresholds['baselines'].items():
        c = calib
        print(f'  [{key}] n={t["n_samples"]}')
        print(f'    too_weak  < {t["rms_p_low"] * c["too_weak"]:.5f}   '
              f'too_loud  > {t["rms_p_high"] * c["too_loud"]:.5f}')
        print(f'    late_start< {t["late_start_median"] * c["late_start"]:.4f}   '
              f'silence floor {c["silence_floor_sec"]}s')


if __name__ == '__main__':
    main()
