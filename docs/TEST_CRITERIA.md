# Test criteria

Read this before adding, changing or removing tests. The goal is a small, dependable suite
that catches meaningful failures. Test counts, assertion counts and coverage percentages
are not goals.

## Decide before writing

Be able to answer all three questions:

1. **What can go wrong?** Name a reachable situation and its incorrect result for the user,
   a caller or an external service. Start from the approved behavior in
   [the BRD scenarios](../tests/brd/README.md), not from the current implementation.
2. **What will expose that failure?** Identify an observable result, rejected operation,
   persisted change, unwanted side effect or boundary contract. The assertion must fail
   when the named bug is present. Derive the expectation independently of the code under test.
3. **What do existing tests miss?** Read the nearest tests across domain, UI, shell and E2E
   levels, including their fixtures and parameters. Prefer extending an existing scenario
   when it already has the necessary conditions.

If there is no distinct failure or useful diagnostic advantage, do not add another test.
A new function, class, branch or file alone does not justify one. These questions guide the
decision; they do not require a new comment, docstring template or document for each test.

## Exercise meaningful behavior

- Use plausible inputs and the smallest setup that reproduces the failure. Realistic does
  not mean E2E: a date calculation often needs only a few explicit examples.
- Use the narrowest level that can prove the contract. Test database constraints and
  transactions against a real test database. Exercise actual UI or runtime boundaries when
  the risk is their wiring, cancellation, ordering or state across turns.
- Assert results and important side effects. Do not merely assert that a mock returned its
  configured answer, a constructor copied fields or a helper was called. Such a check needs
  an independent boundary contract that existing consumer tests do not protect.
- Keep domain operations real. Replace remote services and control time when needed for
  repeatability. Do not mock away the rule being tested. A scripted model proves the scripted
  path, not that a real model will choose it.
- Avoid private layout, incidental call order and exact prose unless they are the contract.
  Registration checks, protocol shapes and prompt/schema snapshots are useful when they
  protect a named requirement; do not duplicate them for every implementation detail.
- Keep a test focused on one behavior. Several assertions may describe its result. Share one
  realistic flow when the same setup and action establish related results; do not combine
  unrelated scenarios to reduce the count.
- Keep execution deterministic. Coordinate concurrency explicitly rather than relying on
  arbitrary sleeps, and keep live services opt-in.

## Avoid equivalent cases

Compare preconditions, action, result and execution path before calling two tests duplicates.
Matching bodies, scenario identifiers or covered lines are not enough. Different fixtures,
entry points and parameters may expose different failures.

Keep distinct boundaries, refusals, rollback, security and concurrency cases. A successful E2E
does not replace them. Manual editing and AI Save can each need a check of their own wiring.
For parameterization, choose representatives of different rules and boundary conditions;
do not repeat arbitrary values from one equivalence class. Parameterization alone does not
remove redundant execution.

## Verify the decision

For a bug fix, reproduce the failure before the fix and verify the corrected result afterward;
an existing test can be strengthened for this purpose. For other changes, use checks
proportionate to their effect, following [repository verification rules](../AGENTS.md).

When removing or consolidating tests, name the remaining tests and the conditions and
assertions they preserve in the change description. Transfer unique assertions first.
For substantial reductions, compare full-suite line and branch coverage before and after
the whole change, using identical application sources and settings. Investigate every loss:
restore useful protection or explain the discrepancy with evidence. Matching coverage alone
does not prove that assertions still protect behavior. Unexecuted code is not automatically
unnecessary code. Do not delete it merely to improve a percentage.

Run affected tests first and the broader checks required by the change. Use a targeted fault
injection only when overlap is uncertain or the risk warrants it; no separate full-suite run
or mutation exercise is required for every deleted test. Report what was actually verified.
