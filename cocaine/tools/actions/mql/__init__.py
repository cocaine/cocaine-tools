import json
import logging
import re

log = logging.getLogger('cocaine.tools')

# Token types.
#
# EOF (end-of-file) token is used to indicate that there is no more input left for lexical
# analysis.
LITERAL, NUMBER, FUNCTION, AND, OR, EQ, GE, DOT, COMMA, LPAREN, RPAREN, EOF = (
    'LITERAL',
    'NUMBER',
    'FUNCTION',
    'AND',
    'OR',
    'EQ',
    'GE',
    'DOT',
    'COMMA',
    'LPAREN',
    'RPAREN',
    'EOF',
)

OPERATORS = {
    (COMMA, ','),
    (LPAREN, '('),
    (RPAREN, ')'),
    (AND, '&&'),
    (OR, '||'),
    (EQ, '=='),
    (GE, '>='),
    (FUNCTION, 'contains'),
    (FUNCTION, 'name'),
    (FUNCTION, 'type'),
    (FUNCTION, 'tag'),
}


class Token(object):
    def __init__(self, ty, value):
        self.type = ty
        self.value = value

    def __str__(self):
        """
        String representation of the class instance.

        Examples:
            Token(LITERAL, name)
            Token(LPAREN, '(')
            Token(RPAREN, ')')
        """
        return 'Token({type}, {value})'.format(type=self.type, value=repr(self.value))

    def __repr__(self):
        return 'Token(type: {type}, value: {value})'.format(type=self.type, value=repr(self.value))


class LiteralToken(Token):
    def __init__(self, value):
        try:
            super(LiteralToken, self).__init__(NUMBER, float(value))
        except ValueError:
            if not re.match(r'(\w|\.)+', value):
                raise SyntaxError('Invalid literal token')
            super(LiteralToken, self).__init__(LITERAL, value)


class AST(object):
    pass


class Const(AST):
    def __init__(self, token):
        self.token = token
        self.value = token.value

    def visit(self):
        return {'const': [self.value]}

    def __repr__(self):
        return 'Num(value: {})'.format(self.value)


class Op(AST):
    def __init__(self, op, children):
        self.token = self.op = op
        self._children = children

        self._replace = {
            '&&': 'and',
            '||': 'or',
            '==': 'eq',
            '!=': 'ne',
            '<=': 'le',
            '>=': 'ge',
            '<': 'lt',
            '>': 'gt',
        }

    def __repr__(self):
        return 'Op(op: {}, children: {})'.format(self.op, self._children)

    def visit(self):
        return {self._replace.get(self.op.value, self.op.value): [c.visit() for c in self._children]}


class Func(AST):
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def __repr__(self):
        return 'Func(name: {}, args: {})'.format(self.name, self.args)

    def visit(self):
        return {self.name: [v.value for v in self.args]}


class Parser(object):
    def __init__(self, lexer):
        self.lexer = lexer
        self.current_token = next(self.lexer)
        log.debug('token: %s', self.current_token)

    @staticmethod
    def error():
        raise Exception('Invalid syntax')

    def eat(self, token_type):
        if self.current_token.type == token_type:
            self.current_token = next(self.lexer)
            log.debug('token: %s', self.current_token)
            return self.current_token
        else:
            self.error()

    def term(self):
        node = self.factor()

        while True:
            token = self.current_token
            if token.type == EQ:
                self.eat(EQ)
            elif token.type == GE:
                self.eat(GE)
            else:
                break
            node = Op(op=token, children=[node, self.factor()])
        return node

    def factor(self):
        token = self.current_token
        if token.type == FUNCTION:
            return self.func(token)
        elif token.type == NUMBER:
            self.eat(NUMBER)
            return Const(token)
        elif token.type == LITERAL:
            self.eat(LITERAL)
            return Const(token)
        elif token.type == LPAREN:
            self.eat(LPAREN)
            node = self.expr()
            self.eat(RPAREN)
            return node

    def func(self, name):
        self.eat(FUNCTION)
        self.eat(LPAREN)
        args = []
        while True:
            if self.current_token.type == RPAREN:
                self.eat(RPAREN)
                break
            args.append(self.expr())
            if self.current_token.type == COMMA:
                self.eat(COMMA)
            else:
                self.eat(RPAREN)
                break
        return Op(name, args)

    def expr(self):
        """
        expr    ::= term ((AND | OR) term)*
        term    ::= factor ((EQ | NE) factor)*
        factor  ::= func | const | LPAREN expr RPAREN
        func    ::= lit LPAREN expr (,expr)* RPAREN
        lit     ::= alphanum
        """
        node = self.term()

        while True:
            token = self.current_token
            if token.type == AND:
                self.eat(AND)
            elif token.type == OR:
                self.eat(OR)
            else:
                break
            node = Op(op=token, children=[node, self.term()])
        return node

    def parse(self):
        return self.expr()


def tokenize(query):
    query = query.replace(' ', '')

    idx = 0
    literal = ''
    while idx < len(query):
        for ty, op in OPERATORS:
            if query[idx: idx + len(op)] == op:
                if len(literal) > 0:
                    yield LiteralToken(literal)
                    literal = ''
                idx += len(op)
                yield Token(ty, op)
                break
        else:
            ch = query[idx]
            literal += ch
            idx += 1
    if len(literal) > 0:
        yield LiteralToken(literal)
    yield Token(EOF, None)


def compile_query(query):
    log.debug('tokens: %s', list(tokenize(query)))
    expr = Parser(tokenize(query)).expr()
    tree = expr.visit()
    log.debug('AST: %s', json.dumps(tree, indent=4))
    return tree
