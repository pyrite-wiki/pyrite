---
id: three-competing-db-access-patterns-across-services
title: Three competing DB access patterns across services
type: backlog_item
tags:
- tech-debt
- architecture
- storage
importance: 5
kind: refactor
status: done
priority: medium
effort: M
rank: 0
---

Services use three different ways to query the DB: execute_sql (parameterized), _raw_conn.execute (raw sqlite3, private attr), session.execute(text()) (SQLAlchemy). AuthService is worst offender with ~20 _raw_conn calls. Standardize on one pattern.
