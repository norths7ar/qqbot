# Project Instructions

## Generalization Requirement

- Unless the user explicitly requests a one-off exception, do not fix bugs by adding
  logic that targets a single reported case.
- Do not introduce special branches keyed to a particular nickname, QQ number, group
  member, message, trigger phrase, or incident example.
- Treat a reported case as a reproduction of a broader failure mode. Identify the
  broken protocol boundary, data model, parser, router, or invariant, and fix that
  general mechanism.
- A valid fix must cover equivalent inputs and include tests for the reported shape,
  at least one related case, and a counterexample that must remain unaffected.
- Configuration values that are inherently deployment-specific may remain explicit,
  but they must not be disguised as bug-fix logic.
- If the failure cannot be generalized safely from available evidence, stop and ask
  the user before implementing a case-specific workaround.
