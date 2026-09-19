# Changelog

## Unreleased

### Fixed: `unwrapped_exceptions` now honors inheritance

**Visible symptom.** A visitor declaring a base class in
`unwrapped_exceptions` (e.g. `unwrapped_exceptions = (BaseSemanticError,)`)
only exempted that exact class from `VisitationError` wrapping. Raising a
*subclass* (e.g. `MissingNameError(BaseSemanticError)`) from a `visit_*`
method or `generic_visit` was wrapped in `VisitationError` anyway, so
callers catching `BaseSemanticError` — and reading its custom fields —
silently stopped seeing their exceptions. Raising the declared class
itself worked, which made the bug easy to miss.

**Root cause.** `NodeVisitor.visit` consulted the declaration with an
exact-type membership test, `type(exc) in self.unwrapped_exceptions`,
instead of a subclass-aware check. The guard is now
`isinstance(exc, self.unwrapped_exceptions)`, so a declared class covers
its whole hierarchy (one level, two levels, diamond and multiple
inheritance alike), and an exception matching several tuple entries still
propagates exactly once, unwrapped, with its identity, custom attributes
and original traceback intact.

**Compatibility boundaries.**

- Undeclared exceptions are still wrapped in `VisitationError`, exactly
  once, preserving the failing `node` (now also exposed as
  `VisitationError.node`), the rendered parse tree in the message, the
  original exception as `__cause__`, and the original traceback. Nothing
  was added to the default exemption list: `Exception` as a whole is not
  let through, and already-wrapped `VisitationError`/`UndefinedLabel`
  instances are still never re-wrapped by outer `visit` frames.
- `unwrapped_exceptions` remains a per-class attribute; declarations on
  one visitor subclass never leak into sibling subclasses or into
  `NodeVisitor` itself.
- Visit order (depth-first, children before the parent's method), return
  values, and normal traversal behavior are unchanged.

**Why the old tests missed it.** The pre-existing
`test_unwrapped_exceptions` declared `PrimalScream` and raised
`PrimalScream` itself — the exact class object — so the broken
`type(exc) in ...` check and the correct `isinstance` check agree, and
likewise for the `NotImplementedError` cases raised by `generic_visit`.
No test ever raised a *subclass* of a declared type, so the
exact-type/subclass divergence was unobservable. The new regression
matrix in `parsimonious/tests/test_unwrapped_exceptions.py` drives the
public `visit` entry point across inheritance depth, diamond hierarchies,
undeclared siblings, overlapping tuple entries, both raise sites, and
cross-class declaration leakage; `test_exception_wrapping_mutations.py`
re-introduces the exact-type check (and two neighboring mutations) in a
subprocess to prove the matrix catches each of them, then verifies the
worktree is left clean.
