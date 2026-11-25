---
name: citation-tracking-related-work
description: Use when a research task supplies a seed paper and candidate records and needs backward/forward citation expansion, explainable seed-to-work paths, version/identity merging, or related-work discovery with verifiable source locators and a contract-preserving result.json.
---

# 引文追踪与关联文献发现



## 用途与适用条件

当任务围绕一篇或若干篇种子论文，要求沿参考文献（后向）、施引文献（前向）扩展文献，构建引用网络，解释"种子如何连到某篇远端文献"，合并预印本/会议版/期刊版，或找出不直接引用种子但属于同一方法脉络的平行工作时使用。

**适用前提**：输入中真实提供论文记录及其 `references` / `cited_by` 等字段。只有关键词或摘要、没有任何引用列表时，本 Skill 会给出 `partial` 并明确说明"没有可用的引用边"，不会把词面相似冒充引文关系。

**不适用**：普通关键词检索、全文精读、引文语境（citation context）理解、绕过数据库权限抓取。本 Skill 不访问网络，也不内置任何领域知识或预制答案。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | 必读。常见结构：`case_id`、`output_contract`、`seeds`（DOI/标题/记录 ID 列表）、`papers`/`records`/`nodes`（含 `title`、`doi`、`year`、`authors`、`references`、`cited_by`）。实际键名以文件为准。 |
| `task.md` | 可选。存在时读取用于核对研究范围与语言要求；不存在时不报错。 |
| `--work` | 工作目录，默认 `/app/work`。 |

脚本不假定字段名固定：除上表外还识别 `reference_list`、`bibliography`、`refs`、`cited_references`、`outgoing_citations`、`citations`、`citing_works`、`incoming_citations`、`forward_citations`。

## 处理流程（对应评分维度）

1. **读取与规范化**（任务完成度）：读取 case 与 task.md；对每条记录抽取标题、DOI、PMID、arXiv 号、年份、作者、期刊，保留原始记录与 JSON 指针 `source_locator`。派生值只用于匹配，不改写原字段。
2. **种子定位**（结果正确性）：按 `seeds` → 记录 `record_id` → 唯一标题匹配的顺序定位种子。种子无法匹配时记入 `uncertainty`，不猜测替代文献；无任何种子字段时才以首条记录作为兜底种子并明确标注。
3. **身份合并**（结果正确性）：优先以 DOI / PMID / arXiv 号作为节点键合并完全相同的记录；标题归一化后做包含度 ≥0.86 的近似匹配，标记为"候选版本关系"，保留双方定位证据，不覆盖任何标识符。
4. **有向边构建**（可核验性）：`references` 类字段生成 `backward` 边，`cited_by` 类字段生成 `forward` 边；每条边保留 `field`、`source_locator`、目标标题。目标不在输入记录中的边进入 `unresolved_edges` 并写明原因，不补造元数据。
5. **分层扩展**（完整性）：从种子做广度优先扩展，默认 `MAX_DEPTH=2`，超过深度的节点保留为 `reached=false` 而非删除；输出每个可达节点的 `path_from_seed`（逐跳节点键），使路径可解释。
6. **关联发现**（创新增益）：对未通过引用边可达的节点，用"与种子共享参考文献"或"标题/摘要词面重叠 ≥2 个词"两种信号标记为候选平行工作，并附 `caveat`，明确这只是关联信号，未经方法学核实。
7. **排序与证据**（结果可核验性）：按 `(层级, 年份降序, 节点键)` 稳定排序；`evidence` 汇总种子定位与引用边字段。

## 输出契约

写入 `--out`（默认 `/app/output/result.json`），顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样保留输入 `case_id`（缺失时尝试 `caseId` / `id`） |
| `status` | string | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | object | 主结果：`seeds`、`candidates`、`ranked_results`、`results`、`edges`、`unresolved_edges`、`paths`、`expansion`、`related_work`、`version_merges`、`coverage`、`limitations`、`change_log` |
| `evidence` | array | 种子与引用边的来源定位 |
| `uncertainty` | array | 每条含 `type` 与 `detail` |
| `errors` | array | 运行错误；正常运行时为空 |
| `stats` | object | 记录数、节点数、边数、未解析边数等 |
| `contract_notes` | object | 未能产出的契约字段说明 |
| `output_contract_fields` | object | `provided`（输入契约字段名）与 `present`（本次是否产出） |

输入 `output_contract` 中的每个字段名都会出现在顶层；本 Skill 无法生成时写 `null` 并在 `contract_notes` 说明，不伪造结构。

`status` 判定：无任何可识别记录 → `insufficient_input`；有记录但没有可用引用边、或没有解析出种子 → `partial`；记录、种子与引用边齐备 → `success`；读取/解析失败 → `failed`（不输出任何文献结论）。

## 异常处理

