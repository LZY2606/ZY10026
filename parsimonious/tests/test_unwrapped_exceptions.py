"""Regression matrix for ``NodeVisitor.unwrapped_exceptions`` semantics.

Every test drives the public ``visit`` entry point (directly or through the
``parse`` shortcut) and exercises how exceptions raised by ``visit_*``
methods and ``generic_visit`` interact with the declared
``unwrapped_exceptions`` tuple:

* declared exceptions (including subclasses at any depth, diamond and
  multiple inheritance) must propagate untouched, with identity, custom
  attributes and the original traceback intact;
* undeclared exceptions must be wrapped exactly once in ``VisitationError``
  carrying the node, the parse tree and the original exception as
  ``__cause__``;
* declarations on one visitor class must not leak into another.
"""
import traceback

import pytest

from parsimonious import Grammar, NodeVisitor, VisitationError


GRAMMAR = Grammar(r'''
    entry   = name number
    name    = ~"[a-z]+"
    number  = ~"[0-9]+"
''')
TEXT = 'abc123'

#: Where the bomb goes off, per raise site.
RAISE_SITE = {
    'visit_method': ('visit_name', 'name'),
    'generic_visit': ('generic_visit', 'number'),
}


class BaseSemanticError(Exception):
    """Declared base class carrying custom fields, like a real caller's."""

    def __init__(self, message, *, token, line):
        super().__init__(message)
        self.token = token
        self.line = line


class MissingTypeError(BaseSemanticError):
    """One inheritance level below the declared base."""


class NameLookupError(BaseSemanticError):
    """A second declared base overlapping ``BaseSemanticError``."""


class MissingNameError(NameLookupError):
    """Two levels below ``BaseSemanticError``; matches two tuple entries."""


class DeclaredSemanticError(BaseSemanticError):
    """Declared directly, to give its sibling something to be undeclared next to."""


class UndeclaredSiblingError(BaseSemanticError):
    """Sibling of the declared class; never declared itself."""


class DiamondLeft(BaseSemanticError):
    pass


class DiamondRight(BaseSemanticError):
    pass


class DiamondError(DiamondLeft, DiamondRight):
    """Diamond / multiple inheritance exception below the declared base."""


def make_bomb(exc_class):
    return exc_class('semantic analysis failed', token='abc', line=7)


def make_visitor(declared_exceptions, bomb, site):
    """Build a visitor instance that raises ``bomb`` from the given site."""
    class BombingVisitor(NodeVisitor):
        grammar = GRAMMAR
        unwrapped_exceptions = declared_exceptions

        def __init__(self):
            self.raised_at = None

        def visit_entry(self, node, visited_children):
            return ('entry', visited_children)

        def visit_name(self, node, visited_children):
            if site == 'visit_method':
                self.raised_at = node
                raise bomb
            return node.text

        def generic_visit(self, node, visited_children):
            if site == 'generic_visit' and node.expr_name == 'number':
                self.raised_at = node
                raise bomb
            return node.text

    return BombingVisitor()


def assert_bomb_untouched(caught, bomb, site):
    """The exact same object must surface, fields and traceback included."""
    method_name, _ = RAISE_SITE[site]
    assert caught is bomb
    assert type(caught) is type(bomb)
    assert caught.token == 'abc'
    assert caught.line == 7
    assert str(caught) == 'semantic analysis failed'
    # Nothing was wrapped around it on the way out:
    assert caught.__cause__ is None
    assert caught.__context__ is None
    # The original raise point is the innermost traceback frame:
    frames = traceback.extract_tb(caught.__traceback__)
    assert frames[-1].name == method_name
    assert frames[-1].filename == __file__


