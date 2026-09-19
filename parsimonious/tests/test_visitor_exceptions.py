import inspect
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

from parsimonious import Grammar, NodeVisitor, VisitationError


MATRIX_GRAMMAR = Grammar(r'''
    root = leaf ""
    leaf = "leaf"
''')


class BaseSemanticError(Exception):
    def __init__(self, message, name, code):
        super().__init__(message)
        self.name = name
        self.code = code


class MissingNameError(BaseSemanticError):
    pass


class DeepMissingNameError(MissingNameError):
    pass


class SiblingSemanticError(BaseSemanticError):
    pass


class DiamondLeft(BaseSemanticError):
    pass


class DiamondRight(BaseSemanticError):
    pass


class DiamondError(DiamondLeft, DiamondRight):
    pass


class BaseRaisingVisitor(NodeVisitor):
    grammar = MATRIX_GRAMMAR

    def __init__(self, exception_class, unwrapped_exceptions,
                 from_generic=False):
        self.exception_class = exception_class
        self.unwrapped_exceptions = unwrapped_exceptions
        self.from_generic = from_generic

    def visit_root(self, node, visited_children):
        return visited_children[0]

    def generic_visit(self, node, visited_children):
        if not self.from_generic or node.expr_name != 'leaf':
            return super().generic_visit(node, visited_children)
        error = self.make_error()
        raise error

    def make_error(self):
        if issubclass(self.exception_class, BaseSemanticError):
            return self.exception_class(
                'name is missing', name='answer', code='SEM-42')
        return self.exception_class('runtime failure')


class ConcreteRaisingVisitor(BaseRaisingVisitor):
    def visit_leaf(self, node, visited_children):
        error = self.make_error()
        raise error


class GenericRaisingVisitor(BaseRaisingVisitor):
    pass


class ExceptionCase:
    def __init__(self, case_id, unwrapped_exceptions, exception_class,
                 should_propagate, custom_fields=False):
        self.case_id = case_id
        self.unwrapped_exceptions = unwrapped_exceptions
        self.exception_class = exception_class
        self.should_propagate = should_propagate
        self.custom_fields = custom_fields


EXCEPTION_CASES = [
    ExceptionCase(
        'one_level_subclass',
        (BaseSemanticError,),
        MissingNameError,
        True,
        True,
    ),
    ExceptionCase(
        'two_level_subclass',
        (BaseSemanticError,),
        DeepMissingNameError,
        True,
        True,
    ),
    ExceptionCase(
        'diamond_multiple_matches',
        (RuntimeError, DiamondLeft, DiamondRight),
        DiamondError,
        True,
        True,
    ),
    ExceptionCase(
        'builtin_with_overlapping_bases',
        (RuntimeError, DiamondLeft, DiamondRight),
        RuntimeError,
        True,
    ),
    ExceptionCase(
        'undeclared_sibling',
        (MissingNameError,),
        SiblingSemanticError,
        False,
        True,
    ),
]


def raise_location(method):
    source_lines, first_line = inspect.getsourcelines(method)
    for offset, line in enumerate(source_lines):
        if 'raise error' in line:
            return first_line + offset
    raise AssertionError('Could not locate regression raise statement.')


def original_frame(error, method_name, expected_line):
    for frame, line_number in traceback.walk_tb(error.__traceback__):
        if (frame.f_code.co_name == method_name and
                frame.f_globals.get('__file__') == __file__ and
                line_number == expected_line):
            return frame
    raise AssertionError('Original traceback raise point was not preserved.')


def assert_custom_semantic_fields(error):
    assert error.name == 'answer'
    assert error.code == 'SEM-42'
    assert str(error) == 'name is missing'


def frames_named(error, frame_name):
    return [frame for frame, _ in traceback.walk_tb(error.__traceback__)
            if frame.f_code.co_name == frame_name]


@pytest.mark.parametrize('raise_path',
                         ['concrete_visit_method', 'generic_visit'])
@pytest.mark.parametrize('case', EXCEPTION_CASES,
                         ids=lambda case: case.case_id)
