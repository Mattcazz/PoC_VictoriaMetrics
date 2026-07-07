# Import OTel Collector fileexporter JSON into VictoriaMetrics

Minimal Docker setup that ingests an OTel Collector `fileexporter` JSON output
(`metrics.json`) into VictoriaMetrics.

VictoriaMetrics accepts OTLP **protobuf** natively, not OTLP/JSON. The local
OpenTelemetry Collector reads the JSON file with the `otlpjsonfilereceiver` and
forwards protobuf to VictoriaMetrics on port `8428`.

## Files

- `docker-compose.yml` — VictoriaMetrics + OpenTelemetry Collector
- `otel-collector-config.yaml` — collector pipeline using `otlp_json_file` receiver
- `metrics.json` — your OTel Collector fileexporter output
- `README.md` — this file

## Usage

1. Start the stack:

   ```bash
   docker compose up -d
   ```

   The collector reads `metrics.json` on startup and sends it to VictoriaMetrics.

2. Open the VictoriaMetrics UI and select a time range that covers the
   timestamps in `metrics.json` (for example, **Last 7 days**):

   http://localhost:8428/vmui

3. Or query directly via HTTP:

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

## Cleanup

```bash
docker compose down -v
```
