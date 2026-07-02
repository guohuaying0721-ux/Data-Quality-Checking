# 音频质量检查（QC）管线

工业产线录音的质量检查：判别 corrupt / empty / clipped / too_weak / too_loud /
late_start / silence / too_short / too_long / interference 等异常。纯 `numpy`/`scipy`，
无 librosa。**按项目隔离**——每个客户/工位项目各自学基准、各自验证、各自配置，互不影响。

> **完整路径**：项目根 `/mnt/disk2/tmp/asd`，本文件 `/mnt/disk2/tmp/asd/README.md`，venv 解释器 `/mnt/disk2/tmp/asd/.venv/bin/python`。
> `run_project.py` 不可直接执行（无 +x），且脚本内用相对路径——**必须先 cd 到项目根、用 venv 的 python 跑**：
>
> ```bash
> cd /mnt/disk2/tmp/asd && .venv/bin/python run_project.py <project>
> # 或全路径：/mnt/disk2/tmp/asd/.venv/bin/python /mnt/disk2/tmp/asd/run_project.py <project>
> ```

## 三阶段

| 阶段 | 脚本 | 作用 | 是否需基准 |
|------|------|------|-----------|
| 1 | `phase1_hard_rules.py` | 硬规则快筛 corrupt/empty/clipped（联调当天用） | 否 |
| 2 | `phase2_learn_baselines.py` | 从干净样本按 `检测点×阶段×通道` 学基准统计量 | — |
| 3 | `phase3_unified_check.py` | 套 CALIB 跑 per-group 阈值 + 硬规则做全量判定 | 是 |

`common.py` 是单一来源：音频 IO、特征提取、empty/clipped 硬规则、灵敏度系数 `CALIB`。
**阈值 = 逐组学习的 base（项目自己的）× CALIB 系数（全局默认，项目可覆盖）**，校准只动 `common.CALIB` 或项目 `calib_override`。

可被项目 `calib_override` 覆盖的硬规则阈值（默认值在 `common.CALIB`，行为与原硬规则一致）：
- `interference`（zcr 阈值，默认 0.375）、`clipped_ratio`（削波率阈值，默认 0.001）。
- 适用于信号特征与跨产品经验值差异大的产品（如 EPS 电机信号天生高 zcr、满量程贴顶、削波率偏高）。
- empty 的 `cond_a` 另有能量地板常量 `HARD_RULES['empty']['cond_a_energy_floor']`（默认 0.05），防止稳态实信号被误判为空采。

## 按项目驱动

```bash
.venv/bin/python run_project.py <project>                # 学基准 + 验证全部集 + 出报告（默认）
.venv/bin/python run_project.py <project> --learn        # 只学基准 (phase2)
.venv/bin/python run_project.py <project> --validate     # 只验证全部集 (phase3) + 出报告
.venv/bin/python run_project.py <project> --set <集名>    # 只验证指定集（valid_sets 的键），不重学基准
.venv/bin/python run_project.py <project> --phase1 <dir>  # 对任意目录跑无基准快筛
.venv/bin/python run_project.py --test <dir>             # 夹具测试：无基准 phase3（硬规则+兼容性），落 tests/out/
```

`--set` 可逗号分隔或重复跑多个集；只新增验证集、基准没变时用它最省（如 `--set ds0050_异常验证集`）。

验证完自动出客户报告（自包含，落 `projects/<name>/out/`）：
- **`数据质量报告.html`** — 客户版（浅色，按工位/产品分组，只讲结论，`customer_report.py`）

（内部版报告 `report_html.py` 已于 2026-06-18 移除。）

项目目录约定、`project.json` 字段、CALIB 隔离、怎么新增项目 → 见 **[`projects/README.md`](projects/README.md)**。

## 目录结构

```
asd/
├─ common.py  phase1/2/3_*.py        # 管线核心（项目无关）
├─ run_project.py                    # 按项目驱动入口
├─ customer_report.py                # 客户报告生成器
├─ requirements.txt                  # numpy / scipy / openpyxl
├─ projects/                         # 各项目（jifeng / huakai …），含 README
│  └─ <name>/ project.json · baseline_input/ · valid_input/ · config/ · out/
├─ datasets/                         # 原始数据：datasets/jifeng/ datasets/huakai/（project.json 相对根引用）
├─ tests/fixtures/                   # 考验脚本检测能力的测试夹具（见其 README，标签可能有错）
├─ docs/                             # audio_qc_pipeline_overview.html 流程总览
├─ 异常审查/                          # 早期全局异常筛查产物（一次性）
└─ .venv/
```

## 环境

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 已有项目

- **jifeng** 继峰座椅 EOL —— 16 基准组/152 样本，CALIB 已据继峰验证集标定。
- **huakai** 华楷PLD —— 4 基准组（通道1/2 × stage1/2），CALIB 暂用全局默认。
- **nsk-eps8** NSK EPS 电机（MQB）—— 信号天生高 zcr(≈0.85)+满量程(peak=1.0)，跨产品固定阈值全失准；`calib_override` 设 `interference:0.9` + `clipped_ratio:0.08`，empty 靠 cond_a 能量地板修复。验证集 DS-0063 标定后 80 全 keep。
