# AGENTS.md — JIMU Inspection Platform & Runtime Constitution

本文件为 Codex 及所有自动化开发代理的最高工程约束。开始任何编码前，必须先通读本文件与当前任务对应的冻结契约。

---

## 1. 真理源（Source of Truth）与裁决优先级

`docs/contracts/frozen/` 为**只读冻结契约目录**，严禁修改、删减或重写其中任何文件。

### 契约裁决优先级（由高到低）
1. **D05 Batch 02-A 细化契约（Frozen）**：
   - `docs/contracts/frozen/WP-A1_V1.1.md`
   - `docs/contracts/frozen/WP_A2_Ranking_Schema_V1.1.md`
   - `docs/contracts/frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md`
2. **D05 Batch 01 运行态基线（Frozen）**：
   - `docs/contracts/frozen/D05_R2.2.2.md`
3. **D03 领域与资产管理基线（Frozen）**：
   - `docs/contracts/frozen/D03_V1.1.md`

> **版本对齐说明**：D05 文档头部引用的 `D03 V1.2` 在本仓库中统一映射到 `docs/contracts/frozen/D03_V1.1.md`（若具体字段存在演进差异，以高优先级 D05 文档为准）。
> **冲突红线**：细化契约补充执行细节，但不得推翻 `D05_R2.2.2.md` 已冻结的执行 DAG 与 Comparator Priority 0～7。发现契约冲突必须停止编码并上报，严禁擅自放宽 Schema。

---

## 2. 三层隔离架构与 Import 红线

系统按 **控制面（资产管理）→ 编译层（Bundle 打包）→ 运行面（D05 执行引擎）** 物理隔离，严格区分声明态（Authoring）、编译态（Compiled）与运行态（Runtime）：

```text
src/jimu/
├── contracts/       # 共享契约：StrictModel, FiniteScore, AssetVersionRef, CompiledBundle Schema
├── control_plane/   # 【控制面】Subject/Question/Rule/Tool/Category 资产、版本、引用索引、影响门禁、平台 API
├── compiler/        # 【编译层】WP-A1 静态检查、依赖提取、1:1 校验、冻结不可变 Bundle Snapshot
└── runtime/         # 【运行面】ast_engine(A1), n06_ranking(A2), evidence(A3), aggregation(A4), comparator(A5)
```

### 绝对禁止的跨层依赖
- `src/jimu/runtime/` **严禁** import `src/jimu/control_plane/`，严禁回源读取 Authoring 资产；只能消费 `contracts/` 与编译产出的不可变 Bundle。
- `src/jimu/control_plane/` **严禁** 绕过 `src/jimu/compiler/` 直接构造运行态 Bundle 或手拼 `CompiledRuleFieldDependency` / `CompiledRuleToolDependency`。
- `src/jimu/contracts/` **严禁** import `control_plane/`、`compiler/` 或 `runtime/`。

---

## 3. 全局工程与 Pydantic v2 红线

Python 3.11+ / Pydantic v2 开发必须无条件遵守：

1. **统一基类**：所有核心模型必须继承 `src/jimu/contracts/base.py` 中的 `StrictModel`：
   ```python
   class StrictModel(BaseModel):
       model_config = ConfigDict(
           extra="forbid",
           frozen=True,
           strict=True,
           allow_inf_nan=False,
       )
   ```
2. **禁止裸 `dict`**：核心数据结构、依赖、判断、差异与 Provenance 均须为强类型 Pydantic 模型；JSON 数组统一使用 `list[...]`。
3. **数值与边界**：分数与比例使用 `FiniteScore = Union[StrictFloat, StrictInt]`，显式约束 `ge=0.0, le=1.0`，拒绝 `NaN` / `Infinity` 与隐式类型转换。
4. **确定性排序**：禁止依赖并发返回顺序、数据库顺序或 `set` 遍历顺序：
   - Category 分区与 Diff：`category_id ASC`
   - 失败依赖明细：`(source ASC, dependency_id ASC)`
   - N06 候选排序：`score DESC, category_id ASC`
5. **禁止静默兜底**：重复 Judgment、Identity 不一致、非法枚举、越界索引或协议违约必须显式抛错或输出对应 Protocol Failure，严禁静默去重、覆盖或降级。
6. **JSON Schema 可导出**：所有核心模型必须支持无报错调用 `model.model_json_schema()`。

---

## 4. 各模块核心不变量（不可破坏）

### 控制面与编译层（D03 + Compiler）
- 核心资产：`Subject`、`Question`、`Rule`、`ToolCapability`、`Category`；不引入独立 `Applicability` 对象，适用主体由 Rule 的 `applicable_subject_refs` 维护。
- `Category` 与 `Rule` 严格 **1:1**，禁止多 Rule 投票或优先级覆盖。
- 维护 `asset_reference_index` 反向引用索引，删除或破坏性变更必须经过影响门禁（Impact Gate）。
- 编译期完成 WP-A1 静态检查与依赖提取，锁定 `bundle_digest` 与 `category_catalog_digest`。

