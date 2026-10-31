# Nebius classical-MD input bundles (T6)

**Status: input files only. Nothing has been submitted. Ask Madhan before submitting any Nebius job** (billing
unclear; the team key is private and never goes in git).

## Metal-alloy track first: In, Sn, Bi (`metals/`); Ga dropped

The independent check for the GB10's MACE melting points and latent heats of the low-melting metals: classical
MD with published potentials from the NIST Interatomic Potentials Repository, run with LAMMPS. A
fundamentally different method from an MLIP, same protocol (two-phase coexistence for the melting point,
solid-vs-liquid enthalpy at the melting point for the latent heat).

| Element | Potential (NIST IPR implementation) | Citation | Coexistence box | Bracket (K) | Status |
|---|---|---|---|---|---|
| In | `2008--Do-E-C--In--LAMMPS--ipr1` (2NN MEAM) | Do, Shin, Lee, Calphad 32, 82 (2008) | 1408 atoms | 344–516 | ready |
| Sn | `2018--Ko-W-S--Sn--LAMMPS--ipr1` (2NN MEAM) | Ko, Kim, Kwon, Lee, Metals 8, 900 (2018) | 1900 atoms | 404–606 | ready |
| Bi | `2021--Zhou-H--Bi--LAMMPS--ipr1` (2NN MEAM) | Zhou, Dickel, Baskes, Mun, Asle Zaeem, MSMSE 29, 065008 (2021) | 1728 atoms | 436–654 | ready |
| Ga | `2009--Do-E-C--Ga-In--LAMMPS--ipr1` (Ga part of a Ga-In fit) | Do, Shin, Lee, J. Phys.: Condens. Matter 21, 325801 (2009) | – | – | **dropped: failed the alpha-Ga stability check** |

Full citations, DOIs, NIST URLs, SHA-256 of every potential file and caveats: `metals/<El>/potential/SOURCES.md`
and `metals/<El>/manifest.json`. Brackets are 0.8–1.2 x the measured melting point from
`data/calibration/tier1.json` (Sn: ITS-90 505.078 K; Bi: NIST SRM 2235, 544.556 K; In: Assael 2012, 429.748 K),
used only to place temperatures.

### Ga: reported failure (`metals/Ga/stability/RESULT.md`)
A cheap check before spending compute: relax alpha-Ga and fcc at 0 K, then 20 ps NPT at 303 K. alpha-Ga is below
fcc (−104 meV/atom) and the atoms stay put, but the relaxed cell is off by −8.4% (b) and +6.6% (c), and the
2.44 Å alpha-Ga dimer is lost (first neighbour peak 2.71 Å, ~1.9 neighbours within 2.6 Å). Its melting point
would not be gallium's, so Ga has no jobs.

### Not EAM: MEAM (the brief said EAM)
For these elements the NIST repository has **no plain EAM potential with LAMMPS files**: the elemental,
LAMMPS-ready options are 2NN MEAM (`pair_style meam`). Belashchenko's 2012 EAM for Ga and Bi is listed without
files and is "parameterized for the liquid-state specifically" (NIST), so it can't do melting; Nitol 2023's Sn
RANN-EAM hybrid needs a custom LAMMPS build. **The Nebius LAMMPS build must include the MEAM package** (check
`lmp -h | grep -i meam`, or `run_mdprecheck`).

### Caveats that go into any comparison
- **Bi:** the paper says the potential was fitted to reproduce Bi's melting point, so for Bi the check is partly
  anchored to the experiment it's meant to test independently. Say so.
- The MEAM models' own melting points aren't published on NIST, so the bracket is wide (0.8–1.2 x) and may miss.

### Files
```
metals/in.coexist.lmp     two-phase coexistence at one T (mirrors coolorbit/sim/melting.py)
metals/in.latent.lmp      enthalpy of one phase (solid | liquid) at T (= this potential's Tm)
metals/analyze.py         verdicts / melting point / latent heat + method block (standard library only)
metals/<El>/data.coexist.lmp, data.solid.lmp, potential.mod, potential/, manifest.json
metals/Ga/stability/      the alpha-Ga check and its failed result
build_metal_bundles.py    regenerates metals/<El>/ (structures from coolorbit/sim/alloys.py, guesses from tier1.json)
```

