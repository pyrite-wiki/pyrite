---
id: wip-limits-in-practice
title: WIP limits in practice
type: practice
tags:
- wip
- kanban
- flow
importance: 5
status: active
origin: Kanban, as Anderson adapted it from Toyota's cards and Reinertsen's queueing theory
---

A team caps how many items may sit in a stage of its workflow at once. When
the cap is reached, nobody starts a new item in that stage; they help finish
one. The cap does two jobs: it makes the team pull work instead of having it
pushed, and it shows where work piles up, because a column that stays at its
limit while the one before it empties is the bottleneck.

Why a cap helps comes from queueing theory: queues and waiting times grow
steeply as a stage nears full utilisation, so a team that keeps every
person busy at all times has long lead times ([[reinertsen:wip-constraints]],
[[reinertsen:queueing-theory-applied]]). How teams apply it is Anderson's
account of the Kanban Method ([[anderson:wip-limits]]), and the idea of pull
in software is the Poppendiecks' ([[poppendiecks:pull-systems-in-software]]).

Sources: [[anderson-kanban-successful-evolutionary-change]],
[[reinertsen-principles-of-product-development-flow]].
