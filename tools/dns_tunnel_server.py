#!/usr/bin/env python3
"""DNS Tunnel Server v2 - Proper DNS encoding to pass deep packet inspection.
Runs on VPS. Listens TCP:53. Forwards to local SSH.
Usage: sudo python3 dns_tunnel_server.py
"""
import socket, struct, threading, time, sys, base64

LISTEN_PORT = 53
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 22

def recv_frame(sock):
    """Read one DNS-over-TCP frame."""
    hdr = b""
    while len(hdr) < 2:
        d = sock.recv(2 - len(hdr))
        if not d: return None
        hdr += d
    mlen = struct.unpack("!H", hdr)[0]
    if mlen == 0: return None
    msg = b""
    while len(msg) < mlen:
        d = sock.recv(mlen - len(msg))
        if not d: return None
        msg += d
    return msg

def decode_query_name(msg):
    """Decode base32-encoded data from DNS query name labels."""
    if len(msg) < 12: return 0, b""
    txn = struct.unpack("!H", msg[0:2])[0]
    i = 12
    labels = []
    while i < len(msg) and msg[i] != 0:
        llen = msg[i]; i += 1
        if i + llen > len(msg): break
        labels.append(msg[i:i+llen]); i += llen
    # Remove suffix label "t"
    if labels and labels[-1] == b"t":
        labels = labels[:-1]
    encoded = b"".join(labels)
    if encoded == b"poll" or not encoded:
        return txn, b""
    # Base32 decode
    pad = (8 - len(encoded) % 8) % 8
    encoded = encoded.upper() + b"=" * pad
    try:
        return txn, base64.b32decode(encoded)
    except:
        return txn, b""

def make_response(txn, query_msg, data):
    """Build a valid DNS TXT response with data encoded in TXT RDATA."""
    # Response header
    header = struct.pack("!HHHHHH", txn, 0x8180, 1, 1, 0, 0)
    # Echo the question section from the query
    # Find question section boundaries
    i = 12
    while i < len(query_msg) and query_msg[i] != 0:
        i += query_msg[i] + 1
    i += 5  # null + QTYPE(2) + QCLASS(2)
    question = query_msg[12:i] if i <= len(query_msg) else b"\x01t\x00\x00\x10\x00\x01"
    # Answer section: pointer to question name + TXT record
    # Use a name pointer to offset 12 (start of question name)
    answer_name = b"\xc0\x0c"
    # Build TXT RDATA
    rdata = b""
    if data:
        remaining = data
        while remaining:
            chunk = remaining[:255]
            remaining = remaining[255:]
            rdata += bytes([len(chunk)]) + chunk
    else:
        rdata = b"\x00"  # single empty TXT string
    answer = answer_name + struct.pack("!HHIH", 16, 1, 0, len(rdata)) + rdata
    dns_msg = header + question + answer
    return struct.pack("!H", len(dns_msg)) + dns_msg

def handle_client(csock, addr):
    print("[+] Client: {}".format(addr))
    try:
        tsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tsock.settimeout(5)
        tsock.connect((TARGET_HOST, TARGET_PORT))
        tsock.settimeout(None)
    except Exception as e:
        print("[-] Target connect failed: {}".format(e))
        csock.close(); return

    from_target = bytearray()
    alive = [True]

    def target_reader():
        try:
            while alive[0]:
                d = tsock.recv(16384)
                if not d: alive[0] = False; break
                from_target.extend(d)
        except: alive[0] = False

    threading.Thread(target=target_reader, daemon=True).start()

    try:
        while True:
            msg = recv_frame(csock)
            if msg is None: break

            txn, payload = decode_query_name(msg)
            if payload:
                tsock.sendall(payload)

            time.sleep(0.02)

            # Send back available target data
            resp = bytes(from_target[:16384])
            del from_target[:len(resp)]
            csock.sendall(make_response(txn, msg, resp))

            if not alive[0] and not from_target:
                break
    except Exception as e:
        print("[-] Error: {}".format(e))
    finally:
        alive[0] = False
        csock.close(); tsock.close()
        print("[-] Disconnected: {}".format(addr))

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LISTEN_PORT))
    srv.listen(5)
    print("[*] DNS Tunnel Server v2 on port {} -> {}:{}".format(LISTEN_PORT, TARGET_HOST, TARGET_PORT))
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle_client, args=(c, a), daemon=True).start()

if __name__ == "__main__":
    main()
