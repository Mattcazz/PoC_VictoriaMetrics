#!/usr/bin/env python3
"""Verify that all samples from metrics.json were imported into VictoriaMetrics.

Parses the OTel Collector fileexporter JSON output, reconstructs the expected
labels and values, and compares them against /api/v1/export from VictoriaMetrics.
"""

import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path


VM_EXPORT_URL = "http://localhost:8428/api/v1/export"
TOLERANCE = 1e-9


def parse_value(value_obj: dict) -> float:
    """Extract numeric value from OTLP NumberDataPoint value object."""
    if "asDouble" in value_obj:
        return float(value_obj["asDouble"])
    if "asInt" in value_obj:
        return float(value_obj["asInt"])
    raise ValueError(f"Unknown value format: {value_obj}")


def attr_to_label(attr: dict) -> tuple[str, str]:
    """Convert OTLP attribute to (key, value) label pair."""
    key = attr["key"]
    val_obj = attr["value"]
    if "stringValue" in val_obj:
        return key, val_obj["stringValue"]
    if "intValue" in val_obj:
        return key, str(val_obj["intValue"])
    if "doubleValue" in val_obj:
        return key, str(val_obj["doubleValue"])
    if "boolValue" in val_obj:
        return key, str(val_obj["boolValue"]).lower()
    raise ValueError(f"Unsupported attribute value type: {val_obj}")


def collect_expected_samples(path: Path) -> dict:
    """Read metrics.json and return expected samples keyed by metric name."""
    expected: dict[str, set] = defaultdict(set)

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)

            for resource_metric in request.get("resourceMetrics", []):
                resource_labels = dict(
                    attr_to_label(a) for a in resource_metric.get("resource", {}).get("attributes", [])
                )

                for scope_metric in resource_metric.get("scopeMetrics", []):
                    scope = scope_metric.get("scope", {})
                    scope_labels = {}
                    if "name" in scope:
                        scope_labels["scope.name"] = scope["name"]
                    if "version" in scope:
                        scope_labels["scope.version"] = scope["version"]

                    for metric in scope_metric.get("metrics", []):
                        metric_name = metric["name"]
                        data_points = []

                        if "gauge" in metric:
                            data_points = metric["gauge"].get("dataPoints", [])
                        elif "sum" in metric:
                            data_points = metric["sum"].get("dataPoints", [])
                        elif "summary" in metric:
                            data_points = metric["summary"].get("dataPoints", [])
                        elif "histogram" in metric:
                            # Skip histograms for simplicity in this verification.
                            continue
                        elif "exponentialHistogram" in metric:
                            continue

                        for dp in data_points:
                            dp_labels = dict(
                                attr_to_label(a) for a in dp.get("attributes", [])
                            )
                            labels = {**resource_labels, **scope_labels, **dp_labels}
                            timestamp_ns = int(dp["timeUnixNano"])
                            timestamp_ms = round(timestamp_ns / 1_000_000)
                            value = parse_value(dp)

                            label_tuple = tuple(sorted(labels.items()))
                            expected[metric_name].add((label_tuple, timestamp_ms, value))

    return expected


def fetch_vm_export(metric_names: list[str], start_ms: int, end_ms: int) -> dict:
    """Fetch exported samples from VictoriaMetrics for the given metrics."""
    actual: dict[str, set] = defaultdict(set)

    for name in metric_names:
        params = {
            "match[]": name,
            "start": str(start_ms),
            "end": str(end_ms),
        }
        query = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params.items())
        url = f"{VM_EXPORT_URL}?{query}"

        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")

        for line in body.strip().splitlines():
            if not line:
                continue
            obj = json.loads(line)
            metric = obj["metric"]
            name = metric["__name__"]
            labels = {k: v for k, v in metric.items() if k != "__name__"}
            label_tuple = tuple(sorted(labels.items()))

            for ts_ms, value in zip(obj["timestamps"], obj["values"]):
                actual[name].add((label_tuple, int(ts_ms), float(value)))

    return actual


