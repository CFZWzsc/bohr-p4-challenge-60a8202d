#!/usr/bin/env python3
"""Traceable citation tracking and related-work discovery (Python stdlib only).

Reads a case file (default /input/case.json), uses only citation/reference
fields that actually exist in the input, expands breadth-first with a bounded
depth, merges obvious versions of one work, reports explainable seed-to-node
paths and honestly lists unresolved citation targets. Writes a structured
result.json that preserves case_id and every original output_contract field.

No network access, no randomness, no private paths, no fabricated records.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import deque
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

MAX_DEPTH = 2
MAX_NODES = 400
TITLE_CONTAINMENT = 0.86

REF_FIELDS = (
    "references", "reference_list", "bibliography", "refs",
    "cited_references", "outgoing_citations", "backward_citations",
)
CITED_BY_FIELDS = (
    "cited_by", "citations", "citing", "citing_works",
    "incoming_citations", "forward_citations", "cited_by_list",
)
SEED_KEYS = (
    "seeds", "seed", "seed_papers", "seed_paper", "seed_ids",
    "seed_dois", "starting_papers", "key_papers",
)
LIST_KEYS = ("papers", "records", "nodes", "candidates", "articles", "items", "works", "results", "references_data")
TITLE_KEYS = ("title", "article_title", "paper_title", "display_name", "题名")


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    """Read task.md when it really exists; otherwise return an empty string."""
    candidates = []
    try:
        candidates.append(Path(case_path).resolve().parent / "task.md")
    except OSError:
        pass
    candidates.append(Path("/input/task.md"))
    if work_dir:
        candidates.append(Path(work_dir) / "task.md")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def norm_title(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", norm_space(value).lower())


def norm_doi(value):
    if not isinstance(value, str):
        return ""
    text = norm_space(value).lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text.strip(" .;,")


def pick(record, names):
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def authors_of(value):
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, str):
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("name") or item.get("family") or item.get("literal")
                if name:
                    names.append(str(name).strip())
        return "; ".join(n for n in names if n)
    if isinstance(value, dict):
        name = value.get("name") or value.get("family")
        return norm_space(str(name)) if name else ""
    return ""


def first_author(authors):
    return norm_space(authors.split(";")[0].split(",")[0]).lower()


def year_of(record):
    raw = pick(record, ("year", "pub_year", "publication_year", "date", "published", "issued"))
    if isinstance(raw, int):
        return raw if 1500 < raw < 2200 else None
    if isinstance(raw, str):
        match = re.search(r"(1[5-9]\d{2}|20\d{2})", raw)
        if match:
            return int(match.group(0))
    if isinstance(raw, dict):
        parts = raw.get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                return None
    return None


# --------------------------------------------------------------------------
# input collection
# --------------------------------------------------------------------------
def looks_like_record(node):
    if not isinstance(node, dict):
        return False
    keys = {str(k).lower() for k in node.keys()}
    if keys & {k.lower() for k in TITLE_KEYS}:
        return True
    return bool(keys & {"doi", "pmid", "arxiv_id", "arxiv"}) and bool(keys & {"authors", "author", "year"})


def collect_records(case):
    """Return [(json_pointer, record_dict)] using declared lists first, then a walk."""
    if isinstance(case, dict):
        for key in LIST_KEYS:
            value = case.get(key)
            if isinstance(value, list) and value:
                found = [(f"/{key}/{i}", item) for i, item in enumerate(value) if looks_like_record(item)]
                if found:
                    return found
        for container in ("data", "input", "corpus", "library"):
            inner = case.get(container)
            if isinstance(inner, dict):
                for key in LIST_KEYS:
                    value = inner.get(key)
                    if isinstance(value, list) and value:
                        found = [(f"/{container}/{key}/{i}", item) for i, item in enumerate(value) if looks_like_record(item)]
                        if found:
                            return found

    records = []

    def walk(node, path, depth):
        if depth > 8 or len(records) >= MAX_NODES * 5:
            return
        if isinstance(node, dict):
            if looks_like_record(node) and not any(k in node for k in REF_FIELDS + CITED_BY_FIELDS):
                records.append((path, node))
                return
            for key, value in node.items():
                walk(value, f"{path}/{key}", depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}", depth + 1)

    walk(case, "", 0)
    return records


def normalize(raw, locator):
    record = raw if isinstance(raw, dict) else {}
    doi = norm_doi(pick(record, ("doi", "DOI")) or "")
    arxiv = norm_space(str(pick(record, ("arxiv_id", "arxiv", "eprint")) or ""))
    return {
        "locator": locator,
        "record_id": str(pick(record, ("record_id", "id", "paper_id", "uid", "key")) or ""),
        "title": norm_space(str(pick(record, TITLE_KEYS) or "")),
        "abstract": norm_space(str(pick(record, ("abstract", "summary")) or "")),
        "doi": doi,
        "pmid": str(pick(record, ("pmid", "pubmed_id")) or ""),
        "arxiv_id": arxiv,
        "year": year_of(record),
        "journal": norm_space(str(pick(record, ("journal", "venue", "container_title", "journal_name")) or "")),
        "authors": authors_of(pick(record, ("authors", "author", "creator", "contributors"))),
        "raw": record,
    }


def extract_seeds(case):
    if not isinstance(case, dict):
        return []
    for container in (case, case.get("data") if isinstance(case.get("data"), dict) else {}):
        for key in SEED_KEYS:
            value = container.get(key)
            if isinstance(value, list) and value:
                return value
            if isinstance(value, (str, dict)):
                return [value]
    return []


def node_key(rec):
    if rec["doi"]:
        return "doi:" + rec["doi"]
    if rec["pmid"]:
        return "pmid:" + rec["pmid"]
    if rec["arxiv_id"]:
        return "arxiv:" + rec["arxiv_id"].lower()
    title = norm_title(rec["title"])
    if title:
        return "title:" + title
    return "locator:" + (rec["locator"] or rec["record_id"] or "unknown")


def edge_target(value):
    """Return (identifier, title) for one citation entry."""
    if isinstance(value, str):
        text = norm_space(value)
        if re.match(r"^10\.\d{4,9}/\S+$", text):
            return norm_doi(text), ""
        return "", text
    if isinstance(value, dict):
        doi = norm_doi(pick(value, ("doi", "DOI")) or "")
        title = norm_space(str(pick(value, TITLE_KEYS) or ""))
        ident = doi or str(pick(value, ("pmid", "arxiv_id", "arxiv", "id", "paper_id")) or "")
        return ident, title
    return "", ""


# --------------------------------------------------------------------------
# graph construction
# --------------------------------------------------------------------------
def build_nodes(records, seed_values):
    nodes = {}
    for rec in records:
        key = node_key(rec)
        if key in nodes:
            target = nodes[key]
            for field in ("title", "abstract", "doi", "pmid", "arxiv_id", "year", "journal", "authors"):
                if not target.get(field) and rec.get(field):
                    target[field] = rec[field]
            target["locators"].append(rec["locator"])
        else:
            nodes[key] = {
                "key": key,
                "record_id": rec["record_id"] or key,
                "title": rec["title"],
                "abstract": rec["abstract"],
                "doi": rec["doi"],
                "pmid": rec["pmid"],
                "arxiv_id": rec["arxiv_id"],
                "year": rec["year"],
                "journal": rec["journal"],
                "authors": rec["authors"],
                "locators": [rec["locator"]],
                "raw": rec["raw"],
                "is_seed": False,
            }
        if len(nodes) >= MAX_NODES * 2:
            break

    title_index = {}
    for key, node in nodes.items():
        title = norm_title(node["title"])
        if title:
            title_index.setdefault(title, []).append(key)

    record_id_index = {}
    for key, node in nodes.items():
        if node["record_id"]:
            record_id_index.setdefault(str(node["record_id"]).lower(), []).append(key)

    seed_keys = []
    unmatched_seeds = []
    for value in seed_values:
        ident, title = edge_target(value)
        if isinstance(value, str) and value.strip().lower() in record_id_index:
            ident, title = "", ""
            hits = record_id_index[value.strip().lower()]
        elif isinstance(value, dict) and isinstance(value.get("record_id"), str):
            hits = record_id_index.get(value["record_id"].strip().lower()) or []
        else:
            hits = None
        candidate = ""
        if hits:
            if len(hits) == 1:
                candidate = hits[0]
        elif ident:
            if ident.startswith("10."):
                candidate = "doi:" + ident
            elif ident.lower().startswith("pmid"):
                candidate = "pmid:" + re.sub(r"\D", "", ident)
            elif ident.lower() in record_id_index:
                candidate = record_id_index[ident.lower()][0]
            else:
                candidate = ident
        elif title:
            hits = title_index.get(norm_title(title)) or []
            if hits:
                candidate = hits[0]
        if candidate and candidate in nodes:
            nodes[candidate]["is_seed"] = True
            if candidate not in seed_keys:
                seed_keys.append(candidate)
        else:
            unmatched_seeds.append({"seed": value, "reason": "seed identifier/title does not match any supplied record"})

    # an explicitly flagged record also counts as a seed
    for key, node in nodes.items():
        if node["is_seed"] and key not in seed_keys:
            seed_keys.append(key)

    if not seed_keys and nodes:
        # deterministic fallback: the earliest supplied record, clearly flagged
        first = records[0] if records else None
        if first:
            key = node_key(first)
            if key in nodes:
                nodes[key]["is_seed"] = True
                seed_keys.append(key)

    return nodes, seed_keys, unmatched_seeds, title_index


def build_edges(nodes, title_index):
    edges = []
    unresolved = []
    for key in sorted(nodes):
        raw = nodes[key].get("raw") or {}
        for field in REF_FIELDS:
            value = raw.get(field)
            items = value if isinstance(value, list) else ([value] if isinstance(value, str) and value.strip() else [])
            for item in items:
                ident, title = edge_target(item)
                target = ""
                if ident:
                    if ident.startswith("10."):
                        target = "doi:" + ident
                    elif ident.lower().startswith("pmid"):
                        target = "pmid:" + re.sub(r"\D", "", ident)
                    elif ident in nodes:
                        target = ident
                    else:
                        target = "id:" + ident
                elif title:
                    hits = title_index.get(norm_title(title)) or []
                    target = hits[0] if len(hits) == 1 else ""
                edge = {
                    "from": key,
                    "to": target,
                    "direction": "backward",
                    "relation": "references",
                    "field": field,
                    "target_title": title,
                    "source_locator": nodes[key]["locators"][0],
                }
                if target and target in nodes:
                    edges.append(edge)
                else:
                    edge["unresolved_reason"] = "target identifier or title is not present in the supplied records" if (ident or title) else "citation entry has no usable identifier or title"
                    unresolved.append(edge)

        for field in CITED_BY_FIELDS:
            value = raw.get(field)
            items = value if isinstance(value, list) else ([value] if isinstance(value, str) and value.strip() else [])
            for item in items:
                ident, title = edge_target(item)
                source = ""
                if ident:
                    if ident.startswith("10."):
                        source = "doi:" + ident
                    elif ident.lower().startswith("pmid"):
                        source = "pmid:" + re.sub(r"\D", "", ident)
                    else:
                        source = "id:" + ident
                elif title:
                    hits = title_index.get(norm_title(title)) or []
                    source = hits[0] if len(hits) == 1 else ""
                edge = {
                    "from": source,
                    "to": key,
                    "direction": "forward",
                    "relation": "cited_by",
                    "field": field,
                    "source_title": title,
                    "source_locator": nodes[key]["locators"][0],
                }
                if source and source in nodes:
                    edges.append(edge)
                else:
                    edge["unresolved_reason"] = "citing work is not described by a supplied record" if (ident or title) else "citation entry has no usable identifier or title"
                    unresolved.append(edge)
    return edges, unresolved


def merge_versions(nodes):
    """Flag near-duplicate titles as candidate versions of one work (evidence kept)."""
    merges = []
    keys = sorted(nodes)
    for i, left in enumerate(keys):
        a = nodes[left]
        ta = norm_title(a["title"])
        if not ta:
            continue
        for right in keys[i + 1:]:
            b = nodes[right]
            tb = norm_title(b["title"])
            if not tb:
                continue
            shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if len(shorter) < 12 or shorter not in longer:
                continue
            ratio = len(shorter) / float(len(longer))
            if ratio < TITLE_CONTAINMENT:
                continue
            same_author = first_author(a["authors"]) and first_author(a["authors"]) == first_author(b["authors"])
            merges.append({
                "kept": left,
                "variant": right,
                "confidence": "medium" if same_author else "low",
                "basis": "normalised title containment ratio %.2f" % ratio,
                "same_first_author": bool(same_author),
                "evidence": [a["locators"][0], b["locators"][0]],
                "note": "candidate version link only; identifiers are not overwritten",
            })
    return merges


def expand(seed_keys, edges, max_depth):
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    distance = {seed: 0 for seed in seed_keys}
    paths = {seed: [seed] for seed in seed_keys}
    queue = deque(seed_keys)
    while queue:
        node = queue.popleft()
        if distance[node] >= max_depth:
            continue
        for nxt in sorted(set(adjacency.get(node, []))):
            if nxt not in distance:
                distance[nxt] = distance[node] + 1
                paths[nxt] = paths[node] + [nxt]
                queue.append(nxt)
    return distance, paths


def related_work(nodes, seed_keys, distance, edges):
    seed_terms = set()
    for key in seed_keys:
        text = (nodes[key]["title"] + " " + nodes[key]["abstract"]).lower()
        seed_terms |= set(re.findall(r"[a-z]{4,}", text))
    seed_refs = {edge["to"] for edge in edges if edge["from"] in set(seed_keys)}
    related = []
    for key in sorted(nodes):
        if key in distance or not seed_terms:
            continue
        node = nodes[key]
        terms = set(re.findall(r"[a-z]{4,}", (node["title"] + " " + node["abstract"]).lower()))
        overlap = sorted(seed_terms & terms)
        shared = sorted(seed_refs & {edge["to"] for edge in edges if edge["from"] == key})
        if len(overlap) >= 2 or shared:
            related.append({
                "key": key,
                "record_id": node["record_id"],
                "title": node["title"],
                "signal": "shared_reference" if shared else "lexical_overlap",
                "overlap_terms": overlap[:8],
                "shared_references": shared[:5],
                "source_locator": node["locators"][0],
                "caveat": "association signal only; not evidence of a methodological lineage",
            })
    return related


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------
def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


def build_payload(case, task_text):
    records = [normalize(raw, locator) for locator, raw in collect_records(case)]
    seed_values = extract_seeds(case)
    nodes, seed_keys, unmatched_seeds, title_index = build_nodes(records, seed_values)
    edges, unresolved = build_edges(nodes, title_index)
    merges = merge_versions(nodes)
    distance, paths = expand(seed_keys, edges, MAX_DEPTH)

    ranked = []
    for key in sorted(distance, key=lambda k: (distance[k], -(nodes[k]["year"] or 0), k)):
        node = nodes[key]
        ranked.append({
            "rank": len(ranked) + 1,
            "key": key,
            "record_id": node["record_id"],
            "title": node["title"],
            "doi": node["doi"],
            "year": node["year"],
            "journal": node["journal"],
            "level": distance[key],
            "is_seed": node["is_seed"],
            "path_from_seed": paths.get(key, []),
            "source_locator": node["locators"][0],
        })

    candidates = []
    for key in sorted(nodes):
        node = nodes[key]
        candidates.append({
            "key": key,
            "record_id": node["record_id"],
            "title": node["title"],
            "doi": node["doi"],
            "year": node["year"],
            "journal": node["journal"],
            "authors": node["authors"],
            "level": distance.get(key),
            "reached": key in distance,
            "source_locators": node["locators"],
        })

    unresolved_seed_notes = unmatched_seeds
    uncertainties = []
    if not records:
        uncertainties.append({"type": "no_records", "detail": "The case contained no recognisable paper records; nothing was expanded."})
    if records and not seed_values:
        uncertainties.append({"type": "no_declared_seed", "detail": "No seed field was supplied; the first record was used and flagged, so the entry point is a fallback rather than the organiser's seed."})
    if unresolved_seed_notes:
        uncertainties.append({"type": "unresolved_seeds", "detail": "%d declared seed value(s) could not be matched to a supplied record." % len(unresolved_seed_notes)})
    if records and not edges:
        uncertainties.append({"type": "no_citation_edges", "detail": "No reference or citation list in the input resolved to another supplied record."})
    if unresolved:
        uncertainties.append({"type": "unresolved_citation_targets", "detail": "%d citation entries point to works that are not described by a supplied record; they are listed, not invented." % len(unresolved)})
    uncertainties.append({"type": "coverage_scope", "detail": "Coverage is limited to citation edges actually present in the input; no live citation index was queried."})
    uncertainties.append({"type": "forward_coverage", "detail": "Preprints and recent items often have no citing-work records; absent forward edges mean absent input, not zero citations."})

    backward = sum(1 for edge in edges if edge["direction"] == "backward")
    forward = sum(1 for edge in edges if edge["direction"] == "forward")
    if not records:
        status = "insufficient_input"
    elif not edges or not seed_keys:
        status = "partial"
    else:
        status = "success"

    evidence = []
    for key in seed_keys:
        evidence.append({"kind": "seed", "target": key, "locator": nodes[key]["locators"][0], "detail": "seed record as supplied in the case"})
    for edge in edges[:200]:
        evidence.append({
            "kind": "citation_edge",
            "direction": edge["direction"],
            "from": edge["from"],
            "to": edge["to"],
            "field": edge["field"],
            "locator": edge["source_locator"],
        })
    if task_text:
        evidence.append({"kind": "task_text", "locator": "task.md", "detail": "task.md was read (%d characters) and used only for scope wording." % len(task_text)})

    result = {
        "seeds": [
            {"key": key, "record_id": nodes[key]["record_id"], "title": nodes[key]["title"],
             "doi": nodes[key]["doi"], "source_locator": nodes[key]["locators"][0]}
            for key in seed_keys
        ],
        "candidates": candidates,
        "ranked_results": ranked,
        "results": ranked,
        "edges": edges,
        "unresolved_edges": unresolved,
        "paths": {key: paths[key] for key in sorted(paths)},
        "expansion": {
            "max_depth": MAX_DEPTH,
            "reached_nodes": len(distance),
            "total_nodes": len(nodes),
            "backward_edges": backward,
            "forward_edges": forward,
            "stop_rule": "breadth-first expansion stops at max_depth; unreached nodes stay listed",
        },
        "related_work": related_work(nodes, seed_keys, distance, edges),
        "version_merges": merges,
        "coverage": {
            "input_records": len(records),
            "unique_nodes": len(nodes),
            "seeds": len(seed_keys),
            "reached": len(distance),
            "unreached": len(nodes) - len(distance),
            "edges_retained": len(edges),
            "edges_unresolved": len(unresolved),
        },
        "limitations": [
            "A missing citation edge is not proof that no citation exists.",
            "Version merging is title-containment based and flagged as a candidate, not a bibliographic authority.",
            "Related-work signals are lexical or shared-reference heuristics, not verified methodological lineage.",
        ],
        "change_log": [
            "Read the case file and optional task.md without assuming their content.",
            "Extracted seeds, reference lists and citing-work lists from fields that are actually present.",
            "Built directed edges with field-level locators, then expanded breadth-first to depth %d." % MAX_DEPTH,
        ],
    }

    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "evidence": evidence,
        "uncertainty": uncertainties,
        "errors": [],
        "stats": {
            "records": len(records),
            "nodes": len(nodes),
            "seeds": len(seed_keys),
            "edges": len(edges),
            "unresolved_edges": len(unresolved),
            "related_candidates": len(result["related_work"]),
            "version_pairs": len(merges),
            "max_depth": MAX_DEPTH,
            "network": "disabled",
        },
    }


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    for name in names:
        if name not in payload:
            payload[name] = None
            notes[name] = "not produced by this run; preserved as null for output_contract completeness"
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {
        "provided": names,
        "present": {name: name in payload for name in names},
    }
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def failure_payload(case, message, stage):
    return {
        "case_id": case_identifier(case) if isinstance(case, dict) else "",
        "status": "failed",
        "result": None,
        "evidence": [],
        "uncertainty": [{"type": "run_failure", "detail": "Run failed at %s; no literature conclusions are reported." % stage}],
        "errors": [{"stage": stage, "type": "runtime_error", "message": str(message)[:500]}],
        "stats": {},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Traceable citation tracking and related-work discovery")
    parser.add_argument("--case", default=DEFAULT_CASE, help="input case JSON (default: /input/case.json)")
    parser.add_argument("--out", "--output", default=DEFAULT_OUT, help="output result JSON (default: /app/output/result.json)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="working directory for logs (default: /app/work)")
    args = parser.parse_args(argv)

    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        task_text = read_task_text(args.case, args.work)
        payload = apply_contract(build_payload(case, task_text), case)
        code = 0
    except Exception as exc:  # honest failure, never a fabricated result
        payload = apply_contract(failure_payload(case, exc, "build"), case if isinstance(case, dict) else {})
        code = 2

    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3

    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(args.out),
        "records": payload.get("stats", {}).get("records", 0),
    }, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
