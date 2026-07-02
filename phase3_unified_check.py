"""
阶段三：合并检测（项目全局标准）

流程：corrupt → empty/clipped（硬规则，全量）→ 无匹配基准则 incompatible
      → 有匹配基准则套 CALIB 跑 per-group 阈值规则。

设计要点（2026-06-11 重构）：
- IO/特征/empty/clipped 统一走 common，消除与 phase1/phase2 的定义分叉。
- 阈值 = 基准原始统计量 × common.CALIB，校准只此一处。
- "无匹配基准"即跨产品判据（incompatible）；已匹配到自己组的文件不再过全局
  兼容门——同产品但过响改由 too_loud 正确归类（旧版会误标成 incompatible）。
"""
import json
import os
import shutil
import argparse

import common

CALIB = common.CALIB


def get_baseline_key(filepath, baselines):
    """读边车 JSON 拼 key，命中基准组才返回；否则 None（=跨产品/无元数据）。"""
    key = common.parse_sidecar_key(filepath)
    if key and key in baselines:
        return key
    return None


# ── per-group 阈值规则（base 原始统计量 × CALIB）────────────────────
def check_too_weak(features, b):
    threshold = b['rms_p_low'] * CALIB['too_weak']
    if features['rms'] < threshold:
        return True, f'too_weak: rms={features["rms"]:.5f} < {threshold:.5f}'
    return False, None


def check_too_loud(features, b):
    threshold = b['rms_p_high'] * CALIB['too_loud']
    if features['rms'] > threshold:
        return True, f'too_loud: rms={features["rms"]:.5f} > {threshold:.5f}'
    return False, None


def check_late_start(features, b):
    threshold = b['late_start_median'] * CALIB['late_start']
    if features['late_start_ratio'] < threshold:
        return True, f'late_start: front/back={features["late_start_ratio"]:.4f} < {threshold:.4f}'
    return False, None


def check_pre_silence(features, b):
    threshold = max(b['pre_silence_p95'] * CALIB['pre_silence'], CALIB['silence_floor_sec'])
    if features['pre_silence_sec'] > threshold:
        return True, f'pre_silence: {features["pre_silence_sec"]:.3f}s > {threshold:.3f}s'
    return False, None


def check_post_silence(features, b):
    threshold = max(b['post_silence_p95'] * CALIB['post_silence'], CALIB['silence_floor_sec'])
    if features['post_silence_sec'] > threshold:
        return True, f'post_silence: {features["post_silence_sec"]:.3f}s > {threshold:.3f}s'
    return False, None


def check_too_short(features, b):
    threshold = b['duration_min'] * CALIB['too_short']
    if features['duration_sec'] < threshold:
        return True, f'too_short: {features["duration_sec"]:.2f}s < {threshold:.2f}s'
    return False, None


def check_too_long(features, b):
    threshold = b['duration_max'] * CALIB['too_long']
    if features['duration_sec'] > threshold:
        return True, f'too_long: {features["duration_sec"]:.2f}s > {threshold:.2f}s'
    return False, None


def check_interference(features, b):
    if features['zcr'] > CALIB['interference']:
        return True, f'interference: zcr={features["zcr"]:.4f} > {CALIB["interference"]:.4f}'
    return False, None


# (label, severity, fn) —— 顺序即优先级
BASELINE_RULES = [
    ('too_weak', 'medium', check_too_weak),
    ('too_loud', 'high', check_too_loud),
    ('late_start', 'medium', check_late_start),
    ('pre_silence', 'medium', check_pre_silence),
    ('post_silence', 'medium', check_post_silence),
    ('too_short', 'medium', check_too_short),
    ('too_long', 'medium', check_too_long),
    ('interference', 'medium', check_interference),
]


