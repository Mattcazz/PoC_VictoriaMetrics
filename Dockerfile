FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod import_metrics.go ./
RUN go build -o import_metrics .

FROM alpine:latest
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=builder /app/import_metrics .
ENTRYPOINT ["./import_metrics"]
