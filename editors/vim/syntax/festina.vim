" Vim syntax file for Festina (https://github.com/uraikus/festina)
" Language:     Festina
" Maintainer:   Festina project
"
" Grammar reference: festina/lexer.py (KEYWORDS, TOKEN_SPEC, regex
" literal handling) and festina/types.py (built-in type names).
"
" Ordering note: for two `:syn match`/`:syn region` items that can
" both start at the same buffer position, vim gives the LATER
" definition priority, regardless of which one matches more text (see
" `:help :syn-priority`). Everything below is deliberately ordered
" low-priority-first so that strings/comments/regex literals -- which
" must always win over a bare operator character or a keyword-like
" word appearing inside them -- are defined last.

if exists('b:current_syntax')
  finish
endif

let s:cpo_save = &cpo
set cpo&vim

syn case match

" ---- Booleans / null ----
syn keyword festinaBoolean true false
syn keyword festinaNull null

" ---- Types (festina/types.py's PrimitiveType/ImageType/AudioType/...
" plus the contextual type names -- color/font/url/regex -- that are
" ordinary identifiers to the lexer but resolved as types by
" semantic.py's resolve_type_name). ----
syn keyword festinaType int float bool text blob img aud arr map func
syn keyword festinaType color font url regex

" ---- Declaration keywords that introduce a named type (like C's
" struct/union/enum) -- highlighted as Structure, with the declared
" name itself picked up as a Type for readability. ----
syn keyword festinaStructure struct table enum nextgroup=festinaTypeName skipwhite
syn match   festinaTypeName "\<[A-Za-z_][A-Za-z0-9_]*\>" contained

" ---- Control flow ----
syn keyword festinaConditional if else
syn keyword festinaRepeat for while
syn keyword festinaException try catch throw fail

" ---- Other statement/declaration keywords ----
syn keyword festinaKeyword const var let return break continue on
syn keyword festinaKeyword free delete typeof amor
syn keyword festinaInclude import

" ---- `func` declarations: `<type> func name(...)` / `void func
" name(...)`. `func` itself is already festinaType above (it also
" doubles as the func[T]:U type keyword); this only picks out the
" declared name right after it. ----
syn match festinaFuncName "\<func\s\+\zs[A-Za-z_][A-Za-z0-9_]*\ze\s*("

" ---- A representative set of built-in global functions
" (festina/semantic.py's _BUILTIN_RETURN_TYPES/_BUILTIN_SIGNATURES and
" friends) -- not exhaustive, but covers the ones written in ordinary
" code often enough to be worth their own colour. ----
syn keyword festinaBuiltin log sqlite exec now formatTime mkdir ls
syn keyword festinaBuiltin parseURL close
syn keyword festinaBuiltin setTimeout setInterval clearTimeout clearInterval
syn keyword festinaBuiltin openPort closePort openSecurePort
syn keyword festinaBuiltin render saveCanvas clearCanvas clearRect
syn keyword festinaBuiltin drawRect drawPixel drawCircle drawText drawImage
syn keyword festinaBuiltin clearPixel clearCircle
syn keyword festinaBuiltin beginPath moveTo lineTo curveTo closePath
syn keyword festinaBuiltin fillPath strokePath
syn keyword festinaBuiltin translate rotate scale resetTransform
syn keyword festinaBuiltin saveState restoreState fillAlpha
syn keyword festinaBuiltin fillLinearGradient fillRadialGradient
syn keyword festinaBuiltin lineWidth measureTextWidth measureTextHeight
syn keyword festinaBuiltin setMaxAudioPlayers maxAudioPlayers isAudioPlayerPlaying
syn keyword festinaBuiltin setClientWidth setClientHeight

" ---- Read-only global identifiers and the Math namespace ----
syn keyword festinaBuiltin screenWidth screenHeight clientWidth clientHeight
syn keyword festinaBuiltin Math

" ---- Numbers (festina/lexer.py: `\d+\.\d+` or `\d+`, no hex/exponent) ----
syn match festinaNumber "\<\d\+\%(\.\d\+\)\?\>"

