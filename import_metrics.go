// import_metrics reads an OTLP/protobuf file as produced by the OpenTelemetry
// Collector fileexporter with `format: proto` and streams each
// ExportMetricsServiceRequest directly to VictoriaMetrics over HTTP at
// /opentelemetry/v1/metrics. No OpenTelemetry Collector middleman is required.
//
// File framing (per exporter/fileexporter/file_writer.go exportMessageAsBuffer):
//
//	for each exported object:
//	  4 bytes  - unsigned 32-bit big-endian integer = length N of the next object
//	  N bytes  - the protobuf-encoded ExportMetricsServiceRequest
//
// The program is pure Go standard library: it does not parse, convert or limit
// the OTLP metric types it forwards — VictoriaMetrics handles gauges, sums,
// summaries, histograms and exponential histograms natively at the OTLP endpoint.
package main

import (
	"bytes"
	"encoding/binary"
	"flag"
	"io"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	var (
		file    = flag.String("file", "metrics.pb", "OTLP/protobuf file (fileexporter format: proto)")
		vmURL   = flag.String("url", "http://localhost:8428/opentelemetry/v1/metrics", "VictoriaMetrics OTLP/protobuf ingestion endpoint")
		timeout = flag.Duration("timeout", 30*time.Second, "HTTP request timeout")
	)
	flag.Parse()

	f, err := os.Open(*file)
	if err != nil {
		log.Fatalf("open %q: %v", *file, err)
	}
	defer f.Close()

	client := &http.Client{Timeout: *timeout}

	var (
		lenBuf [4]byte
		sent   int
		failed int
	)

	for {
		switch _, err := io.ReadFull(f, lenBuf[:]); err {
		case nil:
		case io.EOF:
			goto done
		case io.ErrUnexpectedEOF:
			log.Printf("truncated length header at end of file; stopping")
			goto done
		default:
			log.Fatalf("read length prefix: %v", err)
		}

		size := binary.BigEndian.Uint32(lenBuf[:])

		buf := make([]byte, size)
		if _, err := io.ReadFull(f, buf); err != nil {
			log.Fatalf("read message body (size=%d): %v", size, err)
		}

		req, err := http.NewRequest(http.MethodPost, *vmURL, bytes.NewReader(buf))
		if err != nil {
			log.Fatalf("create request: %v", err)
		}
		req.Header.Set("Content-Type", "application/x-protobuf")
		req.ContentLength = int64(size)

		resp, err := client.Do(req)
		if err != nil {
			log.Fatalf("send request: %v", err)
		}
		respBody, _ := io.ReadAll(resp.Body)
		resp.Body.Close()

		if resp.StatusCode >= 300 {
			failed++
			log.Printf("POST %s: status %d: %s", *vmURL, resp.StatusCode, string(respBody))
			continue
		}
		sent++
	}

done:
	log.Printf("done: %d OTLP requests sent, %d failed", sent, failed)
	if failed > 0 {
		os.Exit(1)
	}
}