def compare(expected: dict, actual: dict) -> bool:
    """Compare expected vs actual samples with a small timestamp tolerance."""
    all_names = set(expected.keys()) | set(actual.keys())
    total_expected = 0
    total_actual = 0
    total_missing = 0
    total_extra = 0
    mismatch_values = 0
    ts_window_ms = 2  # allow +/- 2 ms rounding differences

    for name in sorted(all_names):
        exp = expected.get(name, set())
        act = actual.get(name, set())
        total_expected += len(exp)
        total_actual += len(act)

        # Build actual lookup: (labels) -> list of (timestamp, value)
        act_by_labels: dict = defaultdict(list)
        for lt, ts, v in act:
            act_by_labels[lt].append((ts, v))

        missing = []
        for lt, exp_ts, exp_v in exp:
            matches = act_by_labels.get(lt, [])
            found = False
            for idx, (act_ts, act_v) in enumerate(matches):
                if abs(act_ts - exp_ts) <= ts_window_ms and abs(act_v - exp_v) <= TOLERANCE:
                    found = True
                    matches.pop(idx)
                    break
            if not found:
                missing.append((lt, exp_ts, exp_v))

        # Extra = actual samples that were not matched.
        extra = [(lt, ts, v) for lt, items in act_by_labels.items() for ts, v in items]
        total_missing += len(missing)
        total_extra += len(extra)

        # Check value mismatches on exact timestamp matches.
        exp_by_key = {(lt, ts): v for lt, ts, v in exp}
        act_by_key = {(lt, ts): v for lt, ts, v in act}
        common_keys = set(exp_by_key.keys()) & set(act_by_key.keys())
        for key in common_keys:
            if abs(exp_by_key[key] - act_by_key[key]) > TOLERANCE:
                mismatch_values += 1
                print(f"  Value mismatch for {name} {key}: expected {exp_by_key[key]}, got {act_by_key[key]}")

        if missing:
            print(f"\nMetric: {name}")
            print(f"  Expected: {len(exp)}, Actual: {len(act)}")
            print(f"  Missing {len(missing)} sample(s):")
            for sample in sorted(missing)[:5]:
                labels, ts, value = sample
                print(f"    ts={ts}, value={value}, labels={dict(labels)}")
            if len(missing) > 5:
                print(f"    ... and {len(missing) - 5} more")

        if extra:
            print(f"\nMetric: {name}")
            print(f"  Expected: {len(exp)}, Actual: {len(act)}")
            print(f"  Unmatched actual {len(extra)} sample(s):")
            for sample in sorted(extra)[:3]:
                labels, ts, value = sample
                print(f"    ts={ts}, value={value}, labels={dict(labels)}")

    print("\n=== Verification Summary ===")
    print(f"Metrics checked: {len(all_names)}")
    print(f"Expected samples: {total_expected}")
    print(f"Actual samples:   {total_actual}")
    print(f"Missing samples:  {total_missing}")
    print(f"Extra samples:    {total_extra}")
    print(f"Value mismatches: {mismatch_values}")

    return total_missing == 0 and mismatch_values == 0


def main() -> int:
    path = Path("metrics.json")
    if not path.is_file():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1

    print("Parsing metrics.json...")
    expected = collect_expected_samples(path)
    print(f"Found {len(expected)} metric names, "
          f"{sum(len(v) for v in expected.values())} expected samples")

    # Determine time range from expected data.
    all_timestamps = [
        ts for samples in expected.values() for _, ts, _ in samples
    ]
    if not all_timestamps:
        print("No samples found in metrics.json", file=sys.stderr)
        return 1

    start_ms = min(all_timestamps)
    end_ms = max(all_timestamps)

    print(f"Querying VictoriaMetrics export from {start_ms} to {end_ms}...")
    actual = fetch_vm_export(list(expected.keys()), start_ms, end_ms)
    print(f"Found {len(actual)} metric names, "
          f"{sum(len(v) for v in actual.values())} actual samples")

    ok = compare(expected, actual)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
