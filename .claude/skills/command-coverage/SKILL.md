---
name: command-coverage
description: Measure, report and improve real-model command coverage — the three coverage levels, per-model and total statistics, untriaged tables, gap analysis. Use for any coverage number or coverage-improvement plan.
---

# Command coverage

Coverage is recorded per model: one `src/simubench/library/instruments/<id>.coverage.yaml` lists
every command in that model's reference and its degree. Read `docs/real-models.md` section 2 (the
three degrees) and section 6 (the table) before interpreting a number; do not restate those rules
here. D42 fixes the principle: an unimplemented command errors, it is never silently accepted.

## The three degrees

| Degree | Covered | Meaning |
| --- | --- | --- |
| `implemented` | yes, as implemented | behaviour follows the reference; a case in `tests/models/<id>/cases.yaml` proves it |
| `accepted` | yes, not as implemented | parsed, validated, stored; must not change any data and must carry `affects_data: false` |
| `not_modeled` | no | replies `-113 … not implemented in this definition`; do it later or declare it out of scope |

## Why a command is not modeled (`gap`, #1037)

Every `not_modeled` entry should carry `gap` (real-models.md section 6):

| `gap` | Meaning | Also |
| --- | --- | --- |
| `out_of_scope` | never simulated: the application layer of product-plan.md section 6 (jitter, eye, PAM, equalization, protocol decode, user code), cloud and repository services, the real-time clock, and service procedures that need real hardware (calibration, self-test, probe calibration) | — |
| `blocked` | waits for a platform capability | `blocked_by`: a product-plan.md 4.2 id (`C5`) or a short name of a capability 4.2 does not list yet (`mask-test`) |
| `model_work` | the platform can already do it; only the definition is missing | — |

SimuBench is a local tool, so a user script's file and setup commands must work (#1073). Map them:

| Commands | `gap` / `blocked_by` |
| --- | --- |
| file system: directories, copy, delete, catalog, load/put/get a file | `blocked` / `file-system` (#1069) |
| setup save and recall (`*SAV`/`*RCL`, `*LRN`, `STORe:SETup`, setup files) | `blocked` / `setup-memory` (#1070) |
| waveforms, readings and measurement results saved to a file | `blocked` / `data-file` (#1071) |
| screen image, hardcopy, print | `blocked` / `screenshot` (#1072) |
| interface configuration (LAN, GPIB, USB, LXI identify), beeper, display and GUI preferences, printer selection | `model_work`, candidate `accepted` with `affects_data: false` |

Saving the results of an application-layer analysis stays `out_of_scope` with that analysis.

An entry without `gap` is untriaged. The `-113` event of a command with a `gap` carries `gap`,
`blocked_by` and `note`, so users see why in the TUI log and the Web event history.

Report two numbers: `implemented` alone and `implemented + accepted`. Never move a data-affecting
command to `accepted` to raise the percentage — the consistency check rejects it, and D42 exists so
that a script passing on the virtual model also passes on the real one.

## Measure

One model — prints the table and the summary line `实现 N · 接受 N · 未实现 N · 共 N`:

```bash
uv run simubench coverage keysight-34465a
```

All models:

```bash
for f in src/simubench/library/instruments/*.coverage.yaml; do
  id=$(basename "$f" .coverage.yaml)
  uv run simubench coverage "$id" | tail -1
done
```

One model's split of the not-modeled commands is the last line of `simubench coverage <id>`
(`未实现：不做 N · 等能力 N · 可做 N · 未分拣 N`).

Standard report in one pass (totals, per-model ranking, untriaged audit):

```bash
uv run python - <<'EOF'
import glob, collections
from pathlib import Path
from simubench.library.coverage import load_coverage

tot = collections.Counter()
rows = []
for f in glob.glob("src/simubench/library/instruments/*.coverage.yaml"):
    cov = load_coverage(Path(f))
    c = collections.Counter(e.level for s in cov.sections for e in s.commands)
    tot.update(c)
    blank = cov.gap_stats()["untriaged"]
    rows.append((cov.model, c, blank))

n = sum(tot.values())
print("models", len(rows), "commands", n, "implemented", tot["implemented"],
      "accepted", tot["accepted"], "not_modeled", tot["not_modeled"])
print("coverage", f"{100 * (tot['implemented'] + tot['accepted']) / n:.1f}%",
      "implemented only", f"{100 * tot['implemented'] / n:.1f}%")
for model, c, blank in sorted(rows, key=lambda r: -(r[1]["implemented"] + r[1]["accepted"]) / sum(r[1].values())):
    t = sum(c.values())
    print(f"{model:24s} {c['implemented']:4d} {c['accepted']:3d} {c['not_modeled']:5d} "
          f"{100 * (c['implemented'] + c['accepted']) / t:5.1f}%  untriaged={blank}")
EOF
```

Use `load_coverage`; do not read these files with `yaml.safe_load` — some plain command headers in
the flow style are not valid plain YAML.

## Report

Say, in this order:

1. **Totals** — models, commands, implemented, accepted, not_modeled, both percentages.
2. **Per model** — sorted by coverage, with the implemented/accepted/not_modeled split. Flag tables
   whose `not_modeled` entries are mostly untriaged (no `gap`): those are drafts, and their
   percentage means nothing yet.
3. **Gap buckets** — group `not_modeled` by `gap` and `blocked_by` (untriaged tables: by note
   keywords or section). `out_of_scope` is permanent; `blocked` groups by capability; `model_work`
   is the backlog that needs no new capability.
4. **Next work** — by capability, not by model: one capability unlocks commands across many models.

The denominator is the whole reference command index, so percentages are not comparable between a
small DMM and a flagship analyser. Say that before comparing models.

## Improve

Order of work:

1. **Triage drafts** (no behaviour change): for each untriaged `not_modeled` entry write `gap`
   (and `blocked_by`) and the note by the tables above — permanent out of scope, blocked by a named
   capability, or model work. This produces the real
   backlog and makes the metric honest. Coverage-table work is `可认领`.
2. **Build the shared capability** that unblocks a cluster (`核心`, lead agent): status-register
   groups, math/statistics, DMM measurement functions, option handling, multi-marker. See the
   `component` skill.
3. **Wire models and add cases** once the capability exists (`可认领`; `real-model` skill).

If a model issue needs a missing capability, do not build it inside the model issue: open a `核心`
issue and comment on yours that it is blocked (`AGENTS.md`). Re-run the report at the end of each
step and quote the before/after numbers.
