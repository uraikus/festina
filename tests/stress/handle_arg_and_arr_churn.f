// claude.md #192/#194: the argument-coercion and arr[handle] ownership
// fixes. Three bugs, all exercised here under a real ASan/LeakSanitizer
// loop:
//   1. A text literal or template passed to an img/blob/aud PARAMETER
//      mints a fresh handle via _coerce -- previously freed with a
//      plain free() on the handle payload (heap corruption) for an
//      owning text source, or never released (leak) for a literal.
//   2. An escaping/stored arr[blob] (arr[img]/arr[aud]) released every
//      element as a plain buffer free, leaking each element handle.
//   3. .push()/.indexOf() of a text->handle coerced element over-
//      retained (push) or never released (indexOf) the minted handle.
// A leak or invalid free in any of these shows up as an ASan error
// rather than a wrong answer, which is why this hammers each in a loop.

struct Holder { files:arr[blob] }

blob func firstOf(b:blob) {
    // takes a blob PARAMETER -- callers below pass a text literal and a
    // template, each of which _coerce turns into a fresh handle the
    // callee only borrows.
    return b
}

// The blob source file, written once up front (the same self-contained
// approach regex_and_files_churn.f uses -- no external fixture needed).
blob seed = 'handle_arg_data.txt'
seed.write('some bytes')
free seed

int total = 0
int i = 0
while i < 800 {
    // handle-arg: literal path and template path into a blob param.
    blob a = firstOf('handle_arg_data.txt')
    free a
    blob c = firstOf(`handle_arg_data.txt`)
    free c

    // arr[blob] element release: build, push a coerced-from-text
    // element, store into a struct field (escaping), let scope exit
    // release the whole thing and cascade to each element.
    arr[blob] tmp = ['handle_arg_data.txt', 'handle_arg_data.txt']
    tmp.push('handle_arg_data.txt')
    tmp.push(`handle_arg_data.txt`)
    Holder h
    h.files = tmp
    if h.files.length == 4 { total = total + 1 }

    i = i + 1
}
log(total)
