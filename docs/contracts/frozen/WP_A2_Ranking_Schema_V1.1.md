# D05 Batch 02-A — WP-A2 N06 Ranking & Wire Schema

> 文档类型：Technical Contract  
> 所属阶段：D05 Batch 02-A  
> Work Package：WP-A2  
> 对应 OPEN：R2-O01、R2-O02、R2-O03  
> 版本：V1.1  
> 状态：Proposed — Pending Independent Review  
> 适用领域基线：D03 V1.2  
> 适用技术基线：D05 R2.2.2  
> 前置契约：D05 Batch 02-A WP-A1 AST Type System V1.1  
> 技术实现：Python 3.11+ / Pydantic v2

> **修订说明：** 本文件仅收录 V1.1 的 6 处局部替换片段，供原位合并至 V1.0 主文档；不重写全文，不改变未列出的既有章节与冻结契约。

---

## 1. Pydantic v2 FiniteScore 与 NaN/Inf 防护

**替换范围：§2.1、§2.3、§2.4、§3.3、§3.4、§4.1、§5.2。**

### §2.1 StrictModel 基础定义

```python
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


FiniteScore = Union[StrictFloat, StrictInt]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )
```

### 所有分数字段统一替换

以下字段均使用 `FiniteScore`，并保留原有 `[0.0, 1.0]` 范围约束：

```python
# §2.3 RawRankedCategory
score: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

# §2.4 RawTargetJEVResult
non_violation_score: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

# §3.3 RuntimeRankedCategory
score: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

# §3.4 TargetJEVJudgment
non_violation_score: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

# §4.1 N06RankingPolicy
min_candidate_score: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

# §5.2 NonViolationMappingPolicy
margin: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)

empty_ranking_non_violation_threshold: FiniteScore = Field(
    ge=0.0,
    le=1.0,
)
```

约束：

- `StrictFloat` 接受严格浮点数；`StrictInt` 接受严格整数。
- 兼容 vLLM JSON 输出中的整数分数 `0` / `1`。
- 拒绝 `bool` 和数字字符串。
- `allow_inf_nan=False` 禁止 NaN、`+Inf`、`-Inf`。
- 所有分数字段继续执行 `[0.0, 1.0]` 范围校验。

---

## 2. bbox_xyxy 类型与 Top-K 截断公式

**替换范围：§3.2、§4.5。**

### §3.2 VisualRegionRef

```python
class VisualRegionRef(StrictModel):
    source: Literal["visual"]

    image_execution_id: StrictStr

    evidence_id: StrictStr

    bbox_xyxy: list[Union[StrictFloat, StrictInt]] = Field(
        min_length=4,
        max_length=4,
    )
```

### §4.5 Top-K 截断公式

将原公式：

```text
A = R_t[0:K]
```

替换为：

\[
A = R_t[:K]
\]

其中：

- \(K = top\_k\)
- \(\tau = min\_candidate\_score\)

其余 Ranking Pipeline 顺序与语义保持不变。

---

## 3. RawRankedCategory 字段顺序与 JSON 示例

**替换范围：§2.3、§2.5。**

### §2.3 RawRankedCategory

```python
class RawRankedCategory(StrictModel):
    category_index: StrictInt = Field(
        ge=0
    )

    evidence: RawCategoryEvidence

    reasoning: StrictStr

    score: FiniteScore = Field(
        ge=0.0,
        le=1.0,
    )
```

字段声明顺序固定为：

1. `category_index`
2. `evidence`
3. `reasoning`
4. `score`

### §2.5 模型返回示例

```json
{
  "results": [
    {
      "target_ref_index": 0,
      "ranked_categories": [
        {
          "category_index": 1,
          "evidence": {
            "visual_evidence_indices": [0],
            "observation_evidence_indices": [0, 1],
            "tool_evidence_indices": [0]
          },
          "reasoning": "目标存在明确的违规停放特征",
          "score": 0.91
        }
      ],
      "non_violation_score": 0.08
    }
  ]
}
```

Guided Decoding Schema 与 Pydantic 字段声明顺序保持一致。

---

## 4. §1.3 删除模型输出列表中的 top_verdict