def assert_bomb_wrapped(caught, bomb, visitor, site):
    """Undeclared exceptions get exactly one VisitationError layer."""
    method_name, node_name = RAISE_SITE[site]
    assert type(caught) is VisitationError
    assert caught.original_class is type(bomb)
    assert caught.__cause__ is bomb
    # Exactly one layer: the cause is the original, not another wrapper.
    assert not isinstance(caught.__cause__, VisitationError)
    # The node at which visitation failed is attached:
    assert caught.node is visitor.raised_at
    assert caught.node.expr_name == node_name
    # The parse tree is rendered into the message:
    assert 'Parse tree:' in str(caught)
    assert 'We were here' in str(caught)
    # The original raise point survives in the cause's traceback:
    frames = traceback.extract_tb(caught.__cause__.__traceback__)
    assert frames[-1].name == method_name
    assert frames[-1].filename == __file__


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_declared_class_itself_propagates(site):
    """Baseline: raising the declared class directly was always exempt."""
    bomb = make_bomb(BaseSemanticError)
    visitor = make_visitor((BaseSemanticError,), bomb, site)
    with pytest.raises(BaseSemanticError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_untouched(excinfo.value, bomb, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_one_level_subclass_propagates(site):
    """A direct subclass of the declared base must not be wrapped."""
    bomb = make_bomb(MissingTypeError)
    visitor = make_visitor((BaseSemanticError,), bomb, site)
    with pytest.raises(MissingTypeError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_untouched(excinfo.value, bomb, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_two_level_subclass_propagates(site):
    """A grandchild of the declared base must not be wrapped either."""
    bomb = make_bomb(MissingNameError)
    visitor = make_visitor((BaseSemanticError,), bomb, site)
    with pytest.raises(MissingNameError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_untouched(excinfo.value, bomb, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_diamond_subclass_propagates(site):
    """Diamond / multiple inheritance below the declared base is exempt."""
    bomb = make_bomb(DiamondError)
    visitor = make_visitor((BaseSemanticError,), bomb, site)
    with pytest.raises(DiamondError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_untouched(excinfo.value, bomb, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_undeclared_sibling_is_wrapped(site):
    """A sibling of the declared class is NOT covered by the declaration."""
    bomb = make_bomb(UndeclaredSiblingError)
    visitor = make_visitor((DeclaredSemanticError,), bomb, site)
    with pytest.raises(VisitationError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_wrapped(excinfo.value, bomb, visitor, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_overlapping_tuple_entries(site):
    """Tuple with a builtin plus two overlapping custom bases.

    ``MissingNameError`` matches both ``BaseSemanticError`` and
    ``NameLookupError``; it must still surface exactly once, unwrapped.
    """
    bomb = make_bomb(MissingNameError)
    visitor = make_visitor((ValueError, BaseSemanticError, NameLookupError),
                           bomb, site)
    with pytest.raises(MissingNameError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_untouched(excinfo.value, bomb, site)


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_declared_builtin_exception_propagates(site):
    """A builtin exception declared in the tuple is also exempt."""
    bomb = ValueError('bad value')
    visitor = make_visitor((ValueError, BaseSemanticError), bomb, site)
    with pytest.raises(ValueError) as excinfo:
        visitor.parse(TEXT)
    assert excinfo.value is bomb


@pytest.mark.parametrize('site', ['visit_method', 'generic_visit'])
def test_visitation_error_not_double_wrapped(site):
    """An undeclared exception raised deep in the tree is wrapped once.

    The wrapper must survive the outer ``visit`` frames untouched: its
    cause is the original exception, not another ``VisitationError``.
    """
    bomb = ValueError('deep failure')
    visitor = make_visitor((), bomb, site)
    with pytest.raises(VisitationError) as excinfo:
        visitor.parse(TEXT)
    assert_bomb_wrapped(excinfo.value, bomb, visitor, site)


def test_declarations_do_not_leak_between_visitor_classes():
    """Two visitor classes in one process keep independent declarations."""
    bomb = make_bomb(MissingNameError)

    class LenientVisitor(NodeVisitor):
        grammar = GRAMMAR
        unwrapped_exceptions = (BaseSemanticError,)

        def visit_name(self, node, visited_children):
            raise bomb

        def generic_visit(self, node, visited_children):
            return node.text

    class StrictVisitor(NodeVisitor):
        grammar = GRAMMAR
        unwrapped_exceptions = (KeyError,)

        def visit_name(self, node, visited_children):
            raise bomb

        def generic_visit(self, node, visited_children):
            return node.text

    class DefaultVisitor(NodeVisitor):
        grammar = GRAMMAR

        def visit_name(self, node, visited_children):
            raise bomb

        def generic_visit(self, node, visited_children):
            return node.text

    with pytest.raises(MissingNameError) as excinfo:
        LenientVisitor().parse(TEXT)
    assert excinfo.value is bomb

    for visitor_class in (StrictVisitor, DefaultVisitor):
        with pytest.raises(VisitationError) as excinfo:
            visitor_class().parse(TEXT)
        assert excinfo.value.__cause__ is bomb

    # The class attributes themselves are untouched and independent:
    assert LenientVisitor.unwrapped_exceptions == (BaseSemanticError,)
    assert StrictVisitor.unwrapped_exceptions == (KeyError,)
    assert DefaultVisitor.unwrapped_exceptions == ()
    assert NodeVisitor.unwrapped_exceptions == ()


def test_normal_traversal_order_and_results_unchanged():
    """Plain visitation still works: post-order calls, usual return values."""
    class OrderRecordingVisitor(NodeVisitor):
        grammar = GRAMMAR

        def __init__(self):
            self.calls = []

        def visit_entry(self, node, visited_children):
            self.calls.append('entry')
            return ('entry', visited_children)

        def visit_name(self, node, visited_children):
            self.calls.append('name')
            return node.text.upper()

        def generic_visit(self, node, visited_children):
            self.calls.append(node.expr_name)
            return node.text

    visitor = OrderRecordingVisitor()
    result = visitor.parse(TEXT)
    assert visitor.calls == ['name', 'number', 'entry']
    assert result == ('entry', ['ABC', '123'])
