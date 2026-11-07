---
name: memory-curate
description: Audit and clean up a Claude Code memory directory (MEMORY.md index + topic .md files) — checks index/file consistency, dead links, index size limits, stale state assertions, and redundancy clusters, plus two corruption forms mechanical checks cannot see: an index hook that contradicts its body, and cited SHAs or code paths that no longer resolve. Fixes fall into four buckets (correct facts / condense logs / tighten hooks / propose merges for approval) after first deciding each entry's proper home (durable knowledge / reusable procedure / one-off artifact). Use when the user asks to clean, audit, tidy, or review memory, memory files, a MEMORY.md that is too large or over its limit, or memories that are redundant, stale, or wrong.
---

# memory-curate

Audit a Claude Code **memory directory**, then fix it by category.
The memory directory is derived from `$PWD` (decode the slug under `~/.claude/projects/<slug>/` back to its real path, and pick the one that contains `$PWD`); `--dir` overrides it.

> Maintaining this skill's scripts: after any edit, run `bash scripts/self_test.sh`.
> §9/§10 are **heuristics** with tunable windows and negation rules — easy to break while editing.

## Why four categories (the core mental model)

Memories decay in three typical forms, and each needs a different fix. Mixing them causes wrongful deletions or missed edits:

| Form | Symptom | Fix |
|---|---|---|
| **Pointer drift** | `file:line`, branch names, commit SHAs are snapshots from write time | Anchor on **symbol names**; keep line numbers only as "approx location" |
| **State reversal** | Says "not pushed / unfinished / still a bug" long after it was done | **Verify against the repo** before editing; the costliest class — it makes you redo finished work |
| **Index–body decoupling** | MEMORY.md hooks say the opposite of the topic file body | Update the hook when you edit the body; hooks should hold only directly usable recall cues |

Plus one **structural invariant**: index entries ↔ files should be 1:1; every `[[wikilink]]` must have a target file.

There is also a **prior question** (not corruption, but "does this belong here"): before editing, ask whether each entry *should* be a memory. There are three homes:
**durable knowledge → memory / reusable procedure → skill / one-off artifact → task directory**. Temporary artifacts (a batch list, counts, tiering conclusions, "first batch / next batch" wording) **should not stay in memory and should not be "condensed" — they should be moved out**.
Criteria and examples: `references/memory-hygiene.md` §8.

## Steps

### 1. Run the audit script

```bash
python3 ~/.claude/skills/memory-curate/scripts/memory_audit.py --record
# Optional: --dir <memory dir>   --repos <root containing git repos>
#           --cap <index byte cap, default 24986>   --target-headroom <closing budget, default 2000>
#           --record  cross-run baseline (read last snapshot, report delta, append this run)
#           --quick   only §1-4; exit 1 when structural errors exist (can be wired as a PostToolUse gate)
#           --fetch   git fetch before §10 (offline by default; uses local origin/*)
#           --json
```

It prints 10 sections. **§1-6 are mechanical facts (act directly); §7-10 are heuristics (verify first)**:

| Section | Content | Nature |
|---|---|---|
| 1 Inventory | file/entry counts, index bytes vs cap, **budget PASS/FAIL**, hook-recoverable bytes, cross-run delta with `--record` | fact |
| 2 Consistency | orphan / missing | fact |
| 3 Dead links | `[[wikilink]]` with no target file | fact |
| 4 Frontmatter | name/description/metadata present; name matches slug | fact |
| 5 Index hot spots | hooks > 250B | fact |
| 6 Large files | size ranking | fact |
| 7 Staleness | **STRONG** (state assertions — verify each) vs WEAK (often legitimate wording; count only) | heuristic |
| 8 Redundancy | filename prefix buckets (**not** semantic duplicates) + body overlap over distinctive anchors (real merge candidates) | heuristic |
| 9 Index↔body drift | a hard token in the hook is **negated** by the body, or absent from it | heuristic |
| 10 Citation resolvability | does the SHA live on the default branch; does the cited code path still exist; **noise tiers** (high-signal "claimed" vs folded rest); default-ref date | heuristic |