def test_unwrapped_exception_regression_matrix(case, raise_path):
    tree = MATRIX_GRAMMAR.parse('leaf')
    leaf = tree.children[0]
    visitor_class = (GenericRaisingVisitor if raise_path == 'generic_visit'
                     else ConcreteRaisingVisitor)
    visitor = visitor_class(
        case.exception_class,
        case.unwrapped_exceptions,
        from_generic=raise_path == 'generic_visit',
    )
    method_name = ('generic_visit' if visitor.from_generic
                   else 'visit_leaf')
    method_owner = (BaseRaisingVisitor if visitor.from_generic
                    else ConcreteRaisingVisitor)
    expected_line = raise_location(getattr(method_owner, method_name))

    if case.should_propagate:
        with pytest.raises(case.exception_class) as caught:
            visitor.visit(tree)

        error = caught.value
        assert type(error) is case.exception_class
        assert error.__cause__ is None
        assert len(frames_named(error, method_name)) == 1
        frame = original_frame(error, method_name, expected_line)
        assert frame.f_locals['error'] is error
        if case.custom_fields:
            assert isinstance(error, BaseSemanticError)
            assert_custom_semantic_fields(error)
        return

    with pytest.raises(VisitationError) as caught:
        visitor.visit(tree)

    wrapped = caught.value
    original = wrapped.__cause__
    assert type(wrapped) is VisitationError
    assert wrapped.node is leaf
    assert wrapped.original_class is case.exception_class
    assert leaf.prettily(error=leaf) in str(wrapped)
    assert 'Parse tree:' in str(wrapped)
    assert type(original) is case.exception_class
    assert not isinstance(original, VisitationError)
    frame = original_frame(original, method_name, expected_line)
    assert frame.f_locals['error'] is original
    if case.custom_fields:
        assert_custom_semantic_fields(original)


def test_unwrapped_exceptions_do_not_leak_between_visitor_classes():
    class FirstVisitor(ConcreteRaisingVisitor):
        unwrapped_exceptions = (BaseSemanticError,)

        def __init__(self):
            super().__init__(MissingNameError, self.unwrapped_exceptions)

    class SecondVisitor(ConcreteRaisingVisitor):
        unwrapped_exceptions = ()

        def __init__(self):
            super().__init__(MissingNameError, self.unwrapped_exceptions)

    with pytest.raises(MissingNameError) as first_error:
        FirstVisitor().visit(MATRIX_GRAMMAR.parse('leaf'))
    assert type(first_error.value) is MissingNameError

    with pytest.raises(VisitationError) as second_error:
        SecondVisitor().visit(MATRIX_GRAMMAR.parse('leaf'))
    assert type(second_error.value.__cause__) is MissingNameError


def test_visit_order_and_return_values_remain_unchanged():
    grammar = Grammar(r'''
        root = left right
        left = "L"
        right = "R"
    ''')

    class OrderVisitor(NodeVisitor):
        def __init__(self):
            self.visited = []

        def visit_root(self, node, visited_children):
            self.visited.append(node.expr_name)
            return tuple(visited_children)

        def visit_left(self, node, visited_children):
            self.visited.append(node.expr_name)
            return node.text

        def visit_right(self, node, visited_children):
            self.visited.append(node.expr_name)
            return node.text

    visitor = OrderVisitor()

    assert visitor.visit(grammar.parse('LR')) == ('L', 'R')
    assert visitor.visited == ['left', 'right', 'root']


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODES_SOURCE = REPOSITORY_ROOT / 'parsimonious' / 'nodes.py'

EXACT_TYPE_MUTATION = (
    'exact_type_check',
    'if isinstance(exc, self.unwrapped_exceptions):',
    'if type(exc) in self.unwrapped_exceptions:',
)
ALLOW_ALL_EXCEPTIONS_MUTATION = (
    'allow_all_exceptions',
    'if isinstance(exc, self.unwrapped_exceptions):',
    'if True:',
)
REWRITE_VISITATION_ERROR_MUTATION = (
    'rewrap_visitation_error',
    'except (VisitationError, UndefinedLabel):',
    'except UndefinedLabel:',
)

