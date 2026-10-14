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

One function decides it: `split_frontmatter` in `pyrite/utils/frontmatter.py`. Its reference is the convention other tools follow (Hugo and YAML), not Pyrite's old loader (design.md, principles 1 and 8). A file opens with a `---` line (a BOM and leading blank lines are ignored; spaces or a `# comment` may follow the dashes; a first line that starts with `---` and is anything else, such as `----`, `---x` or `---` + a tab, is `Malformed`, never plain text) and closes at the first later line that starts with `---` in column 0. It returns a typed result: `Frontmatter` (YAML text, body and the span, i.e. offsets of the delimiter lines, so a writer can edit in place), `NoFrontmatter`, `Unterminated`, `Malformed` or `Unsupported`. `load_frontmatter` adds the YAML parse; `require_frontmatter` raises `FrontmatterError` for everything but `Frontmatter`.

Callers: `Entry.from_markdown`, `KBRepository._load_entry`, `read_entry_id` / `explicit_entry_id`, `ids pin`, `create --body-file/--stdin`, `add_entry_from_file`, `KBService._extract_frontmatter` (git change summaries), `TemplateService` (a bad template is skipped with a warning in `list_templates`, raised by `get_template`), the markdown importer, collection export, `schema validate`, and the cascade and journalism extensions. A reader that cannot read a file refuses it with a typed error; none reads the YAML as body.

### Where Pyrite departs from Hugo

Each is pinned by a row in `ORACLE` in `tests/test_frontmatter_splitter.py`, next to Hugo's recorded answer (and a live Hugo run when `hugo` is installed).

- `----`, `---x`, or `---` + NBSP in column 0 before the closing line: Hugo closes on the `---` prefix and reads the tail as body. Pyrite refuses the file (`Malformed`, with the line number).
- TOML (`+++`) and JSON (`{`) frontmatter: Hugo reads them. Pyrite refuses with a message naming the format (`Unsupported`). `create --body-file` treats a leading `{` as body text, since a body is not a file.
- A `# comment` after a delimiter belongs to the delimiter line (YAML); Hugo leaves it at the start of the body.
- `---` + a tab on the opening line: Hugo errors and so do PyYAML and ruamel's safe loader (read on the whole file), so Pyrite refuses it (`Malformed`). On a closing line a tab is accepted: Hugo, PyYAML and ruamel's safe loader accept it, and Pyrite's own loader (ruamel round-trip) never sees a delimiter line, since the splitter hands it only the text between the fences. The ORACLE's YAML column is recorded from PyYAML and ruamel's *safe* loader, not from the round-trip loader Pyrite uses.
- Leading blank lines are ignored (Hugo does the same). That reopens Tier A r1030's risk for a file that starts with blank lines: when the text between its first two `---` rules parses as a mapping, it is read as frontmatter and indexes with an empty title. Stated, not refused.
- A markdown file is one entry: `import_markdown` (and `pyrite import --format markdown`, `POST /api/entries/import`) never splits a body into entries by default. Multi-entry streams are an explicit opt-in (`stream=True`, `pyrite import --stream`, `stream=true`): after an entry's frontmatter, a `---` line directly followed by a line starting `title:`, `type:` or `id:` begins the next entry, which must then close and parse or the whole file is refused. Limits of the opt-in: an entry must start with one of those keys, and a `---` inside a code block is not understood (a fenced frontmatter example in a body splits the entry or refuses the file; 9 of the 931 valid entries in Pyrite's own `kb/` did, which is why it is off by default).
- `...` does not close frontmatter (Hugo agrees), and a `---` inside a quoted value that starts a line is a closer (Hugo agrees; the YAML then fails to load).
- The cascade migration scripts write with `write_text`: a file they change comes out with LF line endings.

### What the structural test catches

`test_no_other_module_reads_or_splits_a_fence_of_its_own` fails on a string or bytes constant that spells a fence (`---` with no word in it, once regex syntax is taken out), a constant expression that folds to one (`"--" + "-"`, `"-" * 3`), a string that compiles as a regex matching exactly dashes (`-{3}`, `^[-]{3}`, `^-{2}-`), or `load_all`/`safe_load_all` (called, named or imported) anywhere under `pyrite/` or `extensions/*/src` outside the one module. Writers that emit a fence are listed with their counts. It does not see: a fence built with `chr(45)`, joined from a variable or read from config; attribute access by name (`getattr(yaml, "safe_load_all")`); a fence written in an f-string expression; or a regex string that holds a word besides its dashes (`"^---$ # frontmatter"`), which the word filter reads as prose.

## Type Resolution

```
YAML frontmatter → entry_from_frontmatter() → ENTRY_TYPE_REGISTRY lookup → concrete class
```

Protocol mixins promote fields to DB columns (Assignable → assignee, Temporal → date, etc.) enabling indexed queries on protocol fields.

## Related

- [[entry-factory]] — build_entry() creation path
- [[entry-protocol-mixins]] — composable field groups
- [[kb-repository]] — file I/O using Entry.save()/load()
