" Filetype plugin for Festina.

if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

let s:cpo_save = &cpo
set cpo&vim

" festina/lexer.py: `//` line comments, `/* */` block comments. This
" is exactly the leading segment of 'comments' own DEFAULT value
" (`:help 'comments'`: "s1:/*,mb:*,ex:*/,://,b:#,..."), trimmed to
" just the two forms Festina actually has -- s/m/e mark the three
" pieces of a /* ... */ block, and the trailing `://` is the separate,
" single-line // form. An earlier version of this line used "mid:*"
" (English-ish, but not a real flag combination) instead of "mb:*",
" which errored loudly (E539) on every single file this ftplugin ever
" loaded -- confirmed directly by opening real files headless and
" checking v:errmsg, not just by re-reading the option string.
setlocal commentstring=//\ %s
setlocal comments=s1:/*,mb:*,ex:*/,://

" 4-space indentation, no tabs -- the convention every .f file in this
" repository (examples/, tests/stress/) actually uses; confirmed zero
" literal tab characters across all of them.
setlocal expandtab
setlocal shiftwidth=4
setlocal softtabstop=4
setlocal tabstop=4

" NOT 'cindent'. Verified directly (not just by inspection) that it
" mis-indents real Festina code, badly and cumulatively -- e.g.
" reindenting examples/tic_tac_toe.f with `gg=G` under 'cindent'
" produces indentation that drifts deeper with almost every following
" line, never recovering. 'cindent' assumes C's own conventions
" (statement-terminating `;`, `:` only ever a label/case/ternary in
" contexts it specifically special-cases) -- Festina has neither: no
" statement terminator at all, and `:` is *routine* here, in a
" parameter/field type annotation (`name:type`) as often as in a
" ternary, which is exactly the shape that confuses cindent's own
" label-detection heuristics. Vim's own built-in fallback indenter
" (used by the `=` operator when no 'indentexpr' is set, regardless of
" 'cindent') has the identical problem and is not Festina-specific --
" confirmed by reproducing the same drift with NO filetype plugin
" loaded at all. 'autoindent' sidesteps this entirely: a new line
" always simply copies the indent already on the line above it, never
" trying to be clever about braces or colons, so it cannot drift no
" matter what the surrounding code contains -- verified directly by
" simulating realistic line-by-line typing through a comment block
" into a run of sibling statements, which stayed correctly and
" uniformly indented throughout.
setlocal autoindent

setlocal formatoptions-=t
setlocal formatoptions+=croql

" `?`/`:` are ordinary operators (ternary), not word characters, so
" the default 'iskeyword' is left alone -- only noted here since some
" C-like ftplugins adjust it and Festina doesn't need to.

let b:undo_ftplugin = 'setlocal commentstring< comments< expandtab< shiftwidth< softtabstop< tabstop< autoindent< formatoptions<'

let &cpo = s:cpo_save
unlet s:cpo_save
