// claude.md #5, #6: multi-file compilation -- `import geometry.f` pulls
// that file's struct/function declarations into this compilation unit.
// Build and run with:
//
//   ./bin/festina examples/multifile.f -o multifile
//   ./multifile

import geometry.f

Point origin
origin.x = 0
origin.y = 0

Point destination
destination.x = 3
destination.y = 4

log(describePoint(origin))
log(describePoint(destination))
