# Scale results

What the agent does when a campaign has hundreds or thousands of gaps instead
of a few dozen. `RESULTS.md` listed it as *not measured*: *"Campaigns with
hundreds of gaps (the server's 200-row cap stops the run)"*. This file is the
measure, for the part that needs no model, and an **estimate** for the part
that does.

## 2026-11-10: the deterministic parts, fake model

Nothing here ran a language model. A synthetic campaign
(`tools/synthetic_campaign.py`) holds N planted gaps and 3 clean results per
gap; the whole graph runs on it (`tools/bench.py`), the data going through
the real MCP server (`readonly-sql-mcp`, in process, so the start-up of the
server process is not counted). The model is a stand-in that answers the
planted truth and counts its calls. Mix: 30 % noise, 30 % announced changes,
20 % known defects, 20 % regressions (route: block).

Machine: the developer's Windows 11 PC, Python 3.12. Fastest of 3 timed runs
per size; memory from a separate run under `tracemalloc`. Reproduce with:

    python tools/bench.py --sizes 200 1000 5000 20000 50000 --max-rows 100000

| gaps | results | SQL alone ms | compute_gaps ms | classify ms | score ms | report ms | total s | peak MB | judge calls | checkpoint MB | memo KB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 800 | 1.0 | 16 | 15 | 0 | 0 | 0.09 | 1 | 201 | 0.6 | 17 |
| 1,000 | 4,000 | 3.9 | 31 | 0 | 0 | 0 | 0.13 | 4 | 1,001 | 2.7 | 85 |
| 5,000 | 20,000 | 31.7 | 188 | 15 | 0 | 16 | 0.40 | 16 | 5,001 | 13.6 | 423 |
| 20,000 | 80,000 | 104 | 765 | 63 | 16 | 94 | 1.42 | 63 | 20,001 | 54.3 | 1,692 |
| 50,000 | 200,000 | 512 | 2,125 | 171 | 47 | 203 | 3.64 | 145 | 50,001 | 136.7 | 4,254 |

- **Per-node times come from the trace file, whose clock ticks every 16 ms on
  Windows**: a 0 or a 16 means "below the tick". Read the total, and the
  large rows.
- `total` includes the checkpoint writes and the trace file; `compute_gaps`
  is the gap query through MCP plus the arithmetic on every gap; `classify`
  is the evidence reads and the check of every answer by the code (the fake
  model answers instantly).
- The row limit of the server was raised to run past 200 gaps (see below);
  with the production default, only the first row (200 gaps) can run.

### What it says

1. **The deterministic parts scale linearly and are not the problem.** 50,000
   gaps (200,000 results) triaged in 3.6 s, 145 MB. 5,000 gaps: 0.4 s.
2. **Inside them, the slowest step is the transport**: `compute_gaps` is
   4 to 8 times the SQL alone (2.1 s against 0.5 s at 50,000): turning rows
   into JSON, through the MCP session, then into dicts. The score is
   negligible.
3. **The state grows with the campaign and is written in full after every
   node**: 13.6 MB of checkpoint for 5,000 gaps (136 MB for 50,000), a 423 KB
   memo for 5,000 rows. Not slow, but a human reviewer reading a 423 KB memo
   is the real limit of that table.

### Correctness at scale

`tests/test_scale.py` runs campaigns of 200 and 1,000 gaps in four profiles
(route go, human_review, block, mixed), and 5,000 mixed. On each, the code
finds exactly the planted gaps (none missed, none invented among the clean
results), the category counts, the score (computed from the plan with the
weights written out in the test) and the route are the expected ones.
A model that calls everything *noise* cannot ship a 1,000-gap campaign: the
code accepts noise only below 5 tolerances, so all the other claims are
refused and the route is block.

### Walls found (none is a speed problem)

| Wall | Where | What happens | Measured |
|---|---|---|---|
| **200 rows per query** | `readonly-sql-mcp` default (`Limits.max_rows`); `BankData.stdio` starts the server with the default and the server's command line has no option to change it | a campaign of 201 gaps or more is **refused** (`DataError: the server cut the result`), by design: half a triage is worse than none | 200 gaps pass, 201 stop (`test_at_the_production_row_limit_200_gaps_pass_and_201_stop`) |
| **Summary prompt** | `report.facts` writes one line per gap; `OllamaJudge` uses `num_ctx` 8192 | the facts of the summary outgrow the model's context | 66 gaps: 5.2 k characters; 200: 14.8 k; 1,000: 72 k; 5,000: 360 k. At about 4 characters per token (estimate), the context is full at about 400 gaps |
| **5 s query timeout** | `readonly-sql-mcp` default | not reached | the gap query takes 0.5 s on 200,000 results |

Not done, because it changes production files (the clone of this sprint does
not touch them): a `limits` argument or an environment variable for the
server (`readonly-sql-mcp`), or paging of `find_gaps` by `r.id`; a bounded
`facts` (counts by category, then only the worst gaps) for the summary.

## The model side: an estimate, not a measure

No model ran at this scale. Starting point, measured in `RESULTS.md` run 3
(8 campaigns x 3 repetitions): 90 model calls (66 classifications, 24
summaries), 43,323 tokens in, 8,107 out, 176 s. That gives, per call,
about 481 tokens in and 90 out; a classification takes about 1.65 s (176 s
minus 24 summaries at about 2.8 s, divided by 66). The prompt of one
classification does not grow with the campaign (one gap, the evidence of its
product); only the summary does (about 18 tokens per gap, see above).

Estimate for one run on N gaps (N + 1 calls: one per gap, one summary):

| gaps | model calls | tokens in | tokens out | time (qwen3.5:9b, same PC) |
|---:|---:|---:|---:|---:|
| 22 (one campaign of the evaluation) | 23 | about 11 k | about 2 k | about 40 s |
| 200 | 201 | about 100 k | about 18 k | about 5.5 min |
| 1,000 | 1,001 | about 480 k (+ a summary that no longer fits) | about 90 k | about 28 min |
| 5,000 | 5,001 | about 2.4 M | about 450 k | about 2.3 h |

Error margin: wide (a factor of 1.5 either way). The cost in money stays 0 EUR
on a local model; at this size the cost is time, and the model is the only
real bottleneck: a run of 5,000 gaps costs 2.3 h of model against 0.4 s of
everything else. Classifying by (product, metric, category) groups instead of
one call per gap would be the first lever, and would need its own evaluation:
it changes what the model sees.
