"""
客户版数据质量报告生成器（浅色看板，自包含离线）。

与内部用的 report_html.py（暗色、含阈值/calib 技术细节）分工不同：本报告面向客户，
只讲客观数据特征与质量结论，不暴露 CALIB/阈值内部参数。

**核心：按工位/产品分开，绝不揉在一起。** 两级结构：
  验证集（=产品/批次，如 双人椅 / 验证集1）
    └─ 检测点（=工位，detectPointName，如 EOL-60%-1号机-右 / 通道1·HKPLD-GZ-0007-中）
         └─ 阶段（stage）：时长 / RMS 能量 / 启动延迟 / 静音 / 样本数 / 判定

数据来自 out/check_<set>/result.json 的实测特征 + 各 wav 同名 sidecar 的工位/阶段字段。
产出 projects/<name>/out/数据质量报告.html。单独跑：python customer_report.py <project>
"""
import glob
import json
import os
import statistics as st
import sys
from datetime import datetime
from html import escape

import spectro

ASD_ROOT = os.path.dirname(os.path.abspath(__file__))

LABEL_CN = {
    'keep': '正常', 'too_weak': '能量偏弱', 'too_loud': '能量偏高', 'late_start': '启动延迟',
    'pre_silence': '前段静音异常', 'post_silence': '后段静音异常', 'too_short': '时长偏短',
    'too_long': '时长偏长', 'interference': '噪声干扰', 'empty': '空采', 'clipped': '削波',
    'corrupt': '文件损坏', 'incompatible': '数据类型不匹配',
}


def load_json(p):
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def sidecar(wav_path):
    jp = wav_path[:-4] + '.json' if wav_path.endswith('.wav') else wav_path + '.json'
    if os.path.exists(jp):
        try:
            return load_json(jp)
        except Exception:
            return {}
    return {}


def station_label(dp, ds):
    """工位展示名：检测点 + 工位号（若有且不同）。"""
    dp = dp or '未知工位'
    if ds and ds != dp:
        return f'{dp} · {ds}'
    return dp


def rng(vals, nd=2, unit=''):
    if not vals:
        return '—'
    return f'{min(vals):.{nd}f}~{max(vals):.{nd}f}{unit}'


def stage_num(s):
    try:
        return int(s)
    except Exception:
        return 99


def energy_levels(stage_means):
    """给各阶段标相对能量水平（峰值/常规/低能量）。"""
    if not stage_means:
        return {}
    hi = max(stage_means, key=stage_means.get)
    lo = min(stage_means, key=stage_means.get)
    out = {}
    for stg, v in stage_means.items():
        if stg == hi and len(stage_means) > 1:
            out[stg] = ('⚡ 能量峰值', '#b45309')
        elif stg == lo and len(stage_means) > 1:
            out[stg] = ('低能量段', '#2c7da0')
        else:
            out[stg] = ('常规能量', '#3f6b52')
    return out


def signal_tags(items):
    """工位级信号完整性标签（不含计数，纯特征 + 状态）。"""
    labels = {it['pred_label'] for it in items if it['pred_label'] != 'keep'}
    pre = [it.get('pre_silence_sec', 0) for it in items]
    post = [it.get('post_silence_sec', 0) for it in items]
    pre_m = st.mean(pre) if pre else 0
    post_m = st.mean(post) if post else 0

    def tag(title, value, ok):
        cls = 'qv-ok' if ok else 'qv-bad'
        return f'<div class="qbox"><div class="qt">{title}</div><div class="qv {cls}">{value}</div></div>'

    return ''.join([
        tag('🔇 前段静音', f'{pre_m:.2f}s', 'pre_silence' not in labels),
        tag('🔇 后段静音', f'{post_m:.2f}s', 'post_silence' not in labels),
        tag('🎚️ 起声状态', '正常' if 'late_start' not in labels else '晚启动', 'late_start' not in labels),
        tag('🎧 背景噪声', '纯净' if 'interference' not in labels else '偏高', 'interference' not in labels),
        tag('📦 信号完整性', '完整' if not (labels & {'empty', 'clipped', 'corrupt'}) else '异常',
            not (labels & {'empty', 'clipped', 'corrupt'})),
    ])


