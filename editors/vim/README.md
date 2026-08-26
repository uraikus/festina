# Festina support for Vim / Neovim

Syntax highlighting and basic editing settings for `.f` (Festina)
source files. Vim's own bundled `fortran.vim` claims the `.f`
extension by default; this plugin's `ftdetect` script takes it back
for Festina (see that file's own comment for exactly how, and how to
opt out if you also edit real Fortran `.f` files).

## What's included

- `ftdetect/festina.vim` -- recognizes `.f` files as Festina.
- `syntax/festina.vim` -- highlighting for keywords, types, strings,
  template literals (`` `${expr}` ``), regex literals (`/pattern/flags`),
  numbers, comments, and a representative set of built-in functions.
- `ftplugin/festina.vim` -- comment settings (`//`, `/* */`) and
  C-family indenting for Festina's `{ }`-delimited blocks.

## Install

**Vanilla Vim or Neovim, manual:**

```sh
mkdir -p ~/.vim/pack/festina/start
ln -s /path/to/festina/editors/vim ~/.vim/pack/festina/start/festina
```

(Neovim: use `~/.local/share/nvim/site/pack/festina/start/festina`
instead.) Restart your editor, or run `:packloadall | filetype detect`
in an already-open session.

**vim-plug:**

```vim
Plug '/path/to/festina', { 'rtp': 'editors/vim' }
```

**Neovim with lazy.nvim:**

```lua
{ dir = '/path/to/festina', config = function() vim.opt.rtp:append('/path/to/festina/editors/vim') end }
```

Any of these also work straight from a GitHub checkout URL in place
of a local path, the same way you'd install any other vim plugin.

## Verifying it worked

Open any `.f` file and run `:set filetype?` -- it should report
`filetype=festina`, and keywords/strings/comments should be colored
according to your colorscheme's usual `Keyword`/`Type`/`String`/
`Comment` groups (see `syntax/festina.vim`'s `hi def link` lines for
the full mapping).