def unified_check(filepath, baselines):
    samples, sr, error = common.load_audio(filepath)
    if error or samples is None or len(samples) == 0:
        return 'corrupt', 'high', f'load error: {error}', {}

    features = common.extract_features(samples, sr)
    if features is None:
        return 'corrupt', 'high', 'feature extraction failed', {}

    baseline_key = get_baseline_key(filepath, baselines)
    has_baseline = baseline_key is not None

    # 硬规则（全量）
    is_empty, reason = common.check_empty(features, has_baseline=has_baseline)
    if is_empty:
        return 'empty', 'high', reason, features
    is_clipped, reason = common.check_clipped(features, CALIB.get('clipped_ratio'))
    if is_clipped:
        return 'clipped', 'high', reason, features

    # 无匹配基准 = 跨产品/无元数据
    if not has_baseline:
        return 'incompatible', 'high', 'no_matching_baseline: 无匹配的基准组，数据类型不匹配', features

    # per-group 阈值规则
    b = baselines[baseline_key]
    for label, severity, fn in BASELINE_RULES:
        hit, reason = fn(features, b)
        if hit:
            return label, severity, reason, features

    return 'keep', 'ok', 'passed all checks', features


def export_anomalies(results, input_dir, output_dir, skip_labels=None):
    """把非 keep 的样本（wav + 同名 sidecar）拷到 output_dir/异常样本/<label>/，方便人工检查。
    每次先清空该目录，避免上一轮陈旧文件堆积。skip_labels 内的标签不导出
    （夹具测试时跳过 incompatible，免得把整个外来数据集都拷一遍）。返回 (导出数, 目标根目录)。"""
    skip = set(skip_labels or ())
    dest_root = os.path.join(output_dir, '异常样本')
    if os.path.isdir(dest_root):
        shutil.rmtree(dest_root)
    n = 0
    for r in results:
        if r['pred_label'] == 'keep' or r['pred_label'] in skip:
            continue
        src = os.path.join(input_dir, r['file'])
        label_dir = os.path.join(dest_root, r['pred_label'])
        os.makedirs(label_dir, exist_ok=True)
        base = os.path.basename(r['file'])
        try:
            shutil.copy2(src, os.path.join(label_dir, base))           # copy2 跟随软链
            sidecar = os.path.splitext(src)[0] + '.json'
            if os.path.exists(sidecar):
                shutil.copy2(sidecar, os.path.join(label_dir, os.path.splitext(base)[0] + '.json'))
            n += 1
        except OSError as e:
            print(f'  ⚠ 导出失败 {base}: {e}')
    return n, dest_root


