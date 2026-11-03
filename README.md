# Packet Loss Detection and SDN Dynamic Rerouting

This bundle contains a four-switch Mininet topology, a UDP sender/receiver with sequence tracking, ACK/NACK and retransmission, and a Ryu OpenFlow 1.3 controller. The controller moves traffic from the primary route (`h1-s1-s2-s4-h2`) to the alternate route (`h1-s1-s3-s4-h2`) when it detects loss.

```
        +---- s2 ----+          primary:   h1-s1-s2-s4-h2
h1 -- s1             s4 -- h2
        +---- s3 ----+          alternate: h1-s1-s3-s4-h2
```

| Member | Files |
|---|---|
| Member 1: UDP + loss detection | `member1/sender.py`, `member1/receiver.py` |
| Member 2: Mininet + testing | `member2/files/run_experiment.py`, `analyze.py`, `compare.py`, `probe.py` |
| Member 3: SDN controller | `member3/controller.py` |

## How the controller decides to reroute

Any one of these triggers the switch to the alternate path:

1. **Loss alerts:** the receiver sends a `LOSS_ALERT` for every sequence gap. 3 alerts within 5 s trigger a reroute.
2. **Path monitoring:** every 1 s the controller polls OpenFlow port statistics. It compares packets entering the primary path (s2 port 1) with packets leaving it (s4 port 2). Two windows in a row with 15% or more estimated loss trigger a reroute.
3. **Link down:** an OpenFlow port-down event on the primary path.

A 5 s cooldown prevents the controller from flapping between paths. Each reroute is logged to `/tmp/sdn_controller_events.jsonl` (you can change this with the `SDN_EVENT_LOG` environment variable). `run_experiment.py` reads that log to record the exact time the path was switched.

## Requirements

- Ubuntu Linux (a Linux VM is fine)
- Mininet, Open vSwitch, `iproute2`
- Python 3 with `matplotlib` for analysis
- Ryu in its own Python environment (the commands below use a pyenv environment named `sdn-controller`)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iproute2 python3-pip python3-matplotlib
```

If `ryu-manager` fails to start on Python 3.10 or newer with an eventlet error, run `pip install eventlet==0.30.2` inside the Ryu environment.

## 1. Controller-mode experiment

Terminal 1, from the project root:

```bash
cd ~/Downloads/packet_loss_project_fixed
sudo mn -c                      # cleanup first (this also kills ryu-manager)
pyenv activate sdn-controller
ryu-manager member3/controller.py
```

Terminal 2, from the project root:

```bash
cd ~/Downloads/packet_loss_project_fixed
sudo /usr/bin/python3 member2/files/run_experiment.py \
  --mode controller --fault loss --loss 30 \
  --duration 20 --fault-at 6 --rate 100 \
  --out results/controller_loss30
```

The controller log should show all four switches connected, then `PRIMARY path active`, `Path monitor: ... est_loss=...`, and `ALTERNATE path active` after the fault.

## 2. Manual baseline (no controller)

```bash
sudo mn -c
sudo /usr/bin/python3 member2/files/run_experiment.py \
  --mode manual --fault loss --loss 30 --duration 20 --fault-at 6 \
  --rate 100 --reroute-delay 1 --out results/manual_loss30
```

In manual mode the static flows are switched to the alternate path `--reroute-delay` seconds after the fault.

To test a link failure instead of random loss, use `--fault down`.

## 3. Analyse and compare

```bash
python3 member2/files/analyze.py results/controller_loss30
python3 member2/files/analyze.py results/manual_loss30
python3 member2/files/compare.py results/manual_loss30 results/controller_loss30
```

`analyze.py` writes `summary.json` and `loss_plot.png` for each run. It reports loss before the fault, during the outage and after recovery, plus the path-switch delay and recovery time. `compare.py` prints a side-by-side table and saves `results/comparison.png`.

## Check the forwarding rules

Run with `--cli`, then at the `mininet>` prompt:

```text
sh ovs-ofctl -O OpenFlow13 dump-flows s1
sh ovs-ofctl -O OpenFlow13 dump-flows s2
sh ovs-ofctl -O OpenFlow13 dump-flows s3
sh ovs-ofctl -O OpenFlow13 dump-flows s4
```

Forwarding rules have `priority=100`; the `priority=0` rule is the table-miss rule (send to controller).

## Testing the UDP application on its own (no Mininet)

```bash
python3 member1/receiver.py --port 5000 --drop-once 3 7 --loss 0.2 --idle 2 &
python3 member1/sender.py --port 5000 --count 200 --interval 0.005
```

The receiver reports first-attempt loss of about 20% and a final loss of 0%, because every missing packet is recovered through NACK or timeout retransmission.

## Output files

Each run writes `sent.csv`, `recv.csv`, `sender_out.txt`, `receiver_out.txt`, `sender_stats.json`, `receiver_stats.json`, `received.txt` and `events.json` into the results folder. `analyze.py` adds `summary.json` and `loss_plot.png`. Report only results you have reproduced yourselves.

## Cleanup

```bash
sudo mn -c
```

This also stops Ryu, so start it again before the next controller-mode run.