### 运行面（D05 R2.2.2 + Batch 02-A）
- **目标范围**：仅支持 `OBJECT` / `OBJECT_GROUP`（无 `TargetKind.IMAGE`）；全图工具事实使用 `GLOBAL` scope；零有效评估走 Clean Frame 短路。
- **WP-A1（Rule AST）**：Kleene 三值逻辑 + Required Fact 缺失 Fail-Fast + 显式 `rule_trace_ref` 溯源。
- **WP-A2（N06 Ranking）**：模型 Wire Output 仅输出索引与分数；Runtime 解析 Catalog 与证据并执行确定性排序与 Policy 映射；图像/目标/类别三级错误隔离。
- **WP-A3（Evidence Completeness）**：仅消费编译态依赖与 N04 Final Attempt / N05 Final Execution；`UNKNOWN` / `NULL` / `NOT_APPLICABLE` 均属合法事实（非失败）；`MISSING` / `ERROR` / `UNAVAILABLE` 触发 `UNAVAILABLE`（Required）或 `PARTIAL`（Optional）；Category 级严格隔离；三类失败列表两两互斥且按 `(source, dependency_id)` 唯一排序；`n04_final_attempt_id` 允许在纯 Tool 依赖或 N04 未执行时为 `None`。
- **WP-A4（AST Aggregation）**：三态聚合 `ALL_SUCCEEDED` / `PARTIAL_FAILED` / `ALL_FAILED`；`PARTIAL_FAILED` 必须无损保留成功类别的 `triggered_categories` / `non_triggered_categories` / `uncertain_categories`。
- **WP-A5（Comparator）**：
  - 严格按 D05 Priority 0～7 判定唯一主状态，同时保留全量 `per_category_diffs`。
  - JEV 内部语义违约必须在 **P3** 拦截为 `JEV_FAILED`，不得泄漏到 P4/P5。
  - JEV 不可用（`FAILED`/`MISSING`）时，非 FAILED 的 AST Category 必须标为 `AST_ONLY_JEV_UNAVAILABLE`，不得误标为 `AST_TRIGGERED_JEV_MISSED`。
  - `TopKMetrics` 在 P1/P2/P3 必须为 `None`，在 P4～P7 且 AST `ALL_SUCCEEDED` 时必须完整生成且不发生除零。
  - **严禁输出 `final_decision` 或 `needs_hitl`**。

---

## 5. 模块与冻结契约映射表

| 里程碑 | 必读冻结契约 | 目标代码目录 | 验收测试 |
| :--- | :--- | :--- | :--- |
| M0 共享基座 | 全部契约 §0 | `src/jimu/contracts/` | `tests/contracts/`、`tests/architecture/` |
| R1 WP-A1 | `docs/contracts/frozen/WP-A1_V1.1.md` | `src/jimu/runtime/ast_engine/` | `tests/runtime/test_wp_a1_ast.py` |
| R2 WP-A2 | `docs/contracts/frozen/WP_A2_Ranking_Schema_V1.1.md` | `src/jimu/runtime/n06_ranking/` | `tests/runtime/test_wp_a2_n06.py` |
| R3 WP-A3/A4 | `docs/contracts/frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md` (§1-2) | `src/jimu/runtime/{evidence,aggregation}/` | `tests/runtime/test_wp_a3_a4.py` |
| R4 WP-A5 | `docs/contracts/frozen/WP-A3_A4_A5_Evidence_Comparison_Closure_V1.1.md` (§3-7) | `src/jimu/runtime/comparator/` | `tests/runtime/test_wp_a5_comparator.py` |
| C1-C2 资产与门禁 | `docs/contracts/frozen/D03_V1.1.md` | `src/jimu/control_plane/` | `tests/control_plane/` |
| C3 编译与打包 | `D03_V1.1.md` + `D05_R2.2.2.md` + `WP-A1_V1.1.md` | `src/jimu/compiler/` | `tests/compiler/` |

---

## 6. 单工单交付流程与自动化门禁

每次只执行一个里程碑工单，严格按顺序推进：
1. 实现 Pydantic v2 Schema 与全部 `@model_validator`；
2. 实现无副作用的纯函数核心逻辑；
3. 按契约 §5 正反例与 Exit Gate 补齐 `pytest` 测试；
4. 运行以下全部门禁，全部通过后方可汇报完成：

```bash
ruff check src/ tests/
mypy --strict src/
pytest -q
pytest -q tests/architecture/test_layer_boundaries.py
python -m jimu.contracts.verify_schemas
```

汇报时必须列出：变更文件清单、已通过的 Exit Gate 条目、以及上述 5 条命令的实际执行结果。
