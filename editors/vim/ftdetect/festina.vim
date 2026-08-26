" Festina source files use the .f extension (festina/lexer.py's own
" SOURCE_EXTENSION) -- the same extension vim's bundled
" $VIMRUNTIME/ftdetect/fortran.vim already claims for fixed-form
" Fortran. That built-in detection uses the soft `setf` (setfiletype),
" which only assigns a filetype if none is set yet -- so an
" unconditional `set filetype=festina` here always wins for a *.f
" buffer, regardless of whether this file happens to load before or
" after vim's own fortran.vim during :filetype detect.
"
" If you genuinely edit both Festina and real Fortran .f files in the
" same vim, set
"
"   let g:festina_disable_ftdetect = 1
"
" in your vimrc before this plugin loads, and assign the filetype
" yourself per buffer (a modeline, or your own autocmd matching a
" project directory).
if exists('g:festina_disable_ftdetect') && g:festina_disable_ftdetect
  finish
endif

augroup festina_ftdetect
  autocmd!
  autocmd BufRead,BufNewFile *.f set filetype=festina
augroup END
