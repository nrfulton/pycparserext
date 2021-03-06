from pycparser.c_lexer import CLexer as CLexerBase
from ply.lex import TOKEN




class GNUCLexer(CLexerBase):
    # support '3i' for imaginary literal
    floating_constant = '(((('+CLexerBase.fractional_constant+')'+CLexerBase.exponent_part+'?)|([0-9]+'+CLexerBase.exponent_part+'))i?[FfLl]?)'

    @TOKEN(floating_constant)
    def t_FLOAT_CONST(self, t):
        return t


class OpenCLCLexer(CLexerBase):
    tokens = CLexerBase.tokens + ('LINECOMMENT',)
    states = (
            #('comment', 'exclusive'),
            #('preproc', 'exclusive'),
            ('ppline', 'exclusive'), # unused
            )

    def t_LINECOMMENT(self, t):
        r'\/\/([^\n]+)\n'
        t.lexer.lineno += t.value.count("\n")

    # overrides pycparser, must have same name
    def t_PPHASH(self, t):
        r'[ \t]*\#([^\n]|\\\n)+[^\n\\]\n'
        t.lexer.lineno += t.value.count("\n")
        return t




def add_lexer_keywords(cls, keywords):
    cls.keywords = cls.keywords + tuple(
            kw.upper() for kw in keywords)

    cls.keyword_map = cls.keyword_map.copy()
    cls.keyword_map.update(dict(
        (kw, kw.upper()) for kw in keywords))

    cls.tokens = cls.tokens + tuple(
            kw.upper() for kw in keywords)

add_lexer_keywords(GNUCLexer, [
    '__attribute__', '__asm__', '__asm', '__typeof__',
    '__real__', '__imag__', '__builtin_types_compatible_p',
    '__const', '__restrict', '__inline', '__inline__',
    '__extension__'])

_CL_KEYWORDS = ['kernel', 'constant', 'global', 'local', 'private',
        "read_only", "write_only", "read_write"]
add_lexer_keywords(OpenCLCLexer, [
    '__attribute__', '__asm__', '__asm']
    + _CL_KEYWORDS + ["__"+kw for kw in _CL_KEYWORDS])

# vim: fdm=marker