### Jobs (each one LAMMPS run from inside `metals/<El>/`, each with its own log; commands in `manifest.json`)
1. **Melting point (ready):** 5 coexistence jobs,
   `lmp -in ../in.coexist.lmp -log log_<id>.lammps -var T <T> -var Tprep <..> -var Tmelt <3 x Tprep> -var seed 2026`.
   Then `python ../analyze.py coexist pe_T*.dat`: melting / freezing / stable per T, and Tm interpolated within
   [highest freezing, lowest melting] like `melting.py` (± half the bracket). **Any run whose upper half did not
   melt fails the whole analysis** (that box was single-phase heating). All melting -> shift down 20%; all
   freezing -> up 20%; then refine with 4 temperatures 2% apart.
2. **Latent heat (BLOCKED until step 1 gives this potential's Tm):** `-var phase solid` and `-var phase liquid` at
   `T = <Tm_MEAM>`. `python ../analyze.py latent h_solid_T<Tm>.dat h_liquid_T<Tm>.dat --log-solid ... --log-liquid ...`
   gives J/kg; fails on phase checks, enthalpy drift or a solid box-shape change.

Protocol details: 2 fs, Nosé-Hoover thermostat 0.1 ps, barostat 1 ps, equilibrate 4000, melt 6000, quench 3000,
production 25,000 steps (50 ps), record every 50, threshold 0.002 eV/atom or 3x the run's own block noise.
- Coexistence melt/quench: the thermostat measures **only the liquid half** (`compute tliq` + `fix_modify temp`).
  `T_melt = 3 x T_prep`.
- Latent: the solid uses an **anisotropic** barostat (non-cubic crystals; isotropic would keep the experimental
  c/a and bias L low), the liquid an isotropic one; the liquid is made at 3 x T.
- Every output file starts with a header holding T, atoms, timestep, steps, seed and (coexistence) the melt check;
  box lengths and the thermostat/barostat energy exchange (`ecouple`) are logged every 50 steps.

### Laptop validation (inputs only, not results)
LAMMPS 22 Jul 2025 (PyPI wheel, MEAM package present), CPU, full-length preparation stages, after the review fixes:

| Check | In | Sn | Bi | Want |
|---|---|---|---|---|
| Coexistence: liquid half melted (moved > 1.5 Å) | 0.92 | 0.99 | 1.00 | ≥ 0.8 |
| Coexistence: liquid-half temperature during the melt (set 3 x Tprep) | 1255 K (set 1289) | 1576 K (set 1515) | 1618 K (set 1634) | ≈ set |
| Coexistence: crystal half disturbed | 0.00 | 0.00 | 0.00 | ≤ 0.05 |
| Latent: liquid melted at 3 x T | 0.98 | 0.99 | 1.00 | ≥ 0.8 |
| Latent: liquid still liquid after 50 ps at T | 0.88 | 0.93 | 1.00 | ≥ 0.5 |
| Latent: solid stayed solid (aniso barostat) / box shape change | 0.00 / ≤0.04% | 0.00 / ≤0.2% | 0.00 / ≤0.1% | ≤ 0.2 / ≤ 3% |

What validation and review caught, in order: (1) obsolete `dilate partial` syntax; (2) superheating in the
latent-heat liquid prep (1.6 x T left Ga, In, Bi solid; now 3 x T); (3) **review:** the melt-stage thermostat
averaged over all atoms incl. the frozen half, so the liquid ran at ~2x its set T (fixed with `compute tliq`;
the table above shows the liquid now at its set T); (4) **`fix print` writes its title without substituting
variables**, so the output headers held `${T}` instead of numbers; headers are now written with `print`.
analyze.py now reads real LAMMPS output in its tests.

No production coexistence runs were done on the laptop (they are the Nebius jobs); no numbers here are results.

### Not done yet
- Waxes (n-octadecane, n-eicosane): AMBER/GAFF2 via `submit_amber_workflow`, after the metals.
- The exact Nebius job format: each job is a plain `lmp -in ... -log ... -var ...` command with the element
  folder as its working directory; adapt to the tool's fields.
