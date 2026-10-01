# kb.yaml Exemplar

Annotated guide to writing a high-quality kb.yaml for an intellectual-biography KB. These patterns apply to movement KBs as well, with minor adaptations.

## Top-Level Structure

```yaml
name: <short-kebab-name>           # e.g., deming, goldratt, ohno
description: >                     # 2-3 sentence summary of subject and scope
  <Subject's full name> (<dates>) -- <role/significance>.
  <What the KB covers -- writings, concepts, network, influence>.

kb_type: intellectual-biography     # or 'movement' for multi-founder subjects

guidelines:
  contributing: |
    <Sourcing standards -- what are primary vs secondary sources for this subject?
     What are the known pitfalls or areas of confusion?>
  quality: |
    <Intellectual standards -- what makes a good entry for THIS subject?
     What distinguishes this KB from a Wikipedia summary?>
  voice: |
    <Tone -- analytical/balanced/respectful. Note subject-specific considerations.>

goals:
  primary: |
    <What the KB should achieve when complete -- one paragraph>
  success_criteria: |
    <How to know the KB is successful -- what can a reader do with it?>

evaluation_rubric:
  - "<KB-level quality criterion 1>"
  - "<KB-level quality criterion 2>"
  - "<KB-level quality criterion 3>"
  - "<KB-level quality criterion 4>"
  - "<KB-level quality criterion 5>"
```

## Type Definitions -- What to Include

Every type needs:

1. **description** -- one sentence
2. **subdirectory** -- where files go
3. **required** -- fields that must be present (be aggressive -- if 90% of entries should have it, make it required)
4. **optional** -- fields that are nice to have
5. **fields** -- full field definitions with type, description, and enum values where applicable
6. **ai_instructions** -- 3-5 sentences telling Claude how to write good entries of this type, specific to the subject

### Field Definition Pattern

```yaml
fields:
  writing_type:
    type: select
    options: [book, paper, talk, blog-post, report, briefing]
    description: "Type of writing"
  date:
    type: date
    description: "Publication or presentation date"
  coauthors:
    type: list
    description: "Co-authors or co-developers"
```

Field types: `string`, `date`, `text`, `list`, `select`, `integer`, `boolean`

### The Concept Type

Most intellectual biographies need a `concept` type that the template doesn't include. Add it when the subject has distinct named frameworks, theories, or methods:

```yaml
concept:
  description: "A key idea, framework, or intellectual contribution"
  subdirectory: concepts
  required: [title]
  optional: [first_appeared, key_writings, related_concepts, research_status]
  fields:
    first_appeared:
      type: string
      description: "Writing or approximate date where this concept first appears"
    key_writings:
      type: list
      description: "Writings where this concept is developed (entry IDs)"
    related_concepts:
      type: list
      description: "Related concepts (entry IDs)"
    research_status:
      type: select
      options: [stub, partial, draft, complete]
      description: "How thoroughly this entry has been researched"
  ai_instructions: >
    <Subject-specific instructions for concept entries>
```

### Type-Level Evaluation Rubric

High-value types (writing, concept, person) benefit from their own rubric:

```yaml
  evaluation_rubric:
    - "Writing has a date"
    - "Writing has a writing_type classification"
    - "Body describes the work's significance, not just its contents"
```

## Required Fields -- What to Promote

The template makes most fields optional. Promote these to required for quality:

| Type | Promote to Required |
|------|-------------------|
| writing | writing_type, date |
| era | date_range |
| event | date |
| person | role |
| source | source_type, date |
| organization | org_type |

## Relationship Types

Start with the standard set, then add domain-specific ones:

```yaml
relationship_types:
  # Intellectual
  influenced_by: "Intellectually influenced by"
  influenced: "Intellectually influenced"
  develops: "Develops or elaborates (concept in writing)"
  builds_on: "Builds on earlier work"
  critiques: "Critiques or argues against"
  mentored: "Mentored"
  mentored_by: "Mentored by"
  collaborates_with: "Collaborates with"

  # Institutional
  authored: "Authored"
  authored_by: "Authored by"
  affiliated_with: "Affiliated with"
  member_of: "Member of"
  employed_by: "Employed by"

  # Structural
  is_part_of: "Part of a larger whole"
  contains_part: "Contains a part"
  related_to: "Generally related"
  wikilink: "Inline wikilink reference"
  wikilinked_by: "Referenced by inline wikilink"
```

## Policies

Always enable:

```yaml
policies:
  qa_on_write: true
```

Declared enums (`options:` on a `select`, `multi-select` or `list` field, and
`enum:` in `validation.rules`) are enforced by default: a write with an
off-list value is refused (`validation.enforce_enums`, default `true`). Write
`options:`; `values:` is an accepted alias. When adopting a schema over
existing entries that drift from it, set this while you clean up, guided by
`pyrite qa validate` and `pyrite index health` (`off_list_values`), then
remove the line:

```yaml
validation:
  enforce_enums: false
```

## Common Mistakes

1. **Leaving fields optional that should be required** -- If an entry type doesn't make sense without a field (writing without a date, person without a role), make it required.
2. **Generic ai_instructions** -- "A note about the subject" is useless. Write instructions specific to the subject: what makes THIS subject's notes different?
3. **Missing concept type** -- Almost every intellectual biography needs it. The template doesn't include it.
4. **No evaluation_rubric** -- Without rubric items, QA validation has nothing to check beyond structural completeness.
5. **Copy-pasting from another KB without adapting** -- Customize enum values and field descriptions for the specific subject.
6. **Weak guidelines** -- "Write good entries" is not a guideline. Identify the specific sourcing challenges, quality traps, and voice considerations for this subject.
