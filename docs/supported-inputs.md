# Supported input contract

Section numbering is flat (integer section numbers). Nested section trees are rejected until subtree/alias semantics are implemented. Text replacement operates on the clause body, not its section heading. A family supports up to four amendments and an instrument up to ten operations; snapshot artifacts are bounded to 8 MB. Exceeding a build bound fails visibly and preserves the existing publication.

Only synthetic inputs are used in the demo and tests. TXT is strict UTF-8. CRLF/CR are normalized to LF; clause offsets address that canonical text. DOCX supports ordinary paragraphs with typed section numbering. Tables, explicitly automatic numbering, revisions, drawings and alternate content are unsupported. ZIP entry count and declared expansion size are bounded. PDF/OCR is deferred.

Base format:

```text
Master Services Agreement: Atlas
Parties: Atlas Customer Example LLC | Atlas Supplier Example LLC
Effective date: 2025-01-01

1. Payment
Invoices are payable within 30 days.
```

Sections have unique numeric paths. Headers, section headings and interstitial whitespace are retained in source text; clause spans represent the section region, not a claimed reconstruction of all raw file bytes. Dates are ISO calendar dates. The eight fields are effective date, payment days, convenience termination notice days, automatic renewal, renewal months, governing law, liability cap, and assignment consent.

Accepted semantic sentences are listed in `cip.domain.PATTERNS`. For example, `This Agreement does not automatically renew.` is explicitly false. A conditional statement, an unsupported paraphrase, multiple evidence candidates, or extra substantive text stays unknown. This makes the offline engine useful as a testable baseline; it does not establish general legal language understanding.

Amendment format:

```text
Amendment 1: Atlas
Parties: Atlas Customer Example LLC | Atlas Supplier Example LLC
Effective date: 2025-06-01
Amends agreement effective: 2025-01-01

In Section 1, replace "30 days" with "60 days".
```

Alternatively:

```text
Replace Section 1 with:
"Invoices are payable within 60 days."
```

Payloads must be single-line quoted text without embedded quotation marks. Operations execute in source order within an instrument. Effective date orders instruments; amendment numbering must be contiguous. An arbitrary document ID never establishes precedence between conflicting same-date changes. Add/delete/restatement, multi-line payloads, clause subtrees, aliases and implicit references are unsupported.

Review resolution keys are `sha256(canonical([source_document_id, target_section]))`. The supported resolution is `skip`; no reviewer-authored text is injected. Request a new snapshot with `resolutions`, then review and explicitly promote it with the expected active snapshot. The prior artifact remains accessible.

The skip key applies to all operations on that target section within the specified instrument. It is deliberately a group decision, not an operation-ordinal editor.

To mark an admitted source outside the supported/reviewed scope, supply `exclusions: {document_id: reason}` in a new snapshot request. Unknown or cross-tenant document IDs and empty reasons are rejected. Original bytes remain retained. The new artifact records the exclusion and aggregate coverage is explicitly partial; lookups report the scope exclusions. This is an auditable reviewer decision, not an automatic claim that an unsupported attachment is unrelated.
