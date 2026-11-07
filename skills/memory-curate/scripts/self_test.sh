#!/usr/bin/env bash
# Regression test for memory_audit.py's heuristic probes (§9 index<->body drift,
# §10 citation resolvability) and the budget/quick/record modes.
# These are heuristics with tunable windows and negation rules — easy to break
# when editing. Run this after touching the script.
#
#   bash ~/.claude/skills/memory-curate/scripts/self_test.sh
#
# Hermetic: §10 builds its own throwaway git repo, so it does NOT depend on any
# live repos (a real sha can get merged and silently invalidate a fixture).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
R="$TMP/memory"; mkdir -p "$R"
REPOS="$TMP/repos"; mkdir -p "$REPOS/r1"
fail=0

ck() { # ck <description> <file-with-output> <must-contain|must-not-contain> <needle>
  if [ "$3" = "must" ]; then
    grep -qF -- "$4" "$2" && echo "  ok   $1" || { echo "  FAIL $1 (expected: $4)"; fail=1; }
  else
    grep -qF -- "$4" "$2" && { echo "  FAIL $1 (unexpected: $4)"; fail=1; } || echo "  ok   $1"
  fi
}

# ---------------------------------------------------------------------------
# §10 fixture: a hermetic repo with a dev branch, a side-only commit, and a
# branch tip that is explicitly described as unmerged.
# ---------------------------------------------------------------------------
G="$REPOS/r1"
git -C "$G" init -q
git -C "$G" config user.email test@example.com
git -C "$G" config user.name test
echo base > "$G/f.txt"; git -C "$G" add .; git -C "$G" commit -q -m base
BASE="$(git -C "$G" rev-parse --short=9 HEAD)"
git -C "$G" checkout -q -b dev
echo dev > "$G/d.txt"; git -C "$G" add .; git -C "$G" commit -q -m dev-advance
DEV="$(git -C "$G" rev-parse --short=9 HEAD)"
git -C "$G" checkout -q -b side "$BASE"
echo side > "$G/s.txt"; git -C "$G" add .; git -C "$G" commit -q -m side-only
SIDE="$(git -C "$G" rev-parse --short=9 HEAD)"
git -C "$G" checkout -q -b side2 "$BASE"
echo side2 > "$G/s2.txt"; git -C "$G" add .; git -C "$G" commit -q -m side2-tip
TIP2="$(git -C "$G" rev-parse --short=9 HEAD)"
git -C "$G" update-ref refs/remotes/origin/dev refs/heads/dev
git -C "$G" checkout -q dev
DANGLING="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

