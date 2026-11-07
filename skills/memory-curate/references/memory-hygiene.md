# Memory hygiene rules (field notes)

Source: retrospectives from two full memory cleanups (86 files / ~360KB).

## 1. Why memories rot

A memory is a **snapshot at write time**, not live state. The three decay forms each have a fingerprint:

- **Pointer drift**: `app_init.go:146` becomes `:381` weeks later. Symbol names (`homeWs`) usually survive; line numbers almost never do.
  => Cite "symbol name (file, line ~= XXX)"; look code up with `grep -rn "<symbol>"`.
- **State reversal**: "still not pushed" was true when written; later it was pushed. **This is the costliest class** — it pushes you to redo finished work,
  or conversely makes you afraid to touch something already confirmed.
- **Index-body decoupling**: the MEMORY.md hook is a compressed second-hand summary; the body changed but the hook did not, so the index says the opposite.
  Typical: the body starts with "the patches/ flow is retired" while the index line still says "changes go through patches/".

## 2. The size economics of the index (MEMORY.md)

- The index **must** stay short — it has a hard read cap (common implementations measure ~24.4KB); over it, trailing entries are **silently truncated**. That is not "more tokens",
  it is **memory actually lost**.
- But "short" refers to **hooks**, not "titles only": one directly usable recall cue (a port number, an R², an invariant name) is worth a line.
- How to judge an oversized hook: hooks **> 250 bytes** are almost always "mini-summaries"; push details into the topic file.
  (A 250B Chinese hook is roughly 83 characters, so the threshold is more generous for Chinese than for English.)
- Size-reduction priority: (1) tighten oversized hooks -> (2) merge similar historical incident entries -> (3) the rest.
- **But the return on (1) depends on which round the directory is in** — calibrated by two real runs:
  - **First cleanup**: 86 index entries; tightening hooks took 22.4KB -> 20.1KB (**-10%**),
    by trimming the 19 longest (mostly 350-600B) to ~180-200B, **without deleting a single entry**.
  - **Maintained directory**: hooks already sit at 250-300B; tightening 15 of them reclaimed only **805B (-4.4%)**,
    which is close to wasted effort when headroom is sufficient.
  => **Check `hook_recoverable_bytes` in section 1 before investing**; if it is not enough, skip to (2) merging.

## 3. File content: condense, don't delete

Two kinds of content in large files are worlds apart in value:

| | Content | Treatment |
|---|---|---|
| **High compounding** | "what must never change" invariants, criteria, mechanism chains, decision rationale, naming/path anchors | **Never delete a word**; often should be emphasized |
| **One-off** | how many assertions a run executed, which commit fixed it, step-by-step logs, verification counts | Compress to "fix essentials + pointer to `docs/`" |

**"Structurally intact != the business rules are present"** is the same family of lesson: verify against **silent failure** (a whole block disappearing), not against errors.
Same when deleting content — as long as you cannot see "which criteria survived", you must not delete.

## 4. Consistency invariants (checked by the script)

**Mechanical (§1-6, act directly):**

1. Index entry count == topic file count (every file indexed, every index item has a file).
2. Every `[[wikilink]]` target file exists (`[[x]]` -> `x.md`).
3. Frontmatter has `name` / `description` / `metadata`; `name` == the filename slug.
4. No dead pointers: if an index links an external file (e.g. `docs/*.md`) that exists only on an unmerged branch, say so in the memory.

**Heuristic (§9-10, hits are leads, not verdicts — verify against the source/git):**

5. **Index hooks must not contradict the body.** If a hard token in the hook (sha / size / % / version / port) sits next to a negation in the body
   (void, never, retired, reverted, no longer, …), that is "the index says the opposite".
   NOTE: **§1-6 cannot see this class at all** — which is exactly why §9 exists.
