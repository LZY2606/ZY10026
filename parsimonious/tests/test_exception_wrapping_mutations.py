"""Automated mutation verification for the exception-wrapping logic.

Each test in this module temporarily rewrites the guard in
``NodeVisitor.visit`` (in ``parsimonious/nodes.py``) into one of three
plausible-but-wrong variants, runs the unwrapped-exceptions regression
matrix in a subprocess, and asserts that the mutation is caught by its own
dedicated assertions. The original source is always restored, and a final
test proves the git worktree carries no residue from the mutations.

The mutations under test:

* ``exact_type_match``: subclass-aware ``isinstance`` replaced by an exact
  ``type(exc) in ...`` comparison (the original bug);
* ``allow_all_exceptions``: every ``Exception`` allowed through unwrapped;
* ``rewrap_visitation_error``: already-wrapped ``VisitationError``
  instances wrapped a second time by outer ``visit`` frames.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NODES_PY = REPO_ROOT / 'parsimonious' / 'nodes.py'
REGRESSION_TESTS = 'parsimonious/tests/test_unwrapped_exceptions.py'

# Each mutation: (text to replace, replacement, test id substring that must
# fail). The signature tests are distinct per mutation, proving each
# mutation is caught by different assertions.
MUTATIONS = {
    'exact_type_match': (
        'if isinstance(exc, self.unwrapped_exceptions):',
        'if type(exc) in self.unwrapped_exceptions:',
        'test_one_level_subclass_propagates',
    ),
    'allow_all_exceptions': (
        'if isinstance(exc, self.unwrapped_exceptions):',
        'if True:  # MUTATION: let every Exception through unwrapped',
        'test_undeclared_sibling_is_wrapped',
    ),
    'rewrap_visitation_error': (
        'except (VisitationError, UndefinedLabel):',
        'except (UndefinedLabel,):  # MUTATION: re-wrap VisitationError',
        'test_visitation_error_not_double_wrapped',
    ),
}


def git_worktree_state():
    """Fingerprint the tracked worktree: modified files plus their diff."""
    status = subprocess.run(
        ['git', 'status', '--porcelain'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    diff = subprocess.run(
        ['git', 'diff'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return status, diff


# Snapshot taken at collection time, before any mutation has run.
BASELINE_GIT_STATE = git_worktree_state()


@pytest.mark.parametrize('mutation', sorted(MUTATIONS))
def test_mutation_is_caught_by_regression_matrix(mutation):
    original_source = NODES_PY.read_text()
    old, new, signature_test = MUTATIONS[mutation]
    assert old in original_source, (
        'mutation anchor %r not found; nodes.py drifted' % old)
    mutated = original_source.replace(old, new, 1)
    assert mutated != original_source
    try:
        NODES_PY.write_text(mutated)
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', REGRESSION_TESTS, '-q',
             '-p', 'no:cacheprovider'],
            cwd=REPO_ROOT, capture_output=True, text=True)
    finally:
        NODES_PY.write_text(original_source)

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        'mutation %r was NOT caught: regression matrix passed\n%s'
        % (mutation, output))
    assert signature_test in output, (
        'mutation %r was caught, but not by its dedicated assertions; '
        'expected %r among the failures:\n%s'
        % (mutation, signature_test, output))
    # The source must be byte-identical to what it was before the mutation:
    assert NODES_PY.read_text() == original_source


def test_git_worktree_has_no_mutation_residue():
    """The mutations are fully reverted: the worktree matches its baseline."""
    assert git_worktree_state() == BASELINE_GIT_STATE
