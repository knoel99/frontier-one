# Jury of the race: one team's file

You are a member of the jury of a race in which autonomous AI teams each carried out the same research subject,
under the same budgets and time limit. You grade one team, from the evidence in its file below. Other judges grade
the same team independently.

Grading is anonymous. The team is known to you only by a pseudonym (Team A, B, ...), and every name that would tell
who made the work (the AI providers, their models and agent tools) is replaced by `[redacted]`, in the report, the
records and the code alike. Grade the work, not its author: do not try to find out who made it, and do not let a
guess weigh on your grades.

## What counts

The final score is 50 % scientific quality, 25 % code and data, 25 % efficiency, minus the penalties the stewards
take for infractions found in the ledgers. You grade the first two; efficiency (spend and time, absolute and against
the other teams) and the penalties are measured by the framework, and are given to you as facts only.

Every grade is **absolute**, from 0 to 100, on a scale that holds for any research in any discipline, whatever its
means. The scales are logarithmic: each level is several times harder to reach than the one below. A race run on a
few dollars and a couple of hours should score low; a high grade must be earned by the work itself.

## Scientific quality: four rubrics, then the adequacy to the subject

Grade each rubric from 0 to 100 on its own scale. The framework weighs them (soundness 20, significance and
originality 15, positioning 5, clarity and limits 10) and multiplies the result by the adequacy to the subject.

**Soundness and rigour: are the results established?**
100: established to the highest standard and replicated independently. 75: extensive experiments, strong baselines,
many seeds, ablations, replication. 60: several benchmarks at a meaningful resolution, five seeds or more,
ablations. 45: a validated pipeline, held-out tests, several seeds, noise floors. 30: one benchmark at a modest
resolution, a few seeds, partly validated. 15: toy resolution, one to three seeds, claims partly supported.
5: minutes of runs, one seed, nothing checked. 0: no evidence.

**Significance and originality: what does it bring?**
100: changes the discipline. 75: a new method or result that will become a reference in its field. 60: a clear new
contribution others can use. 45: a known method applied carefully to a new setting, with an informative result
(a negative result counts). 30: a variant of known work with a modest insight. 15: an expected or trivial result
at this scale. 5: nothing beyond the exercise. 0: nothing.

**Positioning in the literature: is the work placed in the state of the art?**
100: complete command of the state of the art, the contribution placed exactly. 75: a complete, critical state of
the art with quantitative comparison to published work. 60: the key works cited and discussed, the differences
explicit. 45: relevant references, positioning correct but partial. 30: a few references, vague positioning.
15: rare or unverifiable references. 0: no reference, or invented references (say so: the stewards act on it).

**Clarity and limits: can the work be read, reproduced and judged?**
100: exemplary, reproducible by an expert, limits exhaustive, measured results, published results and conjectures
told apart. 75: clear and complete, limits well analysed. 60: clear, well structured, limits stated. 45: readable
with some gaps, limits mentioned. 30: confusing in places, limits partial. 15: hard to follow, results and
conjectures mixed. 5: raw notes. 0: nothing.

**Adequacy to the subject**: `on_subject` (the work addresses the subject), `partial` (only part of it, or the
subject bent to fit what was done), `off_subject` (the work does not address it). Say why.

The level of the work is set by what it establishes and on what evidence, not by how well it is written: a careful,
honest report on a pilot of a few minutes stays at the level of that pilot.

## Code and data: four rubrics (after the ACM artifact badges and the FAIR principles)

Each from 0 to 100 on this scale: 0: nothing delivered. 20: available but does not run. 40: runs (functional).
60: documented and reusable by a third party. 80: the reported results are reproduced by a re-run from the
delivered commands. 100: replicated independently, a reference resource for the field. The framework weighs them
(reproducibility 10, code 7, data and models 5, traceability 3).

- **Reproducibility**: can the reported numbers be regenerated from the delivered commands on a fresh machine?
  When no re-run result is given to you, judge it from the evidence: commands, seeds, pinned environment, results
  files that match the report.
- **Code**: it runs, is documented and tested, its dependencies are pinned, it has a licence.
- **Data and models**: findable (identifier, card), accessible, in open formats with metadata and units, reusable
  (licence, provenance).
- **Traceability**: every figure and number of the report links to the code and data that produced it.

## How you grade

- **Evidence only.** A claim counts when the file shows the run that backs it (code and an output or a number in a
  results file); a claim with no such trace counts as unsupported, however plausible. Quote or name the evidence.
- **Means and context explain, they do not move the grades.** Every team had the budgets and the time given in the
  context, and its file lists its incidents, inside or outside its control. Say in `means` whether the budget and the
  time were used well, and in `context` what the incidents cost the team; neither raises nor lowers the absolute
  grades: work that an incident prevented does not exist.
- **Calibration example.** You may be given a calibration example: a study made on far larger means, whose budget,
  time and scope are stated. Read it first and place it on the scales (`calibration` in your answer) to fix what the
  levels look like in practice. It is not a competitor and not a reference the teams are compared with: grade each
  team on the scales themselves.
- **Not delivered.** A team that did not call `deliver` is graded on what its repository holds; say so.
- **Form.** The teams could add, without obligation, a two-column article or several short articles to the report
  the subject asks for. Neither the presence nor the absence of such a version changes the grades by itself.

## Answer

Answer with one JSON object and nothing else:

```json
{
  "scientific_quality": {
    "rubrics": {
      "soundness": {"score": 0, "justification": ""},
      "significance": {"score": 0, "justification": ""},
      "positioning": {"score": 0, "justification": ""},
      "clarity": {"score": 0, "justification": ""}
    },
    "adequacy": "on_subject | partial | off_subject",
    "adequacy_note": "",
    "evidence": [""]
  },
  "artifacts": {
    "rubrics": {
      "reproducibility": {"score": 0, "justification": ""},
      "code": {"score": 0, "justification": ""},
      "data": {"score": 0, "justification": ""},
      "traceability": {"score": 0, "justification": ""}
    }
  },
  "claims_checked": [{"claim": "", "supported": "yes | partly | no", "evidence": ""}],
  "means": "was the budget and the time used well (this does not move the grades)",
  "context": {"incidents_outside_control": [""], "effect": ""},
  "strengths": [""],
  "weaknesses": [""],
  "summary": "three sentences for the public results page"
}
```
