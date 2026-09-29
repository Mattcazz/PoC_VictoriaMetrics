# metrics.json → OTel Collector → VictoriaMetrics → Grafana

Replays an OpenTelemetry Collector `fileexporter` dump (`metrics.json`) into
VictoriaMetrics and serves it through a Grafana host-metrics dashboard.

```
metrics/*.json ──▶ otel-collector ──▶ VictoriaMetrics ──▶ Grafana
   OTLP/JSON       otlp_json_file       OTLP protobuf      Prometheus
   (on disk)         receiver              :8428         datasource :3000
```

Drop one file or a hundred into `metrics/` — the receiver globs the whole
directory.

VictoriaMetrics ingests OTLP **protobuf**, not OTLP/JSON, so the collector's only
job is to re-encode the dump and ship it.

## Usage

```bash
docker compose up -d
```

| | |
|---|---|
| Grafana | <http://localhost:3000> — opens straight into the dashboard, no login |
| VictoriaMetrics UI | <http://localhost:8428/vmui> |

Grafana runs with anonymous access as Admin and the login form disabled. That is
fine for a localhost stack replaying a static dump — **do not expose it on a
public interface as-is**.

The dashboard is [Host Metrics (opentelemetry)](https://grafana.com/grafana/dashboards/24638-host-metrics/)
from grafana.com, provisioned automatically.

Tear down with `docker compose down`.

No storage volume is declared: the files in `metrics/` are the source of truth and are
re-imported from the beginning on every start, so VictoriaMetrics' copy is a
disposable cache. `docker compose down` is therefore always a clean reset — no
stale series can leak into the next run, and no `-v` is needed.

## Three settings that are not optional

Each of these fails *silently* — the import reports success and the dashboard
comes up blank or, worse, confidently wrong.

**`--retentionPeriod=10y`** (`docker-compose.yml`). VictoriaMetrics deletes
samples older than the retention period and drops their monthly partition. The
current dump is from 2026-07-07; under the default `30d` every sample is
rejected with `cannot insert row with too small timestamp` and the dashboard is
empty. (There is also `--futureRetention`, default `2d`, if a dump's clock runs
ahead.)

**`--opentelemetry.usePrometheusNaming`** (`docker-compose.yml`). Stores
`system.cpu.time` as `system_cpu_time_seconds_total` — dots to underscores, unit
appended, `_total` on counters. Every OpenTelemetry dashboard on grafana.com is
written against those names; with the raw OTLP names **none** of this
dashboard's 30 queries match anything.

**`timeInterval: "15s"`** (`grafana/provisioning/datasources/datasource.yml`).
**This is the one value tied to the scrape interval of the dump — see below.**

## Does it work for any scrape interval?

Yes, but `timeInterval` has to match the dump. It is one line in
`grafana/provisioning/datasources/datasource.yml`, and it is the only setting
that depends on how the original collector was configured.

Grafana derives the rate window from it:

```
$__rate_interval = max($__interval + timeInterval, 4 * timeInterval)
```

The dashboard's CPU panel is `1 - avg(rate(cpu idle[$__rate_interval]))`. If
that window is narrower than the gap between samples, `rate()` finds no sample
pair, returns 0, and the panel reads **100% CPU** — a confidently wrong number
rather than an obvious blank.

Measured against this dump (60s scrape gap, correct answer 3.12%), varying only
`timeInterval`:

| `timeInterval` | rate window | step | CPU panel |
|---|---|---|---|
| `5s`  | 20s | 5s  | 3.12% **and 100%** — wrong |
| `10s` | 40s | 10s | 3.12% **and 100%** — wrong |
| `15s` | 1m  | 15s | **3.12%** — correct |
| `30s` | —   | 30s | no data — step coarser than the dump |
| `60s` | —   | 60s | no data |

So the value is bounded on both sides:

* **lower:** `timeInterval >= scrape_gap / 4`, so the rate window covers the gap;
* **upper:** the step must stay well inside the dump's total span.

### Mixed intervals in one file

A dump — or a set of them — can mix scrape intervals. This one already does:

| Family | Interval | Series |
|---|---|---|
| hostmetrics receiver | 60s | 539 |
| prometheus receiver (`inesrp_*`) | 10s | 306 |

One `timeInterval` covers both, **provided it is sized from the widest gap**.
Verified with `15s` against this file: the 60s family reports CPU 3.12% and load
0.48, and the 10s family reports a request rate of 0.1/s and correct gauge
values.

The cost is resolution, not correctness: the finer family is sampled at the
coarser step, so a 10s series returns 4-5 of its 7 points. Values are right;
the line is just less dense.

That degrades gracefully until the ratio gets extreme. Stretched to 360x (host
60min vs app 10s, `timeInterval: 15m`), every query still returns data, but each
family collapses to a single point — the step is then wider than the finer
family's entire span. If you ever have a dump like that, one datasource setting
is no longer enough; give the fast panels a per-panel **Min interval**, or add a
second datasource with its own `timeInterval` and point those panels at it.

**Set `timeInterval` to about a quarter of the *widest* scrape interval** in the
file:

| Collector scrape interval | `timeInterval` |
|---|---|
| 10s | `3s` |
| 30s | `8s` |
| 60s | `15s` (current) |
| 5m  | `75s` |
| 15m | `4m` |

The upper bound only bites on very short dumps. This one is only 70s long with a
60s scrape gap — just two host samples — so `15s` is nearly the only value that
satisfies both bounds. A dump covering hours or days has a wide margin.

## Does the time of day of the original scrape matter?

No. The dashboard's default range is stored as an **absolute UTC instant**:

```json
"time": { "from": "2026-07-07T12:49:01.648Z", "to": "2026-07-07T12:50:24.946Z" }
```

An absolute range is independent of the wall clock, so a dump recorded at 03:00
behaves exactly like one recorded at 15:00. What matters is the *age* of the
data, and only because of `--retentionPeriod` above — not the time of day.

Two related things that do matter:

* The dashboard is set to `"timezone": "utc"`, so its axis labels match the
  timestamps in `metrics.json`. Change it to `"browser"` for local time; the
  data shown is identical either way.
* A **relative** range (`now-2d`) is the one case where the clock matters, which
  is exactly why the default range is absolute. If the time picker is left on
  something like "Last 6 hours" or "This fiscal year", the panels will look
  empty or distorted — reset it to the range above.

## Adding or replacing dumps

Put any number of `*.json` files in `metrics/`:

```bash
cp /path/to/*.json metrics/
chmod 644 metrics/*.json        # the collector reads them as a non-root user
```

**Files are picked up live** — no restart. Verified: with the stack running, a
file dropped into `metrics/` was ingested within seconds and the sample count
rose to exactly match the source.

To start clean instead (for example after *removing* a file, which does not
retract the samples already stored):

```bash
docker compose down && docker compose up -d
```

Storage is disposable, so `down` alone is a full reset.

Re-imports are idempotent, but only because of `--dedup.minScrapeInterval=1ms`.
Restarting the collector makes it re-read every file from the beginning, and
VictoriaMetrics stores those identical samples *twice* rather than overwriting
them — without dedup, one restart doubled the stored sample count from 3426 to
6852. Query results stayed correct either way; the flag keeps storage honest.

Two files containing the *same* content are harmless: duplicate samples collapse
to one.

Two things are pinned to the *current* data and need updating if the new dumps
differ:

1. **the dashboard's default time range** — `time` in
   `grafana/dashboards/host-metrics.json`, set to the window the files cover
   (the union across all of them);
2. **`timeInterval`** — a quarter of the widest scrape gap found in *any* file,
   per the table above.

## Known gaps in this dump

- **Three filesystem panels are empty.** The collector that produced
  `metrics.json` did not run the filesystem scraper, so there are no
  `system_filesystem_*` metrics. 26 of the 30 queries return data.
- **The Memory gauge reads 98%.** It computes `1 - free / sum(all states)`,
  which counts 45 GiB of reclaimable page cache as used. The honest figure,
  `used / limit`, is 27%. Likewise "Total Memory" sums overlapping states and
  reads 66.5 GiB where the host has 62.2 GiB. Both are the upstream dashboard's
  own definitions, not a problem with this setup.
- Grafana logs two `level=error` lines at startup about missing
  `provisioning/plugins` and `provisioning/alerting` directories. Harmless.

## Adding another dashboard from grafana.com

Drop the JSON into `grafana/dashboards/` — it is picked up within 10 seconds, no
restart. Exports from grafana.com need three edits first, because Grafana only
resolves them when you import through the UI, not when provisioning from disk:

1. delete the `__inputs` and `__requires` blocks;
2. replace every `${DS_PROMETHEUS}` datasource reference with `victoriametrics`;
3. set `time` to a range that covers your data.

## Files

| | |
|---|---|
| `docker-compose.yml` | VictoriaMetrics + OTel Collector + Grafana |
| `otel-collector-config.yaml` | `otlp_json_file` receiver (globs `metrics/`) → OTLP/protobuf exporter |
| `metrics/*.json` | The fileexporter dumps being replayed — any number of files |
| `grafana/provisioning/` | Datasource and dashboard provider config |
| `grafana/dashboards/` | The provisioned dashboard |
