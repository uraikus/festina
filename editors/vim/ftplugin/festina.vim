" Filetype plugin for Festina.

if exists('b:did_ftplugin')
  finish
endif
let b:did_ftplugin = 1

let s:cpo_save = &cpo
set cpo&vim

" festina/lexer.py: `//` line comments, `/* */` block comments.
setlocal commentstring=//\ %s
setlocal comments=s1:/*,mid:*,ex:*/,://

" C-brace-family indenting is a reasonable default -- Festina's
" `{ }`-delimited blocks (if/else, for, while, try/catch, struct/table/
" func bodies) follow the same shape cindent already knows.
setlocal cindent
setlocal cinoptions=

setlocal formatoptions-=t
setlocal formatoptions+=croql

" `?`/`:` are ordinary operators (ternary), not word characters, so
" the default 'iskeyword' is left alone -- only noted here since some
" C-like ftplugins adjust it and Festina doesn't need to.

let b:undo_ftplugin = 'setlocal commentstring< comments< cindent< cinoptions< formatoptions<'

let &cpo = s:cpo_save
unlet s:cpo_save
