# 按项目隔离的音频 QC 管线

每个客户/工位项目一个目录 `projects/<name>/`，各自学基准、各自验证、各自配置，互不影响。
管线脚本（`../common.py` `../phase1/2/3*.py`）项目无关，由 `../run_project.py` 按项目驱动。

## 目录约定

```
projects/<name>/
├─ project.json      # 唯一真相源：基准集 / 验证集 / calib 覆盖
├─ baseline_input/   # run_project 据 baseline_sets 重建的软链（phase2 输入）
├─ valid_input/      # run_project 据 valid_sets 重建的软链（每个验证集一个子目录）
├─ config/           # phase2 产物：baselines.json + thresholds.json（含本项目固化的 calib）
│                    #   有 calib 覆盖时还会有 calib_override.json
└─ out/              # phase3 产物：每集 check_<set>/ + 数据质量报告.html（客户版）
```

验证完自动生成客户报告（自包含、离线可用）：

- **`out/数据质量报告.html`** — **客户版**（浅色看板，面向客户）。**按工位/产品分组，绝不揉在一起**：
  顶部状态横幅（结论+综合评级，不写计数）→ 每个验证集(产品)一节 → 每个检测点(工位)一张卡，
  卡内含 ⏱️时长分布表 + 🔊能量分布表（能量水平徽章）+ 📶信号质量条（前后静音/起声状态/噪声/
  完整性，定性显示）+ 本工位结论 → 底部🏆综合质量评估。**只讲客观数据特征与"是否正常"，
  不写样本数/文件数，不暴露阈值/比值等内部参数**。手动重生成：`python customer_report.py <name>`。

（内部版报告 `report_html.py` 已于 2026-06-18 移除，run_project 不再生成 `report.html`。）

分组维度取 sidecar 的 `detectPointName`（工位/检测点），有 `detectStationName` 时附在后面
（如 `通道1 · HKPLD-GZ-0007-中`）；产品/批次维度取验证集名。

`baseline_input` / `valid_input` 下的软链由 `run_project.py` 每次自动重建，**不要手改**；
改数据集归属只动 `project.json`。

## project.json

```json
{
  "name": "huakai",
  "display_name": "华楷PLD",
  "note": "工位说明、标定历史等",
  "baseline_sets": ["datasets/huakai/DS-202606-0043_华楷PLD"],
  "valid_sets": { "ds0044_验证集": "datasets/huakai/DS-202606-0044_华楷PLD" },
  "calib_override": {}
}
```

- `name`：必须等于 `projects/` 下的文件夹名，也是命令行 `<project>` 参数。
- `display_name`：报告标题里显示的中文名。
- `note`：**纯人读备注，程序不读、不影响运行**。写给以后的自己看，建议含三类：①工位/产品说明
  与通道对应关系；②标定历史（calib 调没调、依据什么）；③特殊约定/坑。新项目最简一句即可，
  如 `"<客户><产品> 工位。CALIB 暂用全局默认。"`，调过之后再补。
- 路径相对仓库根（`asd/`）解析，也可写绝对路径。
- `baseline_sets`：列表，多个会合并成一份基准（按 `detectPointName_stage_ch` 分组）。
- `valid_sets`：`{结果目录名: 数据集路径}`，每个独立跑 phase3，落 `out/check_<名>/`。
- `calib_override`：只写要改的灵敏度键，其余继承 `common.CALIB`。空 `{}` = 全用全局默认。

## 灵敏度（CALIB）隔离

`common.CALIB` 是全局默认。项目在 `calib_override` 里覆盖个别键，`run_project` 写出
`config/calib_override.json` 喂给 phase2，**固化进该项目的 `thresholds.json`**；phase3 检测时
以文件内 calib 为准。所以调一个项目的灵敏度不会牵动其他项目。继峰那套 ×0.5/×1.5/×0.35 是据
继峰验证集标的，华楷等新项目按自己的复核结果各填各的。

## 用法

解释器固定用 `.venv/bin/python`（`.venv/bin/` 下的 `python` / `python3` / `python3.10`
都是软链，最终都指向同一个系统 `python3.10`，随便哪个都一样）。**必须走 `.venv/bin/` 路径**，
直接敲 `python` 用的是系统环境、没有 numpy/scipy/openpyxl。

```bash
.venv/bin/python run_project.py <project>                # 学基准 + 验证全部集（默认，不带开关时）
.venv/bin/python run_project.py <project> --learn        # 只学基准 (phase2)
.venv/bin/python run_project.py <project> --validate     # 只验证全部集 (phase3，需先 --learn 过)
.venv/bin/python run_project.py <project> --set <集名>    # 只验证指定集，不重学基准
.venv/bin/python run_project.py <project> --phase1 <dir>  # 对任意目录跑无基准快筛
.venv/bin/python run_project.py --test <dir>             # 夹具测试：无基准 phase3（硬规则+兼容性），落 tests/out/，不需项目
```

- `<project>`：`projects/` 下的文件夹名（= project.json 的 `name`），如 `jifeng` / `huakai`。
- `--set`：值是 `valid_sets` 里的**键名**（不是数据集路径），如 `ds0050_异常验证集`。可逗号分隔或重复
  `--set a --set b` 跑多个；隐含只验证、不重学基准。软链仍重建全部，客户报告含所有已验证的集。
  集名写错会报错并列出可选项。
- 选开关的经验：新项目第一次 / 改了基准集 / 改了 `calib_override` → 不带开关全跑（或 `--learn`）；
  只新增了**验证集**、基准没变 → `--validate` 全验，或 `--set <新集名>` 只验新增那个。

## 新增一个项目

1. 把数据集放进 `datasets/<name>/`（如 `datasets/jifeng/`、`datasets/huakai/`）。
2. `mkdir -p projects/<name>`，写 `project.json`（基准集 + 验证集，路径写 `datasets/<name>/...`）。
3. `python run_project.py <name>` —— 软链、config、out、客户报告全自动生成。
4. 看 `out/check_*/report.txt`，据误报调 `calib_override`，重跑。

## 给已有项目加数据集

只动该项目的 `project.json`：往 `baseline_sets` / `valid_sets` 加 `datasets/<name>/...` 路径，
再 `python run_project.py <name>`（只验证不重学基准用 `--validate`）。**新机台/新阶段的数据必须
先加进 `baseline_sets` 重学基准，否则该 `机台×stage×ch` 组不在基准里会全判 `incompatible`。**

## 已有项目

- **jifeng** 继峰座椅 EOL —— 16 基准组/152 样本，calib 已据验证集标定。
- **huakai** 华楷PLD —— 4 基准组/40 样本（通道1/2 × stage1/2），calib 暂用全局默认。
