"""
阶段一：硬规则快速检测（联调当天，无需基准数据）

检测项: corrupt（无法读取）/ empty（空采）/ clipped（削波）
empty、clipped 与 phase3 共用 common 里的同一份定义，避免快筛与复检结果不一致。
无基准上下文，empty 用跨项目保守判据（has_baseline=False）。
"""
import json
import os
import argparse

import common


def process_file(filepath):
    samples, sr, error = common.load_audio(filepath)
    if error or samples is None or len(samples) == 0:
        return 'corrupt', 'high', f'load error: {error}', {}

    features = common.extract_features(samples, sr)
    if features is None:
        return 'corrupt', 'high', 'feature extraction failed', {}

    is_empty, reason = common.check_empty(features, has_baseline=False)
    if is_empty:
        return 'empty', 'high', reason, features

    is_clipped, reason = common.check_clipped(features)
    if is_clipped:
        return 'clipped', 'high', reason, features

    return 'keep', 'ok', 'passed all phase-1 checks', features


def main():
    parser = argparse.ArgumentParser(description='阶段一：硬规则快速检测（联调当天）')
    parser.add_argument('input_dir', help='输入音频目录')
    parser.add_argument('output_dir', help='输出目录')
    args = parser.parse_args()

    print('=' * 80)
    print('阶段一：硬规则快速检测（联调当天）')
    print('=' * 80)
    print(f'输入: {args.input_dir}')
    print(f'输出: {args.output_dir}\n')

    wav_files = common.find_wavs(args.input_dir)
    print(f'找到 {len(wav_files)} 个音频文件\n')

    results = []
    for idx, filepath in enumerate(wav_files):
        rel_path = os.path.relpath(filepath, args.input_dir)
        print(f'[{idx + 1}/{len(wav_files)}] {rel_path}', end=' ')
        label, severity, reason, features = process_file(filepath)
        result = {'file': rel_path, 'pred_label': label, 'severity': severity, 'reason': reason}
        if features:
            result.update({
                'duration_sec': round(features['duration_sec'], 2),
                'rms': round(features['rms'], 5),
                'peak_abs': round(features['peak_abs'], 5),
                'clipping_ratio': round(features['clipping_ratio'], 5),
                'frame_rms_mean': round(features['frame_rms_mean'], 5),
                'frame_rms_cv': round(features['frame_rms_cv'], 3),
                'frame_rms_max': round(features['frame_rms_max'], 5),
                'frame_rms_p50': round(features['frame_rms_p50'], 5),
                'frame_rms_p99': round(features['frame_rms_p99'], 5),
            })
        results.append(result)
        print(f'-> {label}')

    pred_counts = {}
    for r in results:
        pred_counts[r['pred_label']] = pred_counts.get(r['pred_label'], 0) + 1
    summary = {'total': len(results), 'pred_counts': pred_counts}

    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump({'items': results, 'total': len(results)}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    with open(os.path.join(args.output_dir, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write('=' * 80 + '\n音频质量检测 - 阶段一报告（硬规则）\n' + '=' * 80 + '\n\n')
        f.write(f'总文件数: {len(results)}\n')
        f.write(f'异常文件数: {len([r for r in results if r["pred_label"] != "keep"])}\n\n')
        f.write('【检测规则】empty（信号结构特征） / clipped（峰值贴顶+削波率+能量）\n\n')
        f.write('【异常汇总】\n')
        for label, count in sorted(pred_counts.items()):
            if label != 'keep':
                f.write(f'  {label}: {count}\n')
        f.write('\n【详细结果】\n')
        for r in results:
            if r['pred_label'] != 'keep':
                f.write(f'\n[{r["pred_label"]}] {r["file"]}\n  严重度: {r["severity"]}\n  原因: {r["reason"]}\n')
                if 'duration_sec' in r:
                    f.write(f'  时长: {r["duration_sec"]}s, RMS: {r["rms"]}, Peak: {r["peak_abs"]}\n')

    print('\n' + '=' * 80 + '\n检测完成\n' + '=' * 80)
    print(f'总计: {summary["total"]}')
    for label, count in sorted(pred_counts.items()):
        print(f'  {label}: {count}')
    print(f'\n结果保存到: {args.output_dir}')


if __name__ == '__main__':
    main()