def build_report(results, thresholds):
    c = thresholds.get('calib', CALIB)
    lines = ['=' * 80, '音频质量检测 - 项目全局标准报告', '=' * 80, '']
    n_bad = len([r for r in results if r['pred_label'] != 'keep'])
    lines += [f'总文件数: {len(results)}', f'异常文件数: {n_bad}', '']
    lines += ['【检测标准】（生效阈值 = 基准统计量 × 系数）',
              '硬规则（全量）: empty（信号结构特征） / clipped（峰值贴顶+削波率+能量）',
              '跨产品: 无匹配基准组 → incompatible',
              '基准阈值（per-group）:',
              f'  too_weak:     rms < rms_P5 × {c["too_weak"]}',
              f'  too_loud:     rms > rms_P95 × {c["too_loud"]}',
              f'  late_start:   front/back < late_median × {c["late_start"]}',
              f'  pre_silence:  > max(pre_P95 × {c["pre_silence"]}, {c["silence_floor_sec"]}s)',
              f'  post_silence: > max(post_P95 × {c["post_silence"]}, {c["silence_floor_sec"]}s)',
              f'  too_short:    dur < min × {c["too_short"]}    too_long: dur > max × {c["too_long"]}',
              f'  interference: zcr > {c["interference"]}（经验值）', '']
    pred_counts = {}
    for r in results:
        pred_counts[r['pred_label']] = pred_counts.get(r['pred_label'], 0) + 1
    lines.append('【异常汇总】')
    for label, count in sorted(pred_counts.items()):
        if label != 'keep':
            lines.append(f'  {label}: {count}')
    lines.append('')
    lines.append('【详细结果】')
    for r in results:
        if r['pred_label'] != 'keep':
            lines.append(f'\n[{r["pred_label"]}] {r["file"]}')
            lines.append(f'  严重度: {r["severity"]}')
            lines.append(f'  原因: {r["reason"]}')
            if 'duration_sec' in r:
                lines.append(f'  时长: {r["duration_sec"]}s, RMS: {r["rms"]}, Peak: {r["peak_abs"]}')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description='阶段三：合并检测（项目全局标准）')
    parser.add_argument('input_dir', help='待检测音频目录')
    parser.add_argument('thresholds_file', help='thresholds.json 路径')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--export-skip', default='', metavar='LABELS',
                        help='导出异常样本时跳过的标签（逗号分隔），如 incompatible；夹具测试用')
    args = parser.parse_args()

    print('=' * 80)
    print('阶段三：合并检测（项目全局标准）')
    print('=' * 80)
    print(f'输入目录: {args.input_dir}')
    print(f'标准文件: {args.thresholds_file}')
    print(f'输出目录: {args.output_dir}\n')

    with open(args.thresholds_file, 'r', encoding='utf-8') as f:
        thresholds = json.load(f)
    baselines = thresholds.get('baselines', {})
    global CALIB
    CALIB = thresholds.get('calib', common.CALIB)  # 以 thresholds 内固化的 calib 为准

    print(f'加载标准: {thresholds["meta"]["n_baselines"]} 个基准组')
    print(f'硬规则: empty + clipped；跨产品: incompatible；基准阈值: per-group × CALIB\n')

    wav_files = common.find_wavs(args.input_dir)
    print(f'找到 {len(wav_files)} 个音频文件\n')

    results = []
    for idx, filepath in enumerate(wav_files):
        rel_path = os.path.relpath(filepath, args.input_dir)
        print(f'[{idx + 1}/{len(wav_files)}] {rel_path}', end=' ')
        label, severity, reason, features = unified_check(filepath, baselines)
        result = {'file': rel_path, 'pred_label': label, 'severity': severity, 'reason': reason}
        if features:
            result.update({
                'duration_sec': round(features['duration_sec'], 2),
                'rms': round(features['rms'], 5),
                'peak_abs': round(features['peak_abs'], 5),
                'clipping_ratio': round(features['clipping_ratio'], 5),
                'frame_rms_mean': round(features['frame_rms_mean'], 5),
                'frame_rms_cv': round(features['frame_rms_cv'], 3),
                'frame_rms_p50': round(features.get('frame_rms_p50', 0), 5),
                'frame_rms_p99': round(features.get('frame_rms_p99', 0), 5),
                'pre_silence_sec': round(features['pre_silence_sec'], 3),
                'post_silence_sec': round(features['post_silence_sec'], 3),
                'late_start_ratio': round(features['late_start_ratio'], 4),
                'zcr': round(features['zcr'], 4),
            })
        results.append(result)
        print(f'-> {label}')

    pred_counts, severity_counts = {}, {}
    for r in results:
        pred_counts[r['pred_label']] = pred_counts.get(r['pred_label'], 0) + 1
        severity_counts[r['severity']] = severity_counts.get(r['severity'], 0) + 1
    summary = {'total': len(results), 'pred_counts': pred_counts, 'severity_counts': severity_counts}

    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, 'result.json'), 'w', encoding='utf-8') as f:
        json.dump({'items': results, 'total': len(results)}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output_dir, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(build_report(results, thresholds))

    skip_labels = [s.strip() for s in args.export_skip.split(',') if s.strip()]
    n_anom, dest_root = export_anomalies(results, args.input_dir, args.output_dir, skip_labels)

    print('\n' + '=' * 80)
    print('检测完成')
    print('=' * 80)
    print(f'总计: {summary["total"]}')
    for label, count in sorted(pred_counts.items()):
        print(f'  {label}: {count}')
    print(f'\n结果保存到: {args.output_dir}')
    if n_anom:
        print(f'异常样本已导出: {dest_root}（{n_anom} 个，按异常类型分目录）')


if __name__ == '__main__':
    main()
