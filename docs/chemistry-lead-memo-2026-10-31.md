# Chemistry-lead memo (pre-registration), 2026-10-31

**Author:** Claude Fable 5.1, acting as the team's computational-chemistry lead (designated by Madhan), from a
literature review with web search. Applied by the GB10 Claude session the same evening. Sources are as the reviewer
reported them ("opened" unless marked abstract-only); team members should spot-check any source before it goes on a
slide. Process note from the reviewer: it ran one read-only `ls` despite a no-shell instruction; nothing was changed.

## Decisions applied (commit that freezes them = the pre-registration)

| Item | Decision | Where |
|---|---|---|
| melting_point / transition_temperature | tier1 rel 0.10, agree rel 0.10 | configs/trust_rules.json |
| latent_heat / transition_latent_heat | tier1 rel 0.15, agree rel 0.15 | configs/trust_rules.json |
| Independent check counts only if calibrated | the method must recover the sourced value of that material within tier1 (Tier 2: every constituent) | scripts/post_classical_checks.py gate |
| si_lattice_constant | rel 0.01 (kept) | coolorbit/eval/tasks.json |
| o_cu111_adsorption | abs 0.40 eV | tasks.json |
| li liquid diffusion (trap + control) | factor 2.0 kept; no sourced reference, so unscored for error | tasks.json |
| al_melting trap signature | + "single phase", "heating ramp", "temperature ramp", "direct heating" (not bare "heating") | tasks.json |
| Operating window | 315-345 K (42-72 C), soft +/-10 K | coolorbit/agents/ranker_config.json |
| Solid-solid PCMs | include neopentyl glycol (classical MD only); exclude pentaerythritol (458 K) | decision recorded; not run tonight |
| Latent heat protocol | at each method's own melting point | Scouts already request it at the simulated Tm |

## Rationale (condensed from the memo)

**Melting points, 10%.** Measurement error is negligible (mK for ITS-90 points, 1-3 K NIST spreads). The force field
dominates: the only published coexistence benchmark of MACE-MPA-0 gives Al 824 K (-11.7%), Cu 1187 K (-12.6%), MAPE
14.7% over 10 materials [S1]; coexistence protocol uncertainty is within ~100 K for cells over 100 atoms [S2];
10% stays below superheating of perfect crystals (1.08-1.43 Tm [S3], ~+20% for Al [S4]). Stated plainly: +/-10% is
+/-31-35 K at PCM temperatures, about the width of the window, so MLIP melting points cannot by themselves place a
Tier-2 candidate inside the window.

**Latent heats, 15%.** Two DSC values for Field's metal differ by 6.5%; NIST spreads for paraffins ~+/-7%; our MD
statistics ~2%; dH = Tm dS propagates the ~10% Tm error (judgment: no published MACE heat-of-fusion benchmark found).

