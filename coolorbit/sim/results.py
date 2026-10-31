"""Turn a melting-workflow result into a board ``property_result`` payload (no GPU imports)."""

from __future__ import annotations

PROTOCOLS = {
    "coexistence": "two-phase coexistence",
    "heating": "single-phase heating",
    "latent": "NPT enthalpy difference (solid vs liquid)",
}
ENSEMBLES = {
    "coexistence": "NPT anisotropic (MTK + Nose-Hoover chains)",
    "heating": "NPT isotropic (MTK + Nose-Hoover chains)",
    "latent": "NPT isotropic (MTK + Nose-Hoover chains)",
}


def build_property_payload(candidate: dict, result: dict, settings: dict, n_atoms: int, run_dir: str) -> dict:
    """``candidate`` is a candidate payload; ``settings`` is melting.Settings as a dict."""
    return {
        "candidate": candidate["name"],
        "family": candidate["family"],
        "tier": candidate["tier"],
        "property": result["property"],
        "value": result["value"],
        "units": result["units"],
        "uncertainty": result.get("uncertainty"),
        "flags": list(result.get("flags", [])),
        "method": {
            "engine": "ALCHEMI",
            "model": settings["model"],
            "protocol": PROTOCOLS[settings["mode"]],
            "ensemble": ENSEMBLES[settings["mode"]],
            "timestep_fs": settings["dt_fs"],
            "n_atoms": n_atoms,
            "sim_length_ps": settings["prod_steps"] * settings["dt_fs"] / 1000,
            "seeds": 1,
            "temperatures_K": settings["temps"],
            "run_dir": run_dir,
        },
    }
