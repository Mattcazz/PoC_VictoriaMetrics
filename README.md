# Import OTel Collector fileexporter JSON into VictoriaMetrics

Minimal Docker setup that ingests an OTel Collector `fileexporter` JSON output
(`metrics.json`) into VictoriaMetrics.

VictoriaMetrics accepts OTLP **protobuf** natively, not OTLP/JSON. The local
OpenTelemetry Collector reads the JSON file with the `otlpjsonfilereceiver` and
forwards protobuf to VictoriaMetrics on port `8428`.

## Files

- `docker-compose.yml` — VictoriaMetrics + OpenTelemetry Collector + Grafana
- `otel-collector-config.yaml` — collector pipeline using `otlp_json_file` receiver
- `metrics.json` — your OTel Collector fileexporter output
- `grafana/` — Grafana provisioning (datasource + sample dashboard)
- `verify.py` — confirms all samples from `metrics.json` are in VictoriaMetrics
- `README.md` — this file

## Usage

1. Start the stack:

   ```bash
   docker compose up -d
   ```

   The collector reads `metrics.json` on startup and sends it to VictoriaMetrics.

2. Open Grafana and explore the pre-loaded dashboard:

   http://localhost:3000

   Login: `admin` / `admin`

    A Prometheus datasource pointing at VictoriaMetrics is already configured
    (VictoriaMetrics is Prometheus-compatible). Because `metrics.json` contains
    historical timestamps, set the dashboard time range to **Last 7 days** or a
    custom range that covers your data.

3. Or use the built-in VictoriaMetrics UI:

   http://localhost:8428/vmui

4. Or query directly via HTTP:

   ```bash
   curl -s 'http://localhost:8428/prometheus/api/v1/query_range' \
     -d 'query=system.cpu.time' \
     -d 'start=-7d' \
     -d 'step=1h'
   ```

## Replacing `metrics.json`

1. Overwrite `metrics.json` with your new file. Make sure it is readable by the
   container user:

   ```bash
   chmod 644 metrics.json
   ```

2. Restart the collector so it reads the file from the beginning:

   ```bash
   docker compose restart otel-collector
   ```

   Because no persistent storage extension is configured, the receiver re-reads
   the file from the start on every restart.

## Verify no data loss

Run the verification script to compare every sample in `metrics.json` against
what VictoriaMetrics stored:

```bash
python3 verify.py
```

Expected output:

```
Metrics checked: 223
Expected samples: 3426
Actual samples:   3426
Missing samples:  0
Extra samples:    0
Value mismatches: 0
```

The script allows a ±2 ms timestamp tolerance because nanosecond timestamps are
rounded differently during ingestion; all values and label sets are matched
exactly.

## Cleanup

```bash
docker compose down -v
```
