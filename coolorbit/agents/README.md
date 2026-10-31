# Agents

Independent processes that coordinate only through the results board (no boss, no direct messages).
Each one polls the board, claims needs it can serve, posts, and logs every decision to
`<board>/logs/<agent>.jsonl`.

| Agent | Module | Owns | Does |
|---|---|---|---|
| Scouts (`scout_metals`, `scout_paraffin`, `scout_fatty_acid`, `scout_sugar_alcohol`, `scout_salt_hydrate`, `scout_plastic_crystal`) | `scout.py`, seeds in `scout_seeds.json` | one material family + its candidate budget | posts candidates, routes each to an engine that can handle it (ALCHEMI for inorganic crystals, `classical_md` for molecular organics), requests the latent heat once a usable melting point exists |
| Rule referee (`referee_rules`) | `referee_rules.py`, thresholds in `audit_rules.json` | the trap catalog | audits every `property_result` once: single-phase heating, Langevin transport, out-of-domain model, H timestep, size/length, missing metadata, blocking producer flags |
| Premise checker (`referee_premise`) | `premise.py` (reuses `audit_rules.json` lists) | the premise question | before anything runs, checks every eval task (from its text only) and every simulation request: is the property measurable with this method, model, size and timescale? Rules first (pristine-crystal diffusion, single-phase heating, Langevin transport, model domain; recognises well-posed controls), Claude Opus 5.5 (structured JSON) only when rules are undecided. Advisory: never claims; carries a `fail` onto any result produced from a failed request so it is flagged |
| Librarian (`librarian`) | `librarian.py`, data in `data/calibration/` | the measured-value dataset | answers `reference_value` needs (by name or alias); says "not found" instead of guessing |
| Ranker (`ranker`) | `ranker.py`, config in `ranker_config.json` | the shortlist | ranks trusted results only, in the operating window, by latent heat per kg |
| FakeSimulator (`simulator_fake`) | `fake_simulator.py` | nothing real | the real Simulator agent with a synthetic runner, `dev-*` boards only, every result marked SYNTHETIC; optional traps |

The real Simulator (`coolorbit/sim/simulator_agent.py`) runs on the GB10; requests follow
`docs/simulation-requests.md`.

```bash
# laptop: whole pipeline on a dev board, with a silent superheating trap the referee must catch
python -m coolorbit.agents --board boards/dev-<name> --fake-sim --trap Al=silent_heating
python -m coolorbit.board show --board boards/dev-<name> -v

# production: one process per agent
python -m coolorbit.agents.scout --board boards/<id> --scout scout_metals
python -m coolorbit.agents.referee_rules --board boards/<id>
python -m coolorbit.agents.librarian --board boards/<id> --serve
python -m coolorbit.agents.ranker --board boards/<id>
```

Placeholders awaiting team decisions (each file says so, and outputs carry a warning):
`audit_rules.json` thresholds (chemistry lead), `ranker_config.json` operating window (team),
`scout_plastic_crystal` disabled until solid-solid PCMs are approved.

Premise checks without the board (e.g. for the eval harness):
`python -m coolorbit.agents.premise --task '{"material": "LiF", "property": "self_diffusion", "description": "pristine crystalline LiF"}'`
or `--tasks file.json`. The LLM layer needs `pip install anthropic` and the team's shared keys in `.env`
(`ANTHROPIC_API_KEY_PRIMARY` / `_BACKUP`, handled by `coolorbit/llm_keys.py`: primary until it runs out, then
backup for every agent). Without a usable key, undecided tasks are reported `inconclusive`, never passed.

Not done yet: the LLM method-audit referee (needs the API key), periodic structures in `data/structures/` for gallium, indium, salt hydrates and the organics.
