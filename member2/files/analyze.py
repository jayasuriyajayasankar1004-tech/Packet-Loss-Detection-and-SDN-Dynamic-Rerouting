import argparse
import csv
import json
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p = argparse.ArgumentParser()
    p.add_argument('folder')
    p.add_argument('--window', type=float, default=0.25, help='window size (s)')
    p.add_argument('--thr', type=float, default=5.0,
                   help='loss %% considered recovered (2 consecutive windows)')
    a = p.parse_args()

    sent = {int(r['seq']): float(r['ts'])
            for r in csv.DictReader(open(os.path.join(a.folder, 'sent.csv')))}
    got = {int(r['seq']) for r in csv.DictReader(open(os.path.join(a.folder, 'recv.csv')))}
    ev = json.load(open(os.path.join(a.folder, 'events.json')))

    t0 = min(sent.values())
    fault = ev['fault_time'] - t0
    n = int((max(sent.values()) - t0) / a.window) + 1
    tot, lost = [0] * n, [0] * n
    for seq, ts in sent.items():
        w = int((ts - t0) / a.window)
        tot[w] += 1
        if seq not in got:
            lost[w] += 1
    loss = [100.0 * l / t if t else 0 for l, t in zip(lost, tot)]
    starts = [i * a.window for i in range(n)]

    fw = int(fault / a.window)
    seen_loss, rec = False, None
    for i in range(fw, n - 1):
        if loss[i] > a.thr:
            seen_loss = True
        if seen_loss and loss[i] <= a.thr and loss[i + 1] <= a.thr:
            rec = starts[i]
            break
    rw = int(rec / a.window) if rec is not None else n

    def pct(lo, hi):
        t, l = sum(tot[lo:hi]), sum(lost[lo:hi])
        return round(100.0 * l / t, 2) if t else None

    summary = {
        'packets_sent': len(sent),
        'packets_received': len(got & set(sent)),
        'loss_before_fault_%': pct(0, fw),
        'loss_during_outage_%': pct(fw, rw),
        'loss_after_recovery_%': pct(rw, n),
        'fault_at_s': round(fault, 3),
        'recovery_at_s': None if rec is None else round(rec, 3),
        'recovery_time_s': None if rec is None else round(rec - fault, 3),
        'window_s': a.window,
    }
    summary['mode'] = ev.get('mode')
    reroute = None
    if 'reroute_time' in ev:
        reroute = ev['reroute_time'] - t0
        # Manual mode: fixed operator delay. Controller mode: detection + reroute time.
        summary['reroute_delay_s'] = round(ev['reroute_time'] - ev['fault_time'], 3)
        if ev.get('reroute_reason'):
            summary['reroute_reason'] = ev['reroute_reason']

    with open(os.path.join(a.folder, 'summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))

    plt.figure(figsize=(9, 4))
    plt.step(starts, loss, where='post', label='packet loss per window')
    plt.axvline(fault, color='red', ls='--', label='fault injected')
    if reroute is not None:
        plt.axvline(reroute, color='orange', ls=':', label='path switched')
    if rec is not None:
        plt.axvline(rec, color='green', ls='--', label='traffic recovered')
    plt.xlabel('time (s)')
    plt.ylabel('packet loss (%)')
    plt.title('Packet loss before / during / after recovery')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(a.folder, 'loss_plot.png'), dpi=150)


if __name__ == '__main__':
    main()