def render_anomalies(all_items):
    """异常样本明细：按所属工件（productUniqueCode）归集，先报工件再列该件下的异常录音。"""
    anoms = [it for it in all_items if it['pred_label'] != 'keep']
    if not anoms:
        return ''
    groups = {}  # puc -> [item]，保持出现顺序
    for it in anoms:
        groups.setdefault(it.get('_puc') or '未知工件', []).append(it)

    H = ['<div class="set-title">🔧 异常样本明细（按工件）</div>',
         '<div class="grid"><div class="card wide">',
         '<div class="insight bad" style="margin:14px 22px">以下为本次检出异常的录音，'
         '按所属工件（productUniqueCode）归集，便于按件定位复核。</div>']
    for puc, items in groups.items():
        main = puc.split(',')[0].strip() if puc else puc   # 逗号前为工件唯一编号
        sub = puc[len(puc.split(',')[0]) + 1:].strip() if puc and ',' in puc else ''
        H.append(f'<div class="mini-h">🔧 工件 <span class="mono">{escape(main)}</span>'
                 + (f'<span class="sub-cell" style="margin-left:8px;font-weight:400">{escape(sub)}</span>' if sub else '')
                 + '</div>')
        H.append('<div class="table-wrapper"><table><thead><tr>'
                 '<th>产品/批次</th><th>工位</th><th>阶段</th><th>异常类型</th><th>文件名</th>'
                 '<th>频谱图</th></tr></thead><tbody>')
        for it in items:
            cn = LABEL_CN.get(it['pred_label'], it['pred_label'])
            uri, cap = it.get('_spec', ''), it.get('_spec_cap', '')
            if uri:
                spec = (f'<img class="spec" src="{uri}" alt="频谱图" loading="lazy">'
                        f'<div class="spec-cap">{escape(cap)}</div>')
            else:
                spec = '<span class="sub-cell">—</span>'
            H.append(f'<tr><td>{escape(it.get("_set", ""))}</td>'
                     f'<td>{escape(it.get("_station", ""))}</td>'
                     f'<td>阶段{escape(str(it.get("_stage", "?")))}</td>'
                     f'<td><span class="warn-tag">{escape(cn)}</span></td>'
                     f'<td class="mono sub-cell">{escape(os.path.basename(it["file"]))}</td>'
                     f'<td>{spec}</td></tr>')
        H.append('</tbody></table></div>')
    H.append('</div></div>')
    return ''.join(H)


