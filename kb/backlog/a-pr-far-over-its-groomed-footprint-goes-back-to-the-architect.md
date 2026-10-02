---
id: a-pr-far-over-its-groomed-footprint-goes-back-to-the-architect
title: A PR far over its groomed footprint goes back to the architect before review
type: backlog_item
tags:
- process
importance: 5
kind: improvement
status: proposed
priority: medium
effort: S
rank: 0
---

Approved as a follow-up by the maintainer, 2026-10-02, from the root-cause analysis of PR #612.

## Problem

A groom ends with a predicted footprint, "scored after merge". Nothing acts on the score before merge. #582's groom predicted 3 existing files, 2 new, about 300 lines; PR #612 reached 14 files and 1,849 added lines before its second cold read. An overrun that size means the theme was larger than the groom understood, which is the moment its invariant is most likely incomplete. On #612 it was: the groom had no rule for telling Pyrite's entries from the user's (standard `pyrite-is-a-guest-in-state-it-does-not-own`).

## Proposal

When a draft PR exceeds twice its groomed footprint in files or lines, the conductor sends the groom and the diff's file list back to the architect before the cold read, with one question: what does the worker know that the groom did not, and does the invariant still describe the change? The architect answers on the ticket; the cold read then reviews against the corrected invariant.

## To settle

- The threshold (twice, by files or by lines) and whether tests count.
- Where the check lives: the conductor's Absorb step, or a script that reads the groom's `Touches:` line and the PR's stats.
- What the worker does meanwhile: continue, or stop.

## Acceptance

- The conductor skill states the trigger and the question.
- The next three themes that overrun are recorded here with what the architect found, to show whether the trigger earns its cost.
