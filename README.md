# Claude Code Skills

A collection of [Claude Code](https://claude.com/claude-code) skills I use daily, cleaned up and shared for reuse.

## Skills

| Skill | What it does |
|---|---|
| [`memory-curate`](skills/memory-curate/) | Audits and cleans a Claude Code memory directory (`MEMORY.md` + topic files): index/file drift, dead links, size budget, stale assertions, and citations that no longer resolve |

## What is a skill?

A skill is a folder with a `SKILL.md` file (YAML frontmatter + instructions) that Claude Code loads on demand. The `description` field is what the model uses to decide when to invoke it, so keep it specific.

Skills can bundle helper scripts and reference docs; Claude reads them only when it needs to.

## Install

### Personal (available in every project)

```bash
git clone https://github.com/SethXu123/sethxu-skills.git ~/src/sethxu-skills

mkdir -p ~/.claude/skills
cp -R ~/src/sethxu-skills/skills/* ~/.claude/skills/
# or, to keep them in sync with the repo:
# ln -s ~/src/sethxu-skills/skills/memory-curate ~/.claude/skills/memory-curate
```

### Project-scoped (only this repo)

```bash
mkdir -p .claude/skills
cp -R ~/src/sethxu-skills/skills/memory-curate .claude/skills/
```

Restart Claude Code (or start a new session) and the skill becomes available. Type `/memory-curate` or just describe the task — e.g. "clean up my memory, the index is too big".

## `memory-curate`

Claude Code's auto-memory writes plain markdown into `~/.claude/projects/<project-slug>/memory/`. It works well, and then it rots:

- `file:line` pointers drift after every refactor
- "not pushed yet" stays true for months after the push
- the `MEMORY.md` index slowly starts contradicting the topic files
- the index hits its read cap and the **trailing entries are silently truncated**

This skill turns cleanup into a repeatable audit instead of a vibe check:

1. **Mechanical checks** (exact, act directly): index/file 1:1, broken `[[wikilinks]]`, frontmatter, oversized hooks, index byte budget.
2. **Heuristic checks** (leads to verify, not verdicts): stale state assertions, redundant clusters, index↔body contradictions, and citations (SHAs / paths) that no longer resolve against the repo.

It also enforces one rule that keeps memory small: **durable knowledge → memory, reusable procedure → skill, one-off artifact → out**. Temporary task output does not belong in memory, and "condensing" it there is not a fix.

### Usage

```bash
# Full audit for the memory dir of the current project
python3 ~/.claude/skills/memory-curate/scripts/memory_audit.py --record

# Just the structural checks (fast, offline, exit 1 on problems — usable as a write-time gate)
python3 ~/.claude/skills/memory-curate/scripts/memory_audit.py --quick
```

Then ask Claude to work through the report, or read [`skills/memory-curate/SKILL.md`](skills/memory-curate/SKILL.md).

Options: `--dir`, `--repos`, `--cap` (index byte limit, default 24986), `--target-headroom` (default 2000), `--fetch` (network), `--json`.

### Requirements

- `python3` (standard library only)
- `bash` and `git` (git only for the citation checks)

### Design notes

- The script is **report-only** and never mutates memory. Destructive actions are always proposed for approval.
- Heuristics (`§9`, `§10`) are deliberately conservative and mark their own noise. They are tuned against a Chinese-language memory directory, so token-level patterns (negation words, "on dev" phrasing) are Chinese; the structural checks are language-agnostic.
- `scripts/self_test.sh` builds a throwaway git repo and memory fixture, so the regression tests are hermetic.

## License

MIT — see [LICENSE](LICENSE).
