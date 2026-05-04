#!/usr/bin/env python3
"""DNS Tunnel Client v2 - Proper DNS encoding to pass deep packet inspection.
Runs on university PC. Creates local port forwarding through DNS-over-TCP.
Usage: python dns_tunnel_client.py [VPS_IP] [local_port]
Then:  ssh -D 1080 -N -p 2222 root@127.0.0.1
"""
import socket, struct, threading, time, sys, base64

VPS_IP = sys.argv[1] if len(sys.argv) > 1 else "165.22.174.8"
VPS_PORT = 53
LOCAL_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 2222
MAX_CHUNK = 110  # bytes of raw data per DNS query (~176 base32 chars in labels)

def encode_query_name(data):
    """Encode data into DNS query name as base32 labels."""
    if not data:
        encoded = b"poll"
    else:
        encoded = base64.b32encode(data).rstrip(b"=").lower()
    # Split into DNS labels (max 63 chars each)
    labels = b""
    for i in range(0, len(encoded), 63):
        chunk = encoded[i:i+63]
        labels += bytes([len(chunk)]) + chunk
    # Add suffix label "t" and null terminator
    labels += b"\x01t\x00"
    return labels

def make_query_frame(txn, data):
    """Build a valid DNS query with data encoded in the query name."""
    header = struct.pack("!HHHHHH", txn, 0x0100, 1, 0, 0, 0)
    qname = encode_query_name(data)
    qtype_qclass = struct.pack("!HH", 16, 1)  # TXT, IN
    dns_msg = header + qname + qtype_qclass
    return struct.pack("!H", len(dns_msg)) + dns_msg

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

def decode_txt_response(msg):
    """Extract data from DNS TXT response answer section."""
    if len(msg) < 12: return b""
    # Get answer count
    ancount = struct.unpack("!H", msg[6:8])[0]
    if ancount == 0: return b""
    # Skip header (12 bytes)
    i = 12
    # Skip question section (name + QTYPE + QCLASS)
    while i < len(msg) and msg[i] != 0:
        if msg[i] & 0xC0 == 0xC0:
            i += 2; break
        i += msg[i] + 1
    else:
        i += 1  # null terminator
    i += 4  # QTYPE + QCLASS
    # Parse answer section
    # Skip answer name (pointer or labels)
    if i >= len(msg): return b""
    if msg[i] & 0xC0 == 0xC0:
        i += 2
    else:
        while i < len(msg) and msg[i] != 0:
            i += msg[i] + 1
        i += 1
    # TYPE(2) + CLASS(2) + TTL(4) + RDLENGTH(2)
    if i + 10 > len(msg): return b""
    rdlen = struct.unpack("!H", msg[i+8:i+10])[0]
    i += 10
    # Read TXT strings
    data = b""
    end = min(i + rdlen, len(msg))
    while i < end:
        if i >= len(msg): break
        slen = msg[i]; i += 1
        if slen == 0: break
        data += msg[i:i+slen]; i += slen
    return data

def handle_local(lsock, addr):
    print("[+] Local connection: {}".format(addr))
    try:
        tsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tsock.settimeout(10)
        tsock.connect((VPS_IP, VPS_PORT))
        tsock.settimeout(None)
        print("[+] Tunnel connected to {}:{}".format(VPS_IP, VPS_PORT))
    except Exception as e:
        print("[-] Tunnel failed: {}".format(e))
        lsock.close(); return

    send_buf = bytearray()
    local_alive = [True]
    txn = [0]

    def local_reader():
        try:
            while True:
                d = lsock.recv(8192)
                if not d: local_alive[0] = False; break
                send_buf.extend(d)
        except: local_alive[0] = False

    threading.Thread(target=local_reader, daemon=True).start()

    try:
        while True:
            # Send a chunk of data (max MAX_CHUNK bytes per query)
            chunk = bytes(send_buf[:MAX_CHUNK])
            del send_buf[:len(chunk)]

            txn[0] = (txn[0] + 1) & 0xFFFF
            tsock.sendall(make_query_frame(txn[0], chunk))

            resp_msg = recv_frame(tsock)
            if resp_msg is None: break

            resp_data = decode_txt_response(resp_msg)
            if resp_data:
                lsock.sendall(resp_data)

            if not local_alive[0] and not send_buf:
                txn[0] = (txn[0] + 1) & 0xFFFF
                tsock.sendall(make_query_frame(txn[0], b""))
                final = recv_frame(tsock)
                if final:
                    fd = decode_txt_response(final)
                    if fd: lsock.sendall(fd)
                break

            if not chunk and not send_buf:
                time.sleep(0.05)
    except Exception as e:
        print("[-] Error: {}".format(e))
    finally:
        tsock.close(); lsock.close()
        print("[-] Closed: {}".format(addr))

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PORT))
    srv.listen(5)
    print("[*] DNS Tunnel Client v2 on 127.0.0.1:{}".format(LOCAL_PORT))
    print("[*] Tunneling to {}:{} via DNS-over-TCP".format(VPS_IP, VPS_PORT))
    print("[*] Use: ssh -D 1080 -N -p {} root@127.0.0.1".format(LOCAL_PORT))
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle_local, args=(c, a), daemon=True).start()

if __name__ == "__main__":
    main()