EXACT_TYPE_PROBE = r'''
from parsimonious import Grammar, NodeVisitor, VisitationError


class BaseSemanticError(Exception):
    pass


class MissingNameError(BaseSemanticError):
    pass


class Visitor(NodeVisitor):
    grammar = Grammar('root = leaf ""\nleaf = "leaf"')
    unwrapped_exceptions = (BaseSemanticError,)

    def visit_root(self, node, visited_children):
        return visited_children

    def generic_visit(self, node, visited_children):
        return visited_children

    def visit_leaf(self, node, visited_children):
        raise MissingNameError()


try:
    Visitor().parse('leaf')
except BaseSemanticError:
    print('declared subclass propagated')
except VisitationError as error:
    assert type(error.__cause__) is MissingNameError
    raise AssertionError('mutation exact type guard caught regression')
else:
    raise AssertionError('declared subclass was not propagated')
'''

ALLOW_ALL_EXCEPTIONS_PROBE = r'''
from parsimonious import Grammar, NodeVisitor, VisitationError


class Visitor(NodeVisitor):
    grammar = Grammar('root = leaf ""\nleaf = "leaf"')

    def visit_leaf(self, node, visited_children):
        raise ValueError('undeclared failure')


tree = Visitor.grammar.parse('leaf')
try:
    Visitor().visit(tree)
except VisitationError as error:
    assert error.node is tree.children[0]
    assert isinstance(error.__cause__, ValueError)
    print('undeclared exception wrapped once')
except Exception as error:
    assert type(error) is ValueError
    raise AssertionError('mutation allow-all guard caught regression')
else:
    raise AssertionError('undeclared exception was not wrapped')
'''

REWRITE_VISITATION_ERROR_PROBE = r'''
from parsimonious import Grammar, NodeVisitor, VisitationError


class Visitor(NodeVisitor):
    grammar = Grammar('outer = root ""\nroot = leaf ""\nleaf = "leaf"')

    def visit_leaf(self, node, visited_children):
        raise ValueError('undeclared failure')

    def generic_visit(self, node, visited_children):
        return visited_children


tree = Visitor.grammar.parse('leaf')
try:
    Visitor().visit(tree)
except VisitationError as error:
    assert type(error) is VisitationError
    assert type(error.__cause__) is ValueError, (
        'mutation rewrap guard caught regression')
    assert not isinstance(error.__cause__, VisitationError)
    print('VisitationError was not rewrapped')
else:
    raise AssertionError('undeclared exception was not wrapped')
'''


def git_status():
    result = subprocess.run(
        ['git', 'status', '--short'],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def run_mutation_guard(mutation, probe, expected_output):
    original_source = NODES_SOURCE.read_text()
    status_before = git_status()
    stage_name, old_source, mutated_source = mutation

    try:
        assert original_source.count(old_source) == 1
        NODES_SOURCE.write_text(
            original_source.replace(old_source, mutated_source, 1))
        result = subprocess.run(
            [sys.executable, '-c', probe],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
        )
    finally:
        NODES_SOURCE.write_text(original_source)

    assert result.returncode != 0, (
        'Mutation stage {stage} escaped regression coverage:\n{output}'.format(
            stage=stage_name, output=result.stdout))
    assert expected_output in result.stderr
    assert NODES_SOURCE.read_text() == original_source
    assert git_status() == status_before


def test_mutation_guard_exact_type_check():
    run_mutation_guard(
        EXACT_TYPE_MUTATION,
        EXACT_TYPE_PROBE,
        'mutation exact type guard caught regression',
    )


def test_mutation_guard_allow_all_exceptions():
    run_mutation_guard(
        ALLOW_ALL_EXCEPTIONS_MUTATION,
        ALLOW_ALL_EXCEPTIONS_PROBE,
        'mutation allow-all guard caught regression',
    )


def test_mutation_guard_rewrap_visitation_error():
    run_mutation_guard(
        REWRITE_VISITATION_ERROR_MUTATION,
        REWRITE_VISITATION_ERROR_PROBE,
        'mutation rewrap guard caught regression',
    )
