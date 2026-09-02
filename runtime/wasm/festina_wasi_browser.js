// claude.md #237: a WASI Preview 1 host for the browser -- runs a
// compiled wasm32-wasi Festina binary with no dependency beyond the
// WebAssembly and TextEncoder/TextDecoder APIs every browser (and Node)
// already has. Everything a compiled program imports is implemented
// here: the 25 `wasi_snapshot_preview1` functions a Festina binary
// actually names (checked against a real `.wasm`'s import section --
// args/environ, clock_time_get, the fd_* family, path_open and friends,
// poll_oneoff, proc_exit), plus the handful more wasi-libc can reach
// for on other code paths (fd_readdir, random_get, fd_tell,
// fd_pread/pwrite, path_rename, sched_yield), and a catch-all that
// answers ENOSYS for anything else rather than throwing a LinkError.
//
// The filesystem is in memory: a Map from an absolute path to a file
// (bytes) or a directory, with "/" preopened as fd 3 -- the same
// single-preopen sandbox runtime/wasm/run_wasi.mjs grants a program
// under Node, so `blob`, `mkdir`, `ls` and SQLite's own database file
// all work, and whatever the program wrote can be read back
// afterwards (`host.files()`). Nothing here touches the real disk; a
// caller seeds initial files and collects results.
//
// stdout/stderr are delivered line by line to callbacks and also kept
// whole; `proc_exit` ends the run by throwing a sentinel that `run()`
// turns into the exit code, exactly the way node:wasi's `start()`
// returns one. A `poll_oneoff` clock subscription (a Festina
// setTimeout/setInterval waiting its interval out) sleeps with
// Atomics.wait where the environment allows it (a Worker on a
// cross-origin-isolated page, or Node) and otherwise spins -- neither
// blocks a page's own main thread if the run happens in a Worker,
// which festina_wasi_worker.js arranges.
//
// Usage (see browser.html / festina_wasi_worker.js / run_wasi_js.mjs):
//
//   import { FestinaWasi } from "./festina_wasi_browser.js";
//   const host = new FestinaWasi({ args: ["program.wasm"],
//                                  files: { "/notes.txt": "hello" },
//                                  stdout: line => console.log(line) });
//   const code = await host.run(wasmBytes);   // the program's exit code
//   host.files();                             // Map<path, Uint8Array>

const ERRNO = {
  SUCCESS: 0, E2BIG: 1, ACCES: 2, BADF: 8, EXIST: 20, INVAL: 28, IO: 29,
  ISDIR: 31, NOENT: 44, NOSYS: 52, NOTDIR: 54, NOTEMPTY: 55, NOTSUP: 58,
  SPIPE: 70,
};

const FILETYPE = { UNKNOWN: 0, CHARACTER_DEVICE: 2, DIRECTORY: 3, REGULAR_FILE: 4 };
const OFLAGS = { CREAT: 1, DIRECTORY: 2, EXCL: 4, TRUNC: 8 };
const FDFLAGS = { APPEND: 1 };
const WHENCE = { SET: 0, CUR: 1, END: 2 };
const SUBCLOCK_FLAGS_ABSTIME = 1;
const EVENTTYPE_CLOCK = 0;
const RIGHTS_ALL = 0x1fffffffn;

class ExitSignal {
  constructor(code) { this.code = code; }
}

function normalizePath(base, rel) {
  const parts = [];
  const push = (segment) => {
    if (segment === "" || segment === ".") return;
    if (segment === "..") { parts.pop(); return; }
    parts.push(segment);
  };
  if (!rel.startsWith("/")) base.split("/").forEach(push);
  rel.split("/").forEach(push);
  return "/" + parts.join("/");
}

function parentOf(path) {
  const i = path.lastIndexOf("/");
  return i <= 0 ? "/" : path.slice(0, i);
}

