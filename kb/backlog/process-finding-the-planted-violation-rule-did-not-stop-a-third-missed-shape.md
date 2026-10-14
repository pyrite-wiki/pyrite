---
id: process-finding-the-planted-violation-rule-did-not-stop-a-third-missed-shape
title: 'Process finding: the planted-violation rule did not stop a third missed-shape cold read (Andon #822)'
type: backlog_item
tags:
- process
- retro
importance: 5
kind: improvement
status: proposed
priority: medium
effort: S
rank: 0
---

A process finding for the next retro. It proposes nothing; the retro decides.

## What happened

The retro of 2026-10-08 added a rule to review.md and dispatch.md: a structural or ratchet test ships with a planted-violation test for every shape the property names, plus one shape the reviewer picks. Its stated expected effect was "ratchet themes land with 0 fix rounds caused by a missed shape (next two: outcome slices 2 and 3)", with a revert condition tied to cost.

The first theme under the rule was #793 round 1 (outcome-contract slice 1). The worker planted nine shapes and showed each red. The delta cold read on 2026-10-10 planted two more and the structural tests stayed green; of 31 further shapes, 27 were not flagged. The conductor pulled the Andon cord (#822) instead of sending a round that added the new shapes.

## What the evidence says

- The rule made the guard's holes visible sooner. It did not make the guard hold: a deny-list of source shapes always has another shape, so "plus one the reviewer picks" fails every time the reviewer picks well.
- The rule's second sentence ("where a behavioural assertion can stand in for the shape scan, prefer it") was the part that mattered, and it was written as a preference. Round 1 followed the first sentence and not the second.
- The spike for #822 built the behavioural version in one run: a guard at the root group's `invoke`, an allow-list, 16 of 18 named shapes caught through the real root, 129 lines, every existing task test green. The groom is on the outcome-contract backlog item; ADR-0046 carries the amendment (proposed).
- Three themes hit this (#779 r0, #793 r0, #793 r1). Two fix rounds and three cold reads were spent before the model was named.

## Questions for the retro

- Should the order of the rule's two sentences be reversed: a property about what code does is enforced at the one point it passes through and tested by running it, and a scan is allowed only as a named hint or where the property really is about how code is written (imports, layers)?
- Should a groom whose acceptance is "a scan holds X" be treated as a risk trigger for a spike, as #612's lesson already says for uncertain approaches?
- The Andon was pulled at the second same-shaped failure of one theme. #779 r0 was the first instance of the class. What would have let the first instance name the model?

## Evidence

Andon #822; PR #793 (comments of 2026-10-08 and 2026-10-10); PR #779; the retro entry of 2026-10-08T16:30Z in the conductor log.
