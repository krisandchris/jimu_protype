# JIMU 契约索引

## 冻结契约

按仓库约定，优先级 1 最高。同一工作包的实现和验收使用下表所列版本。D05 中引用的 D03 V1.2 在本仓库映射到 D03 V1.1。Batch 02-A 细化条款沿用 D05 R2.2.2 的执行 DAG 和 Comparator Priority 0～7。[仓库工程约束](../../AGENTS.md)

| 优先级 | 冻结契约 | 适用里程碑与条款 |
| --- | --- | --- |
| 1 | [WP-A1 V1.1](frozen/WP-A1_V1.1.md) | M0 资产引用取自 §2.1。R1、C3 使用 Rule AST 类型系统、静态检查与依赖提取。 |
| 1 | [WP-A2 V1.1](frozen/WP_A2_Ranking_Schema_V1.1.md) | §2 定义 Wire 与 Catalog，§3～7 定义运行态判断、版本化策略与错误分级，§9 给出五个验收用例。 |
| 1 | [WP-A3/A4/A5 V1.1](frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md) | M0 共用 §0.2 的严格模型配置。R3 使用 §1～2 的证据完整性与 AST 聚合。R4 使用 §3～7 的比较规则与验收用例。 |
| 2 | [D05 R2.2.2](frozen/D05_R2.2.2.md) | 运行态与 C3 的编译基线，§0 定义声明态、编译态与运行态边界。 |
| 3 | [D03 V1.1](frozen/D03_V1.1.md) | C1～C3 的资产与编译基线，§5.1 定义跨资产引用的版本锁定。 |

## WP-A2 验收样例

第 01、04、05 组的输入保存 §9 的完整 Wire JSON。第 02 组使用原文的 Active Ranking 与映射参数。第 03 组使用三个同分类别的无序输入和确定性排名。期望值均来自对应案例的文字说明。[WP-A2 §9](frozen/WP_A2_Ranking_Schema_V1.1.md#9-minimal-positive--negative-json-examples)

| Case | 验收重点 | 输入 | 期望输出 |
| --- | --- | --- | --- |
| 01 | 保留三个 Raw 候选，阈值与 Top-K 后保留两个 Active 候选。 | [输入](fixtures/wp_a2/case_01.input.json) | [输出](fixtures/wp_a2/case_01.expected.json) |
| 02 | `0.85 >= 0.55 + 0.10` 映射为 `NON_VIOLATION`。 | [输入](fixtures/wp_a2/case_02.input.json) | [输出](fixtures/wp_a2/case_02.expected.json) |
| 03 | 同分按 `category_id ASC` 排列。 | [输入](fixtures/wp_a2/case_03.input.json) | [输出](fixtures/wp_a2/case_03.expected.json) |
| 04 | Target 0 同时保留分数越界和 Catalog 外索引两个原因。 | [输入](fixtures/wp_a2/case_04.input.json) | [输出](fixtures/wp_a2/case_04.expected.json) |
| 05 | Target 0 注入运行态字段，产生 Target 级 Schema 校验失败。 | [输入](fixtures/wp_a2/case_05.input.json) | [输出](fixtures/wp_a2/case_05.expected.json) |

**样例与正式 Schema 的边界**

- WP-A2 的 `FiniteUnitFloat` 先通过共享 `FiniteScore` 校验，再将合法数值显式表示为浮点数。A3～A5 和共享基类继续使用保持数值类型的 `FiniteScore`。
- Case 02 验收 A2 的策略映射。如果该结果连同非空 Active Ranking 进入比较，A5 §3.9 在 P3 给出 `JEV_FAILED`，保留 AST 结果。
- §9 省略的 Catalog、版本、执行身份和证据坐标由校验器的测试上下文提供。正式入口要求调用方提供请求快照及策略。Evidence Index 在各来源内独立解析，允许稀疏索引。
- §7.3 规定了 Target 状态。此处使用 `target_ref_index`、`status`、`jev_judgment` 与 `error_codes` 记录逐目标结果。多个独立错误按结构校验、Catalog 校验顺序保留。

## WP-A3/A4/A5 验收样例

每行的两个 JSON 文件分别保存 §5 的输入与期望输出。文件保留原文的字段、数组顺序、数值精度和 `null`。这些样例使用已解析的运行态事实投影。[§5 原文](frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md#5-minimal-positive--negative-json-examples)

校验器将每对文件与冻结 JSON 逐字节核对，再核验输入对应的计数、分区、差异与指标。缺少版本或 Provenance 的片段按原文投影验证。比率允许集合公式的完整结果，以及 Case 04 示范的十位小数表示。

| Case | 验收重点 | 输入 | 期望输出 |
| --- | --- | --- | --- |
| 01 · A3 | Category 级隔离。A 为 `UNAVAILABLE`，消费 GLOBAL 工具事实的 B 为 `COMPLETE`。 | [输入](fixtures/wp_a345/case_01.input.json) | [输出](fixtures/wp_a345/case_01.expected.json) |
| 02 · A3 | Required `UNKNOWN` 得到 `COMPLETE`，Required `MISSING` 得到 `UNAVAILABLE`。 | [输入](fixtures/wp_a345/case_02.input.json) | [输出](fixtures/wp_a345/case_02.expected.json) |
| 03 · A4 | `PARTIAL_FAILED` 保留成功与失败类别，以及按类别排序的两条 Trace 引用。 | [输入](fixtures/wp_a345/case_03.input.json) | [输出](fixtures/wp_a345/case_03.expected.json) |
| 04 · A5 | AST 触发类别位于 JEV 第 2 名，Top-K 完整覆盖，主状态为 `AGREE_TOPK_RANK_DIFF`。 | [输入](fixtures/wp_a345/case_04.input.json) | [输出](fixtures/wp_a345/case_04.expected.json) |
| 05 · A5 | JEV 判断无违规，AST 触发一个类别，主状态为 `EXISTENCE_CONFLICT`。 | [输入](fixtures/wp_a345/case_05.input.json) | [输出](fixtures/wp_a345/case_05.expected.json) |
| 06 · A5 | O006 得到 `JEV_FAILED` 并保留 AST 差异，O007 独立得到 `AGREE_NON_VIOLATION`。 | [输入](fixtures/wp_a345/case_06.input.json) | [输出](fixtures/wp_a345/case_06.expected.json) |

## Schema 与夹具验收

在 Python 3.11+ 虚拟环境中，从仓库根目录安装开发依赖并运行门禁。

```bash
python -m pip install -e '.[dev]'
ruff check src/ tests/
mypy --strict src/
pytest -q
pytest -q tests/architecture/test_layer_boundaries.py
python -m jimu.contracts.verify_schemas
```

最后一条命令校验全部 11 对样例，将 JSON Schema 按完整 Python 类型名输出到标准输出，将统计写到标准错误。可用 `--contracts-dir` 指定含 `frozen/` 和 `fixtures/` 的契约目录。样例核对或 Schema 导出失败时，命令以非零状态退出。
