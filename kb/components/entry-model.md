---
id: entry-model
type: component
title: "Entry Model"
kind: module
path: "pyrite/models/"
owner: "markr"
dependencies: ["pyrite.schema"]
tags: [core, data-model]
---

Entry type hierarchy built on abstract `Entry` dataclass with composable protocol mixins. All entries share invariant fields (id, title, body, tags, links, sources) and implement type-specific serialization. Type dispatch at load time uses `ENTRY_TYPE_REGISTRY` populated by core types and plugin registration.

## Architecture

- `base.py` — abstract `Entry` dataclass with `to_frontmatter()`, `from_frontmatter()`, `to_markdown()`, `from_markdown()`
- `protocols.py` — 6 composable mixin dataclasses: Assignable, Temporal, Locatable, Statusable, Prioritizable, Parentable
- `core_types.py` — 8 concrete types: Note, Person, Organization, Event, Document, Topic, Relationship, Timeline
- `factory.py` — `build_entry()` canonical creation with type resolution and metadata overflow
- `generic.py` — `GenericEntry` fallback for unknown/plugin types
- `collection.py` — `CollectionEntry` for ordered lists/boards
- `task.py` — `TaskEntry` with dependency DAG support

## Where the frontmatter ends

One function decides it: `split_frontmatter` in `pyrite/utils/frontmatter.py`. Its reference is the convention other tools follow (Hugo and YAML), not Pyrite's old loader (design.md, principles 1 and 8). A file opens with a `---` line (a BOM and leading blank lines are ignored; blanks or a `# comment` may follow the dashes) and closes at the first later line that starts with `---` in column 0. It returns a typed result: `Frontmatter` (YAML text, body and the span, i.e. offsets of the delimiter lines, so a writer can edit in place), `NoFrontmatter`, `Unterminated`, `Malformed` or `Unsupported`. `load_frontmatter` adds the YAML parse; `require_frontmatter` raises `FrontmatterError` for everything but `Frontmatter`.

Callers: `Entry.from_markdown`, `KBRepository._load_entry`, `read_entry_id` / `explicit_entry_id`, `ids pin`, `create --body-file/--stdin`, `add_entry_from_file`, `KBService._extract_frontmatter` (git change summaries), `TemplateService` (a bad template is skipped with a warning in `list_templates`, raised by `get_template`), the markdown importer, collection export, `schema validate`, and the cascade and journalism extensions. A reader that cannot read a file refuses it with a typed error; none reads the YAML as body.

### Where Pyrite departs from Hugo

Each is pinned by a row in `ORACLE` in `tests/test_frontmatter_splitter.py`, next to Hugo's recorded answer (and a live Hugo run when `hugo` is installed).

- `----`, `---x`, or `---` + NBSP in column 0 before the closing line: Hugo closes on the `---` prefix and reads the tail as body. Pyrite refuses the file (`Malformed`, with the line number).
- TOML (`+++`) and JSON (`{`) frontmatter: Hugo reads them. Pyrite refuses with a message naming the format (`Unsupported`). `create --body-file` treats a leading `{` as body text, since a body is not a file.
- A `# comment` after a delimiter belongs to the delimiter line (YAML); Hugo leaves it at the start of the body.
- `---` + a tab on the opening line: Hugo errors; YAML and Pyrite accept it.
- `...` does not close frontmatter (Hugo agrees), and a `---` inside a quoted value that starts a line is a closer (Hugo agrees; the YAML then fails to load).
- The cascade migration scripts write with `write_text`: a file they change comes out with LF line endings.

### What the structural test catches

`test_no_other_module_reads_or_splits_a_fence_of_its_own` fails on a string constant that spells a fence (`---` with no word in it), a `-{3}` quantifier, `"-" * 3`, or `load_all`/`safe_load_all` anywhere under `pyrite/` or `extensions/*/src` outside the one module. Writers that emit a fence are listed with their counts. It does not see a fence built with `chr(45)` or read from config.

## Type Resolution

```
YAML frontmatter → entry_from_frontmatter() → ENTRY_TYPE_REGISTRY lookup → concrete class
```

Protocol mixins promote fields to DB columns (Assignable → assignee, Temporal → date, etc.) enabling indexed queries on protocol fields.

## Related

- [[entry-factory]] — build_entry() creation path
- [[entry-protocol-mixins]] — composable field groups
- [[kb-repository]] — file I/O using Entry.save()/load()
