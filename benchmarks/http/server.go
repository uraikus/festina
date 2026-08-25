// See server.f's own comment for what this benchmark measures. A raw
// net.Listener with a single-threaded, sequential accept loop (no
// goroutine per connection) -- deliberately not net/http's own default
// server, to match every other language here using a hand-rolled
// connection-per-request loop rather than a mature framework doing the
// concurrency and keep-alive handling for it.
package main

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
)

func main() {
	port := 8080
	if len(os.Args) > 1 {
		if p, err := strconv.Atoi(os.Args[1]); err == nil {
			port = p
		}
	}
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		panic(err)
	}
	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		handle(conn)
	}
}

func handle(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReader(conn)
	requestLine, err := reader.ReadString('\n')
	if err != nil {
		return
	}
	// Drain the rest of the headers up to the blank line -- unused by
	// this handler, but a real request has to be read off the wire
	// before responding, the same as every other server here.
	for {
		line, err := reader.ReadString('\n')
		if err != nil || line == "\r\n" || line == "\n" {
			break
		}
	}

	fields := strings.Fields(requestLine)
	path := "/"
	if len(fields) > 1 {
		path = fields[1]
	}

	contentType := "text/plain"
	body := "Hello, world!"
	if path == "/json" {
		contentType = "application/json"
		body = `{"message":"Hello, world!"}`
	}

	fmt.Fprintf(conn, "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
		contentType, len(body), body)
}
