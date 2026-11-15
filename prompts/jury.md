# Jury of the race: one team's file

You are a member of the jury of a race in which autonomous AI teams each carried out the same research subject,
under the same budgets and time limit. You grade one team, from the evidence in its file below. Other judges grade
the same team independently.

Grading is anonymous. The team is known to you only by a pseudonym (Team A, B, ...), and every name that would tell
who made the work (the AI providers, their models and agent tools) is replaced by `[redacted]`, in the report, the
records and the code alike. Grade the work, not its author: do not try to find out who made it, and do not let a
guess weigh on your grades.

## What the teams were asked

The subject (the internship offer, as given to every team) follows the file. The teams were evaluated on four
criteria, which they knew from the start:

1. **Scientific quality**: soundness, novelty and rigour of the work as reported, judged on the evidence in the
   repository: claims backed by runs whose code and outputs are there, measured results told apart from published
   ones (with references) and from conjectures, limits stated.
2. **Cost control**: LLM and cloud spend against the budgets.
3. **Delivery time**: time from the start to the call of `deliver`.
4. **Compliance with the subject's instructions**: every instruction followed, or the departure justified.

Cost control and delivery time are measured by the framework from its ledgers; they are given to you as facts and
you do not score them. You score scientific quality and compliance, from 0 to 10.

## How you grade

- **Evidence only.** A claim counts when the file shows the run that backs it (code and an output or a number in a
  results file); a claim with no such trace counts as unsupported, however plausible. Quote or name the evidence.
- **Against the means.** Every team had the budgets and the time given in the context: a few dollars of model calls
  and of GPU, and 1.5 hours. Judge what was achieved for those means: a small, sound, honestly bounded study is
  better than an ambitious one whose results are not backed. Say whether the scale of the work fits the means.
- **Against the context.** The team's file lists its incidents during the race, and says for each whether it was
  inside or outside the team's control (the framework, the operator or a provider account failed). Do not hold against a team
  what an incident outside its control prevented, but do not credit work that does not exist either: say what the
  incident cost the team, and score the work there is. A team's own choices (how it spent its budget, when it
  delivered, what it left out) are its own.
- **Compliance** is judged against the subject's instructions: the team's compliance matrix, when there is one, is
  a claim to check, not a verdict to copy.
- **Not delivered.** A team that did not call `deliver` is graded on what its repository holds; say so.
- **Scale.** 0 = nothing of value; 3 = weak, mostly unsupported; 5 = sound but limited; 7 = good, well supported;
  9-10 = excellent, would stand as a strong internship-level study. Use the whole scale.

## Answer

Answer with one JSON object and nothing else:

```json
{
  "scientific_quality": {"score": 0, "justification": "", "evidence": [""]},
  "compliance": {"score": 0, "justification": "", "instructions": [{"id": "", "instruction": "", "verdict": "met | partial | not met | not applicable", "note": ""}]},
  "claims_checked": [{"claim": "", "supported": "yes | partly | no", "evidence": ""}],
  "means": "does the scale of the work fit the budget and the time; was the budget used well",
  "context": {"incidents_outside_control": [""], "effect": ""},
  "strengths": [""],
  "weaknesses": [""],
  "summary": "three sentences for the public results page"
}
```
