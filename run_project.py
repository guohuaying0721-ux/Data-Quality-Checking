"""
按项目驱动音频 QC 管线（基准学习 + 验证），实现项目间隔离。

每个项目一个目录 projects/<name>/：
  project.json     —— 唯一真相源：basel ine_sets / valid_sets / calib_override
  baseline_input/  —— 由本脚本据 baseline_sets 重建的软链（phase2 输入，合并多个基准集）
  valid_input/     —— 由本脚本据 valid_sets 重建的软链（每个验证集一个子目录）
  config/          —— phase2 产物：baselines.json + thresholds.json（含本项目固化的 calib）
  out/             —— phase3 产物：每个验证集一份 check_<set>/

灵敏度系数：common.CALIB 是全局默认；项目在 project.json.calib_override 里只写要改的键，
本脚本写出 config/calib_override.json 并喂给 phase2 固化进该项目 thresholds.json。
项目之间改谁都不影响别人。

用法：
  python run_project.py <project> --learn       只学基准（phase2）
  python run_project.py <project> --validate     只验证全部集（phase3，需先有 config）
  python run_project.py <project> --set <名>     只验证指定集（可重复或逗号分隔），不重学基准
  python run_project.py <project> --all          学完接着验证（默认）
  python run_project.py <project> --phase1 <dir> 对任意目录跑无基准快筛（phase1）
  python run_project.py --test <dir>             夹具测试：无基准 phase3（硬规则+兼容性），落 tests/out/

phase3 验证时会把非 keep 的样本（wav + sidecar）按异常类型导出到
out/check_<set>/异常样本/<label>/，方便人工检查。
"""
import argparse
import json
import os
import subprocess
import sys

ASD_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(ASD_ROOT, 'projects')
PYTHON = sys.executable


def load_project(name):
    pdir = os.path.join(PROJECTS_DIR, name)
    pjson = os.path.join(pdir, 'project.json')
    if not os.path.exists(pjson):
        sys.exit(f'找不到项目: {pjson}')
    with open(pjson, 'r', encoding='utf-8') as f:
        return pdir, json.load(f)


def _resolve(path):
    """项目清单里的路径相对 ASD_ROOT 解析为绝对路径。"""
    return path if os.path.isabs(path) else os.path.join(ASD_ROOT, path)


def rebuild_symlinks(link_dir, mapping):
    """清空 link_dir 下的软链并据 mapping {name: target} 重建。"""
    os.makedirs(link_dir, exist_ok=True)
    for entry in os.listdir(link_dir):
        p = os.path.join(link_dir, entry)
        if os.path.islink(p):
            os.unlink(p)
    for name, target in mapping.items():
        target = _resolve(target)
        if not os.path.isdir(target):
            print(f'  ⚠ 跳过不存在的目录: {target}')
            continue
        os.symlink(target, os.path.join(link_dir, name))
        print(f'  {name} -> {target}')


def run(cmd):
    print('\n$ ' + ' '.join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f'子进程失败（退出码 {r.returncode}）: {" ".join(cmd)}')


def do_learn(pdir, proj):
    print('\n' + '#' * 80 + f'\n# 学基准: {proj["display_name"]} ({proj["name"]})\n' + '#' * 80)
    baseline_input = os.path.join(pdir, 'baseline_input')
    config_dir = os.path.join(pdir, 'config')
    os.makedirs(config_dir, exist_ok=True)

    print('\n重建 baseline_input 软链:')
    rebuild_symlinks(baseline_input, {os.path.basename(_resolve(s)): s
                                      for s in proj.get('baseline_sets', [])})

    cmd = [PYTHON, os.path.join(ASD_ROOT, 'phase2_learn_baselines.py'),
           baseline_input, config_dir]

    override = proj.get('calib_override') or {}
    if override:
        calib_file = os.path.join(config_dir, 'calib_override.json')
        with open(calib_file, 'w', encoding='utf-8') as f:
            json.dump(override, f, ensure_ascii=False, indent=2)
        cmd += ['--calib-file', calib_file]
        print(f'\n应用项目级 calib 覆盖: {override}')
    run(cmd)