" ---- Operators/punctuation (festina/lexer.py's OP token) ----
syn match festinaOperator "===\|!==\|==\|!=\|<=\|>=\|=>\|&&\|||\|++\|--"
syn match festinaOperator "[+\-*/%=<>!?:]"
syn match festinaDelimiter "[.,;]"

" ---- Everything below wins over the groups above when they overlap
" at the same starting column (comments over a bare '/', a string's
" own contents over any keyword spelled out inside it, ...). ----

syn keyword festinaTodo contained TODO FIXME XXX NOTE

" ---- String escapes (festina/lexer.py: _ESCAPES) ----
syn match festinaEscape "\\[ntr\\'\"`0]" contained

" ---- Plain strings: 'single' or "double" quoted ----
syn region festinaString start=+'+ skip=+\\.+ end=+'+ contains=festinaEscape,@Spell
syn region festinaString start=+"+ skip=+\\.+ end=+"+ contains=festinaEscape,@Spell

" ---- Template literals: `...${expr}...` (festina/lexer.py: TEMPLATE,
" _split_template). `${...}` interpolates an arbitrary expression, so
" its contents reuse every top-level syntax group via `contains=TOP`
" -- the same trick vim's own javascript/typescript syntax files use
" for identical template-literal interpolation. This region's own
" `end=+}+` isn't brace-depth-aware, so an interpolation containing a
" nested `{...}` (a struct/map literal) can end early -- an accepted
" cosmetic limitation shared with those same javascript/typescript
" files, not a functional one. ----
syn region festinaTemplateString matchgroup=festinaString start=+`+ skip=+\\.+ end=+`+ contains=festinaEscape,festinaInterp,@Spell
syn region festinaInterp matchgroup=festinaInterpDelim start=+\${+ end=+}+ contained contains=TOP

" ---- Regex literals: /pattern/flags (festina/lexer.py:
" _try_lex_regex_literal). The lexer disambiguates a leading '/' from
" division using the *previous token* -- vim's regex engine can't
" replay that, so this uses a lookbehind approximating the same rule:
" a '/' cannot open a regex literal right after something that could
" itself end an expression (a name, a number, a string, `)`, `]`,
" `++`/`--`). Good enough for real code; an edge case built specifically
" to defeat it (rare in practice) may still highlight as division. ----
syn match festinaRegex "\%(\%(\w\|[)\]]\|++\|--\)\s*\)\@100<!/\%(\\.\|[^/\\\n]\)\+/[a-zA-Z]*"

" ---- Comments (festina/lexer.py: `//...` and `/* ... */`). Defined
" LAST so a comment always wins over a Regex/Operator/Number/keyword
" match that could otherwise fire on the same '/' -- see the ordering
" note at the top of this file. ----
syn match   festinaLineComment  "//.*$" contains=festinaTodo,@Spell
syn region  festinaBlockComment start="/\*" end="\*/" contains=festinaTodo,@Spell

syn sync fromstart

hi def link festinaLineComment      Comment
hi def link festinaBlockComment     Comment
hi def link festinaTodo             Todo
hi def link festinaNumber           Number
hi def link festinaString           String
hi def link festinaTemplateString   String
hi def link festinaInterpDelim      Special
hi def link festinaEscape           SpecialChar
hi def link festinaRegex            String
hi def link festinaBoolean          Boolean
hi def link festinaNull             Constant
hi def link festinaType             Type
hi def link festinaStructure        Structure
hi def link festinaTypeName         Type
hi def link festinaConditional      Conditional
hi def link festinaRepeat           Repeat
hi def link festinaException        Exception
hi def link festinaKeyword          Keyword
hi def link festinaInclude          Include
hi def link festinaFuncName         Function
hi def link festinaBuiltin          Function
hi def link festinaOperator         Operator
hi def link festinaDelimiter        Delimiter

let b:current_syntax = 'festina'

let &cpo = s:cpo_save
unlet s:cpo_save
