import argparse
import socket
import struct
import time

FMT = '!Id'


def send(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / a.rate
    start = time.time()
    seq = 0
    with open(a.log, 'w') as f:
        f.write('seq,ts\n')
        while time.time() - start < a.duration:
            seq += 1
            ts = time.time()
            s.sendto(struct.pack(FMT, seq, ts), (a.dst, a.port))
            f.write('%d,%.6f\n' % (seq, ts))
            delay = start + seq * interval - time.time()
            if delay > 0:
                time.sleep(delay)


def recv(a):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', a.port))
    s.settimeout(0.5)
    end = time.time() + a.duration
    with open(a.log, 'w') as f:
        f.write('seq,send_ts,recv_ts\n')
        while time.time() < end:
            try:
                data, _ = s.recvfrom(64)
            except socket.timeout:
                continue
            seq, ts = struct.unpack(FMT, data[:12])
            f.write('%d,%.6f,%.6f\n' % (seq, ts, time.time()))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('role', choices=['send', 'recv'])
    p.add_argument('--dst', default='10.0.0.2')
    p.add_argument('--port', type=int, default=5001)
    p.add_argument('--rate', type=int, default=100)
    p.add_argument('--duration', type=float, default=20)
    p.add_argument('--log', required=True)
    a = p.parse_args()
    send(a) if a.role == 'send' else recv(a)
