"""Compare summary.json from several runs (e.g. manual vs controller).

Usage:
  python3 member2/files/compare.py results/manual_loss30 results/controller_loss30
Run analyze.py on each folder first.
"""
import json
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

FIELDS = [
    ('mode', 'Mode'),
    ('loss_before_fault_%', 'Loss before fault (%)'),
    ('loss_during_outage_%', 'Loss during outage (%)'),
    ('loss_after_recovery_%', 'Loss after recovery (%)'),
    ('reroute_delay_s', 'Path switch after fault (s)'),
    ('recovery_time_s', 'Recovery time (s)'),
    ('packets_sent', 'Packets sent'),
    ('packets_received', 'Received first try'),
]


def main(folders):
    if not folders:
        sys.exit(__doc__)
    runs = []
    for folder in folders:
        with open(os.path.join(folder, 'summary.json'), encoding='utf-8') as file:
            runs.append((os.path.basename(os.path.normpath(folder)), json.load(file)))

    width = max(len(label) for _, label in FIELDS) + 2
    print(''.ljust(width) + ''.join(name.ljust(22) for name, _ in runs))
    for key, label in FIELDS:
        row = ''.join(str(summary.get(key, '-')).ljust(22) for _, summary in runs)
        print(label.ljust(width) + row)

    phases = [('loss_before_fault_%', 'before'), ('loss_during_outage_%', 'during'),
              ('loss_after_recovery_%', 'after')]
    bar = 0.8 / len(runs)
    plt.figure(figsize=(8, 4))
    for i, (name, summary) in enumerate(runs):
        values = [summary.get(key) or 0 for key, _ in phases]
        plt.bar([p + i * bar for p in range(len(phases))], values, width=bar, label=name)
    plt.xticks([p + bar * (len(runs) - 1) / 2 for p in range(len(phases))],
               [label for _, label in phases])
    plt.ylabel('packet loss (%)')
    plt.title('Packet loss before / during / after recovery')
    plt.legend()
    plt.tight_layout()
    out = os.path.join(os.path.dirname(os.path.normpath(folders[0])) or '.', 'comparison.png')
    plt.savefig(out, dpi=150)
    print('\nSaved', out)


if __name__ == '__main__':
    main(sys.argv[1:])
