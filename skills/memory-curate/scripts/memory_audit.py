#!/usr/bin/env python3
"""
memory_audit.py — mechanical hygiene audit for a Claude Code memory directory.

Report-only; never mutates. Exit code 0 always.

Structural checks (cheap, exact):
  1. inventory            file count, index entry count, index size vs read cap,
                          + estimated bytes recoverable by tightening hooks
  2. index <-> file 1:1   orphans / missing
  3. broken wikilinks     [[name]] with no name.md
  4. frontmatter sanity   name/description/metadata present; name matches slug
  5. index hot spots      longest hooks (mini-summaries that belong in the file)
  6. size ranking         largest memory files (condensation candidates)
  7. staleness flags      split STRONG (state assertions — verify) vs WEAK
                          (words that are often legitimate), deduped by claim
  8. redundancy           filename-prefix buckets (labeling) + content overlap
                          over *distinctive* anchors (real merge candidates)

Semantic checks (heuristic — flag for a human read, do not treat as verdicts):
  9. index<->body drift   hard tokens asserted in a hook that the body either
                          does not contain, or contains next to a negation
                          (作废/从未/推翻/…). Catches "hook says X, body says
                          not-X" — the corruption form the skill names but that
                          no structural check can see.
 10. citation resolvability
                          - shas: does each resolve in a sibling repo? is it on
                            that repo's default branch? (catches "claimed merged
                            into dev but isn't", incl. replay commits that share
                            content under a different sha)
                          - file paths: does the cited path still exist?
                            (path drift is fatal; LINE drift is NOT checked —
                            per the hygiene doc, line numbers are expected to
                            rot and are explicitly allowed as "approx location")

Usage:
  python3 memory_audit.py                     # derive dir from $PWD (robust)
  python3 memory_audit.py --dir <path>        # explicit memory dir
  python3 memory_audit.py --repos <path>      # repo root for §10 (auto if omitted)
  python3 memory_audit.py --cap 24986         # override index byte cap (24.4 KiB)
  python3 memory_audit.py --target-headroom 2000
                                              # end-of-run budget; verdict PASS/FAIL
  python3 memory_audit.py --record            # delta since last run + append snapshot
  python3 memory_audit.py --quick             # only §1-4; exit 1 if structural issue
  python3 memory_audit.py --fetch             # git fetch repos before §10 (network)
  python3 memory_audit.py --json              # machine-readable
"""
import argparse, json, os, re, sys, difflib, pathlib, subprocess
from collections import defaultdict
from datetime import datetime, timezone

INDEX = "MEMORY.md"
FRONT_KEYS = ("name", "description", "metadata")
HOOK_SOFT_LIMIT = 250   # bytes; above this a hook is a mini-summary
HOOK_TARGET = 200       # bytes; the size a tightened hook should reach
CAP_WARN = 3000         # headroom below this = "tight"
DEFAULT_CAP = 24986     # 24.4 KiB — common budgets cap MEMORY.md here; override with --cap
                        # (24400 was 24.4 *decimal* KB and under-reported by 586B)
TARGET_HEADROOM = 2000  # end-of-run budget: a curate run should leave this much
CLAUSE_DELIM = "。！？；\n"
# a negation in a clause carrying these markers is about *another* object
# (e.g. "另一台设备上曾有一份未提交的同向修复，会与 <sha> 分叉") — not a denial.
QUALIFIER = re.compile(r"另一[台份处个]|曾|会与|将与|分叉|不同于")
RUN_LOG = ".memory-curate-runs.jsonl"

# §7 — state assertions that rot (verify each) vs words that are usually innocent
STRONG_STALE = [r"未推送", r"未提交", r"未落地", r"仍未", r"尚未"]
WEAK_STALE = [r"待办", r"待确认", r"待定", r"\bTODO\b", r"\bFIXME\b", r"暂时", r"临时"]
NEGATION = re.compile(r"作废|从未|已废|推翻|不再|勿|无效|取代|已删|已退役|已改")

CITATION = re.compile(
    r"([\w./-]+\.(?:go|ts|tsx|js|mjs|vue|py|rs|java|md|json|ya?ml))(?::(\d+))?")