6. **Citations must resolve**:
   - whether the sha is on the branch it **claims** (especially claims like "merged into dev");
   - whether the cited code path still exists. **Line numbers are excluded** — line drift is expected and they count only as "approx location".
   NOTE: do not decide sha membership with `git branch --contains` alone: **replay commits with identical content report as unmerged**.
   We hit this for real: a memory said "fix `<sha-A>` was moved into dev", while dev actually contains `<sha-B>`,
   so `branch --contains <sha-A>` forever returns "unmerged". §10 instead asks "is this sha in the default branch's
   `rev-list` set", and only warns for shas the text explicitly claims are on-dev, so a branch tip is not reported as an error.

> Note: near-miss links like `[[readtime-acceptance-traps]]` are common dead links — a link name must match the target slug verbatim.

## 5. Discipline for citing external state

Any sentence in memory about "the current state of a repo/branch/service" is an **assertion that expires**:
"fixed and pushed", "still unmerged", "TODO not done", "**still missing a LICENSE**".

Treatment:
- **When writing**, bind the claim to **recomputable evidence** (commit sha, the result of `git merge-base --is-ancestor`)
  rather than a bare conclusion word.
- **When reading/cleaning**, verify each one; if you cannot (e.g. "has production been redeployed?"), **mark it uncertain** rather than repeating it as fact.
- Cross-repo dependencies (e.g. "the platform-side default originates in another repo") must say **where to look**, because it is naturally uncheckable here.

## 6. Safe technique for bulk index edits

Editing dozens of lines by hand is error-prone and miss-prone. A steadier posture (field-tested):

1. Locate each line by a **unique key** (e.g. the entry's `(file.md)` marker);
2. In the script, **assert each key hits exactly once** (`assert hits[marker] == 1`);
3. `cp` a backup first;
4. Run a consistency check afterwards (entry count / dead links).

This "**replace by unique key + assert hits**" pattern recurs across bulk text replacement: every replacement needs an assertion,
otherwise misses are silent.

The same applies when editing the audit script itself: `bash scripts/self_test.sh` is the regression gate for §9/§10 —
they are heuristics with windows and negation rules, and widening or narrowing a window can turn a true positive into a silent miss.

## 7. Irreversible actions need confirmation

Merging or deleting memories causes information loss; adopt a rule: "confirm user intent before deleting a backup".
The default mode = **condense + tighten only**; hand the "merge/delete list" to the user for a decision.

## 8. Memory / procedure / artifact: decide the home first

Before cleaning a memory, ask whether it **should be a memory at all**. There are three homes, not one switch:

| Home | Criteria (fingerprint) | Counter-example (does not belong here) |
|---|---|---|
| **memory** | invariants / mechanism chains / root causes / criteria / decision rationale / recall cues (port, R², invariant name); **still true across a different batch of tasks** | the concrete output of one batch |
| **skill** | numbered steps + command sequences; "reuse for the next batch" | one-off results |
| **task directory** | a batch's **concrete output**: lists, counts (22 files = 21 people), tiering, dated conclusions; "**first / this / next batch**" language | invariants, criteria |

**Why**: the index is a **hard-capped, silently truncating** resource (see §2) — temporary content consumes quota, dilutes the truly useful
recall cues, and when over the cap it **evicts** durable entries at the end. So "temporary tasks do not go into memory" is the same point as §2, not fussiness.

**One memory can hold all three components** — take a candidate-screening task:

- the three-condition criteria (hard requirements + fundamentals + domain experience) -> close to memory, but better folded into a skill;
- the resume-parsing + keyword-filtering procedure (encoding compatibility, PDF text extraction) -> **reusable procedure -> skill**;
- the tiered candidate list -> **one-off artifact**, meaningful only for that batch.

=> Treatment: extract the procedure into a skill, move the artifact out, delete the memory body. **Do not "condense" it inside memory** — that polishes something that should have been moved.

**Where artifacts go: nearby, no fixed directory.** Reusable procedures go into the corresponding skill's output directory; everything else goes into the project's existing delivery
directory (e.g. `docs/`, `handoff/`). If unsure, do not write it into memory — the memory directory should not contain non-memory content.

**This is a write-side discipline, not only a cleanup-side one.** Run the table above before saving any memory; when wording like "first / next batch / list"
appears, assume it belongs in a task directory.
