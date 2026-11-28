---
id: split-qa-service-py-1970-lines-into-validation-assessment-fix-analytics
title: Split qa_service.py (1970 lines) into validation, assessment, fix, analytics
type: backlog_item
tags:
- tech-debt
- architecture
- refactor
importance: 5
kind: refactor
status: done
priority: high
effort: L
rank: 0
---

qa_service.py is the largest service at 1970 lines with 51 methods covering 4 distinct concerns: structural validation, assessment entry management, auto-fix, and gap analysis. Split into QAValidationService, QAAssessmentService, QAFixService, QAAnalyticsService.
