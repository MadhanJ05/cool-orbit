# Request for blind evaluation tasks (send to someone outside the team)

**Why:** our comparison experiment tests whether agents catch scientifically wrong simulation requests.
If we write all the test tasks ourselves, anyone can say we designed a test our system passes. We need a few
tasks written by someone outside the team (an NVIDIA mentor or another hackathon team).

**Status:** not yet requested. Who was asked, and when: _(fill in)_.

---

## Message to send

> Hi! We're Cool Orbit (ScienceClaw hackathon). We're measuring whether AI agents running atomistic
> simulations (NVIDIA ALCHEMI, MACE-type ML force fields, classical MD) push back on ill-posed requests or
> silently return confident wrong numbers. Could you write **4 to 6 short task prompts** for us, without
> telling us which ones are which?
>
> - Some **well-posed**: a normal, answerable property calculation.
> - Some **ill-posed or trap**: the property isn't measurable that way, the method is known to give wrong
>   answers, the model is out of its training domain, etc.
>
> For each task, please send privately (we keep your answers sealed until after the runs):
> 1. the prompt, exactly as an agent would receive it (one or two sentences);
> 2. well-posed or ill-posed/trap, and in one line why;
> 3. if well-posed: the expected value with units and a source (DOI or handbook), if you have one.
>
> Materials or properties near our topic (melting, latent heat, diffusion, metals, salts, organic
> waxes) are great, but anything simulatable with MD or ML force fields works. Thank you!

---

## How we add them

- Add each task to `coolorbit/eval/tasks.json` with `"origin": "blind"`, the author's classification as
  `kind`, and their source as the reference (or `null`).
- Add them in the pre-registration commit, before any comparison run. Don't change their wording.
- Note the author in this file (with their permission).
