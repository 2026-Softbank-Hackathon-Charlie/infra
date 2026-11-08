FROM docker.io/library/golang:1.25.3-alpine@sha256:aee43c3ccbf24fdffb7295693b6e33b21e01baec1b2a55acc351fde345e9ec34 AS build
WORKDIR /src
COPY server.go .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-s -w" -o /server server.go
FROM scratch
COPY --from=build /server /server
USER 65532:65532
ENTRYPOINT ["/server"]