def build(proj, checks_grouped):
    disp = proj.get('display_name', proj['name'])
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')

    all_items = [it for g in checks_grouped.values() for st_map in g['stations'].values()
                 for it in st_map['items']]
    overall_ok = all(it['pred_label'] == 'keep' for it in all_items)
    bad_labels = sorted({LABEL_CN.get(it['pred_label'], it['pred_label'])
                         for it in all_items if it['pred_label'] != 'keep'})

    H = ['''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes">
<title>''' + escape(disp) + '''音频数据质量报告</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f0f4f9;font-family:'Segoe UI','Roboto','Noto Sans',system-ui,-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;padding:32px 24px;color:#1e293b}
.dashboard{max-width:1280px;margin:0 auto}
.header{margin-bottom:22px;border-left:5px solid #2c7da0;padding-left:20px}
.header h1{font-size:1.95rem;font-weight:600;background:linear-gradient(135deg,#1e4b5e,#2c7da0);background-clip:text;-webkit-background-clip:text;color:transparent;letter-spacing:-.3px}
.header .sub{color:#5b6e8c;margin-top:8px;font-size:.9rem;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.badge-date{background:#e2e8f0;padding:4px 12px;border-radius:40px;font-size:.75rem;color:#1e2a3e}
.hero{border-radius:22px;padding:26px 32px;margin-bottom:26px;display:flex;align-items:center;gap:24px;flex-wrap:wrap;box-shadow:0 8px 20px rgba(0,0,0,.04)}
.hero.ok{background:linear-gradient(135deg,#e8f7ee,#eaf3fb);border:1px solid #cfe9d8}
.hero.bad{background:linear-gradient(135deg,#fdeee9,#fbf3ea);border:1px solid #f3d6c8}
.hero .big{font-size:1.55rem;font-weight:700;color:#16633f}
.hero.bad .big{color:#b1492a}
.hero .desc{color:#4a5e74;font-size:.92rem;margin-top:6px;max-width:760px;line-height:1.7}
.hero .rating{margin-left:auto;text-align:center;background:rgba(255,255,255,.7);border-radius:18px;padding:14px 24px}
.hero .rating .r{font-size:1.5rem;font-weight:700;color:#1f7b4d}
.hero.bad .rating .r{color:#b1492a}
.hero .rating .rl{font-size:.7rem;color:#5b6e8c;letter-spacing:.05em}
.set-title{font-size:1.2rem;font-weight:600;color:#0f3b4c;margin:30px 0 14px;display:flex;align-items:center;gap:10px}
.set-title .pill{font-size:.72rem;font-weight:500;border-radius:30px;padding:3px 12px}
.pill-ok{background:#e6f7ec;color:#1f7b4d}.pill-bad{background:#fdecea;color:#c0392b}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(540px,1fr));gap:22px}
.card{background:white;border-radius:22px;box-shadow:0 8px 20px rgba(0,0,0,.03),0 2px 6px rgba(0,0,0,.05);border:1px solid #e9edf2;overflow:hidden}
.card.wide{grid-column:1/-1}
.card-header{padding:16px 24px 12px;border-bottom:2px solid #eff3f6;display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:10px}
.card-header h2{font-size:1.1rem;font-weight:600;color:#0f3b4c}
.card-header .meta{font-size:.74rem;color:#7187a0}
.status-badge{padding:4px 12px;border-radius:30px;font-size:.72rem;font-weight:500}
.sb-ok{background:#e6f7ec;color:#1f7b4d}.sb-bad{background:#fdecea;color:#c0392b}
.mini-h{font-size:.82rem;font-weight:600;color:#33506a;padding:14px 24px 4px;display:flex;align-items:center;gap:7px}
.table-wrapper{padding:2px 20px 8px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;padding:9px 10px;background:#f8fafc;color:#2c3e4e;font-weight:600;border-bottom:1px solid #e2e8f0;white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid #f0f2f5;color:#1f2a40}
tr:last-child td{border-bottom:none}
.mono{font-family:'JetBrains Mono','SF Mono',monospace;font-weight:500}
.sub-cell{font-size:.72rem;color:#7187a0}
.lvl{font-size:.72rem;font-weight:600;border-radius:30px;padding:2px 9px;display:inline-block}
.good-tag{background:#dff9e6;color:#146b3a;border-radius:40px;padding:2px 10px;font-size:.72rem;font-weight:500;display:inline-block}
.warn-tag{background:#fdecea;color:#c0392b;border-radius:40px;padding:2px 10px;font-size:.72rem;font-weight:500;display:inline-block}
.qstrip{display:flex;flex-wrap:wrap;gap:10px;padding:8px 24px 16px}
.qbox{flex:1;min-width:110px;background:#f8fafc;border:1px solid #eef2f6;border-radius:12px;padding:9px 13px}
.qbox .qt{font-size:.7rem;color:#5b6e8c;margin-bottom:3px}
.qbox .qv{font-size:.84rem;font-weight:600;font-family:'JetBrains Mono','SF Mono',monospace}
.qv-ok{color:#1f7b4d}.qv-bad{color:#c0392b}
.spec{width:420px;max-width:46vw;height:auto;border-radius:8px;border:1px solid #e2e8f0;display:block;background:#000}
.spec-cap{font-size:.68rem;color:#7187a0;margin-top:4px}
.insight{background:#eef4fb;border-left:4px solid #2c7da0;padding:11px 18px;margin:2px 22px 18px;border-radius:12px;font-size:.83rem;color:#274a63}
.insight.bad{background:#fdecea;border-left-color:#e07a5f;color:#7a2e1f}
.footer-note{margin-top:30px;text-align:center;font-size:.82rem;color:#516b86;background:white;border-radius:40px;padding:14px 28px;max-width:980px;margin-left:auto;margin-right:auto;box-shadow:0 1px 2px rgba(0,0,0,.05);line-height:1.7}
@media(max-width:700px){body{padding:20px 16px}.grid{grid-template-columns:1fr}th,td{padding:8px;font-size:.78rem}}
</style></head><body><div class="dashboard">''']

    H.append(f'''<div class="header"><h1>📊 {escape(disp)} 音频数据质量报告</h1>
<div class="sub"><span>🎛️ 按工位 / 产品分组 · 时长 · 能量 · 信号质量</span>
<span class="badge-date">📅 {ts}</span></div></div>''')

    # 顶部状态横幅（不写数据量，只给结论）
    if overall_ok:
        H.append('''<div class="hero ok"><div><div class="big">✅ 最新验证：数据全部正常</div>
<div class="desc">本次对各工位最新采集数据完成质量复核，时长分布稳定、各阶段能量层次清晰稳定、信号完整无静音/削波/空采等异常，数据可正常用于后续声学分析与模型训练。</div></div>
<div class="rating"><div class="r">优秀</div><div class="rl">综合评级</div></div></div>''')
    else:
        H.append(f'''<div class="hero bad"><div><div class="big">⚠ 最新验证：检出异常，建议复核</div>
<div class="desc">大部分数据正常，部分工位存在 {escape("、".join(bad_labels))} 等异常（详见下方对应工位明细）。建议针对异常工位复核采集环节后重新验证。</div></div>
<div class="rating"><div class="r">需复核</div><div class="rl">综合评级</div></div></div>''')

    # 异常样本明细（按工件归集，紧跟横幅，便于第一时间定位）
    H.append(render_anomalies(all_items))

    for set_name, g in checks_grouped.items():
        set_bad = any(it['pred_label'] != 'keep' for s in g['stations'].values() for it in s['items'])
        pill = ('<span class="pill pill-bad">检出异常</span>' if set_bad
                else '<span class="pill pill-ok">数据正常</span>')
        stations_txt = '、'.join(sorted(g['stations']))
        H.append(f'<div class="set-title">📦 {escape(set_name)} {pill}'
                 f'<span style="font-size:.74rem;color:#7187a0;font-weight:400">覆盖工位：{escape(stations_txt)}</span></div>')
        H.append('<div class="grid">')

        for station, s in sorted(g['stations'].items()):
            items = s['items']
            s_bad = [it for it in items if it['pred_label'] != 'keep']
            ok = not s_bad
            badge = ('<span class="status-badge sb-ok">✅ 数据正常</span>' if ok
                     else f'<span class="status-badge sb-bad">⚠ 检出异常</span>')
            ordered = sorted(s['stages'], key=stage_num)
            stage_means = {stg: st.mean([it['rms'] for it in s['stages'][stg] if 'rms' in it] or [0])
                           for stg in ordered}
            lvl = energy_levels(stage_means)

            H.append(f'''<div class="card"><div class="card-header"><h2>🏭 {escape(station)}</h2>
<span class="meta">覆盖阶段 {escape("、".join(ordered))}</span>{badge}</div>''')

            # 时长分布
            H.append('<div class="mini-h">⏱️ 时长分布</div><div class="table-wrapper"><table><thead><tr>'
                     '<th>阶段</th><th>平均时长</th><th>时长范围</th><th>波动(标准差)</th><th>判定</th></tr></thead><tbody>')
            for stg in ordered:
                fl = s['stages'][stg]
                dur = [it['duration_sec'] for it in fl if 'duration_sec' in it]
                labels = [it['pred_label'] for it in fl]
                n_bad = sum(1 for x in labels if x != 'keep')
                verdict = ('<span class="good-tag">正常</span>' if n_bad == 0 else
                           f'<span class="warn-tag">{escape("/".join(sorted({LABEL_CN.get(x,x) for x in labels if x!="keep"})))}</span>')
                std = st.pstdev(dur) if len(dur) > 1 else 0
                H.append(f'''<tr><td>阶段{escape(str(stg))}</td>
<td class="mono">{(st.mean(dur) if dur else 0):.2f}s</td>
<td class="mono sub-cell">{rng(dur,2,"s")}</td>
<td class="mono">±{std:.2f}s</td><td>{verdict}</td></tr>''')
            H.append('</tbody></table></div>')

            # 能量分布
            H.append('<div class="mini-h">🔊 频谱能量分布 (RMS)</div><div class="table-wrapper"><table><thead><tr>'
                     '<th>阶段</th><th>RMS 均值</th><th>RMS 范围</th><th>能量水平</th></tr></thead><tbody>')
            for stg in ordered:
                fl = s['stages'][stg]
                rms = [it['rms'] for it in fl if 'rms' in it]
                txt, col = lvl.get(stg, ('常规能量', '#3f6b52'))
                H.append(f'''<tr><td>阶段{escape(str(stg))}</td>
<td class="mono">{(st.mean(rms) if rms else 0):.4f}</td>
<td class="mono sub-cell">{rng(rms,4)}</td>
<td><span class="lvl" style="background:{col}1a;color:{col}">{txt}</span></td></tr>''')
            H.append('</tbody></table></div>')

            # 信号完整性标签条
            H.append('<div class="mini-h">📶 信号质量与完整性</div>')
            H.append(f'<div class="qstrip">{signal_tags(items)}</div>')

            # 工位结论
            if ok:
                hi = max(stage_means, key=stage_means.get) if stage_means else None
                hint = f'，其中阶段{hi}能量相对最高' if hi and len(stage_means) > 1 else ''
                H.append(f'<div class="insight">📈 <strong>本工位结论</strong> — 各阶段时长一致、能量层次稳定{hint}，'
                         f'信号起止完整、无静音/削波/噪声异常，数据正常。</div>')
            else:
                lines = '；'.join(f'{escape(it["file"])} → {LABEL_CN.get(it["pred_label"], it["pred_label"])}'
                                 for it in s_bad)
                H.append(f'<div class="insight bad">⚠ <strong>本工位检出异常</strong><br>{lines}</div>')
            H.append('</div>')  # card
        H.append('</div>')  # grid

    # 综合质量评估（整页收尾，宽卡）
    rows = [
        ('时长一致性', overall_ok, '各工位各阶段时长集中、波动小，采集过程稳定可控'),
        ('能量分布', overall_ok, '各阶段能量层次稳定，无异常偏弱/偏响'),
        ('信号结构', overall_ok, '起止完整，无前后异常静音、无启动延迟畸变'),
        ('噪声水平', overall_ok, '背景纯净，无明显干扰'),
        ('数据完整性', overall_ok, '无空采、无削波、无文件损坏'),
    ]
    H.append('<div class="set-title">🏆 综合质量评估</div><div class="grid"><div class="card wide">'
             '<div class="table-wrapper" style="padding-top:14px"><table><thead><tr>'
             '<th>评估项</th><th>状态</th><th>说明</th></tr></thead><tbody>')
    for name, good, desc in rows:
        st_html = ('<span class="good-tag">✅ 正常</span>' if good else '<span class="warn-tag">⚠ 待复核</span>')
        H.append(f'<tr><td style="font-weight:600">{name}</td><td>{st_html}</td><td>{desc}</td></tr>')
    H.append('</tbody></table></div></div></div>')

    verdict_txt = ('各工位数据时长稳定、能量分层合理、信号完整纯净，最新验证数据全部正常，可用于高精度声学分析。'
                   if overall_ok else
                   '部分工位检出异常（见上方明细），建议复核后重新验证；其余工位数据正常。')
    H.append(f'<div class="footer-note">✅ <strong>{escape(disp)}</strong> 音频数据质量报告 · 结论：{escape(verdict_txt)}</div>')
    H.append('</div></body></html>')
    return ''.join(H)