def do_validate(pdir, proj, only_sets=None):
    print('\n' + '#' * 80 + f'\n# 验证: {proj["display_name"]} ({proj["name"]})\n' + '#' * 80)
    thresholds = os.path.join(pdir, 'config', 'thresholds.json')
    if not os.path.exists(thresholds):
        sys.exit(f'缺少 {thresholds}，请先 --learn')

    valid_input = os.path.join(pdir, 'valid_input')
    out_dir = os.path.join(pdir, 'out')
    all_valid = proj.get('valid_sets', {})

    if only_sets:
        missing = [s for s in only_sets if s not in all_valid]
        if missing:
            sys.exit(f'project.json 无此验证集: {missing}；可选: {list(all_valid)}')
        run_sets = {k: all_valid[k] for k in only_sets}
        print(f'\n只验证指定集: {list(run_sets)}')
    else:
        run_sets = all_valid

    print('\n重建 valid_input 软链:')
    rebuild_symlinks(valid_input, all_valid)  # 始终重建全部软链，保持链完整

    for set_name in run_sets:
        in_dir = os.path.join(valid_input, set_name)
        check_dir = os.path.join(out_dir, f'check_{set_name}')
        run([PYTHON, os.path.join(ASD_ROOT, 'phase3_unified_check.py'),
             in_dir, thresholds, check_dir])

    print('\n' + '=' * 80 + '\n各验证集结果汇总:')
    for set_name in run_sets:
        sjson = os.path.join(out_dir, f'check_{set_name}', 'summary.json')
        if os.path.exists(sjson):
            with open(sjson, 'r', encoding='utf-8') as f:
                s = json.load(f)
            print(f'  [{set_name}] {s.get("pred_counts")}')

    # 验证完自动出客户报告（浅色、按工位/产品分组）
    import customer_report
    customer_report.generate(proj['name'])


def do_phase1(target_dir, out_dir):
    print('\n' + '#' * 80 + '\n# 无基准快筛 (phase1)\n' + '#' * 80)
    os.makedirs(out_dir, exist_ok=True)
    run([PYTHON, os.path.join(ASD_ROOT, 'phase1_hard_rules.py'), target_dir, out_dir])


def do_test(target_dir):
    """夹具测试：对目录跑无基准 phase3（=硬规则 empty/clipped/corrupt + 兼容性 incompatible），
    不需要项目/基准，结果落 tests/out/<目录名>/。用 tests/nobaseline_thresholds.json 作标准。"""
    print('\n' + '#' * 80 + '\n# 夹具测试（无基准 phase3：硬规则 + 兼容性）\n' + '#' * 80)
    if not os.path.isdir(target_dir):
        sys.exit(f'目录不存在: {target_dir}')
    thresholds = os.path.join(ASD_ROOT, 'tests', 'nobaseline_thresholds.json')
    if not os.path.exists(thresholds):  # 缺了就据 common.CALIB 现造
        import common
        os.makedirs(os.path.dirname(thresholds), exist_ok=True)
        with open(thresholds, 'w', encoding='utf-8') as f:
            json.dump({'meta': {'n_baselines': 0}, 'baselines': {}, 'calib': common.CALIB},
                      f, ensure_ascii=False, indent=2)
    name = os.path.basename(os.path.normpath(target_dir))
    out_dir = os.path.join(ASD_ROOT, 'tests', 'out', name)
    # 跳过 incompatible 的导出：无基准下外来数据全是 incompatible，只拎硬规则命中(empty/clipped/corrupt)
    run([PYTHON, os.path.join(ASD_ROOT, 'phase3_unified_check.py'),
         target_dir, thresholds, out_dir, '--export-skip', 'incompatible'])
    print(f'\n夹具测试结果: {out_dir}（report.txt / result.json / 异常样本/ 只含硬规则命中）')


def main():
    parser = argparse.ArgumentParser(description='按项目驱动音频 QC 管线')
    parser.add_argument('project', nargs='?', help='项目名（projects/ 下的目录名，如 jifeng / huakai）；'
                                                    '用 --test 时可省略')
    parser.add_argument('--learn', action='store_true', help='只学基准 (phase2)')
    parser.add_argument('--validate', action='store_true', help='只验证 (phase3)')
    parser.add_argument('--all', action='store_true', help='学+验证（不带任何开关时的默认）')
    parser.add_argument('--set', dest='sets', action='append', metavar='NAME', default=None,
                        help='只验证指定验证集（project.json valid_sets 的键），可重复或逗号分隔；'
                             '隐含只验证、不重学基准')
    parser.add_argument('--phase1', metavar='DIR', default=None,
                        help='对指定目录跑无基准快筛（结果落 projects/<proj>/out/phase1_screen）')
    parser.add_argument('--test', metavar='DIR', default=None,
                        help='夹具测试：对目录跑无基准 phase3（硬规则+兼容性），落 tests/out/<目录名>/，'
                             '不需要项目/基准')
    args = parser.parse_args()

    if args.test:  # 夹具测试与项目无关，project 可省
        do_test(args.test)
        return

    if not args.project:
        parser.error('需要指定项目名（除非用 --test）')
    pdir, proj = load_project(args.project)

    if args.phase1:
        do_phase1(args.phase1, os.path.join(pdir, 'out', 'phase1_screen'))
        return

    only_sets = None
    if args.sets:
        only_sets = [s for grp in args.sets for s in grp.split(',') if s.strip()]

    do_all = args.all or not (args.learn or args.validate or only_sets)
    if args.learn or do_all:
        do_learn(pdir, proj)
    if args.validate or only_sets or do_all:
        do_validate(pdir, proj, only_sets=only_sets)

    print('\n完成。配置: ' + os.path.join(pdir, 'config') +
          '   结果: ' + os.path.join(pdir, 'out'))


if __name__ == '__main__':
    main()