**替换范围：§1.3「模型只返回」列表。**

将原列表替换为：

```text
模型只返回：

target_ref_index
category_index
score
reasoning
evidence_indices
non_violation_score
```

从该列表中删除 `top_verdict`。

保留原有语义说明：`top_verdict` 仅由 Runtime 根据 Policy 计算，模型不返回该字段。

---

## 5. N06_DUPLICATE_TARGET 错误传播规则

**替换范围：§7.1、§7.3。**

### §7.1 错误分类表

在「重复 Category」之后增加：

| 层级 | 示例 | 处理 |
|---|---|---|
| Target | 重复 `target_ref_index`（`N06_DUPLICATE_TARGET`） | 对应 Target 标记 `JEV_FAILED`；其他合法 Target 继续执行 |

### §7.3 B. Target-level Failure

```text
### B. Target-level Failure

例如：

- 单个 Target 的 Category Score 非法。
- 单个 Target 的 Evidence 引用无效。
- 单个 Target 重复 Category。
- 单个 Target 存在重复 target_ref_index。
- 单个 Target 存在非法 Catalog Index。
- 单个 Target 结构无法完整验证，但能够确定对应 Target。

特别地，当同一个 N06 响应中出现重复 target_ref_index：

1. Runtime 在处理 Target 结果前，先检查 results 中的 target_ref_index 是否重复。
2. 对每个重复的 target_ref_index，通过 RuntimeTargetReferenceCatalog 定位对应 Target。
3. 将该 Target 标记为 JEV_FAILED，failure_code = N06_DUPLICATE_TARGET。
4. 该 index 对应的全部重复结果均不得进入正常 Ranking / Mapping。
5. 不采用 first-wins、last-wins 或合并重复结果的处理方式。
6. 其他 target_ref_index 唯一且合法的 Target 继续执行。

处理：

该 Target:
    ComparisonStatus = JEV_FAILED
    failure_code = N06_DUPLICATE_TARGET

其他合法 Target:
    继续执行正常处理流程。

重复 target_ref_index 属于 Target-level Failure，
不得仅因该重复项升级为 Image-level Failure。
```

在顶层 Envelope 有效且其他 Target 可正常对账、处理的前提下，整图仍可保持：

```text
InferenceStatus = SUCCEEDED
execution_outcome = EVALUATED
```

---

## 6. TargetJEVJudgment.evidence_refs 聚合语义

**替换范围：§3.4 TargetJEVJudgment 约束列表中的原第 10、11 项，并顺延编号。**

```text
10. evidence_refs 必须是 raw_ranking 中全部
    RuntimeRankedCategory.evidence_refs 的去重并集。

11. 去重并集必须按照以下顺序构建：

    a. 按照 raw_ranking 的列表顺序遍历。
    b. 按照每个 RuntimeRankedCategory.evidence_refs
       的原始列表顺序遍历。
    c. 以解析后的完整结构化 EvidenceRef 值作为去重依据。
    d. 重复 EvidenceRef 仅保留首次出现的记录。
    e. 保留首次出现时的原始顺序，不重新排序。

12. 当 raw_ranking 为空时：

    evidence_refs = []

13. evidence_refs 中的所有引用必须属于
    当前 Image Execution。

14. 不允许模型自行填充上述 Runtime Identity。
```

聚合语义：

\[
EvidenceRefs(Target)
=
UniqueOrdered
\left(
\bigcup_{c \in RawRanking(Target)}
c.evidence\_refs
\right)
\]

其中：

\[
RawRanking(Target)=\varnothing
\Rightarrow EvidenceRefs(Target)=[]
\]

`evidence_refs` 是 Runtime 从 `raw_ranking` 确定性派生的聚合字段，不是模型独立生成的证据集合；其来源是 `raw_ranking`，不是 `active_ranking`。

---

## 修订边界

以上仅为 V1.1 的 6 处局部替换片段。除上述明确列出的内容外，V1.0 原文其他章节、D05 R2.2.2、WP-A1 V1.1 及既有冻结契约均保持不变。

本补丁文档不代表 WP-A2 已正式冻结；合并后仍需经过独立 Reviewer 验收。
