# Changelog

## Unreleased

- Fix declared visitor exception handling so subclasses of entries in
  `NodeVisitor.unwrapped_exceptions` propagate without a
  `VisitationError` wrapper.
  - Visible symptom: declaring a base exception such as
    `BaseSemanticError` and raising a subclass such as
    `MissingNameError` forced callers to catch `VisitationError`; the
    original exception's custom fields were only reachable through the
    wrapper's cause. Raising the exact base class worked.
  - Root cause: visitation compared `type(exc)` against the declared
    tuple with exact membership instead of using inheritance-aware
    `isinstance` matching.
  - Compatibility boundary: only exceptions whose type is listed in
    `unwrapped_exceptions` or a subclass thereof propagate directly.
    Undeclared exceptions remain wrapped with the failing node, parse
    tree, original traceback, and exception cause; existing
    `VisitationError` and `UndefinedLabel` instances still are not
    rewrapped.
  - Test gap: the old tests raised only the exact declared class and a
    separately declared built-in exception. They did not combine
    subclass or multiple-inheritance exceptions with visitor-level
    declarations, undeclared sibling types, concrete and generic visit
    methods, or independent visitor configurations in one process.
