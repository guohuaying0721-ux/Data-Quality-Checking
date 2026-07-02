# 测试夹具（考验脚本检测能力）

这里的数据**不是生产项目**，是用来验证 QC 脚本（phase1 硬规则 / phase3 检测）
能不能正确逮出各类异常的边界样本集。

| 目录 | 用途 | 标注 |
|------|------|------|
| `empty_library/` | 已知**空采**样本（`*-empty.wav`）+ 一个全静音 `empty.wav`，验证 empty 检测 | 文件名即标签 |
| `huade/` | 华德数据，train/valid 两批，含已知**干扰**等异常 | `label.xlsx`（sample_id, issue_type） |
| `zass/` | ZASS 数据，4089 wav，量最大，验证整体跑量与多类异常 | `label.xlsx`（sample_id = wav 文件名主干） |

## ⚠ 标签可信度

`label.xlsx` 的标注**不是黄金答案**，本身存在错标。已知案例：zass 有 37 条真**削波**
文件原先被错标成 `interference`，已据脚本检出结果改正为 `clipped`（2026-06-17，原表见
`*.xlsx.bak`）。

因此任何基于这些 label 的回归不要做成 pass/fail 的查准/查全，应做成
**「脚本判定 ↔ 标签」差异复核清单**：差异 = 脚本误报 *或* 标签错，由人工定夺。

## 跑一遍看检出

**这些夹具只用来验证两类规则：硬规则（corrupt/empty/clipped）+ 兼容性（无匹配基准→incompatible）。
不测 per-group 阈值（too_weak/too_loud/late_start/静音/时长/interference）——那些需要学基准，
不属于夹具测试范围。** 所以**不需要也不应该学基准**。

推荐用 `run_project --test`（无基准 phase3，硬规则 + 兼容性一次跑全，结果落 `tests/out/<目录名>/`）：

```bash
.venv/bin/python run_project.py --test tests/fixtures/zass     # 不用给项目名
```

- 用固定的 `tests/nobaseline_thresholds.json`（空基准）作标准，缺了会自动据 common.CALIB 现造。
- 外来数据无匹配基准 → 全判 `incompatible`（这正是在验证"未知数据必须被拒、无兜底"的兼容性机制）；
  其中被硬规则逮到的 empty/clipped/corrupt 单独归类。
- 异常样本导出**跳过 incompatible**（否则无基准下整套外来数据都会被拷一遍），只拎硬规则命中到
  `tests/out/<目录名>/异常样本/<label>/`，方便核对削波/空采。

只要硬规则那一半，也可单跑 phase1（更轻，不判 incompatible）：

```bash
.venv/bin/python phase1_hard_rules.py tests/fixtures/zass /tmp/zass_screen
```
