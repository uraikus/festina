// claude.md #195/#197/#198: `thread NAME { ... }` -- real message-
// passing concurrency, at real volume, under ASan/LeakSanitizer (see
// scripts/leak_stress.sh's own new "thread" runtime object). Every
// message crossing either queue is a fresh malloc'd box, a fresh
// festina_text_own copy, a fresh _clone_fn_for_* cascade, or a fresh
// festina_*_clone runtime call -- a leak of even one per message is
// unmissable at these counts. Exercises every message shape this
// compiler supports today (int, the raw-bits box path; text, the "the
// box IS the owned buffer" path; struct/arr[T]/map[T]/enum, the
// claude.md #197 deep-clone cascades; blob/img/aud/url, the claude.md
// #198 runtime-written clones -- each one's own release path too,
// since every reply is actually consumed and compared, not just
// delivered) and the two lifecycle operations that themselves
// allocate/tear down real OS resources (kill()/live(), each
// pthread_join()ing a real thread).
//
// All N messages for a given worker are posted up front, in one tight
// loop, rather than one-reply-triggers-the-next-send -- a ping-pong
// design was tried first and confirmed real but uninteresting: the
// drain step only runs once per festina_run_timer_loop iteration (a
// bounded ~20ms poll when nothing else is scheduled sooner, see that
// function's own doc comment), so a design that waits for each reply
// before sending the next serializes the whole test on that interval
// instead of exercising real throughput. Posting everything up front
// lets each worker drain its own inbound queue at full speed and the
// main loop pick up however many outbound replies have piled up in as
// few drains as it needs.
//
// This test cannot busy-wait for a reply inside top-level code (no
// top-level statement ever runs concurrently with the main loop's own
// drain step), so it drives itself the other way around: once EVERY
// counter reaches its target, whichever onXReply callback notices
// last calls close(0) -- an ordinary main-thread function call, never
// "inside a thread body", so none of claude.md #195's isolation
// restrictions apply to it.

struct Item { n:int label:text }
enum DataPacket = int, text

thread pinger {
  on message(p:int) {
    postMessage(p + 1)
  }
}

thread echoer {
  on message(p:text) {
    postMessage(`echo:${p}`)
  }
}

thread structWorker {
  on message(p:Item) {
    Item out = p
    out.n = p.n + 1
    postMessage(out)
  }
}

thread arrayWorker {
  on message(p:arr[int]) {
    arr[int] out = []
    int i = 0
    while i < p.length {
        out.push(p[i] * 2)
        i = i + 1
    }
    postMessage(out)
  }
}

thread mapWorker {
  on message(p:map[int]) {
    map[int] out = {}
    out['a'] = p['a'] + 1
    out['b'] = p['b'] + 1
    postMessage(out)
  }
}

thread enumWorker {
  on message(p:DataPacket) {
    postMessage(p)
  }
}

// claude.md #198 Phase 4: blob/img/aud/url clone -- festina_blob_clone/
// _image_clone/_audio_clone/_url_clone, exercised at real volume the
// identical way struct/arr[T]/map[T]/enum already are above. imgWorker
// also draws on its own received clone before posting it back, the
// same shape as TestThreads' own image-round-trip test -- proving the
// img-method allow-list survives real concurrent churn, not just one
// message.
thread blobWorker {
  on message(p:blob) {
    postMessage(p)
  }
}

thread imgWorker {
  on message(p:img) {
    color red = 'red'
    p.drawPixel(0, 0, red)
    postMessage(p)
  }
}

thread audWorker {
  on message(p:aud) {
    postMessage(p)
  }
}

thread urlWorker {
  on message(p:url) {
    postMessage(p)
  }
}

int TOTAL = 20000
int intRepliesSeen = 0
int intSum = 0
int TEXT_TOTAL = 6000
int textRepliesSeen = 0
int STRUCT_TOTAL = 3000
int structRepliesSeen = 0
int structSum = 0
int ARRAY_TOTAL = 2000
int arrayRepliesSeen = 0
int arraySum = 0
int MAP_TOTAL = 2000
int mapRepliesSeen = 0
int mapSum = 0
int ENUM_TOTAL = 2000
int enumRepliesSeen = 0
int enumIntCount = 0
int BLOB_TOTAL = 1000
int blobRepliesSeen = 0
int blobMatchCount = 0
int IMG_TOTAL = 500
int imgRepliesSeen = 0
int imgWidthSum = 0
int AUD_TOTAL = 500
int audRepliesSeen = 0
int URL_TOTAL = 1000
int urlRepliesSeen = 0
int urlMatchCount = 0

void func maybeDone() {
    if intRepliesSeen >= TOTAL && textRepliesSeen >= TEXT_TOTAL
            && structRepliesSeen >= STRUCT_TOTAL && arrayRepliesSeen >= ARRAY_TOTAL
            && mapRepliesSeen >= MAP_TOTAL && enumRepliesSeen >= ENUM_TOTAL
            && blobRepliesSeen >= BLOB_TOTAL && imgRepliesSeen >= IMG_TOTAL
            && audRepliesSeen >= AUD_TOTAL && urlRepliesSeen >= URL_TOTAL {
        log('int churn done')
        log(intRepliesSeen)
        log(intSum)
        log('text churn done')
        log(textRepliesSeen)
        log('struct churn done')
        log(structRepliesSeen)
        log(structSum)
        log('array churn done')
        log(arrayRepliesSeen)
        log(arraySum)
        log('map churn done')
        log(mapRepliesSeen)
        log(mapSum)
        log('enum churn done')
        log(enumRepliesSeen)
        log(enumIntCount)
        log('blob churn done')
        log(blobRepliesSeen)
        log(blobMatchCount)
        log('img churn done')
        log(imgRepliesSeen)
        log(imgWidthSum)
        log('aud churn done')
        log(audRepliesSeen)
        log('url churn done')
        log(urlRepliesSeen)
        log(urlMatchCount)
        close(0)
    }
}