function baseOf(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function toBytes(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  return new TextEncoder().encode(String(value));
}

class LineSink {
  constructor(onLine) {
    this.onLine = onLine;
    this.decoder = new TextDecoder();
    this.pending = "";
    this.all = "";
  }
  write(bytes) {
    const text = this.decoder.decode(bytes, { stream: true });
    this.all += text;
    this.pending += text;
    let nl;
    while ((nl = this.pending.indexOf("\n")) >= 0) {
      const line = this.pending.slice(0, nl);
      this.pending = this.pending.slice(nl + 1);
      if (this.onLine) this.onLine(line);
    }
  }
  flush() {
    const tail = this.decoder.decode();
    this.all += tail;
    this.pending += tail;
    if (this.pending && this.onLine) this.onLine(this.pending);
    this.pending = "";
  }
}

export class FestinaWasi {
  constructor(options = {}) {
    this.args = options.args || ["program.wasm"];
    this.env = options.env || {};
    this.stdoutSink = new LineSink(options.stdout);
    this.stderrSink = new LineSink(options.stderr);
    this.fs = new Map([["/", { kind: "dir" }]]);
    for (const [path, content] of Object.entries(options.files || {})) {
      this.putFile(normalizePath("/", path), toBytes(content));
    }
    this.fds = new Map();
    this.fds.set(0, { kind: "stdin" });
    this.fds.set(1, { kind: "stdout" });
    this.fds.set(2, { kind: "stderr" });
    this.fds.set(3, { kind: "dir", path: "/", preopen: true });
    this.nextFd = 4;
    this.memory = null;
    this.startNs = null;
    this.imports = this.buildImports();
  }

  // ---- the in-memory filesystem ----

  putFile(path, bytes) {
    // every ancestor directory exists once a file is put under it
    let dir = parentOf(path);
    const missing = [];
    while (!this.fs.has(dir)) { missing.push(dir); dir = parentOf(dir); }
    for (const d of missing.reverse()) this.fs.set(d, { kind: "dir" });
    const node = { kind: "file", data: new Uint8Array(bytes.length), len: bytes.length };
    node.data.set(bytes);
    this.fs.set(path, node);
    return node;
  }

  files() {
    const out = new Map();
    for (const [path, node] of this.fs) {
      if (node.kind === "file") out.set(path, node.data.subarray(0, node.len));
    }
    return out;
  }

  get stdout() { return this.stdoutSink.all; }
  get stderr() { return this.stderrSink.all; }

  // ---- memory helpers (a fresh view per call: memory can grow) ----

  view() { return new DataView(this.memory.buffer); }
  bytes() { return new Uint8Array(this.memory.buffer); }
  readString(ptr, len) { return new TextDecoder().decode(this.bytes().subarray(ptr, ptr + len)); }
  writeBytes(ptr, bytes) { this.bytes().set(bytes, ptr); }
  readIovs(iovsPtr, iovsLen) {
    const view = this.view();
    const out = [];
    for (let i = 0; i < iovsLen; i++) {
      const base = view.getUint32(iovsPtr + i * 8, true);
      const len = view.getUint32(iovsPtr + i * 8 + 4, true);
      out.push([base, len]);
    }
    return out;
  }

  nowNs() {
    const ms = (typeof performance !== "undefined" && performance.now)
      ? performance.timeOrigin + performance.now()
      : Date.now();
    return BigInt(Math.round(ms * 1e6));
  }

  sleepNs(ns) {
    const ms = Number(ns) / 1e6;
    if (ms <= 0) return;
    if (typeof SharedArrayBuffer !== "undefined" && typeof Atomics !== "undefined" && Atomics.wait) {
      try {
        Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
        return;
      } catch (_) {
        // Atomics.wait is refused on a page's main thread -- fall through
      }
    }
    const until = Date.now() + ms;
    while (Date.now() < until) { /* spin */ }
  }

  // ---- fd helpers ----

  fdEntry(fd) { return this.fds.get(fd); }

  fileFor(entry) {
    const node = this.fs.get(entry.path);
    return node && node.kind === "file" ? node : null;
  }

  writeFilestat(buf, node) {
    const view = this.view();
    view.setBigUint64(buf, 0n, true);                       // dev
    view.setBigUint64(buf + 8, 0n, true);                   // ino
    view.setUint8(buf + 16, node.kind === "dir" ? FILETYPE.DIRECTORY : FILETYPE.REGULAR_FILE);
    view.setBigUint64(buf + 24, 1n, true);                  // nlink
    view.setBigUint64(buf + 32, BigInt(node.kind === "file" ? node.len : 0), true);
    view.setBigUint64(buf + 40, 0n, true);                  // atim
    view.setBigUint64(buf + 48, 0n, true);                  // mtim
    view.setBigUint64(buf + 56, 0n, true);                  // ctim
  }

  ensureCapacity(node, needed) {
    if (needed <= node.data.length) return;
    let cap = Math.max(64, node.data.length);
    while (cap < needed) cap *= 2;
    const grown = new Uint8Array(cap);
    grown.set(node.data.subarray(0, node.len));
    node.data = grown;
  }

  resolve(dirfd, pathPtr, pathLen) {
    const entry = this.fdEntry(dirfd);
    if (!entry) return { errno: ERRNO.BADF };
    if (entry.kind !== "dir") return { errno: ERRNO.NOTDIR };
    const rel = this.readString(pathPtr, pathLen);
    return { path: normalizePath(entry.path, rel) };
  }

  // ---- the WASI imports ----

  buildImports() {
    const host = this;
    const wasi = {
      args_sizes_get(argcPtr, bufSizePtr) {
        const view = host.view();
        const encoded = host.args.map((a) => new TextEncoder().encode(a + "\0"));
        view.setUint32(argcPtr, encoded.length, true);
        view.setUint32(bufSizePtr, encoded.reduce((n, a) => n + a.length, 0), true);
        return ERRNO.SUCCESS;
      },
      args_get(argvPtr, bufPtr) {
        const view = host.view();
        let cursor = bufPtr;
        host.args.forEach((arg, i) => {
          const encoded = new TextEncoder().encode(arg + "\0");
          view.setUint32(argvPtr + i * 4, cursor, true);
          host.writeBytes(cursor, encoded);
          cursor += encoded.length;
        });
        return ERRNO.SUCCESS;
      },
      environ_sizes_get(countPtr, bufSizePtr) {
        const view = host.view();
        const entries = Object.entries(host.env).map(([k, v]) => new TextEncoder().encode(`${k}=${v}\0`));
        view.setUint32(countPtr, entries.length, true);
        view.setUint32(bufSizePtr, entries.reduce((n, e) => n + e.length, 0), true);
        return ERRNO.SUCCESS;
      },
      environ_get(environPtr, bufPtr) {
        const view = host.view();
        let cursor = bufPtr;
        Object.entries(host.env).forEach(([k, v], i) => {
          const encoded = new TextEncoder().encode(`${k}=${v}\0`);
          view.setUint32(environPtr + i * 4, cursor, true);
          host.writeBytes(cursor, encoded);
          cursor += encoded.length;
        });
        return ERRNO.SUCCESS;
      },
      clock_time_get(id, _precision, timePtr) {
        // 0 = realtime, 1 = monotonic; both answer nanoseconds, the
        // monotonic one from the run's own start so it never goes
        // backwards.
        const now = host.nowNs();
        if (host.startNs === null) host.startNs = now;
        host.view().setBigUint64(timePtr, id === 0 ? now : now - host.startNs, true);
        return ERRNO.SUCCESS;
      },
      random_get(bufPtr, len) {
        const out = host.bytes().subarray(bufPtr, bufPtr + len);
        if (typeof crypto !== "undefined" && crypto.getRandomValues) {
          // getRandomValues caps one call at 65536 bytes
          for (let i = 0; i < len; i += 65536) crypto.getRandomValues(out.subarray(i, Math.min(len, i + 65536)));
        } else {
          for (let i = 0; i < len; i++) out[i] = Math.floor(Math.random() * 256);
        }
        return ERRNO.SUCCESS;
      },
      sched_yield() { return ERRNO.SUCCESS; },
      proc_exit(code) { throw new ExitSignal(code); },

      fd_close(fd) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.preopen) return ERRNO.SUCCESS;
        host.fds.delete(fd);
        return ERRNO.SUCCESS;
      },
      fd_fdstat_get(fd, buf) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        const view = host.view();
        let filetype = FILETYPE.CHARACTER_DEVICE;
        if (entry.kind === "dir") filetype = FILETYPE.DIRECTORY;
        else if (entry.kind === "file") filetype = FILETYPE.REGULAR_FILE;
        view.setUint8(buf, filetype);
        view.setUint8(buf + 1, 0);
        view.setUint16(buf + 2, entry.append ? FDFLAGS.APPEND : 0, true);
        view.setUint32(buf + 4, 0, true);
        view.setBigUint64(buf + 8, RIGHTS_ALL, true);
        view.setBigUint64(buf + 16, RIGHTS_ALL, true);
        return ERRNO.SUCCESS;
      },
      fd_fdstat_set_flags(fd, flags) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        entry.append = (flags & FDFLAGS.APPEND) !== 0;
        return ERRNO.SUCCESS;
      },
      fd_fdstat_set_rights() { return ERRNO.SUCCESS; },
      fd_filestat_get(fd, buf) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.kind === "file" || entry.kind === "dir") {
          const node = host.fs.get(entry.path);
          if (!node) return ERRNO.NOENT;
          host.writeFilestat(buf, node);
        } else {
          host.writeFilestat(buf, { kind: "chardev" });
          host.view().setUint8(buf + 16, FILETYPE.CHARACTER_DEVICE);
        }
        return ERRNO.SUCCESS;
      },
      fd_filestat_set_size(fd, size) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        const node = entry.kind === "file" ? host.fileFor(entry) : null;
        if (!node) return ERRNO.BADF;
        const newLen = Number(size);
        host.ensureCapacity(node, newLen);
        if (newLen > node.len) node.data.fill(0, node.len, newLen);
        node.len = newLen;
        return ERRNO.SUCCESS;
      },
      fd_filestat_set_times() { return ERRNO.SUCCESS; },
      fd_prestat_get(fd, buf) {
        const entry = host.fdEntry(fd);
        if (!entry || !entry.preopen) return ERRNO.BADF;
        const view = host.view();
        view.setUint8(buf, 0);                                       // preopentype dir
        view.setUint32(buf + 4, new TextEncoder().encode(entry.path).length, true);
        return ERRNO.SUCCESS;
      },
      fd_prestat_dir_name(fd, pathPtr, pathLen) {
        const entry = host.fdEntry(fd);
        if (!entry || !entry.preopen) return ERRNO.BADF;
        host.writeBytes(pathPtr, new TextEncoder().encode(entry.path).subarray(0, pathLen));
        return ERRNO.SUCCESS;
      },
      fd_read(fd, iovsPtr, iovsLen, nreadPtr) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.kind === "stdin") { host.view().setUint32(nreadPtr, 0, true); return ERRNO.SUCCESS; }
        if (entry.kind !== "file") return ERRNO.BADF;
        const node = host.fileFor(entry);
        if (!node) return ERRNO.BADF;
        let total = 0;
        for (const [base, len] of host.readIovs(iovsPtr, iovsLen)) {
          const n = Math.max(0, Math.min(len, node.len - entry.pos));
          if (n === 0) break;
          host.writeBytes(base, node.data.subarray(entry.pos, entry.pos + n));
          entry.pos += n;
          total += n;
        }
        host.view().setUint32(nreadPtr, total, true);
        return ERRNO.SUCCESS;
      },
      fd_pread(fd, iovsPtr, iovsLen, offset, nreadPtr) {
        const entry = host.fdEntry(fd);
        if (!entry || entry.kind !== "file") return ERRNO.BADF;
        const node = host.fileFor(entry);
        if (!node) return ERRNO.BADF;
        let pos = Number(offset);
        let total = 0;
        for (const [base, len] of host.readIovs(iovsPtr, iovsLen)) {
          const n = Math.max(0, Math.min(len, node.len - pos));
          if (n === 0) break;
          host.writeBytes(base, node.data.subarray(pos, pos + n));
          pos += n;
          total += n;
        }
        host.view().setUint32(nreadPtr, total, true);
        return ERRNO.SUCCESS;
      },
      fd_write(fd, iovsPtr, iovsLen, nwrittenPtr) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        let total = 0;
        if (entry.kind === "stdout" || entry.kind === "stderr") {
          const sink = entry.kind === "stdout" ? host.stdoutSink : host.stderrSink;
          for (const [base, len] of host.readIovs(iovsPtr, iovsLen)) {
            sink.write(host.bytes().slice(base, base + len));
            total += len;
          }
        } else if (entry.kind === "file") {
          const node = host.fileFor(entry);
          if (!node) return ERRNO.BADF;
          if (entry.append) entry.pos = node.len;
          for (const [base, len] of host.readIovs(iovsPtr, iovsLen)) {
            host.ensureCapacity(node, entry.pos + len);
            node.data.set(host.bytes().subarray(base, base + len), entry.pos);
            entry.pos += len;
            if (entry.pos > node.len) node.len = entry.pos;
            total += len;
          }
        } else {
          return ERRNO.BADF;
        }
        host.view().setUint32(nwrittenPtr, total, true);
        return ERRNO.SUCCESS;
      },
      fd_pwrite(fd, iovsPtr, iovsLen, offset, nwrittenPtr) {
        const entry = host.fdEntry(fd);
        if (!entry || entry.kind !== "file") return ERRNO.BADF;
        const node = host.fileFor(entry);
        if (!node) return ERRNO.BADF;
        let pos = Number(offset);
        let total = 0;
        for (const [base, len] of host.readIovs(iovsPtr, iovsLen)) {
          host.ensureCapacity(node, pos + len);
          node.data.set(host.bytes().subarray(base, base + len), pos);
          pos += len;
          if (pos > node.len) node.len = pos;
          total += len;
        }
        host.view().setUint32(nwrittenPtr, total, true);
        return ERRNO.SUCCESS;
      },
      fd_seek(fd, offset, whence, newOffsetPtr) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.kind !== "file") return ERRNO.SPIPE;
        const node = host.fileFor(entry);
        if (!node) return ERRNO.BADF;
        const delta = Number(offset);
        let target;
        if (whence === WHENCE.SET) target = delta;
        else if (whence === WHENCE.CUR) target = entry.pos + delta;
        else if (whence === WHENCE.END) target = node.len + delta;
        else return ERRNO.INVAL;
        if (target < 0) return ERRNO.INVAL;
        entry.pos = target;
        host.view().setBigUint64(newOffsetPtr, BigInt(target), true);
        return ERRNO.SUCCESS;
      },
      fd_tell(fd, offsetPtr) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.kind !== "file") return ERRNO.SPIPE;
        host.view().setBigUint64(offsetPtr, BigInt(entry.pos), true);
        return ERRNO.SUCCESS;
      },
      fd_sync() { return ERRNO.SUCCESS; },
      fd_datasync() { return ERRNO.SUCCESS; },
      fd_advise() { return ERRNO.SUCCESS; },
      fd_allocate(fd, offset, len) {
        const entry = host.fdEntry(fd);
        if (!entry || entry.kind !== "file") return ERRNO.BADF;
        const node = host.fileFor(entry);
        if (!node) return ERRNO.BADF;
        const end = Number(offset) + Number(len);
        host.ensureCapacity(node, end);
        if (end > node.len) { node.data.fill(0, node.len, end); node.len = end; }
        return ERRNO.SUCCESS;
      },
      fd_renumber(from, to) {
        const entry = host.fdEntry(from);
        if (!entry || !host.fdEntry(to)) return ERRNO.BADF;
        host.fds.set(to, entry);
        host.fds.delete(from);
        return ERRNO.SUCCESS;
      },
      fd_readdir(fd, bufPtr, bufLen, cookie, bufUsedPtr) {
        const entry = host.fdEntry(fd);
        if (!entry) return ERRNO.BADF;
        if (entry.kind !== "dir") return ERRNO.NOTDIR;
        const prefix = entry.path === "/" ? "/" : entry.path + "/";
        const names = [];
        for (const [path, node] of host.fs) {
          if (path !== entry.path && path.startsWith(prefix) && !path.slice(prefix.length).includes("/")) {
            names.push([baseOf(path), node.kind === "dir" ? FILETYPE.DIRECTORY : FILETYPE.REGULAR_FILE]);
          }
        }
        names.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
        const view = host.view();
        let used = 0;
        for (let i = Number(cookie); i < names.length; i++) {
          const [name, type] = names[i];
          const nameBytes = new TextEncoder().encode(name);
          const header = new Uint8Array(24);
          const hv = new DataView(header.buffer);
          hv.setBigUint64(0, BigInt(i + 1), true);        // d_next
          hv.setBigUint64(8, BigInt(i + 1), true);        // d_ino
          hv.setUint32(16, nameBytes.length, true);       // d_namlen
          hv.setUint8(20, type);                          // d_type
          const record = new Uint8Array(header.length + nameBytes.length);
          record.set(header); record.set(nameBytes, header.length);
          const room = bufLen - used;
          const n = Math.min(room, record.length);
          host.writeBytes(bufPtr + used, record.subarray(0, n));
          used += n;
          if (n < record.length) break;                   // truncated: the caller comes back with a bigger buffer
        }
        view.setUint32(bufUsedPtr, used, true);
        return ERRNO.SUCCESS;
      },

      path_create_directory(dirfd, pathPtr, pathLen) {
        const r = host.resolve(dirfd, pathPtr, pathLen);
        if (r.errno) return r.errno;
        if (host.fs.has(r.path)) return ERRNO.EXIST;
        const parent = host.fs.get(parentOf(r.path));
        if (!parent || parent.kind !== "dir") return ERRNO.NOENT;
        host.fs.set(r.path, { kind: "dir" });
        return ERRNO.SUCCESS;
      },
      path_filestat_get(dirfd, _flags, pathPtr, pathLen, buf) {
        const r = host.resolve(dirfd, pathPtr, pathLen);
        if (r.errno) return r.errno;
        const node = host.fs.get(r.path);
        if (!node) return ERRNO.NOENT;
        host.writeFilestat(buf, node);
        return ERRNO.SUCCESS;
      },
      path_filestat_set_times() { return ERRNO.SUCCESS; },
      path_open(dirfd, _dirflags, pathPtr, pathLen, oflags, _rightsBase, _rightsInheriting, fdflags, fdPtr) {
        const r = host.resolve(dirfd, pathPtr, pathLen);
        if (r.errno) return r.errno;
        let node = host.fs.get(r.path);
        if (oflags & OFLAGS.DIRECTORY) {
          if (!node) return ERRNO.NOENT;
          if (node.kind !== "dir") return ERRNO.NOTDIR;
          const fd = host.nextFd++;
          host.fds.set(fd, { kind: "dir", path: r.path });
          host.view().setUint32(fdPtr, fd, true);
          return ERRNO.SUCCESS;
        }
        if (node && (oflags & OFLAGS.CREAT) && (oflags & OFLAGS.EXCL)) return ERRNO.EXIST;
        if (!node) {
          if (!(oflags & OFLAGS.CREAT)) return ERRNO.NOENT;
          const parent = host.fs.get(parentOf(r.path));
          if (!parent || parent.kind !== "dir") return ERRNO.NOENT;
          node = host.putFile(r.path, new Uint8Array(0));
        }
        if (node.kind === "dir") {
          const fd = host.nextFd++;
          host.fds.set(fd, { kind: "dir", path: r.path });
          host.view().setUint32(fdPtr, fd, true);
          return ERRNO.SUCCESS;
        }
        if (oflags & OFLAGS.TRUNC) node.len = 0;
        const fd = host.nextFd++;
        host.fds.set(fd, { kind: "file", path: r.path, pos: 0, append: (fdflags & FDFLAGS.APPEND) !== 0 });
        host.view().setUint32(fdPtr, fd, true);
        return ERRNO.SUCCESS;
      },
      path_readlink() { return ERRNO.INVAL; },
      path_remove_directory(dirfd, pathPtr, pathLen) {
        const r = host.resolve(dirfd, pathPtr, pathLen);
        if (r.errno) return r.errno;
        const node = host.fs.get(r.path);
        if (!node) return ERRNO.NOENT;
        if (node.kind !== "dir") return ERRNO.NOTDIR;
        const prefix = r.path + "/";
        for (const path of host.fs.keys()) if (path.startsWith(prefix)) return ERRNO.NOTEMPTY;
        host.fs.delete(r.path);
        return ERRNO.SUCCESS;
      },
      path_unlink_file(dirfd, pathPtr, pathLen) {
        const r = host.resolve(dirfd, pathPtr, pathLen);
        if (r.errno) return r.errno;
        const node = host.fs.get(r.path);
        if (!node) return ERRNO.NOENT;
        if (node.kind === "dir") return ERRNO.ISDIR;
        host.fs.delete(r.path);
        return ERRNO.SUCCESS;
      },
      path_rename(oldFd, oldPtr, oldLen, newFd, newPtr, newLen) {
        const a = host.resolve(oldFd, oldPtr, oldLen);
        if (a.errno) return a.errno;
        const b = host.resolve(newFd, newPtr, newLen);
        if (b.errno) return b.errno;
        const node = host.fs.get(a.path);
        if (!node) return ERRNO.NOENT;
        host.fs.delete(a.path);
        host.fs.set(b.path, node);
        for (const entry of host.fds.values()) if (entry.path === a.path) entry.path = b.path;
        return ERRNO.SUCCESS;
      },
      path_link() { return ERRNO.NOSYS; },
      path_symlink() { return ERRNO.NOSYS; },

      poll_oneoff(inPtr, outPtr, nsubs, neventsPtr) {
        // Only clock subscriptions ever come from a Festina program
        // (nanosleep / a timer loop's bounded wait); the shortest one
        // is slept out and every clock subscription is reported ready.
        // An fd subscription (which nothing here would ever produce --
        // wasi-libc's poll() over a pipe, say) is answered as ready too
        // rather than blocking forever.
        const view = host.view();
        let shortest = null;
        const ready = [];
        for (let i = 0; i < nsubs; i++) {
          const sub = inPtr + i * 48;
          const userdata = view.getBigUint64(sub, true);
          const type = view.getUint8(sub + 8);
          if (type === EVENTTYPE_CLOCK) {
            const timeout = view.getBigUint64(sub + 24, true);
            const flags = view.getUint16(sub + 40, true);
            let wait = timeout;
            if (flags & SUBCLOCK_FLAGS_ABSTIME) {
              const now = host.nowNs();
              wait = timeout > now ? timeout - now : 0n;
            }
            if (shortest === null || wait < shortest) shortest = wait;
          }
          ready.push([userdata, type]);
        }
        if (shortest !== null) host.sleepNs(shortest);
        ready.forEach(([userdata, type], i) => {
          const ev = outPtr + i * 32;
          view.setBigUint64(ev, userdata, true);
          view.setUint16(ev + 8, ERRNO.SUCCESS, true);
          view.setUint8(ev + 10, type);
          view.setBigUint64(ev + 16, 0n, true);   // fd_readwrite.nbytes
          view.setUint16(ev + 24, 0, true);       // fd_readwrite.flags
        });
        view.setUint32(neventsPtr, ready.length, true);
        return ERRNO.SUCCESS;
      },
    };
    // Anything not listed above answers ENOSYS instead of failing to
    // link -- a program that never calls it runs fine, and one that
    // does gets a clean errno rather than a LinkError at instantiation.
    return new Proxy(wasi, {
      get(target, name) {
        if (name in target) return target[name];
        if (typeof name !== "string") return undefined;
        return () => ERRNO.NOSYS;
      },
    });
  }

  // ---- running ----

  async run(wasmBytes) {
    const { instance } = await WebAssembly.instantiate(wasmBytes, {
      wasi_snapshot_preview1: this.imports,
    });
    return this.start(instance);
  }

  start(instance) {
    this.memory = instance.exports.memory;
    let code = 0;
    try {
      instance.exports._start();
    } catch (err) {
      if (err instanceof ExitSignal) code = err.code;
      else throw err;
    } finally {
      this.stdoutSink.flush();
      this.stderrSink.flush();
    }
    return code;
  }
}

export { ERRNO, ExitSignal };