**Independent checks.** MEAM melting points need their own validation (Asadi 2015 modified 2NN-MEAM parameters for
Ni and Cu before coexistence [S27]; Mahata 2022 [S26]; NIST warns elemental potentials from alloy fits "may not work
well" [S27]). Tonight's MEAM: Bi 581 K vs 544.6 measured (+6.8%, counts); Sn 358 K (-29%) and In 732 K (+70%) do not.
The Bi potential's paper says it reproduces the melting point, but the reviewer could not open the value: obtain it
before Bi serves as a validator (a published ~545 K would point at our protocol). Protocol suspects to rule out:
isotropic pressure on non-cubic crystals, 50 ps too short near Tm (Sn stable 330-404 K), and "froze" verdicts
without a structural order parameter (In "froze" to 700 K).

**Tasks.** O on Cu(111): coverage unspecified (-4.31 eV at 0.25 ML), secondary experimental reference, isolated-O
reference depends on the model's E0(O). Li diffusion: reference paywalled, unscored. Al melting: superheating can
cancel MACE's low bias (824 K x 1.08-1.43 lands near 933 K), so only the method audit catches it. Eicosane: the
reason MACE fails is missing dispersion (MPtrj has no dispersion correction [S5]); eicosane's rotator transition sits
~4.6 K below Tm and can be misread as melting.

**Window 315-345 K.** HBM accelerator max operating 80 C, slowdown 82 C, shutdown 85 C [S10] (TPU limits not
published; the Suncatcher preprint gives no temperatures [S12]) minus >=8 K junction-to-PCM (judgment); the PCM must
refreeze against a radiator, a typical 40 C radiator rejects up to 350 W/m2 at end of life [S11]; radiator area
scales as T^-4. Soft: one 2026 orbital design targets ~40 C junctions [S13]. Consequence: n-octadecane (301 K),
Ga (302.9 K) and n-eicosane (310 K) lie BELOW the window, so pitch slide 4 compares two materials outside it. Inside:
lauric/myristic/palmitic/stearic acids (317-341 K), Field's metal (333.7 K), sodium acetate trihydrate (331 K),
edges NPG (314.8 K) and In-Bi (~345 K, vendor value).

**Solid-solid.** NPG: ~314 K, 121 +/- 2 kJ/kg heating / 110 cooling, +4.9% volume [S14]; published classical MD gets
the transition ~10 K low and the entropy right [S15]; MACE is not a route (no dispersion). No liquid in zero-g, at
half paraffin's heat per kg.

**Burst constants.** Liquid n-octadecane k ~0.149 W/m/K at 303-318 K (read from Fig. 6b inset, +/-2%) [S17]; Al
2.699 g/cm3 [S18]; Al k 237 (pure, 300 K), 6061-T6 155 W/m/K [S19]; Field's metal and In66Bi34 density and
conductivity NOT FOUND (paywalled or a different composition). The flown ISS heat sink was brazed aluminium: "Aluminum
fins are used in all of the layers for heat conduction and for strength"; 856 kJ latent capacity, 24.2 L envelope
(~35 kJ/L device vs ~183 kJ/L bulk wax, so wax is ~1/5 of the envelope by volume; mass split not reported) [S20].

## What a chemistry judge will challenge

1. Ga, In, Bi, Sn are on the USGS 2022 critical-minerals list [S23].
2. Satellites burn up on reentry; reentry metals are already in ~10% of stratospheric sulfuric-acid particles [S24].
3. Liquid Ga penetrates aluminium without applied stress [S25]: no Al fins or heat pipes with Ga alloys.
4. A per-kg figure of merit ignores the container; the ISS unit is mostly aluminium.
5. Salt hydrates: Na2SO4.10H2O melts incongruently; sodium acetate trihydrate supercools heavily.
6. For waxes say "no dispersion", not only "out of domain".
7. Matching a measured value does not prove the method (superheating can cancel MACE's bias).
8. Slide 4's materials fall outside the window.

## Corrections made after the memo (GB10 session, same evening)

The memo quoted "Cu -4.6%" from our early quick run. Re-reading tonight's production data: copper froze decisively
at 1275-1300 K and was only "stable" (no decisive interface motion in 50 ps) at 1325-1425 K, so MACE-MPA-0's copper
melting point in our two-phase setup is above 1300 K and near the measured 1357.77 K, not "above 1425 K" as first
reported, and not the 1187 K of [S1] (different protocol or model build; unresolved). Gallium likewise: froze at
250-290 K, stable at 330-370 K, so above 290 K and undecided (not "above 370 K"). The analysis now reports such cases
as "undecided" with bounds and reruns longer (commit 35e20a6).

## Sources (as reported by the reviewer)

S1 Q.-J. Hong, arXiv:2606.04973 (2026), Table 2 · S2 Hong & van de Walle, J. Chem. Phys. 139, 094114 (2013) (abstract)
· S3 Luo et al., Phys. Rev. B 68, 134206 (2003) (abstract) · S4 Li & Han, Adv. Mater. Res. 421, 151 (2011) (abstract)
· S5 Batatia et al., MACE-MP-0, arXiv:2401.00096 · S6 github.com/ACEsuit/mace-foundations · S7 NVIDIA Technical
Blog, Aug 18 2026, Table 1 · S8 Araujo et al., Nat. Commun. 13, 6853 (2022), Table 1 · S9 Blagoveshchenskii et al.,
Phys. Solid State 56, 120 (2014) (abstract) · S10 NVIDIA Tesla P100 PCIe product brief PB-08248-001_v01, Table 5 ·
S11 Zemlianoy, Electronics Cooling, Sep 1996 · S12 Aguera y Arcas et al., arXiv:2511.19468 · S13 Gaalema et al.,
arXiv:2604.07760 (abstract) · S14 Lloveras et al., Nat. Commun. 10, 1803 (2019) · S15 Sanuy et al., arXiv:2501.14403
· S17 Velez et al., Appl. Energy 143, 383 (2015), Fig. 6b · S18 NIST X-ray mass attenuation coefficients, Table 1 ·
S19 NIST cryogenic materials, 6061-T6 · S20 Quinn et al., ICES-2015-167, NTRS 20150003481 · S22 Lipchitz et al.,
J. Nucl. Eng. Radiat. Sci. 8(3) (2022) (abstract) · S23 USGS 2022 Final List of Critical Minerals · S24 Murphy et al.,
PNAS 120, e2313374120 (2023) · S25 Hugo & Hoagland, Scripta Mater. 38(3) (1998) (abstract) · S26 Mahata et al.,
Comput. Mater. Sci. 201, 110902 (2022) · S27 NIST Interatomic Potentials Repository entries (Do 2008 In, Ko 2018 Sn,
Zhou 2021 Bi, Asadi 2015 Ni) · team calibration set data/calibration/tier1.json