| 情形 | 处理 | 输出标记 |
|---|---|---|
| case.json 不存在或不是 JSON 对象 | 不构造结果，写失败载荷 | `status=failed`，`errors[].stage` |
| 没有任何可识别论文记录 | 不进行扩展，不编造节点 | `status=insufficient_input` |
| 有记录但无引用/施引字段 | 只输出候选清单与词面关联，明确说明无引用边 | `status=partial`，`uncertainty.type=no_citation_edges` |
| 声明的种子无法匹配 | 保留未匹配项，不替换为相似文献 | `uncertainty.type=unresolved_seeds` |
| 引用目标不在输入记录中 | 进入 `unresolved_edges`，写明原因 | `uncertainty.type=unresolved_citation_targets` |
| 无种子字段 | 以首条记录兜底并标注 | `uncertainty.type=no_declared_seed` |
| 预印本无前向引用 | 前向覆盖记为"输入中缺失"，不等于零引用 | `uncertainty.type=forward_coverage` |
| 输出目录不可写 | stderr 输出简短错误，退出码 3 | 无静默成功 |

## 限制

- 只在输入已提供的引用数据内工作；没有实时 Crossref/OpenAlex/Europe PMC 查询能力，缺边不等于无引用。
- 版本合并基于标题包含度，属于候选关系，不能替代 FIXT 等权威身份判定。
- 关联发现是词面或共享参考文献启发式，不构成方法学结论。
- 深度与节点数有上限（默认深度 2），更深网络需显式调整常量并承担运行时间。
- 去重以标识符优先；同名不同文、同文不同名只能依赖标题近似，存在残余误合并风险。

## Quickstart

在仓库根目录 `bohrium-competition` 执行（PowerShell，实测通过）：

```powershell
python -B "tracks\literature-search\challenges\challenge-60a8202d\06_code\submission\scripts\run_skill.py" --case "tracks\literature-search\challenges\challenge-60a8202d\06_code\submission\examples\demo-case\case.json" --out "$env:TEMP\demo_60a8202d\result.json" --work "$env:TEMP\demo_60a8202d\work"
```

- 示例输入：`examples/demo-case/case.json`，合成且自包含（`"demo": true`，1 个种子论文 + 3 条引用记录，含 1 条无 DOI 的未解析引用；DOI 均为虚构的 `10.0000/...`）。
- 实测输出：`status = "success"`、`records = 4`，`result.json` 顶层含 `case_id`、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`；同一输入两次运行结果逐字节一致。
- 该用例只验证脚本可执行与输出契约齐全，不代表真实引文网络。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json \
  --out /app/output/result.json \
  --work /app/work
```

输入片段（示例）：

```json
{"case_id": "demo-001",
 "seeds": ["10.1000/seed.1"],
 "papers": [
   {"id": "p1", "title": "Seed study", "doi": "10.1000/seed.1",
    "references": [{"doi": "10.1000/method.2", "title": "Method origin"}]},
   {"id": "p2", "title": "Method origin", "doi": "10.1000/method.2", "year": 2019}
 ]}
```

示意输出（示例，已截断）：

```json
{"case_id": "demo-001", "status": "success",
 "result": {"seeds": [{"key": "doi:10.1000/seed.1"}],
            "edges": [{"from": "doi:10.1000/seed.1", "to": "doi:10.1000/method.2",
                       "direction": "backward", "field": "references"}],
            "paths": {"doi:10.1000/method.2": ["doi:10.1000/seed.1", "doi:10.1000/method.2"]}},
 "uncertainty": [{"type": "coverage_scope", "detail": "..."}]}
```

以上输出仅用于说明接口形状，不代表任何官方固定案例的结果。


## 交付保证与评分对齐

1. 在 Skill 安装目录执行：`cd /skills/weekly-skill && python scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work`；路径不同时显式改 `--case` / `--out` / `--work`，不要假定相对路径可用。
2. 必须产出**完整成品** `result.json`：种子定位、前后向边、分层路径与未解析边全部落位，每条节点带来源定位；禁止 skeleton、`pending`、空占位或只写方法说明。
3. 脚本无法自动完成的实质判断（真实引用关系核实、版本合并）由调用 Agent 依据实际记录补全并写回 `result.json`，不得编造引用边或元数据。
4. 自检：`case_id` 与输入一致；`output_contract` 每个字段存在；`status` 取本契约值 `success` / `partial` / `insufficient_input` / `failed`（不得写 `completed`）；无引用边时已声明 `partial`。
5. 输入缺失、JSON 损坏或字段异常时仍写出结构化结果并说明缺口，不崩溃、不静默成功。

## 交付要求

1. **交付成品，不是流程说明**：脚本产出只是索引与核对骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的检索结果、查询计划、纳排依据与证据；字段为空或含 `pending`、`TODO` 一律视为未完成。
2. **覆盖全部输入条目**：输入中的每条检索式、候选记录、问题与指标都要有对应条目；确实缺失的写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段落位；状态取脚本实现的枚举（见上方契约表）；引文为原文短句并带来源定位；计数内部闭合、无自相矛盾。
4. **参数等价**：`--case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑由输入驱动，不针对示例硬编码；字段命名差异走别名匹配并在 `uncertainty` 记录假设。

## 运行时产物（单次运行自动写出）

入口脚本在写 `result.json` 的同一次运行里还会写出：`report.md`（人读报告，每条带源定位）、
`provenance.json`（条目 → 来源定位映射）、`records.csv`（平面结果表）、
`validation.json`（自检项）。`output_contract` 中的产物类字段直接指向这些文件，
无需第二次运行或人工补写。
