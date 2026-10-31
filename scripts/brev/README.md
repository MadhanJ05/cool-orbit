# Running heavy simulations on Brev (with the GB10 still in charge)

The GB10 stays the home base: the board, the agents and the demo live there. A Brev GPU is extra muscle.
A second Simulator agent on the GB10 sends **long** jobs to the Brev instance while the first agent keeps the
short ones; they coordinate only through claims on the board.

**Money rule:** creating, starting, stopping and deleting instances are **manual steps that Madhan approves**,
with the GPU type and hourly price shown. The code never does any of them. An instance bills until it is
stopped or deleted.

## 1. Pick and create (manual, approved)
```bash
~/.local/bin/brev search gpu --gpu-name H100 --sort price | head      # check price + 'S' (stoppable)
~/.local/bin/brev create coolorbit-h100 --type <TYPE from the search>  # starts billing
```

## 2. Prepare it (~10 to 15 min; installs the env, runs the smoke test with and without torch.compile)
```bash
~/.local/bin/brev exec coolorbit-h100 @scripts/brev/setup_remote.sh
~/.local/bin/brev copy coolorbit-h100:/root/cool-orbit/runs/smoke_remote/ runs/smoke_remote_h100/   # adjust home dir if not /root
```
The smoke results give the real H100 vs GB10 speed ratio and whether `torch.compile` helps.

## 3. Let a second Simulator agent use it
Commit and push first: the instance runs the exact local commit (by hash), so results are reproducible.
```bash
# GB10 agent: jobs under 30 GB10-minutes
.venv/bin/python -m coolorbit.sim.simulator_agent --board boards/<id> --agent simulator_gb10 --max-minutes 30
# Brev agent: jobs of 30+ GB10-minutes, run on the H100, results copied back and posted here
.venv/bin/python -m coolorbit.sim.simulator_agent --board boards/<id> --agent simulator_brev \
    --brev-instance coolorbit-h100 --min-minutes 30
```
Every Brev result records `method.remote = {instance, commit}`.

## 4. Stop or delete when done (manual, approved)
```bash
~/.local/bin/brev stop coolorbit-h100      # stoppable types: GPU billing stops, disk kept
~/.local/bin/brev delete coolorbit-h100    # everything gone; copy results back first
~/.local/bin/brev ls                       # confirm nothing is left running
```
