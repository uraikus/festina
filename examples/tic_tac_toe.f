// A simple two-player tic-tac-toe game -- click a cell to place a mark,
// players alternate X/O, first three in a row/column/diagonal wins.
// Demonstrates claude.md #37/#39/#40 (graphics, mouse events, on close),
// global mutable state shared between top-level code and event handlers
// (arr[int]/int/bool declared here, read and written from `on mouseDown`),
// arrays, functions, and control flow together in one program.
//
// Build and run with:
//
//   ./bin/festina examples/tic_tac_toe.f -o tic_tac_toe
//   ./tic_tac_toe
//
// A note on this engine's drawing model, since it shapes how this game
// is written: every draw call paints in solid black and there is no
// "clear"/erase function (claude.md #39's own examples never take a
// color argument -- see api.md's Graphics section) -- so this game is
// deliberately "draw-only, never erase": marks accumulate on the board
// exactly like a real pen-and-paper game of tic-tac-toe would, and
// nothing here ever needs to undraw a previous frame the way a
// scrolling or bouncing animation would.

const int GRID_X = 250
const int GRID_Y = 150
const int CELL = 100

// 0 = empty, 1 = X, 2 = O -- global, so both the click handler below and
// the helper functions read/write the same board every game keeps state
// in, exactly like a top-level `table`'s data would, just in memory
// instead of festina.sqlite.
arr[int] board = [0, 0, 0, 0, 0, 0, 0, 0, 0]
int turn = 1
bool gameOver = false

void func drawGrid() {
    // Two internal vertical lines and two internal horizontal lines
    // turn the 3x3 area into a 9-cell grid -- drawn as thin rectangles,
    // since there's no dedicated line-drawing function.
    drawRect(GRID_X + CELL, GRID_Y, 2, CELL * 3)
    drawRect(GRID_X + CELL * 2, GRID_Y, 2, CELL * 3)
    drawRect(GRID_X, GRID_Y + CELL, CELL * 3, 2)
    drawRect(GRID_X, GRID_Y + CELL * 2, CELL * 3, 2)
}

void func drawMark(cellIndex:int, player:int) {
    // claude.md #143: / always returns float now -- Math.floor is the
    // way back to the int grid coordinate this genuinely needs.
    int row = Math.floor(cellIndex / 3)
    int col = cellIndex % 3
    int x = GRID_X + col * CELL + 35
    int y = GRID_Y + row * CELL + 60
    text mark = player == 1 ? 'X' : 'O'
    drawText(mark, x, y)
}

bool func checkWin(player:int) {
    // The eight winning lines on a 3x3 board, spelled out directly
    // rather than looped over an array of index triples -- clearer to
    // read for a board this small, and sidesteps needing an
    // arr[arr[int]] of constant line definitions just for this.
    if board[0] == player && board[1] == player && board[2] == player { return true }
    if board[3] == player && board[4] == player && board[5] == player { return true }
    if board[6] == player && board[7] == player && board[8] == player { return true }
    if board[0] == player && board[3] == player && board[6] == player { return true }
    if board[1] == player && board[4] == player && board[7] == player { return true }
    if board[2] == player && board[5] == player && board[8] == player { return true }
    if board[0] == player && board[4] == player && board[8] == player { return true }
    if board[2] == player && board[4] == player && board[6] == player { return true }
    return false
}

bool func boardFull() {
    for int i = 0, i < 9, i++ {
        if board[i] == 0 {
            return false
        }
    }
    return true
}

// claude.md #106: `on mouseDown` is where `on click` used to be -- it
// fires on the press, which is when this game has always placed a mark.
// A board game wants the press; something draggable would want the
// matching `on mouseUp` too.
on mouseDown(x:int, y:int) {
    if gameOver {
        return
    }
    if x < GRID_X || x >= GRID_X + CELL * 3 || y < GRID_Y || y >= GRID_Y + CELL * 3 {
        return  // clicked outside the grid -- ignore
    }

    // claude.md #143: / always returns float now -- Math.floor is the
    // way back to the int grid coordinate this genuinely needs.
    int col = Math.floor((x - GRID_X) / CELL)
    int row = Math.floor((y - GRID_Y) / CELL)
    int index = row * 3 + col

    if board[index] != 0 {
        return  // cell already taken
    }

    board[index] = turn
    drawMark(index, turn)

    if checkWin(turn) {
        gameOver = true
        text winner = turn == 1 ? 'X' : 'O'
        log(`${winner} wins!`)
        drawText(`${winner} wins!`, GRID_X, GRID_Y + CELL * 3 + 40)
        render()
        return
    }

    if boardFull() {
        gameOver = true
        log('draw')
        drawText('draw!', GRID_X, GRID_Y + CELL * 3 + 40)
        render()
        return
    }

    turn = turn == 1 ? 2 : 1
    render()
}

on close() {
    log('thanks for playing')
}

log('tic-tac-toe: click a cell to place a mark. X goes first.')
drawGrid()
// claude.md #95: drawing paints an offscreen canvas; render() is what
// puts it on screen. Every handler below ends with one for the same
// reason -- a move isn't visible until the canvas is presented.
render()
