import json
import logging

log = logging.getLogger('cocaine.tools')

# Token types.
#
# EOF (end-of-file) token is used to indicate that there is no more input left for lexical
# analysis.
LITERAL, FUNCTION, AND, OR, EQ, DOT, COMMA, LPAREN, RPAREN, EOF = (
    'LITERAL', 'FUNCTION', 'AND', 'OR', 'EQ', 'DOT', 'COMMA', '(', ')', 'EOF'
)

OPERATORS = {
    (COMMA, ','),
    (LPAREN, '('),
    (RPAREN, ')'),
    (AND, '&&'),
    (OR, '||'),
    (EQ, '=='),
    (FUNCTION, 'contains'),
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
        if not value.isalnum():
            raise SyntaxError('Invalid literal token')
        super(LiteralToken, self).__init__(LITERAL, value)


class AST(object):
    pass


class BinOp(AST):
    def __init__(self, op, left, right):
        self.token = self.op = op
        self.left = left
        self.right = right

    def __repr__(self):
        return 'BinOp(op: {}, left: {}, right: {})'.format(self.op, self.left, self.right)

    def visit(self):
        if self.op.value == '&&':
            op = 'and'
        elif self.op.value == '||':
            op = 'or'
        else:
            op = 'and'
        return {op: [self.left.visit(), self.right.visit()]}


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

    @staticmethod
    def error():
        raise Exception('Invalid syntax')

    def eat(self, token_type):
        if self.current_token.type == token_type:
            self.current_token = next(self.lexer)
            return self.current_token
        else:
            self.error()

    def term(self):
        token = self.current_token
        if token.type == FUNCTION:
            return self.func(token)
        elif token.type == LITERAL:
            args = [self.current_token]
            self.eat(LITERAL)
            args.append(self.eat(EQ))
            self.eat(LITERAL)
            return Func('eq', args)
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
            args.append(self.current_token)
            token = self.eat(LITERAL)
            if token.type == COMMA:
                self.eat(COMMA)
            elif token.type == RPAREN:
                self.eat(RPAREN)
                break
        return Func(name.value, args)

    def expr(self):
        """
        expr    ::= term ((&& | ||) term)*
        term    ::= func | eq | ne | LPAREN expr RPAREN
        func    ::= lit LPAREN lit (,lit)* RPAREN
        eq      ::= lit EQ lit
        ne      ::= lit NE lit
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
            node = BinOp(left=node, op=token, right=self.term())
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
    expr = Parser(tokenize(query)).expr()
    tree = expr.visit()
    log.debug('AST: %s', json.dumps(tree))
    return tree
