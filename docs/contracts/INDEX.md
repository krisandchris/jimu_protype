# JIMU 契约索引

## 冻结契约

按仓库约定，优先级 1 最高。同一工作包的实现和验收使用下表所列版本。D05 中引用的 D03 V1.2 在本仓库映射到 D03 V1.1。Batch 02-A 细化条款沿用 D05 R2.2.2 的执行 DAG 和 Comparator Priority 0～7。[仓库工程约束](../../AGENTS.md)

| 优先级 | 冻结契约 | 适用里程碑与条款 |
| --- | --- | --- |
| 1 | [WP-A1 V1.1](frozen/WP-A1_V1.1.md) | M0 资产引用取自 §2.1。R1、C3 使用 Rule AST 类型系统、静态检查与依赖提取。 |
| 1 | [WP-A2 V1.1](frozen/WP_A2_Ranking_Schema_V1.1.md) | M0 数值约束取自第 1 项修订。R2 使用 N06 Ranking 与 Wire Schema 的 V1.1 修订片段。 |
| 1 | [WP-A3/A4/A5 V1.1](frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md) | M0 共用 §0.2 的严格模型配置。R3 使用 §1～2 的证据完整性与 AST 聚合。R4 使用 §3～7 的比较规则与验收用例。 |
| 2 | [D05 R2.2.2](frozen/D05_R2.2.2.md) | 运行态与 C3 的编译基线，§0 定义声明态、编译态与运行态边界。 |
| 3 | [D03 V1.1](frozen/D03_V1.1.md) | C1～C3 的资产与编译基线，§5.1 定义跨资产引用的版本锁定。 |

## WP-A3/A4/A5 验收样例

每行的两个 JSON 文件分别保存 §5 的输入与期望输出。文件保留原文的字段、数组顺序、数值精度和 `null`。这些样例使用已解析的运行态事实投影。[§5 原文](frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md#5-minimal-positive--negative-json-examples)

| Case | 验收重点 | 输入 | 期望输出 |
| --- | --- | --- | --- |
| 01 · A3 | Category 级隔离。A 为 `UNAVAILABLE`，消费 GLOBAL 工具事实的 B 为 `COMPLETE`。 | [输入](fixtures/wp_a345/case_01.input.json) | [输出](fixtures/wp_a345/case_01.expected.json) |
| 02 · A3 | Required `UNKNOWN` 得到 `COMPLETE`，Required `MISSING` 得到 `UNAVAILABLE`。 | [输入](fixtures/wp_a345/case_02.input.json) | [输出](fixtures/wp_a345/case_02.expected.json) |
| 03 · A4 | `PARTIAL_FAILED` 保留成功与失败类别，以及按类别排序的两条 Trace 引用。 | [输入](fixtures/wp_a345/case_03.input.json) | [输出](fixtures/wp_a345/case_03.expected.json) |
| 04 · A5 | AST 触发类别位于 JEV 第 2 名，Top-K 完整覆盖，主状态为 `AGREE_TOPK_RANK_DIFF`。 | [输入](fixtures/wp_a345/case_04.input.json) | [输出](fixtures/wp_a345/case_04.expected.json) |
| 05 · A5 | JEV 判断无违规，AST 触发一个类别，主状态为 `EXISTENCE_CONFLICT`。 | [输入](fixtures/wp_a345/case_05.input.json) | [输出](fixtures/wp_a345/case_05.expected.json) |
| 06 · A5 | O006 得到 `JEV_FAILED` 并保留 AST 差异，O007 独立得到 `AGREE_NON_VIOLATION`。 | [输入](fixtures/wp_a345/case_06.input.json) | [输出](fixtures/wp_a345/case_06.expected.json) |

## M0 验收

在 Python 3.11+ 虚拟环境中，从仓库根目录安装开发依赖并运行门禁。

```bash
python -m pip install -e '.[dev]'
ruff check src/ tests/
mypy --strict src/
pytest -q
pytest -q tests/architecture/test_layer_boundaries.py
python -m jimu.contracts.verify_schemas
```

最后一条命令将各共享模型和 `FiniteScore` 的 JSON Schema 输出到标准输出，键为完整 Python 类型名。模型导出失败时命令以非零状态退出。