void func onIntReply(x:int) {
    intRepliesSeen = intRepliesSeen + 1
    intSum = intSum + x
    maybeDone()
}

void func onTextReply(x:text) {
    textRepliesSeen = textRepliesSeen + 1
    maybeDone()
}

void func onStructReply(x:Item) {
    structRepliesSeen = structRepliesSeen + 1
    structSum = structSum + x.n
    maybeDone()
}

void func onArrayReply(x:arr[int]) {
    arrayRepliesSeen = arrayRepliesSeen + 1
    arraySum = arraySum + x[0] + x[1]
    maybeDone()
}

void func onMapReply(x:map[int]) {
    mapRepliesSeen = mapRepliesSeen + 1
    mapSum = mapSum + x['a'] + x['b']
    maybeDone()
}

void func onEnumReply(x:DataPacket) {
    enumRepliesSeen = enumRepliesSeen + 1
    if typeof x == 'int' {
        enumIntCount = enumIntCount + 1
    }
    maybeDone()
}

void func onBlobReply(x:blob) {
    blobRepliesSeen = blobRepliesSeen + 1
    if x.toText() == 'blob-payload-for-thread-churn' {
        blobMatchCount = blobMatchCount + 1
    }
    maybeDone()
}

void func onImgReply(x:img) {
    imgRepliesSeen = imgRepliesSeen + 1
    imgWidthSum = imgWidthSum + x.width
    maybeDone()
}

void func onAudReply(x:aud) {
    audRepliesSeen = audRepliesSeen + 1
    maybeDone()
}

void func onUrlReply(x:url) {
    urlRepliesSeen = urlRepliesSeen + 1
    if x.hostname == 'example.com' {
        urlMatchCount = urlMatchCount + 1
    }
    maybeDone()
}

pinger.onMessage(void (x:int) => onIntReply(x))
echoer.onMessage(void (x:text) => onTextReply(x))
structWorker.onMessage(void (x:Item) => onStructReply(x))
arrayWorker.onMessage(void (x:arr[int]) => onArrayReply(x))
mapWorker.onMessage(void (x:map[int]) => onMapReply(x))
enumWorker.onMessage(void (x:DataPacket) => onEnumReply(x))
blobWorker.onMessage(void (x:blob) => onBlobReply(x))
imgWorker.onMessage(void (x:img) => onImgReply(x))
audWorker.onMessage(void (x:aud) => onAudReply(x))
urlWorker.onMessage(void (x:url) => onUrlReply(x))

// claude.md #195 Phase 2: a handful of real kill()/live() cycles up
// front too -- each one spawns/joins a genuine OS thread, so this is
// what a leaked pthread resource (not just a leaked message box)
// would show up under.
int k = 0
while k < 20 {
    pinger.kill()
    pinger.live(void (ok:bool) => log(ok))
    k = k + 1
}

int i = 0
while i < TOTAL {
    pinger.postMessage(i)
    i = i + 1
}
int j = 0
while j < TEXT_TOTAL {
    echoer.postMessage(`msg${j}`)
    j = j + 1
}
int s = 0
while s < STRUCT_TOTAL {
    Item item
    item.n = s
    item.label = `item${s}`
    structWorker.postMessage(item)
    s = s + 1
}
int a = 0
while a < ARRAY_TOTAL {
    arr[int] nums = [a, a + 1, a + 2]
    arrayWorker.postMessage(nums)
    a = a + 1
}
int m = 0
while m < MAP_TOTAL {
    map[int] entry = {}
    entry['a'] = m
    entry['b'] = m + 1
    mapWorker.postMessage(entry)
    m = m + 1
}
int e = 0
while e < ENUM_TOTAL {
    if e % 2 == 0 {
        DataPacket p = e
        enumWorker.postMessage(p)
    } else {
        DataPacket p = `p${e}`
        enumWorker.postMessage(p)
    }
    e = e + 1
}

// claude.md #198 Phase 4: each source value is built once and posted
// repeatedly (the same shape pinger/echoer/imgSrc already use for their
// own scalar/text payloads above) -- postMessage's own clone is what
// this is stress-testing, so the loop needs a fresh CLONE per message,
// never a fresh source VALUE.
blob blobSrc = 'thread_blob_src.dat'
blobSrc.write('blob-payload-for-thread-churn')
int b = 0
while b < BLOB_TOTAL {
    blobWorker.postMessage(blobSrc)
    b = b + 1
}
img imgSrc = blankImage(4, 4)
int im = 0
while im < IMG_TOTAL {
    imgWorker.postMessage(imgSrc)
    im = im + 1
}
aud audSrc = 'beep.wav'
int au = 0
while au < AUD_TOTAL {
    audWorker.postMessage(audSrc)
    au = au + 1
}
url urlSrc = parseURL('https://example.com/path?a=1')
int u = 0
while u < URL_TOTAL {
    urlWorker.postMessage(urlSrc)
    u = u + 1
}
