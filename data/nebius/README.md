# Nebius classical-MD input bundles (T6)

**Status: input files only. Nothing has been submitted. Ask Madhan before submitting any Nebius job** (billing
unclear; the team key is private and never goes in git).

## Metal-alloy track first: Ga, In, Sn, Bi (`metals/`)

The independent check for the GB10's MACE melting points and latent heats of the low-melting metals: classical
MD with published potentials from the NIST Interatomic Potentials Repository, run with LAMMPS. A
fundamentally different method from an MLIP, same protocol (two-phase coexistence for the melting point,
solid-vs-liquid enthalpy at the melting point for the latent heat).

| Element | Potential (NIST IPR implementation) | Citation | Coexistence box | Bracket (K) |
|---|---|---|---|---|
| Ga | `2009--Do-E-C--Ga-In--LAMMPS--ipr1` (2NN MEAM, Ga part of a Ga-In fit) | Do, Shin, Lee, J. Phys.: Condens. Matter 21, 325801 (2009) | 2688 atoms | 242–364 |
| In | `2008--Do-E-C--In--LAMMPS--ipr1` (2NN MEAM) | Do, Shin, Lee, Calphad 32, 82 (2008) | 1408 atoms | 344–516 |
| Sn | `2018--Ko-W-S--Sn--LAMMPS--ipr1` (2NN MEAM) | Ko, Kim, Kwon, Lee, Metals 8, 900 (2018) | 1900 atoms | 404–606 |
| Bi | `2021--Zhou-H--Bi--LAMMPS--ipr1` (2NN MEAM) | Zhou, Dickel, Baskes, Mun, Asle Zaeem, MSMSE 29, 065008 (2021) | 1728 atoms | 435–653 |

Full citations, DOIs, NIST URLs, SHA-256 of every potential file and caveats: `metals/<El>/potential/SOURCES.md`
and `metals/<El>/manifest.json`.

### Not EAM: MEAM (the brief said EAM)
For these four elements the NIST repository has **no plain EAM potential with LAMMPS files**: the elemental,
LAMMPS-ready options are 2NN MEAM (`pair_style meam`). Belashchenko's 2012 EAM for Ga and Bi is listed without
files and is "parameterized for the liquid-state specifically" (NIST), so it can't do melting; Nitol 2023's Sn
RANN-EAM hybrid needs a custom LAMMPS build. MEAM is still a classical potential, independent of MACE.
**The Nebius LAMMPS build must include the MEAM package** (check `lmp -h | grep -i meam`, or `run_mdprecheck`).

### Caveats that go into any comparison
- **Ga:** parameters from a Ga-In(-N) alloy fit; NIST warns elemental parts of alloy potentials may not suit the
  pure element. alpha-Ga's dimer structure is hard for MEAM: check the solid stays alpha-Ga.
- **Bi:** the paper says the potential was fitted to reproduce Bi's melting point, so for Bi the check is partly
  anchored to the experiment it's meant to test independently. Say so.
- The MEAM models' own melting points aren't published on NIST (its computed melting temperatures cover mostly
  EAM so far), so the bracket is wide (0.8–1.2 x the experimental value) and may still miss.

### Files
```
metals/in.coexist.lmp     two-phase coexistence at one T (mirrors coolorbit/sim/melting.py)
metals/in.latent.lmp      enthalpy of one phase (solid | liquid) at T
metals/analyze.py         verdicts / melting point / latent heat from the outputs (standard library only)
metals/<El>/data.coexist.lmp, data.solid.lmp, potential.mod, potential/, manifest.json
build_metal_bundles.py    regenerates metals/<El>/ (structures from coolorbit/sim/alloys.py)
```

### Jobs (each is one LAMMPS run from inside `metals/<El>/`; exact commands in `manifest.json`)
1. **Melting point:** 5 coexistence jobs, `lmp -in ../in.coexist.lmp -var T <T> -var Tprep <..> -var Tmelt <..> -var seed 2026`,
   T = 0.8, 0.9, 1.0, 1.1, 1.2 x the experimental value. Each writes `pe_T<T>.dat`. Then
   `python ../analyze.py coexist pe_T*.dat` gives melting / freezing / stable per T and the melting point =
   midpoint of [highest freezing, lowest melting] ± half the gap. All melting -> shift down 20%; all freezing ->
   up 20%; then refine with 4 temperatures 2% apart.
2. **Latent heat, at the melting point from step 1** (not the experimental one): `-var phase solid` and
   `-var phase liquid`, same T. `python ../analyze.py latent --mass-amu <m> h_solid_T<T>.dat h_liquid_T<T>.dat
   --log-solid <log> --log-liquid <log>` gives J/kg and checks the phases.

Settings mirror the GB10 production defaults: 2 fs, Nosé-Hoover thermostat 0.1 ps, barostat 1 ps, equilibrate
4000, melt 6000, quench 3000, production 25,000 steps (50 ps), record every 50, threshold 0.002 eV/atom or 3x
the run's own block noise.

### Laptop validation (inputs only, not results)
LAMMPS 22 Jul 2025 (PyPI wheel, MEAM package present), CPU. Every input parses and runs for all four
elements. Full-length preparation stages:

| Check | Ga | In | Sn | Bi | Want |
|---|---|---|---|---|---|
| Coexistence: liquid half melted at Tmelt (moved > 1.5 Å) | 0.90 | 0.94 | 0.99 | 1.00 | ≥ 0.8 |
| Coexistence: crystal half disturbed | 0.00 | 0.01 | 0.00 | 0.00 | ≤ 0.05 |
| Latent: liquid melted at Thot = 3 x T | 0.97 | 0.99 | 0.99 | 1.00 | ≥ 0.8 |
| Latent: liquid still liquid after 50 ps at T = experimental Tm | 0.67 | 0.90 | 0.93 | 0.99 | ≥ 0.5 |

What validation caught: (1) obsolete `dilate partial` syntax (fixed: `dilate liquid`); (2) **superheating in
my own latent-heat input**: melting the periodic crystal at 1.6 x T (the GB10 MACE default) left Ga, In and Bi
completely solid (0%, 0%, 6% of atoms moved). The liquid is now made at 3 x T and the run reports a
MELT_CHECK. The GB10's `melting.py` latent mode prepares its liquid the same 1.6 x way; its phase check
would flag a failure, but it may cost a rerun.

No production coexistence runs were done on the laptop (they are the Nebius jobs); no numbers here are results.

### Not done yet
- Waxes (n-octadecane, n-eicosane): AMBER/GAFF2 via `submit_amber_workflow`, after the metals.
- The exact Nebius job format: we don't have the MCP tool schema on this laptop. Each job is a plain
  `lmp -in ... -var ...` command with the element folder as its working directory; adapt to the tool's fields.