def generate(project_name):
    pdir = os.path.join(ASD_ROOT, 'projects', project_name)
    proj = load_json(os.path.join(pdir, 'project.json'))
    out_dir = os.path.join(pdir, 'out')

    checks_grouped = {}  # set_name -> {stations: {station: {items, stages:{stg:[items]}}}}
    for set_name in proj.get('valid_sets', {}):
        cdir = os.path.join(out_dir, f'check_{set_name}')
        rj = os.path.join(cdir, 'result.json')
        if not os.path.exists(rj):
            print(f'  ⚠ 跳过未验证的集: {set_name}')
            continue
        items = load_json(rj)['items']
        set_dir = os.path.join(pdir, 'valid_input', set_name)
        stations = {}
        for it in items:
            meta = sidecar(os.path.join(set_dir, it['file']))
            station = station_label(meta.get('detectPointName'), meta.get('detectStationName'))
            stg = str(meta.get('stage', '?'))
            it['_puc'] = meta.get('productUniqueCode') or '未知工件'  # 来自哪件工件
            it['_station'] = station
            it['_stage'] = stg
            it['_set'] = set_name
            if it['pred_label'] != 'keep':  # 仅异常样本算频谱图（内嵌报告）
                it['_spec'], it['_spec_cap'] = spectro.spectrogram_datauri(
                    os.path.join(set_dir, it['file']))
            sd = stations.setdefault(station, {'items': [], 'stages': {}})
            sd['items'].append(it)
            sd['stages'].setdefault(stg, []).append(it)
        checks_grouped[set_name] = {'stations': stations}

    if not checks_grouped:
        print('没有可用验证结果，先跑 run_project.py <project> --validate')
        return None

    html = build(proj, checks_grouped)
    out_path = os.path.join(out_dir, '数据质量报告.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    n_st = sum(len(g['stations']) for g in checks_grouped.values())
    print(f'客户版报告: {out_path}（{len(checks_grouped)} 个验证集 / {n_st} 个工位卡）')
    return out_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit('用法: python customer_report.py <project>')
    generate(sys.argv[1])
