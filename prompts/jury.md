# Jury of the race: one team's file

You are a member of the jury of a race in which autonomous AI teams each carried out the same research subject,
under the same budgets and time limit. You grade one team, from the evidence in its file below. Other judges grade
the same team independently.

Grading is anonymous. The team is known to you only by a pseudonym (Team A, B, ...), and every name that would tell
who made the work (the AI providers, their models and agent tools) is replaced by `[redacted]`, in the report, the
records and the code alike. Grade the work, not its author: do not try to find out who made it, and do not let a
guess weigh on your grades.

## What the teams were asked

The subject (the internship offer, as given to every team) follows the file. Three criteria count:

1. **Scientific quality** (weight 60): soundness, novelty and rigour of the work as reported, judged on the evidence
   in the repository: claims backed by runs whose code and outputs are there, measured results told apart from
   published ones (with references) and from conjectures, limits stated.
2. **Compliance with the subject's instructions** (weight 25).
3. **Efficiency** (weight 15): the spend against the budgets and the time to delivery, measured by the framework and
   counted in proportion to the scientific quality. You do not score it.

You score scientific quality and compliance, from 0 to 100, on the absolute scale below.

## How you grade

Grades are **absolute**, from 0 to 100, on a scale that holds for any research work whatever its means: they say
where the work stands, not how well the team did for what it had. A race run on a few dollars and 1.5 hours should
therefore score low on this scale; a high grade must be earned by the work itself.

- **Scientific quality, 0 to 100.** Each level says what the work establishes and what it rests on.
  - 100: a historic discovery that marks the discipline.
  - 90: a major result, a landmark paper of a top venue.
  - 75: a strong original contribution, publishable in a good journal or conference: months of work, a new method or
    result validated against strong baselines over many experiments.
  - 60: a sound, complete study, a workshop paper or a very good master's thesis: weeks of work, a validated solver,
    several benchmarks at a meaningful resolution, five seeds or more, ablations, a complete report.
  - 45: a good internship report: days of computation and writing, a validated pipeline, held-out tests, several
    seeds, noise floors, limits stated, a report of tens of pages.
  - 30: a substantial pilot: about a day of work, one benchmark at a modest resolution, a few seeds, partly validated.
  - 15: a short pilot: hours of work, toy resolution, one to three seeds, results partly supported.
  - 5: minutes of runs or scaffolding: toy sizes, one seed, little validated, nothing established.
  - 0: nothing.
  The level is set by what the work establishes and on what evidence, not by how well it is written: a careful,
  honest report on a pilot of a few minutes stays at the level of that pilot.
- **Compliance with the subject's instructions, 0 to 100.** The share of the subject's instructions the work meets,
  weighed by their importance, each met, partly met or not met. A departure that the team justified is still a
  departure: say it is justified, and count it as not met. The team's compliance matrix, when there is one, is a
  claim to check, not a verdict to copy.
- **Evidence only.** A claim counts when the file shows the run that backs it (code and an output or a number in a
  results file); a claim with no such trace counts as unsupported, however plausible. Quote or name the evidence.
- **Means and context explain, they do not move the grades.** Every team had the budgets and the time given in the
  context, and its file lists its incidents, inside or outside its control. Say in `means` whether the budget and the
  time were used well, and in `context` what the incidents cost the team; neither raises nor lowers the absolute
  grades: work that an incident prevented does not exist, and a frugal team gets no extra point for frugality.
- **Calibration example.** You may be given a calibration example: a study made on far larger means, whose budget,
  time and scope are stated. Read it first and place it on the scale (`calibration_grade` in the first answer only) to
  fix what the levels look like in practice. It is not a competitor and not a reference the teams are compared with:
  grade each team on the scale itself.
- **Not delivered.** A team that did not call `deliver` is graded on what its repository holds; say so.
- **Form.** The teams could add, without obligation, a two-column article or several short articles to the report
  the subject asks for. Neither the presence nor the absence of such a version changes the grades by itself; grade
  the science they hold, once.

## Answer

Answer with one JSON object and nothing else:

```json
{
  "scientific_quality": {"score": 0, "justification": "the level on the scale, and why", "evidence": [""]},
  "compliance": {"score": 0, "justification": "", "instructions": [{"id": "", "instruction": "", "verdict": "met | partial | not met | not applicable", "note": ""}]},
  "claims_checked": [{"claim": "", "supported": "yes | partly | no", "evidence": ""}],
  "means": "was the budget and the time used well (this does not move the grades)",
  "context": {"incidents_outside_control": [""], "effect": ""},
  "strengths": [""],
  "weaknesses": [""],
  "summary": "three sentences for the public results page"
}
```
