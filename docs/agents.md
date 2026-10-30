---
title: Using limen with coding agents
---

[Home](./) · [Tutorial](tutorial) · [Using limen with coding agents](agents) · [Rule kinds](rules) · [CLI reference](cli) · [Evaluation](evaluation)

The evaluation behind limen found that telling an agent the rules before it starts helps, but
prose does that equally well. What prose cannot do is notice when a rule was broken anyway. The
recommended workflow therefore has two halves: show the rules, then check the result and return
the findings until the check passes.

## The loop

1. **Before the change**, give the agent the rules for the files it will touch:

   ```bash
   limen context --spec "$PWD/architecture.yaml" --workspace "$PWD" --paths src/mypkg/estimators/
   ```

2. **After the change**, run the gate:

   ```bash
   limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --changed-only HEAD
   ```

3. **If it fails**, return the findings to the agent as they are (file, line, rule, message,
   remedy) and ask for a corrected change. Repeat until exit code 0, with a limit of a few rounds.
   In the evaluation, returning the findings for up to two further rounds made every one of 135
   answers pass the check.

The same `limen check` runs in CI, so the agent and the merge gate apply the same rules.

## Instruction file snippet

Add to `AGENTS.md` or `CLAUDE.md`:

```markdown
## Architecture contract

This repository's architectural rules are in `architecture.yaml` and are enforced in CI.

- Before changing files, run:
  `limen context --spec "$PWD/architecture.yaml" --workspace "$PWD" --paths <directories you will change>`
  and follow every hard rule it lists.
- Before reporting the task as done, run:
  `limen check --spec "$PWD/architecture.yaml" --workspace "$PWD" --changed-only HEAD`
- If it exits non-zero, fix every finding using its remedy and run it again.
  Do not edit `architecture.yaml` or add exemptions to make the check pass.
```

## Writing rules agents can act on

- Put the source of the norm in the message, for example the line of `CONTRIBUTING.md` it comes
  from, so a reviewer can see why the rule exists.
- Write the remedy as an instruction: what to change, and where. Findings with a message and
  remedy were acted on more often than findings with only rule, file and line.
- Prefer rules about places in the code (imports, exports, file locations) over rules about
  meaning. A pattern is an approximation of a norm; test it on a compliant and a violating example.
- Keep `exemptions` with an owner and an expiry date, so that tolerated deviations stay visible.

## What limen does not check

limen checks structure: files, imports, exports and text patterns. It does not run tests and does
not judge whether a change is functionally correct. Use it next to your test suite, not instead
of it.
