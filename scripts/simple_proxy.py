#!/usr/bin/env python3
"""Simple HTTP/HTTPS logging proxy for mitm interception."""
import socket, threading, select, sys

LISTEN = ("0.0.0.0", 9999)

def handle_client(client_sock, addr):
    try:
        req = client_sock.recv(4096)
        if not req:
            client_sock.close(); return

        first_line = req.split(b"\r\n")[0].decode(errors="replace")
        print(f"[REQ] {addr} {first_line}")

        # Extract host:port from CONNECT
        if req.startswith(b"CONNECT"):
            # HTTPS tunnel
            parts = first_line.split()
            host_port = parts[1] if len(parts) > 1 else ""
            host, _, port_str = host_port.partition(":")
            port = int(port_str) if port_str else 443

            client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

            try:
                remote = socket.create_connection((host, port), timeout=10)
            except Exception as e:
                print(f"[ERR] CONNECT {host}:{port} failed: {e}")
                client_sock.close(); return

            # Bidirectional relay
            sockets = [client_sock, remote]
            try:
                while True:
                    r, _, _ = select.select(sockets, [], [], 30)
                    if not r: break
                    for s in r:
                        data = s.recv(65536)
                        if not data:
                            raise Exception("closed")
                        other = remote if s is client_sock else client_sock
                        other.sendall(data)
                        # Log data direction
                        direction = "->" if s is client_sock else "<-"
                        size = len(data)
                        if size < 5000:
                            try:
                                text = data[:300].decode("utf-8", errors="replace")
                                print(f"[DATA {direction}] {size}b: {text[:200]}")
                            except:
                                print(f"[DATA {direction}] {size}b (binary)")
            except:
                pass
            finally:
                remote.close()
        else:
            # Plain HTTP - just forward
            try:
                import urllib.request as ur
                # Not a proper proxy, just close
                pass
            except:
                pass
    except Exception as e:
        print(f"[ERR] {addr}: {e}")
    finally:
        client_sock.close()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(LISTEN)
    server.listen(10)
    print(f"Simple proxy listening on {LISTEN[0]}:{LISTEN[1]}")
    print("Press Ctrl+C to stop")

    while True:
        client_sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True).start()

if __name__ == "__main__":
    main()
