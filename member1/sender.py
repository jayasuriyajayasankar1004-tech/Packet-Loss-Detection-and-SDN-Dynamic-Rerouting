import argparse
import json
import select
import socket
import time
import uuid

MAX_PACKETS = 100000  # must match receiver.py


def main():
    parser = argparse.ArgumentParser(description="UDP sender with ACK/NACK recovery")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--stats", default="sender_stats.json")
    parser.add_argument("--sent-log", default=None)
    args = parser.parse_args()
    if not (1 <= args.count <= MAX_PACKETS and 1 <= args.port <= 65535):
        parser.error(f"count must be 1 to {MAX_PACKETS} and port must be 1 to 65535")
    if args.interval < 0 or args.timeout <= 0 or args.retries < 0:
        parser.error("interval/retries cannot be negative; timeout must be positive")

    target = (socket.gethostbyname(args.host), args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    run = uuid.uuid4().hex[:12]
    attempts = {}
    last_sent = {}
    acknowledged = set()
    failed = set()
    nack_count = 0
    timeout_count = 0
    udp_send_errors = 0
    duplicate_acks = 0     # ACKs for a packet that was already acknowledged
    start = time.monotonic()
    log = open(args.sent_log, "w") if args.sent_log else None
    if log:
        log.write("seq,ts\n")

    def send_packet(seq, reason):
        nonlocal udp_send_errors
        attempts[seq] = attempts.get(seq, 0) + 1
        packet = {"type": "DATA", "run": run, "seq": seq,
                  "total": args.count, "attempt": attempts[seq],
                  "data": "Packet " + str(seq)}
        try:
            sock.sendto(json.dumps(packet).encode(), target)
        except OSError as exc:
            # Count the failure; the timeout path retries it like a lost packet.
            udp_send_errors += 1
            print(f"UDP ERROR: send {seq} failed ({exc})", flush=True)
        if log and attempts[seq] == 1:
            log.write(f"{seq},{time.time():.6f}\n")
            log.flush()
        last_sent[seq] = time.monotonic()
        print(f"SEND {seq} | attempt {attempts[seq]} | {reason}", flush=True)

    next_seq = 1
    next_send = start
    while len(acknowledged) + len(failed) < args.count:
        now = time.monotonic()
        if next_seq <= args.count and now >= next_send:
            send_packet(next_seq, "original")
            next_seq += 1
            # Schedule from the start time so the real rate matches --interval.
            next_send = start + (next_seq - 1) * args.interval

        ready, _, _ = select.select([sock], [], [], 0.01)
        if ready:
            raw, address = sock.recvfrom(4096)
            try:
                reply = json.loads(raw.decode())
                if not isinstance(reply, dict):
                    continue
                seq = reply.get("seq")
                if (address != target or reply.get("run") != run
                        or type(seq) is not int or seq not in attempts):
                    continue
                if reply.get("type") == "ACK":
                    if seq in acknowledged:
                        duplicate_acks += 1
                    acknowledged.add(seq)
                    failed.discard(seq)
                    print(f"ACK {seq}", flush=True)
                elif reply.get("type") == "NACK" and seq not in acknowledged:
                    nack_count += 1
                    print(f"NACK {seq}", flush=True)
                    if seq not in failed and attempts[seq] <= args.retries:
                        send_packet(seq, "NACK retransmission")
            except (ValueError, UnicodeError):
                continue

        now = time.monotonic()
        for seq in list(last_sent):
            if seq in acknowledged or seq in failed:
                continue
            if now - last_sent[seq] >= args.timeout:
                timeout_count += 1
                if attempts[seq] <= args.retries:
                    send_packet(seq, "timeout retransmission")
                else:
                    failed.add(seq)
                    print(f"UNCONFIRMED {seq}: retry limit reached", flush=True)

    stats = {"run": run, "packets": args.count,
             "transmissions": sum(attempts.values()),
             "retransmissions": sum(attempts.values()) - args.count,
             "acknowledged": len(acknowledged),
             "unconfirmed": sorted(failed), "nacks_received": nack_count,
             "timeouts": timeout_count,
             "udp_send_errors": udp_send_errors,
             "duplicate_acks": duplicate_acks,
             "packets_3_or_more_attempts": sum(1 for n in attempts.values() if n >= 3),
             "max_attempts": max(attempts.values(), default=0),
             "retry_limit": args.retries,
             "elapsed_seconds": round(time.monotonic() - start, 4),
             "success": len(acknowledged) == args.count}
    sock.close()
    if log:
        log.close()
    with open(args.stats, "w") as file:
        json.dump(stats, file, indent=2)
    print("\nSender statistics:\n" + json.dumps(stats, indent=2))
    return 0 if stats["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
