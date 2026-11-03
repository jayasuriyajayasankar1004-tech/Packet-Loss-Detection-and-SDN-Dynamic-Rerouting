import argparse
import json
import random
import socket
import time

MAX_PACKETS = 100000  # must match sender.py


def main():
    parser = argparse.ArgumentParser(description="UDP receiver with loss detection")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--drop-once", type=int, nargs="*", default=[])
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--idle", type=float, default=10.0)
    parser.add_argument("--stats", default="receiver_stats.json")
    parser.add_argument("--output", default="received.txt")
    parser.add_argument("--alert-host", default=None)
    parser.add_argument("--alert-port", type=int, default=9999)
    parser.add_argument("--recv-log", default=None)
    args = parser.parse_args()
    if not 0 <= args.loss <= 1 or args.idle <= 0:
        parser.error("loss must be 0 to 1 and idle must be positive")
    if not (1 <= args.port <= 65535 and 1 <= args.alert_port <= 65535):
        parser.error("ports must be 1 to 65535")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.2)
    rlog = open(args.recv_log, "w") if args.recv_log else None
    if rlog:
        rlog.write("seq,recv_ts\n")
    alert_target = None
    if args.alert_host:
        alert_target = (socket.gethostbyname(args.alert_host), args.alert_port)
    received = {}
    originals = set()
    dropped_once = set()
    detected = {}
    recovery_ms = {}
    peer = None
    run = None
    total = 0
    highest = 0
    datagrams = drops = duplicates = acks = nacks = 0
    retransmissions = 0
    alerts_sent = alert_errors = 0
    malformed = 0          # datagrams that were not valid DATA packets
    foreign = 0            # datagrams from another sender/run
    udp_send_errors = 0    # ACK/NACK sendto() failures
    ack_count = {}         # seq -> how many ACKs we sent for it
    start = last_activity = completed_at = None
    print(f"Listening on {args.host}:{args.port}", flush=True)

    def reply(kind, seq):
        nonlocal udp_send_errors
        message = {"type": kind, "run": run, "seq": seq}
        try:
            sock.sendto(json.dumps(message).encode(), peer)
        except OSError as exc:
            udp_send_errors += 1
            print(f"UDP ERROR: {kind} {seq} not sent ({exc})", flush=True)
            return False
        if kind == "ACK":
            ack_count[seq] = ack_count.get(seq, 0) + 1
        return True

    while True:
        try:
            raw, address = sock.recvfrom(4096)
        except socket.timeout:
            if last_activity is not None and time.monotonic() - last_activity >= args.idle:
                break
            continue
        try:
            packet = json.loads(raw.decode())
            if not isinstance(packet, dict) or packet.get("type") != "DATA":
                malformed += 1
                continue
            seq, count, attempt = packet["seq"], packet["total"], packet["attempt"]
            if (type(seq) is not int or type(count) is not int
                    or type(attempt) is not int or not 1 <= seq <= count <= MAX_PACKETS
                    or attempt < 1 or not isinstance(packet.get("run"), str)
                    or not isinstance(packet.get("data"), str)):
                malformed += 1
                continue
        except (ValueError, KeyError, UnicodeError):
            malformed += 1
            continue
        if peer is None:
            peer, run, total = address, packet["run"], count
            start = time.monotonic()
        if address != peer or packet["run"] != run or count != total:
            foreign += 1
            continue
        last_activity = time.monotonic()
        datagrams += 1
        if attempt > 1:
            retransmissions += 1
        if seq in args.drop_once and seq not in dropped_once:
            dropped_once.add(seq)
            drops += 1
            print(f"DROP {seq} (demo loss)", flush=True)
            continue
        if random.random() < args.loss:
            drops += 1
            print(f"DROP {seq} (random demo loss)", flush=True)
            continue

        if attempt == 1:
            originals.add(seq)
        if seq in received:
            duplicates += 1
            print(f"DUPLICATE {seq}: sending ACK again", flush=True)
        else:
            received[seq] = packet["data"]
            print(f"RECEIVED {seq}", flush=True)
            if rlog and attempt == 1:
                rlog.write(f"{seq},{time.time():.6f}\n")
                rlog.flush()
            if seq in detected:
                recovery_ms[str(seq)] = round((time.monotonic() - detected[seq]) * 1000, 3)
        reply("ACK", seq)
        acks += 1

        if seq > highest:
            for missing in range(highest + 1, seq):
                if missing not in received and missing not in detected:
                    detected[missing] = time.monotonic()
                    reply("NACK", missing)
                    nacks += 1
                    print(f"MISSING {missing}: NACK sent", flush=True)
                    if alert_target:
                        event = {"type": "LOSS_ALERT", "run": run, "seq": missing,
                                 "reason": "sequence_gap", "timestamp": time.time()}
                        try:
                            sock.sendto(json.dumps(event).encode(), alert_target)
                            alerts_sent += 1
                        except OSError as exc:
                            # Alert path unreachable (e.g. no controller link):
                            # keep receiving data instead of crashing.
                            alert_errors += 1
                            if alert_errors == 1:
                                print(f"WARNING: loss alert not sent ({exc})", flush=True)
            highest = seq
        if len(received) == total and completed_at is None:
            completed_at = time.monotonic()
            print(f"All {total} packets received. Waiting for duplicate retries...", flush=True)

    missing = sorted(set(range(1, total + 1)) - received.keys())
    stats = {"run": run, "expected": total, "unique_received": len(received),
             "data_datagrams_seen": datagrams, "simulated_drops": drops,
             "originals_received": len(originals),
             "first_attempt_loss_percent": round((total - len(originals)) * 100 / total, 2),
             "final_loss_percent": round(len(missing) * 100 / total, 2),
             "missing": missing, "duplicates": duplicates,
             "retransmission_datagrams_seen": retransmissions,
             "acks_sent": acks, "nacks_sent": nacks,
             "loss_alerts_sent": alerts_sent, "loss_alert_errors": alert_errors,
             "malformed_datagrams": malformed, "foreign_datagrams": foreign,
             "udp_send_errors": udp_send_errors,
             "seqs_acked_3_or_more": sorted(q for q, n in ack_count.items() if n >= 3),
             "max_acks_for_one_seq": max(ack_count.values(), default=0),
             "gap_sequences": sorted(detected), "gap_recovery_ms": recovery_ms,
             "delivery_seconds": round(completed_at - start, 4) if completed_at else None,
             "success": not missing}
    sock.close()
    if rlog:
        rlog.close()
    with open(args.output, "w") as file:
        for seq in sorted(received):
            file.write(received[seq] + "\n")
    with open(args.stats, "w") as file:
        json.dump(stats, file, indent=2)
    print("\nReceiver statistics:\n" + json.dumps(stats, indent=2))
    return 0 if stats["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