### 2. Work through the sections

0. **Decide the home first** (no script section; pure semantic judgment) — for each entry ask: **durable knowledge / reusable procedure / one-off artifact**?
   One-off artifacts (a batch list, counts, tiering conclusions, "first/next batch" wording) **must not be condensed inside memory — move them out first**: extract reusable procedures into a skill, and put the rest in the project's existing delivery location (e.g. `docs/`, `handoff/` — **no fixed directory**).
   Criteria and a case where one memory held all three components: `references/memory-hygiene.md` §8. Do this first, then handle what remains.
1. **§2-4 structural errors** (orphan / missing / broken wikilink / frontmatter) — **mechanical, fix directly**.
   For orphans, either add them to the index or explain why they are intentionally unindexed; "missing" is a dead pointer — repoint it or drop the entry.
2. **§1 index size** — check `hook_recoverable_bytes` first: it tells you how much tightening **all** hooks can reclaim.
   **Do not assume this is worth doing by default** (see "Index size" below).
3. **§5 hooks** — for hooks > 250B, first decide whether they are a "mini-summary". If so, push details down into the topic file; if they hold **recall cues** (an R², a port number, an invariant name), keep them.
4. **§6 large files** — condense per "Condense, don't delete" below.
5. **§7 staleness** — **verify only the STRONG set**, prioritizing entries with `+evidence` (sha/branch/date in the same sentence). WEAK is a count only; reading each one is usually not worth it. Verification techniques:
   ```bash
   git merge-base --is-ancestor <sha> origin/<default-branch>   # merged?
   git branch -a --contains <sha>                              # note: replay commits with identical content cause false "unmerged"
   grep -rn "<symbol>" --include="*.go"                        # symbol still present? (don't copy line numbers)
   ```
6. **§8 redundancy** — filename prefix buckets are only grouping, **not** a merge signal; `content_overlap` (body overlap over distinctive anchors) is the real candidate set. See "Merging".
7. **§9 index↔body drift** — the script only gives **leads**: `CONTRADICTION` means a hard token in the hook sits next to a negation (void / never / reverted / …) in the body. **Read the body** to decide which side is right, then fix the hook or the body.
   (This is where the skill catches "the index says the opposite" — §1-6 cannot see this at all.)
8. **§10 citation resolvability** — `claimed on-dev but NOT on default branch` is the highest-value signal: the memory claims a commit landed in dev, but it exists only on a side branch. `claimed-on-dev but unresolved` is also high signal.
   Everything else (`other unresolved hex token`, `path not found`) is **folded noise** (references outside the scan root, renames, md5/nonce/date). Do not treat it as an error list to work through. It uses local `origin/*` by default; add `--fetch` when you suspect staleness (the script prints ref dates — check those before going online).
9. **§1 budget and cross-run delta** — before finishing, check `budget: PASS/FAIL` and the `since …` line from `--record`.
   The goal is to **leave headroom** (default 2000B), not to "fix everything this run found"; historical runs show that fixing issues without a budget target keeps the directory bouncing back to the cap (starting headroom is often 650-900B).
   If FAIL and safe merges/moves exist, get approval per section 5; if not, report FAIL with the shortfall.

### 3. Condense, don't delete (default mode)

Large files have very uneven value density: the opening "what must never change / criteria / mechanisms" is high-compounding; the later "how many assertions this run executed, which commit fixed it, step-by-step logs" is **one-off** — `docs/` and `git log` are the authority.

=> Compress **incident logs** into "fix essentials + pointers to in-repo docs", and **never delete a single durable criterion**.
Typically this removes 40-60% of the size without losing reusable knowledge.

### 4. Index size