# ---------------------------------------------------------------------------
# memory fixtures
# ---------------------------------------------------------------------------
cat > "$R/MEMORY.md" <<EOF
- [索引体积政策](policy.md) — 索引须保持**<17KB**，超了末尾会被截断
- [已修好的政策钩子](fixed.md) — 「<17KB」目标从未达成已作废，硬约束是 24.4KB
- [分支状态](branch.md) — 修复 \`$SIDE\` 已单独搬进 dev
- [真已合入](real.md) — 修复 \`$DEV\` 已合入 dev
- [混合段](mixed.md) — 一段里既有分支 tip 又有已合入的提交
- [千分位](comma.md) — 工具 schema 34740字符，是对话内容 4.4 倍
- [跨设备修复](fp.md) — 与 \`$SIDE\` 分叉
- [悬空提交](dangling.md) — 修复 \`$DANGLING\` 已合入 dev
- [日期不是 sha](date.md) — 20261014 已合入 dev
- [UUID 片段](uuid.md) — 修复 e1786ccf-c3e0-4b25-b6ef-89667ffe92ad 已合入 dev
EOF
cat > "$R/comma.md" <<'EOF'
---
name: comma
description: 千分位
metadata:
  type: project
---
实测工具 schema 为 34,740 字符，约等于对话内容（约 8k 字符）的 4.0–4.4 倍。
EOF
cat > "$R/fixed.md" <<'EOF'
---
name: fixed
description: 已修好的政策钩子
metadata:
  type: feedback
---
本条钩子已自曝其短：<17KB 目标从未达成、已作废；硬约束是 24.4KB。
EOF
cat > "$R/policy.md" <<'EOF'
---
name: policy
description: 索引体积政策
metadata:
  type: feedback
---
真正的硬约束只有 24.4KB 读取上限。本条 description 里写的「<17KB」**从未达成过**，已作废。
EOF
cat > "$R/branch.md" <<EOF
---
name: branch
description: 分支状态
metadata:
  type: project
---
其中代码修复 \`$SIDE\` 已单独搬进 dev（见 E 处置）。
EOF
cat > "$R/real.md" <<EOF
---
name: real
description: 真已合入
metadata:
  type: project
---
park 隔离修复 \`$DEV\` 已合入 dev。
EOF
cat > "$R/mixed.md" <<EOF
---
name: mixed
description: 混合段
metadata:
  type: project
---
分支**已推 origin**，tip 已从 \`$BASE\` 前进到 \`$TIP2\`；**仍未合入 dev**。
这条修复已单独落进 dev，**但 dev 里的提交是 \`$DEV\`**（无需再动）。
EOF
cat > "$R/fp.md" <<EOF
---
name: fp
description: 跨设备修复
metadata:
  type: project
---
另一台设备上曾有一份未提交的同向修复，会与 \`$SIDE\` 分叉，以仓库这份为准。
EOF
cat > "$R/dangling.md" <<EOF
---
name: dangling
description: 悬空提交
metadata:
  type: project
---
修复 \`$DANGLING\` 已合入 dev。
EOF
cat > "$R/date.md" <<'EOF'
---
name: date
description: 日期不是 sha
metadata:
  type: project
---
20261014 已合入 dev。
EOF
cat > "$R/uuid.md" <<'EOF'
---
name: uuid
description: UUID 片段
metadata:
  type: project
---
修复 e1786ccf-c3e0-4b25-b6ef-89667ffe92ad 已合入 dev。
EOF

OUT="$TMP/out.txt"
$PY "$HERE/memory_audit.py" --dir "$R" --repos "$REPOS" > "$OUT" 2>&1
# §10 assertions must look only at §10 — §7 echoes whole body lines, so a sha
# can legitimately appear elsewhere in the report without being flagged.
S10="$TMP/s10.txt"
sed -n '/^## 10\./,/^# verdict/p' "$OUT" > "$S10"
S9="$TMP/s9.txt"
sed -n '/^## 9\./,/^## 10\./p' "$OUT" > "$S9"

echo "§9 index<->body drift"
ck "flags a hook token the body negates" "$S9" must "hook says '17KB', body negates it"
ck "does NOT flag a token the hook itself already calls void" "$S9" must-not "fixed.md"
ck "does NOT flag 34740 when the body writes 34,740" "$S9" must-not "comma.md"
ck "does NOT flag a negation about another device" "$S9" must-not "fp.md"

echo "§10 citation resolvability"
ck "flags sha claimed on-dev but only on a side branch" "$S10" must "$SIDE"
ck "does NOT flag a sha that really is on the default branch" "$S10" must-not "$DEV"
ck "does NOT flag a branch tip the text says is unmerged" "$S10" must-not "$TIP2"
ck "flags a claimed sha that resolves in no repo (high signal)" "$S10" must "$DANGLING"
ck "does NOT treat a pure-digit date as a sha" "$S10" must-not "20261014"
ck "does NOT treat a UUID fragment as a sha" "$S10" must-not "89667ffe92ad"

echo "§1-6 structural (fixture has no orphans/dupes by construction)"
ck "index<->file consistent" "$OUT" must "orphan files (not indexed): none"
ck "reports a budget verdict" "$OUT" must "budget: PASS"

echo "budget / quick / record"
Q0="$TMP/quick0.txt"
$PY "$HERE/memory_audit.py" --dir "$R" --quick > "$Q0" 2>&1
q0=$?
[ "$q0" = 0 ] && echo "  ok   --quick exits 0 when structurally clean" \
  || { echo "  FAIL --quick exit=$q0 on clean fixture"; fail=1; }
touch "$R/orphan.md"
Q1="$TMP/quick1.txt"
$PY "$HERE/memory_audit.py" --dir "$R" --quick > "$Q1" 2>&1
q1=$?
[ "$q1" = 1 ] && echo "  ok   --quick exits 1 when an orphan exists" \
  || { echo "  FAIL --quick exit=$q1 with orphan"; fail=1; }
ck "quick names the orphan count" "$Q1" must "orphans=1"
rm -f "$R/orphan.md"

REC1="$TMP/rec1.txt"; REC2="$TMP/rec2.txt"
$PY "$HERE/memory_audit.py" --dir "$R" --record > "$REC1" 2>&1
$PY "$HERE/memory_audit.py" --dir "$R" --record > "$REC2" 2>&1
ck "first --record says it is the first run" "$REC1" must "first recorded run"
ck "second --record prints a cross-run delta" "$REC2" must "since "
[ -s "$R/.memory-curate-runs.jsonl" ] && echo "  ok   run log written" \
  || { echo "  FAIL run log missing"; fail=1; }

[ "$fail" = 0 ] && echo "ALL PASS" || echo "SOME FAILED"
exit $fail