SHA = re.compile(r"(?<![\w-])[0-9a-f]{7,40}(?![\w-])")
BRANCH = re.compile(r"\b(?:feat|fix|chore|release)/[\w./-]+")
DATED = re.compile(r"20\d\d-\d\d-\d\d")
# a sha is only *suspicious* when the text near it claims it is on the default
# branch — branch-only shas are expected to be off-default and are not flagged.
# the claim itself must not be negated ("仍未合入 dev" is the opposite claim).
CLAIM_ON_DEV = re.compile(
    r"(?:已推|合入|搬进|进入?|落在|已落|已上|已进)[^。\n]{0,10}?(?:origin/)?dev")
NEG_BEFORE = re.compile(r"(?:未|不|没|勿|尚未|仍未|还没)$")

# §9 — tokens whose presence in a hook is a checkable claim about the body
# return (pattern, kind); kind 'strong' tokens are claim-like on their own
HARD_TOKEN_PATTERNS = [
    (r"\b\d+(?:\.\d+){1,3}\b", "strong"),                    # versions 1.3.9
    (r"\b\d+(?:\.\d+)?\s*(?:KB|MB|GB|px)\b", "strong"),      # sizes 17KB
    (r"\b\d+(?:\.\d+)?%", "strong"),                         # 4.4%, R²=0.97
    (r"\b\d+(?:\.\d+)?\s*(?:字符|字节|条|个|次|轮|秒|分钟|天|周)\b", "strong"),
    (r"\b[0-9a-f]{7,40}\b", "strong"),                       # sha
    (r"\b(?!19\d\d\b|20\d\d\b)[1-9]\d{3,4}\b", "strong"),    # ports (not years)
    (r"\b[A-Z][A-Z0-9_]{3,}\b", "weak"),                     # CONSTANTS
    (r"\b[a-z]+[A-Z][A-Za-z0-9]{2,}\b", "weak"),             # camelCase
    (r"\b[a-z][a-z0-9]*_[a-z0-9_]{3,}\b", "weak"),           # snake_case
]


def _git(repo, *args, timeout=25):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def derive_dir():
    """Robust: match the memory dir whose decoded project path brackets $PWD."""
    proj_root = pathlib.Path.home() / ".claude" / "projects"
    if not proj_root.is_dir():
        return None
    cwd = os.getcwd()
    best = None
    for m in proj_root.glob("*/memory"):
        slug = m.parent.name
        decoded = "/" + slug.lstrip("-").replace("-", "/")
        if not os.path.isdir(decoded):      # lossy decode -> reject
            continue
        if cwd == decoded or cwd.startswith(decoded + "/") or decoded.startswith(cwd + "/"):
            if best is None or len(decoded) > len(best[1]):
                best = (m, decoded)
    return best[0] if best else None


def project_root_from_dir(d):
    """Decode the memory dir's own project slug back to a path (for repo discovery)."""
    slug = d.parent.name
    decoded = "/" + slug.lstrip("-").replace("-", "/")
    return pathlib.Path(decoded) if os.path.isdir(decoded) else None


def find_repos(root, max_depth=2):
    repos = []
    if not root or not os.path.isdir(root):
        return repos
    for dirpath, dirnames, _ in os.walk(root):
        depth = dirpath[len(str(root)):].count(os.sep)
        if depth >= max_depth:
            dirnames[:] = []
            continue
        if ".git" in dirnames:
            repos.append(pathlib.Path(dirpath))
            dirnames[:] = []               # do not descend into a repo
    return repos


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def _denum(s):
    """Canonicalize numeric spellings so cosmetic differences don't read as drift:
    '34,740' vs '34740', and '34740 字符' vs '34740字符'."""
    s = re.sub(r"(?<=\d),(?=\d)", "", s)           # thousands separators
    s = re.sub(r"(?<=\d)\s+(?=[^\d\s])", "", s)    # space between number and unit
    return s


def hard_tokens(s):
    """-> {token: kind}. 'strong' = claim-like on its own (sha/size/version/%/port)."""
    out = {}
    for pat, kind in HARD_TOKEN_PATTERNS:
        for t in re.findall(pat, s):
            if len(t) >= 4 or "%" in t or "." in t:
                out.setdefault(t, kind)
    return out


CODE_EXT = {"go", "ts", "tsx", "js", "mjs", "vue", "py", "rs", "java"}


def _plausible_path(path):
    """Repo-relative code path worth resolving. Drops elided ('...-foo.md') and
    absolute/generated paths that can never map to a sibling repo."""
    if "..." in path or "/" not in path:
        return False
    if path.startswith(("~", "/", "http")):
        return False
    if any(seg in path for seg in ("node_modules", "/dist/", ".deps/", ".scratch/")):
        return False
    return path.rsplit(".", 1)[-1] in CODE_EXT


