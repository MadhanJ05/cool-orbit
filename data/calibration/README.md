# Tier-1 calibration dataset

`tier1.json` holds measured property values for known phase-change materials (and Cu/Al for method
validation). The trust tier can only reach "trusted" against these values, so every number must be
checkable by a judge.

Load it onto a board with the Librarian:

```bash
python -m coolorbit.agents.librarian --check                         # validate only
python -m coolorbit.agents.librarian --board boards/dev-<name>       # post reference_value artifacts
python -m coolorbit.agents.librarian --board boards/<run> --only copper aluminum
```

## Rules

- **Every value has a source** that was opened and read (URL or DOI, plus table/row). Model memory is
  not a source. If no source could be verified, the record has `value: null` and a `NOT FOUND:` note.
  These null records are posted too, as evidence of what was searched.
- **One record per (candidate, property).** The board compares a trust-tier result with *every*
  sourced reference for that pair and flags on any mismatch, so other measurements go in
  `alternates` (kept here, never posted).
- **Candidate names must match** the `name` of the `candidate` artifact the simulator uses (case and
  extra spaces are ignored). Use the names in `tier1.json` (e.g. `copper`, `n-eicosane`,
  `neopentyl glycol`, `calcium chloride hexahydrate`).
  Metals also list `aliases` (copper -> `Cu`, aluminum -> `Al`, ...): bulk posting posts every alias,
  and the Librarian agent answers a need under the exact name the result uses.
- **Units are the board's**: K, J/kg, g/cm3, W/m/K. Density and conductivity state `phase`.
- `quote` is the verbatim text or table row the value was read from. `note` shows any conversion
  arithmetic and the literature spread.

## Fields

`candidate, family, formula, property, value, units, uncertainty, source` (required), plus optional
`phase, temperature_K, quote, note, alternates, aliases`.

Properties: `melting_point`, `latent_heat` (fusion), `density`, `conductivity`, and for solid-solid
PCMs `transition_temperature` and `transition_latent_heat`. `transition_latent_heat` has no rule in
`configs/trust_rules.json` yet (the chemistry lead decides).

## How values were collected (2026-10-31)

Four literature searches (paraffins and fatty acids; sugar alcohols and plastic crystals; salt
hydrates; metals), each required to return the URL and verbatim quote for every number. The main
sources were then spot-checked by re-fetching them independently (NIST WebBook pages for n-eicosane,
stearic acid, erythritol, neopentyl glycol; NIST-JANAF tables for Cu, Al, Ga; NIST ITS-90 page for Cu;
the Hirschey 2018 review PDF from OSTI). All kJ/mol to J/kg conversions use the molar mass printed on
the NIST page and are recomputed in code, not typed.

Source preference: NIST (WebBook, JANAF, ITS-90, ThermoML) > peer-reviewed papers with DOI > open
review tables ("as compiled in") > handbook values quoted by PubChem/HSDB (marked "secondary").

| Family | Melting point | Latent heat | Notes |
|---|---|---|---|
| Paraffins (C16, C18, C20) | NIST average (+/- spread) | Mondieig 2004 DSC | n-eicosane latent heat is fusion only (solid-solid transition ~4 K below Tm excluded) |
| Fatty acids (C12-C18) | NIST average | Moreno 2007 DSC | conductivity not searched |
| Sugar alcohols | Barone 1990 (via NIST, TRC uncertainty) | Barone 1990 | conductivity literature disagrees widely (xylitol solid 0.373 vs 1.3 W/m/K) |
| Plastic crystals (NPG, pentaerythritol) | Zhang 1988 / Barone 1990 | solid-solid and fusion stored separately | solid-solid transition carries most of the heat |
| Salt hydrates | Lorsch 1975, De Paola 2017, Guion 1983 DSC, Xie 2017 | same | primary papers cited "as compiled in" Hirschey 2018 / Xie 2017 |
| Metals | ITS-90 (Cu), JANAF (Al), NIST (Ga), Assael 2012 (In), Zamora 2021 (Field's metal) | JANAF H-H(Tr) jump; Archer 2003 for In | conductivity from Ho, Powell & Liley 1972 (OCR of a scan) |

## Known gaps and weak spots (reported, not hidden)

- **Conductivity null** for all paraffins and fatty acids, D-mannitol, pentaerythritol,
  Na2SO4.10H2O, Na2HPO4.12H2O, sodium acetate trihydrate, Mg(NO3)2.6H2O and Field's metal.
- **Density null** where the only value was measured at the melting point (phase ambiguous):
  n-octadecane, myristic and palmitic acid; and for n-eicosane (implausible value), Cu, Al, Field's metal.
- **Low confidence:** Mg(NO3)2.6H2O (single secondary compilation; primary paper Sadovska 2015 could
  not be opened). Sodium acetate trihydrate latent heat has a wide spread (226-289 kJ/kg); the DSC
  value used is the top of the range. CaCl2.6H2O melting point spans 25.2-30 C.
- **Secondary citations:** densities via PubChem/HSDB (CRC, Merck Index) were not checked in the
  original handbook. Salt-hydrate primary papers were not opened; the reviews quoting them were.
- **Uncertainty null** where the source states none (most latent heats). Literature spread is in
  `note` instead; it is not an uncertainty.
- Thermal conductivities from Ho, Powell & Liley (1972) were read from OCR text; verify against the
  scanned PDF before quoting in slides.

Open decisions this dataset does not make: operating temperature window, whether solid-solid PCMs are
in scope, trust tolerances.
