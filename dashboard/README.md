# SDN Live Dashboard (frontend + backend)

- **Backend:** `dashboard/server.py`. A small Python web server (standard library only).
  It reads the live system (flow rules with `ovs-ofctl`, link state, the Ryu log,
  your `results/` folder) and can break/restore the s2-s4 link, ping h1 -> h2 and
  start experiments.
- **Frontend:** `dashboard/index.html`. Opened in Firefox at http://localhost:8080.
  It polls the backend every second.

The server listens on 127.0.0.1 only, so nothing outside the VM can reach it.

## Run it (3 terminals + Firefox)

Every terminal starts in the project folder:

    cd ~/Downloads/packet_loss_project_fixedb/packet_loss_project_fixed

**Terminal 1: Ryu controller, saving its log for the dashboard**

    sudo mn -c
    pyenv activate sdn-controller
    ryu-manager member3/controller.py 2>&1 | tee /tmp/ryu_controller.txt

**Terminal 2: dashboard backend**

    sudo /usr/bin/python3 dashboard/server.py

Leave it running. It prints: SDN dashboard running at http://localhost:8080

**Firefox (inside the VM):** open http://localhost:8080

**Terminal 3: the network, for the live demo**

    sudo /usr/bin/python3 member2/files/run_experiment.py --mode controller --cli

Wait for the `mininet>` prompt. The dashboard now shows Network: up,
Active path: primary, and the flow rules on s1-s4.

## Live demo from the dashboard

1. Click **Ping h1 -> h2**. 3 packets received.
2. Click **Break link s2-s4**. The Ryu log shows `Link down detected` and
   `ALTERNATE path active`; the topology switches to s1-s3-s4; the flow rules
   on s1 change from out 2 to out 3.
3. Click **Ping h1 -> h2** again. It still works through s3.
4. Type `exit` at the `mininet>` prompt in Terminal 3 when done.

(You can still type the same commands at the `mininet>` prompt; the dashboard
updates either way.)

## Error panels

- **Errors detected and how each was handled** (for the selected run, or the newest):
  packet loss (sequence gaps -> NACK), ACK timeouts, packets needing 3+ attempts,
  3+ ACKs for one packet, duplicate data, UDP send failures, loss alerts that could
  not reach the controller, bad/foreign datagrams, packets unconfirmed after the
  retry limit, link down (port status), port-statistics loss and loss-alert reroutes.
- **Switch port errors (live)**: every port's state (up / link down) and its RX/TX
  drop and error counters from `ovs-ofctl dump-ports`, plus the s2->s4 loss in the
  last second (the same check the controller's port-stats trigger makes).
- **Live error log**: warnings and errors from the Ryu log and the running experiment.

Older runs (made before this version) show "n/a" for the new counters; run the
experiment again to fill them.

## Experiments from the dashboard

1. Close the --cli demo first (`exit` in Terminal 3). Only one network can run at a time.
2. Controller mode: keep Ryu running in Terminal 1.
3. Choose mode, fault, loss %, duration and click **Run experiment**.
   Output streams on the page; `analyze.py` runs automatically at the end.
4. The run appears in **Results**. Click it to see the evidence for each of the
   8 requirements and its loss graph.
5. After one manual and one controller run, the **Manual vs SDN** table compares them.

Do not run `sudo mn -c` while Ryu or an experiment is running: it stops both.
After `sudo mn -c`, restart Ryu in Terminal 1 (with the `tee` part).

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says the server is not reachable | Terminal 2 stopped. Start `sudo /usr/bin/python3 dashboard/server.py` again. |
| "Address already in use" | An old server is still running: `sudo pkill -f dashboard/server.py`, then start again. Or use another port: `sudo DASH_PORT=8081 /usr/bin/python3 dashboard/server.py` |
| Ryu log panel says "No Ryu log file found" | Start Ryu with `2>&1 \| tee /tmp/ryu_controller.txt` |
| Flow rules say "could not read rules" | The server was not started with `sudo` |
| "Ryu is not running" when starting a controller experiment | Start Ryu in Terminal 1 first |
| "A Mininet network is already running" | Type `exit` at the `mininet>` prompt, or run `sudo mn -c` (then restart Ryu) |