def _strip_frontmatter(text):
    """§10 must not read metadata (originSessionId UUID fragments look like shas)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _looks_like_sha(s):
    """Pure-digit strings are dates/ids, not commit shas (20261014 ≠ sha)."""
    return len(s) >= 7 and len(s) not in (32, 64) and not s.isdigit()


def _clause_at(text, start, end):
    """The sentence/clause containing [start,end), never crossing 。！？；or \\n."""
    left = max(text.rfind(ch, 0, start) for ch in CLAUSE_DELIM)
    rights = [text.find(ch, end) for ch in CLAUSE_DELIM]
    right = min([r for r in rights if r != -1], default=len(text))
    return text[left + 1:right]


def _snapshot(report):
    inv = report["inventory"]
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": inv["files"],
        "index_entries": inv["index_entries"],
        "index_bytes": inv["index_bytes"],
        "headroom": inv["headroom"],
        "target_headroom": inv["target_headroom"],
        "budget_ok": inv["budget_ok"],
        "orphans": len(report["orphan_files"]),
        "missing": len(report["missing_files"]),
        "broken_links": len(report["broken_wikilinks"]),
        "frontmatter_issues": len(report["frontmatter_issues"]),
    }


def _read_last_record(d):
    p = d / RUN_LOG
    if not p.exists():
        return None
    last = None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            last = json.loads(ln)
        except Exception:
            continue
    return last


def _append_record(d, report):
    snap = _snapshot(report)
    with open(d / RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return snap


def _delta(prev, cur):
    def d(k):
        return cur[k] - prev.get(k, cur[k])
    return {"since": prev.get("ts"), "files": d("files"),
            "index_entries": d("index_entries"),
            "index_bytes": d("index_bytes"), "headroom": d("headroom")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None)
    ap.add_argument("--repos", default=None, help="root containing git repos (§10)")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP)
    ap.add_argument("--target-headroom", type=int, default=TARGET_HEADROOM)
    ap.add_argument("--quick", action="store_true",
                    help="only §1-4 (structural); exit 1 if any issue")
    ap.add_argument("--record", action="store_true",
                    help="report delta vs last run and append a snapshot")
    ap.add_argument("--fetch", action="store_true",
                    help="git fetch each repo before §10 (opt-in; uses network)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    d = pathlib.Path(a.dir) if a.dir else derive_dir()
    if not d or not (d / INDEX).exists():
        sys.exit(f"no {INDEX} found\n"
                 f"  tried dir={d}\n"
                 f"  pass --dir <memory dir> explicitly")

    files = sorted(p for p in d.glob("*.md") if p.name != INDEX)
    bodies = {p.name: p.read_text(encoding="utf-8") for p in files}
    index_lines = (d / INDEX).read_text(encoding="utf-8").splitlines()
    index_text = "\n".join(index_lines)
    idx_bytes = len(index_text.encode("utf-8"))

    entries = []
    for ln in index_lines:
        m = re.match(r"^- \[(.*?)\]\(([^)]+)\)\s*(?:—\s*(.*))?$", ln)
        if m:
            entries.append({"title": m.group(1), "file": m.group(2),
                            "hook": m.group(3) or "", "line": ln,
                            "bytes": len(ln.encode("utf-8"))})

    indexed = {e["file"] for e in entries}
    ondisk = {p.name for p in files}
    report = {}
    # §5 estimate: only the excess above HOOK_TARGET is notionally recoverable
    over = [e for e in entries if e["bytes"] > HOOK_SOFT_LIMIT]
    recoverable = sum(max(0, e["bytes"] - HOOK_TARGET) for e in over)
    report["inventory"] = {
        "dir": str(d), "files": len(files), "index_entries": len(entries),
        "index_bytes": idx_bytes, "cap": a.cap, "headroom": a.cap - idx_bytes,
        "total_bytes": sum(p.stat().st_size for p in files),
        "hooks_over_limit": len(over), "hook_recoverable_bytes": recoverable,
        "target_headroom": a.target_headroom,
        "budget_ok": idx_bytes + a.target_headroom <= a.cap,
    }
    report["orphan_files"] = sorted(ondisk - indexed)
    report["missing_files"] = sorted(indexed - ondisk)

    broken = [{"in": p.name, "link": l}
              for p in files
              for l in re.findall(r"\[\[([^\]]+)\]\]", bodies[p.name])
              if not (d / f"{l}.md").exists()]
    report["broken_wikilinks"] = broken

    fm_issues = []
    for p in files:
        fm = parse_frontmatter(bodies[p.name])
        miss = [k for k in FRONT_KEYS if k not in fm]
        if miss:
            fm_issues.append({"file": p.name, "issue": f"missing {miss}"})
        elif fm.get("name") and fm["name"] != p.stem:
            fm_issues.append({"file": p.name,
                              "issue": f"name '{fm['name']}' != filename slug"})
    report["frontmatter_issues"] = fm_issues

    report["long_hooks"] = sorted(over, key=lambda e: -e["bytes"])[:15]

    # --record: cross-run baseline (the directory otherwise treadmills near the cap)
    report["run_delta"] = None
    if a.record:
        prev = _read_last_record(d)
        if prev:
            report["run_delta"] = _delta(prev, _snapshot(report))

    # --quick: write-time / frequent-check mode. Structural only; exit 1 on issue.
    if a.quick:
        inv = report["inventory"]
        bad = bool(report["orphan_files"] or report["missing_files"]
                   or report["broken_wikilinks"] or report["frontmatter_issues"])
        print(f"quick: files={inv['files']} entries={inv['index_entries']} "
              f"index={inv['index_bytes']}B/{inv['cap']}B "
              f"headroom={inv['headroom']}B "
              f"budget={'PASS' if inv['budget_ok'] else 'FAIL'} "
              f"target={inv['target_headroom']}B")
        print(f"  orphans={len(report['orphan_files'])} "
              f"missing={len(report['missing_files'])} "
              f"broken_links={len(report['broken_wikilinks'])} "
              f"frontmatter={len(report['frontmatter_issues'])}"
              + ("  ⚠️ STRUCTURAL ISSUES" if bad else ""))
        if report["run_delta"]:
            D = report["run_delta"]
            print(f"  since {D['since']}: files {D['files']:+d}, "
                  f"entries {D['index_entries']:+d}, index {D['index_bytes']:+d}B, "
                  f"headroom {D['headroom']:+d}B")
        if a.record:
            _append_record(d, report)
        sys.exit(1 if bad else 0)
    report["largest_files"] = sorted(
        [{"file": p.name, "bytes": p.stat().st_size} for p in files],
        key=lambda x: -x["bytes"])[:12]

    # §7 staleness — strong vs weak, deduped by normalized claim
    strong, weak = defaultdict(list), defaultdict(list)
    for p in files:
        seen = set()
        for i, line in enumerate(bodies[p.name].splitlines(), 1):
            s = line.strip()
            if not s:
                continue
            norm = re.sub(r"\s+", "", s)
            if norm in seen:
                continue
            hit_strong = next((pat for pat in STRONG_STALE if re.search(pat, s)), None)
            hit_weak = next((pat for pat in WEAK_STALE if re.search(pat, s)), None)
            if not (hit_strong or hit_weak):
                continue
            seen.add(norm)
            rec = {"line": i, "why": hit_strong or hit_weak, "text": s[:110]}
            if hit_strong:
                rec["evidence"] = bool(SHA.search(s) or BRANCH.search(s)
                                       or DATED.search(s))
                strong[p.name].append(rec)
            else:
                weak[p.name].append(rec)
    report["staleness_strong"] = {k: v[:6] for k, v in strong.items()}
    report["staleness_weak"] = {k: v[:6] for k, v in weak.items()}

    # §8 redundancy
    dups = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            r = difflib.SequenceMatcher(None, entries[i]["title"],
                                        entries[j]["title"]).ratio()
            if r > 0.72:
                dups.append({"a": entries[i]["file"], "b": entries[j]["file"],
                             "ratio": round(r, 2)})
    report["near_duplicate_titles"] = dups
    clusters = defaultdict(list)
    for p in files:
        head = p.stem.split("-")[0]
        clusters[head].append(p.name)
    report["topic_clusters"] = {k: sorted(v) for k, v in clusters.items() if len(v) > 2}

    # §8b content overlap over *distinctive* anchors (df<=3) — real merge candidates
    tok2files = defaultdict(set)
    filetoks = {}
    for p in files:
        toks = set(hard_tokens(bodies[p.name]))
        filetoks[p.name] = toks
        for t in toks:
            tok2files[t].add(p.name)
    distinctive = {name: {t for t in toks if len(tok2files[t]) <= 3}
                   for name, toks in filetoks.items()}
    overlap = []
    names = sorted(filetoks)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, B = distinctive[names[i]], distinctive[names[j]]
            if not A or not B:
                continue
            inter = A & B
            if len(inter) >= 4:
                jac = len(inter) / len(A | B)
                if jac >= 0.20:
                    overlap.append({"a": names[i], "b": names[j],
                                    "shared": len(inter), "jaccard": round(jac, 2),
                                    "examples": sorted(inter)[:3]})
    report["content_overlap"] = sorted(overlap, key=lambda x: -x["jaccard"])[:15]

    # §9 index<->body drift
    drift = []
    for e in entries:
        body = bodies.get(e["file"])
        if body is None or not e["hook"]:
            continue
        hook = _denum(e["hook"])
        body = _denum(body)
        for t, kind in sorted(hard_tokens(hook).items()):
            # symmetric negation: if the hook *itself* already flags the token as
            # void ("「<17KB」目标从未达成已作废"), hook and body agree — not drift.
            hook_void = False
            for hm in re.finditer(re.escape(t), hook):
                if NEGATION.search(hook[max(0, hm.start() - 40): hm.end() + 40]):
                    hook_void = True
                    break
            if hook_void:
                continue
            if t in body:
                negated = []
                positive = False
                for m in re.finditer(re.escape(t), body):
                    cl = _clause_at(body, m.start(), m.end())
                    if NEGATION.search(cl):
                        # a negation about *another* object is not a denial of
                        # this token ("另一台设备上曾有一份未提交的修复，会与 X 分叉")
                        if QUALIFIER.search(cl):
                            positive = True
                            continue
                        negated.append(cl)
                    else:
                        positive = True
                # only flag when EVERY mention of the token is denied; one plain
                # assertion elsewhere means hook and body agree.
                if negated and not positive:
                    drift.append({"file": e["file"], "token": t,
                                  "kind": "negated_in_body",
                                  "ctx": re.sub(r"\s+", " ", negated[0]).strip()[:160]})
            elif kind == "strong":
                # only claim-like tokens are worth reporting as missing; bare
                # symbol absence is usually just paraphrase, not drift
                drift.append({"file": e["file"], "token": t,
                              "kind": "absent_from_body", "ctx": ""})
    # rank: contradictions first
    drift.sort(key=lambda x: 0 if x["kind"] == "negated_in_body" else 1)
    report["index_body_drift"] = drift[:40]

    # §10 citation resolvability
    cites = {"shas": [], "missing_paths": [], "claimed_off_default": [],
             "claimed_dangling": [], "noise_dangling": [], "refs": {},
             "claimed_files": 0, "skipped": False}
    repo_root = pathlib.Path(a.repos) if a.repos else project_root_from_dir(d)
    repos = find_repos(repo_root) if repo_root else []
    all_shas, all_paths, claimed = set(), set(), defaultdict(set)
    path_files = defaultdict(set)
    for p in files:
        t = _strip_frontmatter(bodies[p.name])
        all_shas |= {s for s in SHA.findall(t) if _looks_like_sha(s)}
        for m in CITATION.finditer(t):
            if _plausible_path(m.group(1)):
                all_paths.add(m.group(1))
                path_files[m.group(1)].add(p.name)
        sha_pos = [(m.start(), m.group(0)) for m in SHA.finditer(t)
                   if _looks_like_sha(m.group(0))]
        if sha_pos:
            for c in CLAIM_ON_DEV.finditer(t):
                if NEG_BEFORE.search(t[max(0, c.start() - 3):c.start()]):
                    continue        # "仍未合入 dev" asserts the opposite
                # one paragraph can mention several shas with different claims
                # ("…已推 origin，tip 到 X；仍未合入 dev…；已单独落进 dev，但 dev 里是 Y")
                # — so attribute each claim to the sha it sits closest to, not to
                # every sha in a fixed window.
                nearest = min(sha_pos, key=lambda sp: abs(sp[0] - c.start()))
                claimed[nearest[1]].add(p.name)
    cites["claimed_files"] = len(claimed)
    if len(all_shas) > 150:
        all_shas = set(sorted(all_shas)[:150])
        cites["skipped"] = True

    if repos:
        # one rev-list + one batched cat-file per repo
        default_ref, dev_set = {}, {}
        for r in repos:
            if a.fetch:
                _git(r, "fetch", "--quiet", "--prune", "origin", timeout=60)
            ref = None
            for cand in ("origin/dev", "origin/main", "origin/master"):
                if _git(r, "rev-parse", "--verify", "--quiet", cand) is not None:
                    ref = cand
                    break
            ref = ref or "HEAD"
            default_ref[r] = ref
            out = _git(r, "rev-list", ref) or ""
            dev_set[r] = set(out.split())
            ref_date = (_git(r, "log", "-1", "--format=%cI", ref) or "").strip()
            cites["refs"][r.name] = {"ref": ref, "date": ref_date}
        where = defaultdict(dict)          # short -> {repo: full}
        if all_shas:
            inp = "\n".join(sorted(all_shas)) + "\n"
            for r in repos:
                try:
                    res = subprocess.run(
                        ["git", "-C", str(r), "cat-file", "--batch-check",
                         "--buffer"],
                        input=inp, capture_output=True, text=True, timeout=60)
                except Exception:
                    continue
                if res.returncode != 0:
                    continue
                # --buffer output: "<input> <type> <size>"
                for line, short in zip(res.stdout.splitlines(), sorted(all_shas)):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] != "missing":
                        where[short][r] = parts[0]
            for short in sorted(all_shas):
                holders = where.get(short, {})
                if not holders:
                    rec = {"sha": short, "state": "dangling", "repos": [],
                           "claimed": short in claimed}
                    cites["shas"].append(rec)
                    if short in claimed:
                        cites["claimed_dangling"].append(short)
                    else:
                        cites["noise_dangling"].append(short)
                    continue
                on_def = [r.name for r, full in holders.items() if full in dev_set[r]]
                off = [r.name for r in holders if r.name not in on_def]
                if off and short in claimed:
                    cites["claimed_off_default"].append(
                        {"sha": short, "off": off, "on": on_def,
                         "files": sorted(claimed[short])})
                cites["shas"].append({"sha": short,
                                      "state": "off_default" if off else "on_default",
                                      "repos": off or on_def})
        # file paths — rank by how many memories cite them (noise control)
        missing = []
        for path in all_paths:
            if any((r / path).exists() or (r / path.split("/", 1)[-1]).exists()
                   for r in repos):
                continue
            missing.append(path)
        cites["missing_paths"] = sorted(
            missing, key=lambda p: (-len(path_files[p]), p))
    else:
        cites["skipped"] = True

    report["citations"] = cites

    if a.record:
        _append_record(d, report)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    inv = report["inventory"]
    W = print
    W(f"# memory audit — {inv['dir']}")
    W(f"\n## 1. inventory")
    W(f"- files={inv['files']}  index_entries={inv['index_entries']}  "
      f"total={inv['total_bytes']//1024}KB")
    W(f"- MEMORY.md={inv['index_bytes']}B / cap {inv['cap']}B  "
      f"headroom={inv['headroom']}B" +
      ("  ⚠️ LOW" if inv["headroom"] < CAP_WARN else ""))
    W(f"- hooks >{HOOK_SOFT_LIMIT}B: {inv['hooks_over_limit']}  "
      f"(tightening to ~{HOOK_TARGET}B would recover ~{inv['hook_recoverable_bytes']}B"
      f" — weigh against the {inv['cap']-inv['index_bytes']}B headroom you already have)")
    W(f"- budget: {'PASS' if inv['budget_ok'] else 'FAIL'} "
      f"(headroom {inv['headroom']}B vs target {inv['target_headroom']}B)")
    if report.get("run_delta"):
        D = report["run_delta"]
        W(f"- since {D['since']}: files {D['files']:+d}, entries "
          f"{D['index_entries']:+d}, index {D['index_bytes']:+d}B, "
          f"headroom {D['headroom']:+d}B")
    elif a.record:
        W("- since last run: first recorded run")

    W(f"\n## 2. index <-> file")
    W(f"- orphan files (not indexed): {report['orphan_files'] or 'none'}")
    W(f"- missing files (indexed, absent): {report['missing_files'] or 'none'}")

    W(f"\n## 3. broken wikilinks")
    if report["broken_wikilinks"]:
        for b in report["broken_wikilinks"]:
            W(f"- {b['in']} -> [[{b['link']}]]")
    else:
        W("- none")

    W(f"\n## 4. frontmatter")
    if report["frontmatter_issues"]:
        for f in report["frontmatter_issues"]:
            W(f"- {f['file']}: {f['issue']}")
    else:
        W("- clean")

    W(f"\n## 5. index hot spots (hooks > {HOOK_SOFT_LIMIT}B)")
    for e in report["long_hooks"]:
        W(f"- {e['bytes']}B  {e['file']}")

    W(f"\n## 6. largest files (condensation candidates)")
    for f in report["largest_files"]:
        W(f"- {f['bytes']//1024:>3}KB  {f['file']}")

    W(f"\n## 7. staleness — STRONG = state assertions, verify each against the repo")
    if report["staleness_strong"]:
        for fn, items in report["staleness_strong"].items():
            for it in items[:3]:
                ev = " +evidence" if it.get("evidence") else ""
                W(f"- {fn}:{it['line']}  [{it['why']}{ev}]  {it['text']}")
    else:
        W("- none")
    nw = sum(len(v) for v in report["staleness_weak"].values())
    W(f"- (WEAK, usually legitimate wording — {nw} lines across "
      f"{len(report['staleness_weak'])} files; not listed)")

    W(f"\n## 8. redundancy")
    if report["near_duplicate_titles"]:
        for x in report["near_duplicate_titles"]:
            W(f"- similar titles({x['ratio']}): {x['a']}  ~  {x['b']}")
    W(f"- naming buckets (NOT semantic duplicates): " + (", ".join(
        f"{k}({len(v)})" for k, v in report["topic_clusters"].items()) or "none"))
    if report["content_overlap"]:
        W(f"- content overlap over distinctive anchors (real merge candidates):")
        for x in report["content_overlap"]:
            W(f"    jac={x['jaccard']} shared={x['shared']}  {x['a']} ~ {x['b']}"
              f"   e.g. {', '.join(x['examples'])}")
    else:
        W(f"- content overlap: none above threshold")

    W(f"\n## 9. index<->body drift  (heuristic — read the body, then decide)")
    if report["index_body_drift"]:
        for x in report["index_body_drift"]:
            if x["kind"] == "negated_in_body":
                W(f"- ⚠️ CONTRADICTION {x['file']}: hook says '{x['token']}', "
                  f"body negates it → …{x['ctx']}…")
            else:
                W(f"- {x['file']}: hook token '{x['token']}' absent from body")
    else:
        W("- none")

    W(f"\n## 10. citation resolvability")
    if not repos:
        W("- skipped (no repo root found; pass --repos <path> to enable)")
    else:
        W(f"- repos scanned: {len(repos)}  ·  shas claiming 'on dev': "
          f"{cites['claimed_files']}")
        for c in cites["claimed_off_default"][:12]:
            W(f"- ⚠️ sha {c['sha']} is claimed on-dev in {', '.join(c['files'])} "
              f"but does NOT sit on {', '.join(c['off'])}'s default branch "
              f"(only on a side branch) → the claim is likely wrong")
        dang = [c["sha"] for c in cites["shas"] if c["state"] == "dangling"]
        if cites["claimed_dangling"]:
            W(f"- ⚠️ claimed-on-dev but unresolved by every scanned repo "
              f"(high signal): {', '.join(cites['claimed_dangling'][:10])}")
        if cites["noise_dangling"]:
            W(f"- {len(cites['noise_dangling'])} other unresolved hex token(s) "
              f"(likely md5/nonce/outside the scan root; not an error list)")
        if cites["missing_paths"]:
            W(f"- cited code paths not found ({len(cites['missing_paths'])}; "
              f"top by citation count — renamed/moved or outside the scan root): "
              f"{', '.join(cites['missing_paths'][:8])}")
        ref_dates = [v.get("date") for v in cites["refs"].values() if v.get("date")]
        if ref_dates:
            W(f"- default refs as of {min(ref_dates)} … {max(ref_dates)}"
              + ("" if a.fetch else "  (local refs; pass --fetch to update)"))
        if not (cites["claimed_off_default"] or cites["claimed_dangling"]
                or cites["missing_paths"]):
            W("- all claimed-on-dev shas sit on their default branch; "
              "all cited code paths still exist")

    W(f"\n# verdict: budget {'PASS' if inv['budget_ok'] else 'FAIL'} "
      f"(headroom {inv['headroom']}B vs target {inv['target_headroom']}B) · "
      "§1-6 mechanical (act directly) · §7-10 heuristic "
      "(verify, then act) · CONFIRM before deleting/merging.")


if __name__ == "__main__":
    main()
