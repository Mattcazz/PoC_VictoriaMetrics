# Stream OTLP/protobuf metrics into VictoriaMetrics

A single Go file (`import_metrics.go`, no external dependencies) reads an OTLP/protobuf file and POSTs each message directly to VictoriaMetrics. No OpenTelemetry Collector is involved in the import path.

## How it works

```
metrics.pb ──► import_metrics.go ──► POST /opentelemetry/v1/metrics ──► VictoriaMetrics
```

1. **`metrics.pb`** is an OTLP/protobuf file produced by the OpenTelemetry Collector `fileexporter` with `format: proto`. Each exported object is written as a 4-byte big-endian length prefix followed by the protobuf bytes.
2. **`import_metrics.go`** reads one length-delimited message at a time and POSTs the raw bytes to VictoriaMetrics with `Content-Type: application/x-protobuf`. It never parses or converts the metrics — it forwards the exact wire format VictoriaMetrics already understands.
3. **VictoriaMetrics** stores the OTLP metrics natively (gauges, sums, histograms, summaries, exponential histograms — all handled).

## Producing `metrics.pb`

On your metrics collection side, configure a `file` exporter in your OTel Collector config:

```yaml
exporters:
  file/protobuf:
    path: /data/metrics.pb
    format: proto
```

Then copy `metrics.pb` next to this repo's `docker-compose.yml`.

## Usage

```bash
docker compose up -d --build
```

The `metrics-importer` container waits for VictoriaMetrics to be healthy, streams all messages from `metrics.pb`, then exits. VictoriaMetrics keeps running.

To us VictoriaMetrics: 

```bash
# VictoriaMetrics UI
open http://localhost:8428/vmui


## Cleanup

```bash
docker compose down -v
```