The index has a **hard read cap**: Claude Code injects the whole `MEMORY.md` into context; if you installed a budget hook (or another reader enforces the cap), that value wins. The script defaults to **24986B = 24.4 KiB** (matching common implementations); override with `--cap`.
WARNING: do not write 24400 — that is 24.4 *decimal* KB and under-reports by 586B; over the cap, **trailing entries are silently truncated** (= memory actually lost).
Reduce size in this order:

1. Tighten hooks — **but compute the payoff first**; §1 already gives `hook_recoverable_bytes`:
   - **First cleanup**: hooks are typically 350-600B; tightening reclaims ~10% with no recall loss — do this first.
   - **Maintained directory**: hooks are usually already 250-300B, so tightening often reclaims only 3-5% (measured: 15 hooks tightened = 805B).
     **Do not spend time here**; go to step 2. A 250B Chinese hook is roughly 83 characters — not a "mini-summary" to begin with.
2. Merge **similar historical incident entries** into one (use §8's `content_overlap` to find real candidates).
3. **Only then** consider more aggressive measures.

WARNING: do not tighten hooks down to a bare title — the most-used recall cue (an R², a port number) is worth a whole line.

### 5. Merge / delete: **confirm first**

Merging or deleting memories is **irreversible information loss** (suggest adopting a rule: never delete a backup without asking the user).
Default to "condense + tighten"; present "merge into N entries" / "delete these" as a **checklist for the user to approve**.

### 6. Write-time gate (optional, recommended)

Orphan files, dead links, and frontmatter errors are usually introduced by **other sessions writing**. Post-hoc cleanup is only remediation.
In high-frequency setups, run a `--quick` structural check after writes to the memory directory (read-only, offline, exit 1 = problem):

```bash
python3 ~/.claude/skills/memory-curate/scripts/memory_audit.py --quick
```

When wiring a hook, trigger only on writes to `MEMORY.md` and `*.md` — do not run it on every tool call.

## Output conventions

- Report in three parts: **fixed (factual errors)** / **condensed (size)** / **awaiting your approval (merges/deletions)**.
- Lead with the **budget verdict**: `budget PASS/FAIL`, current headroom vs target, and the cross-run delta from `--record` (entries added/removed, index bytes delta).
- For factual errors, give **evidence** (`measured <command> => <result>`), not just conclusions. Same for §9/§10 hits — quote both sides for "hook says X, body says not-X"; give `git merge-base` output for "claimed merged into dev".
- §9/§10 **lines are leads, not conclusions**: say "heuristic hit; I read the body / checked git and confirmed or refuted it".
- Finish with a consistency check (entry count == file count, no dead links) and report the result.

## Anti-patterns

- Never conclude from MEMORY.md alone — the index is a second-hand summary; **the body is the source of truth**.
- Never copy `file:line` into new memories — line numbers always drift; write symbol names.
- Never treat "not pushed" as stale and delete it — it may genuinely still be unpushed; **verify with git**.
- Never assume "tighten hooks" is always priority one — **on an already-cleaned directory the return is single-digit percent**; check `hook_recoverable_bytes` first.
- Never treat §9/§10 hits as facts — they are heuristics: `absent_from_body` is often just different wording; `noise_dangling` / `path not found` often come from repos outside the scan root, or from dates/md5. **Look before acting.**
- Never treat "fixed everything this run found" as done — check `budget`. Historical runs show directories bounce around 650-900B headroom without a target; aim for `--target-headroom` (default 2000B).
- Never use decimal `24.4KB = 24400B` as the cap — the real truncation point is set by the reader; the script defaults to **24986B = 24.4 KiB**.
- Never delete durable criteria to save size — that trades compounding assets for one-off gains.
- Never "condense" one-off artifacts inside memory — they should be **moved out** (procedures into a skill, artifacts into a nearby archive), not polished.
  Criteria: `references/memory-hygiene.md` §8.
- Never edit dozens of index lines by hand — replace by **unique key** in a script and **assert the hit count**; safer and faster.

See `references/memory-hygiene.md` for details.
