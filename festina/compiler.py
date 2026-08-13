"""High-level compile entry point -- claude.md #7, #8.

This only drives the front end (parse + semantic analysis) and names the
synthesized entry function; LLVM IR generation and native linking
(claude.md #47) are not implemented here.
"""
from . import parser as parser_mod
from . import semantic as semantic_mod

#: claude.md #7: "The exact internal name is implementation-defined."
ENTRY_FUNCTION_NAME = "__festina_main"


class CompileResult:
    def __init__(self, ast, analyzed, entry_function_name):
        self.ast = ast
        self.symbols = analyzed.symbols
        self.tables = analyzed.tables
        self.structs = analyzed.structs
        self.imports = analyzed.imports
        self.entry_function_name = entry_function_name


def compile_source(source, filename="main.f"):
    program = parser_mod.parse(source, filename=filename)
    analyzed = semantic_mod.analyze(program, filename=filename)
    return CompileResult(program, analyzed, ENTRY_FUNCTION_NAME)
