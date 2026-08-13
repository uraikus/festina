#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <ctype.h>
#include "runtime.h"
#include "sqlite3_shim.h"
#include <cairo/cairo.h>

/* ---------------- allocation helpers (leak-for-simplicity; no GC) --------- */
static JSValue* alloc_val(JSType t) {
    JSValue* v = malloc(sizeof(JSValue));
    v->type = t;
    return v;
}

static JSValue g_undefined_singleton = { T_UNDEFINED, { .number = 0 } };
static JSValue g_null_singleton = { T_NULL, { .number = 0 } };

void js_runtime_init(void) { /* reserved for future setup */ }

JSValue* js_undefined(void) { return &g_undefined_singleton; }
JSValue* js_null(void) { return &g_null_singleton; }

JSValue* js_new_number(double v) {
    JSValue* r = alloc_val(T_NUMBER);
    r->as.number = v;
    return r;
}

JSValue* js_new_string(const char* s) {
    JSValue* r = alloc_val(T_STRING);
    r->as.string = strdup(s ? s : "");
    return r;
}

JSValue* js_new_bool(int b) {
    JSValue* r = alloc_val(T_BOOL);
    r->as.boolean = b ? 1 : 0;
    return r;
}

JSValue* js_new_function(void* fnptr) {
    JSValue* r = alloc_val(T_FUNCTION);
    r->as.fn = (JSFnPtr)fnptr;
    return r;
}

JSValue* js_arg_or_undef(JSValue** args, int argc, int i) {
    if (i < argc) return args[i];
    return js_undefined();
}

/* ---------------- number formatting ---------------- */
static char* num_to_cstr(double d) {
    char* buf = malloc(64);
    if (isnan(d)) { strcpy(buf, "NaN"); return buf; }
    if (isinf(d)) { strcpy(buf, d > 0 ? "Infinity" : "-Infinity"); return buf; }
    if (d == (double)(long long)d && fabs(d) < 1e15) {
        snprintf(buf, 64, "%lld", (long long)d);
    } else {
        snprintf(buf, 64, "%.10g", d);
    }
    return buf;
}

/* ---------------- to-string / display ---------------- */
typedef struct { char* buf; int len; int cap; } SB;
static void sb_init(SB* s) { s->cap = 64; s->len = 0; s->buf = malloc(s->cap); s->buf[0] = 0; }
static void sb_append(SB* s, const char* text) {
    int tl = strlen(text);
    while (s->len + tl + 1 > s->cap) { s->cap *= 2; s->buf = realloc(s->buf, s->cap); }
    memcpy(s->buf + s->len, text, tl + 1);
    s->len += tl;
}

static void to_display(SB* sb, JSValue* v, int depth, int quoteStrings) {
    if (!v) { sb_append(sb, "undefined"); return; }
    switch (v->type) {
        case T_UNDEFINED: sb_append(sb, "undefined"); break;
        case T_NULL: sb_append(sb, "null"); break;
        case T_BOOL: sb_append(sb, v->as.boolean ? "true" : "false"); break;
        case T_NUMBER: { char* s = num_to_cstr(v->as.number); sb_append(sb, s); free(s); break; }
        case T_STRING:
            if (quoteStrings) { sb_append(sb, "'"); sb_append(sb, v->as.string); sb_append(sb, "'"); }
            else sb_append(sb, v->as.string);
            break;
        case T_ARRAY: {
            sb_append(sb, "[ ");
            for (int i = 0; i < v->as.array->length; i++) {
                if (i) sb_append(sb, ", ");
                to_display(sb, v->as.array->items[i], depth + 1, 1);
            }
            sb_append(sb, v->as.array->length ? " ]" : "]");
            break;
        }
        case T_OBJECT: {
            sb_append(sb, "{ ");
            /* the property list is prepended on insert, so walk it into an array
               and print in reverse to match insertion order (same as js_object_keys) */
            JSProp* items[4096]; int n = 0;
            JSProp* p = v->as.object->head;
            while (p && n < 4096) { items[n++] = p; p = p->next; }
            for (int i = n - 1; i >= 0; i--) {
                if (i != n - 1) sb_append(sb, ", ");
                sb_append(sb, items[i]->key);
                sb_append(sb, ": ");
                to_display(sb, items[i]->value, depth + 1, 1);
            }
            sb_append(sb, n ? " }" : "}");
            break;
        }
        case T_FUNCTION: sb_append(sb, "[Function]"); break;
        case T_DB: sb_append(sb, "[Database]"); break;
        case T_STMT: sb_append(sb, "[Statement]"); break;
        case T_CANVAS: sb_append(sb, "[Canvas]"); break;
        case T_CTX: sb_append(sb, "[CanvasRenderingContext2D]"); break;
        default: sb_append(sb, "?"); break;
    }
}

char* js_to_display_string(JSValue* v) {
    SB sb; sb_init(&sb);
    to_display(&sb, v, 0, 0);
    return sb.buf;
}

char* js_to_cstring(JSValue* v) {
    /* like String(v): strings unquoted, everything else same as display but strings never quoted */
    SB sb; sb_init(&sb);
    to_display(&sb, v, 0, 0);
    return sb.buf;
}

void js_console_log(JSValue** args, int argc) {
    for (int i = 0; i < argc; i++) {
        if (i) printf(" ");
        char* s = js_to_display_string(args[i]);
        printf("%s", s);
        free(s);
    }
    printf("\n");
}

/* ---------------- truthiness ---------------- */
int js_truthy(JSValue* v) {
    if (!v) return 0;
    switch (v->type) {
        case T_UNDEFINED: case T_NULL: return 0;
        case T_BOOL: return v->as.boolean;
        case T_NUMBER: return v->as.number != 0.0 && !isnan(v->as.number);
        case T_STRING: return v->as.string[0] != '\0';
        default: return 1;
    }
}

static double to_number(JSValue* v) {
    if (!v) return 0.0 / 0.0;
    switch (v->type) {
        case T_NUMBER: return v->as.number;
        case T_BOOL: return v->as.boolean ? 1.0 : 0.0;
        case T_STRING: {
            char* end;
            double d = strtod(v->as.string, &end);
            if (end == v->as.string) return 0.0 / 0.0;
            return d;
        }
        case T_NULL: return 0.0;
        default: return 0.0 / 0.0;
    }
}

/* ---------------- arrays ---------------- */
JSValue* js_new_array(void) {
    JSValue* r = alloc_val(T_ARRAY);
    JSArray* a = malloc(sizeof(JSArray));
    a->length = 0; a->capacity = 8;
    a->items = malloc(sizeof(JSValue*) * a->capacity);
    r->as.array = a;
    return r;
}

static void array_ensure(JSArray* a, int n) {
    if (n <= a->capacity) return;
    while (a->capacity < n) a->capacity *= 2;
    a->items = realloc(a->items, sizeof(JSValue*) * a->capacity);
}

JSValue* js_array_push(JSValue* arr, JSValue* v) {
    JSArray* a = arr->as.array;
    array_ensure(a, a->length + 1);
    a->items[a->length++] = v;
    return js_new_number(a->length);
}

JSValue* js_array_pop(JSValue* arr) {
    JSArray* a = arr->as.array;
    if (a->length == 0) return js_undefined();
    return a->items[--a->length];
}

static int idx_of(JSValue* idxv) {
    return (int)to_number(idxv);
}

JSValue* js_array_get(JSValue* arr, JSValue* idxv) {
    if (arr->type != T_ARRAY) return js_undefined();
    int i = idx_of(idxv);
    JSArray* a = arr->as.array;
    if (i < 0 || i >= a->length) return js_undefined();
    return a->items[i];
}

void js_array_set(JSValue* arr, JSValue* idxv, JSValue* v) {
    JSArray* a = arr->as.array;
    int i = idx_of(idxv);
    if (i < 0) return;
    array_ensure(a, i + 1);
    while (a->length <= i) a->items[a->length++] = js_undefined();
    a->items[i] = v;
}

JSValue* js_array_length(JSValue* arr) {
    if (arr->type == T_STRING) return js_new_number((double)strlen(arr->as.string));
    if (arr->type != T_ARRAY) return js_new_number(0);
    return js_new_number(arr->as.array->length);
}

JSValue* js_array_map(JSValue* arr, JSValue* fn) {
    JSValue* out = js_new_array();
    JSArray* a = arr->as.array;
    for (int i = 0; i < a->length; i++) {
        JSValue* cargs[3] = { a->items[i], js_new_number(i), arr };
        JSValue* r = js_call(fn, cargs, 3);
        js_array_push(out, r);
    }
    return out;
}

JSValue* js_array_filter(JSValue* arr, JSValue* fn) {
    JSValue* out = js_new_array();
    JSArray* a = arr->as.array;
    for (int i = 0; i < a->length; i++) {
        JSValue* cargs[3] = { a->items[i], js_new_number(i), arr };
        JSValue* r = js_call(fn, cargs, 3);
        if (js_truthy(r)) js_array_push(out, a->items[i]);
    }
    return out;
}

JSValue* js_array_forEach(JSValue* arr, JSValue* fn) {
    JSArray* a = arr->as.array;
    for (int i = 0; i < a->length; i++) {
        JSValue* cargs[3] = { a->items[i], js_new_number(i), arr };
        js_call(fn, cargs, 3);
    }
    return js_undefined();
}

JSValue* js_array_join(JSValue* arr, JSValue* sep) {
    char* sepstr = sep->type == T_STRING ? sep->as.string : js_to_cstring(sep);
    JSArray* a = arr->as.array;
    SB sb; sb_init(&sb);
    for (int i = 0; i < a->length; i++) {
        if (i) sb_append(&sb, sepstr);
        char* s = js_to_cstring(a->items[i]);
        sb_append(&sb, s);
        free(s);
    }
    return js_new_string(sb.buf);
}

static int values_eq(JSValue* a, JSValue* b);

JSValue* js_array_indexOf(JSValue* arr, JSValue* v) {
    JSArray* a = arr->as.array;
    for (int i = 0; i < a->length; i++) {
        if (values_eq(a->items[i], v)) return js_new_number(i);
    }
    return js_new_number(-1);
}

JSValue* js_array_includes(JSValue* arr, JSValue* v) {
    JSValue* idx = js_array_indexOf(arr, v);
    return js_new_bool(idx->as.number >= 0);
}

JSValue* js_array_slice(JSValue* arr, JSValue* start, JSValue* end) {
    JSArray* a = arr->as.array;
    int n = a->length;
    int s = (start && start->type != T_UNDEFINED) ? idx_of(start) : 0;
    int e = (end && end->type != T_UNDEFINED) ? idx_of(end) : n;
    if (s < 0) s += n; if (e < 0) e += n;
    if (s < 0) s = 0; if (e > n) e = n;
    JSValue* out = js_new_array();
    for (int i = s; i < e; i++) js_array_push(out, a->items[i]);
    return out;
}

/* ---------------- generic (array/string) dispatch ---------------- */
JSValue* js_generic_indexOf(JSValue* v, JSValue* needle) {
    if (v->type == T_STRING) return js_string_indexOf(v, needle);
    return js_array_indexOf(v, needle);
}
JSValue* js_generic_includes(JSValue* v, JSValue* needle) {
    if (v->type == T_STRING) return js_string_includes(v, needle);
    return js_array_includes(v, needle);
}
JSValue* js_generic_slice(JSValue* v, JSValue* start, JSValue* end) {
    if (v->type == T_STRING) return js_string_slice(v, start, end);
    return js_array_slice(v, start, end);
}

/* ---------------- objects (simple linked-list property bag) ---------------- */
JSValue* js_object_new(void) {
    JSValue* r = alloc_val(T_OBJECT);
    JSObject* o = malloc(sizeof(JSObject));
    o->head = NULL;
    r->as.object = o;
    return r;
}

void js_object_set(JSValue* obj, const char* key, JSValue* v) {
    JSObject* o = obj->as.object;
    JSProp* p = o->head;
    while (p) {
        if (strcmp(p->key, key) == 0) { p->value = v; return; }
        p = p->next;
    }
    JSProp* np = malloc(sizeof(JSProp));
    np->key = strdup(key);
    np->value = v;
    np->next = o->head;
    o->head = np;
}

JSValue* js_object_get(JSValue* obj, const char* key) {
    if (obj->type != T_OBJECT) return js_undefined();
    JSProp* p = obj->as.object->head;
    while (p) {
        if (strcmp(p->key, key) == 0) return p->value;
        p = p->next;
    }
    return js_undefined();
}

JSValue* js_object_keys(JSValue* obj) {
    JSValue* out = js_new_array();
    if (obj->type != T_OBJECT) return out;
    /* reverse to preserve insertion order (list is built head-prepended) */
    JSProp* items[4096]; int n = 0;
    JSProp* p = obj->as.object->head;
    while (p && n < 4096) { items[n++] = p; p = p->next; }
    for (int i = n - 1; i >= 0; i--) js_array_push(out, js_new_string(items[i]->key));
    return out;
}

JSValue* js_object_values(JSValue* obj) {
    JSValue* out = js_new_array();
    if (obj->type != T_OBJECT) return out;
    JSProp* items[4096]; int n = 0;
    JSProp* p = obj->as.object->head;
    while (p && n < 4096) { items[n++] = p; p = p->next; }
    for (int i = n - 1; i >= 0; i--) js_array_push(out, items[i]->value);
    return out;
}

/* ---------------- strings ---------------- */
JSValue* js_string_length(JSValue* s) { return js_new_number(strlen(s->as.string)); }

JSValue* js_string_slice(JSValue* s, JSValue* start, JSValue* end) {
    const char* str = s->as.string;
    int n = strlen(str);
    int a = (start && start->type != T_UNDEFINED) ? idx_of(start) : 0;
    int b = (end && end->type != T_UNDEFINED) ? idx_of(end) : n;
    if (a < 0) a += n; if (b < 0) b += n;
    if (a < 0) a = 0; if (b > n) b = n;
    if (b < a) b = a;
    char* out = malloc(b - a + 1);
    memcpy(out, str + a, b - a);
    out[b - a] = 0;
    JSValue* r = js_new_string(out);
    free(out);
    return r;
}

JSValue* js_string_indexOf(JSValue* s, JSValue* needle) {
    char* nstr = needle->type == T_STRING ? needle->as.string : js_to_cstring(needle);
    char* found = strstr(s->as.string, nstr);
    if (!found) return js_new_number(-1);
    return js_new_number(found - s->as.string);
}

JSValue* js_string_split(JSValue* s, JSValue* sep) {
    JSValue* out = js_new_array();
    const char* sepstr = sep->type == T_STRING ? sep->as.string : ",";
    if (sepstr[0] == '\0') {
        for (const char* c = s->as.string; *c; c++) {
            char buf[2] = { *c, 0 };
            js_array_push(out, js_new_string(buf));
        }
        return out;
    }
    char* dup = strdup(s->as.string);
    char* saveptr;
    char* tok = strtok_r(dup, sepstr, &saveptr);
    /* strtok_r treats sep as a set of chars, acceptable simplification for single-char separators */
    while (tok) {
        js_array_push(out, js_new_string(tok));
        tok = strtok_r(NULL, sepstr, &saveptr);
    }
    free(dup);
    return out;
}

JSValue* js_string_toUpperCase(JSValue* s) {
    char* d = strdup(s->as.string);
    for (char* c = d; *c; c++) *c = toupper((unsigned char)*c);
    JSValue* r = js_new_string(d); free(d); return r;
}
JSValue* js_string_toLowerCase(JSValue* s) {
    char* d = strdup(s->as.string);
    for (char* c = d; *c; c++) *c = tolower((unsigned char)*c);
    JSValue* r = js_new_string(d); free(d); return r;
}
JSValue* js_string_includes(JSValue* s, JSValue* needle) {
    char* nstr = needle->type == T_STRING ? needle->as.string : js_to_cstring(needle);
    return js_new_bool(strstr(s->as.string, nstr) != NULL);
}
JSValue* js_string_charAt(JSValue* s, JSValue* idxv) {
    int i = idx_of(idxv);
    int n = strlen(s->as.string);
    if (i < 0 || i >= n) return js_new_string("");
    char buf[2] = { s->as.string[i], 0 };
    return js_new_string(buf);
}
JSValue* js_string_trim(JSValue* s) {
    const char* str = s->as.string;
    while (*str == ' ' || *str == '\t' || *str == '\n' || *str == '\r') str++;
    int n = strlen(str);
    while (n > 0 && (str[n-1]==' '||str[n-1]=='\t'||str[n-1]=='\n'||str[n-1]=='\r')) n--;
    char* out = malloc(n + 1);
    memcpy(out, str, n); out[n] = 0;
    JSValue* r = js_new_string(out); free(out); return r;
}

/* ---------------- numbers ---------------- */
JSValue* js_number_toFixed(JSValue* n, JSValue* digits) {
    int d = digits ? (int)to_number(digits) : 0;
    char buf[64];
    snprintf(buf, sizeof(buf), "%.*f", d, n->as.number);
    return js_new_string(buf);
}
JSValue* js_number_toString(JSValue* n) {
    char* s = num_to_cstr(n->as.number);
    JSValue* r = js_new_string(s);
    free(s);
    return r;
}

/* ---------------- operators ---------------- */
JSValue* js_add(JSValue* a, JSValue* b) {
    if (a->type == T_STRING || b->type == T_STRING) {
        char* as = js_to_cstring(a);
        char* bs = js_to_cstring(b);
        SB sb; sb_init(&sb);
        sb_append(&sb, as); sb_append(&sb, bs);
        JSValue* r = js_new_string(sb.buf);
        free(as); free(bs); free(sb.buf);
        return r;
    }
    return js_new_number(to_number(a) + to_number(b));
}
JSValue* js_sub(JSValue* a, JSValue* b) { return js_new_number(to_number(a) - to_number(b)); }
JSValue* js_mul(JSValue* a, JSValue* b) { return js_new_number(to_number(a) * to_number(b)); }
JSValue* js_div(JSValue* a, JSValue* b) { return js_new_number(to_number(a) / to_number(b)); }
JSValue* js_mod(JSValue* a, JSValue* b) { return js_new_number(fmod(to_number(a), to_number(b))); }

static int values_cmp_num(JSValue* a, JSValue* b) {
    double x = to_number(a), y = to_number(b);
    if (x < y) return -1;
    if (x > y) return 1;
    return 0;
}

JSValue* js_lt(JSValue* a, JSValue* b) {
    if (a->type == T_STRING && b->type == T_STRING) return js_new_bool(strcmp(a->as.string, b->as.string) < 0);
    return js_new_bool(values_cmp_num(a, b) < 0);
}
JSValue* js_gt(JSValue* a, JSValue* b) {
    if (a->type == T_STRING && b->type == T_STRING) return js_new_bool(strcmp(a->as.string, b->as.string) > 0);
    return js_new_bool(values_cmp_num(a, b) > 0);
}
JSValue* js_le(JSValue* a, JSValue* b) {
    if (a->type == T_STRING && b->type == T_STRING) return js_new_bool(strcmp(a->as.string, b->as.string) <= 0);
    return js_new_bool(values_cmp_num(a, b) <= 0);
}
JSValue* js_ge(JSValue* a, JSValue* b) {
    if (a->type == T_STRING && b->type == T_STRING) return js_new_bool(strcmp(a->as.string, b->as.string) >= 0);
    return js_new_bool(values_cmp_num(a, b) >= 0);
}

static int values_eq(JSValue* a, JSValue* b) {
    if (a->type == T_UNDEFINED || a->type == T_NULL || b->type == T_UNDEFINED || b->type == T_NULL)
        return (a->type == T_UNDEFINED || a->type == T_NULL) && (b->type == T_UNDEFINED || b->type == T_NULL);
    if (a->type == T_STRING && b->type == T_STRING) return strcmp(a->as.string, b->as.string) == 0;
    if (a->type == T_BOOL && b->type == T_BOOL) return a->as.boolean == b->as.boolean;
    if (a->type == T_ARRAY || a->type == T_OBJECT || b->type == T_ARRAY || b->type == T_OBJECT)
        return a == b;
    return to_number(a) == to_number(b);
}

JSValue* js_eq(JSValue* a, JSValue* b) { return js_new_bool(values_eq(a, b)); }
JSValue* js_neq(JSValue* a, JSValue* b) { return js_new_bool(!values_eq(a, b)); }
JSValue* js_not(JSValue* a) { return js_new_bool(!js_truthy(a)); }
JSValue* js_neg(JSValue* a) { return js_new_number(-to_number(a)); }

JSValue* js_call(JSValue* fn, JSValue** args, int argc) {
    if (!fn || fn->type != T_FUNCTION) {
        fprintf(stderr, "TypeError: value is not a function\n");
        exit(1);
    }
    return fn->as.fn(args, argc);
}

/* ================== better-sqlite3-like bindings ================== */
typedef struct { sqlite3* db; } DbWrap;
typedef struct { sqlite3_stmt* stmt; sqlite3* db; } StmtWrap;

JSValue* js_db_open(JSValue* path) {
    sqlite3* db;
    const char* p = (path && path->type == T_STRING) ? path->as.string : ":memory:";
    int rc = sqlite3_open(p, &db);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "sqlite3_open failed: %s\n", sqlite3_errmsg(db));
        exit(1);
    }
    JSValue* r = alloc_val(T_DB);
    DbWrap* w = malloc(sizeof(DbWrap));
    w->db = db;
    r->as.ptr = w;
    return r;
}

JSValue* js_db_exec(JSValue* dbv, JSValue* sql) {
    DbWrap* w = (DbWrap*)dbv->as.ptr;
    char* errmsg = NULL;
    int rc = sqlite3_exec(w->db, sql->as.string, NULL, NULL, &errmsg);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "sqlite3_exec error: %s\n", errmsg ? errmsg : "?");
    }
    return js_undefined();
}

JSValue* js_db_prepare(JSValue* dbv, JSValue* sql) {
    DbWrap* w = (DbWrap*)dbv->as.ptr;
    sqlite3_stmt* stmt;
    int rc = sqlite3_prepare_v2(w->db, sql->as.string, -1, &stmt, NULL);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "sqlite3_prepare_v2 error: %s\n", sqlite3_errmsg(w->db));
        exit(1);
    }
    JSValue* r = alloc_val(T_STMT);
    StmtWrap* sw = malloc(sizeof(StmtWrap));
    sw->stmt = stmt; sw->db = w->db;
    r->as.ptr = sw;
    return r;
}

static void bind_args(sqlite3_stmt* stmt, JSValue** args, int argc) {
    for (int i = 0; i < argc; i++) {
        JSValue* v = args[i];
        int idx = i + 1;
        if (!v || v->type == T_UNDEFINED || v->type == T_NULL) sqlite3_bind_null(stmt, idx);
        else if (v->type == T_NUMBER) sqlite3_bind_double(stmt, idx, v->as.number);
        else if (v->type == T_BOOL) sqlite3_bind_int64(stmt, idx, v->as.boolean);
        else { char* s = js_to_cstring(v); sqlite3_bind_text(stmt, idx, s, -1, SQLITE_TRANSIENT); free(s); }
    }
}

static JSValue* row_to_object(sqlite3_stmt* stmt) {
    JSValue* obj = js_object_new();
    int n = sqlite3_column_count(stmt);
    for (int i = 0; i < n; i++) {
        const char* name = sqlite3_column_name(stmt, i);
        int t = sqlite3_column_type(stmt, i);
        JSValue* v;
        if (t == SQLITE_INTEGER || t == SQLITE_FLOAT) v = js_new_number(sqlite3_column_double(stmt, i));
        else if (t == SQLITE_NULL) v = js_null();
        else v = js_new_string((const char*)sqlite3_column_text(stmt, i));
        js_object_set(obj, name, v);
    }
    return obj;
}

JSValue* js_stmt_run(JSValue* stmtv, JSValue** args, int argc) {
    StmtWrap* sw = (StmtWrap*)stmtv->as.ptr;
    sqlite3_reset(sw->stmt);
    bind_args(sw->stmt, args, argc);
    int rc = sqlite3_step(sw->stmt);
    if (rc != SQLITE_DONE && rc != SQLITE_ROW) {
        fprintf(stderr, "sqlite3_step error: %s\n", sqlite3_errmsg(sw->db));
    }
    JSValue* out = js_object_new();
    js_object_set(out, "changes", js_new_number(sqlite3_changes(sw->db)));
    js_object_set(out, "lastInsertRowid", js_new_number((double)sqlite3_last_insert_rowid(sw->db)));
    sqlite3_reset(sw->stmt);
    return out;
}

JSValue* js_stmt_get(JSValue* stmtv, JSValue** args, int argc) {
    StmtWrap* sw = (StmtWrap*)stmtv->as.ptr;
    sqlite3_reset(sw->stmt);
    bind_args(sw->stmt, args, argc);
    int rc = sqlite3_step(sw->stmt);
    JSValue* result = js_undefined();
    if (rc == SQLITE_ROW) result = row_to_object(sw->stmt);
    sqlite3_reset(sw->stmt);
    return result;
}

JSValue* js_stmt_all(JSValue* stmtv, JSValue** args, int argc) {
    StmtWrap* sw = (StmtWrap*)stmtv->as.ptr;
    sqlite3_reset(sw->stmt);
    bind_args(sw->stmt, args, argc);
    JSValue* out = js_new_array();
    while (sqlite3_step(sw->stmt) == SQLITE_ROW) {
        js_array_push(out, row_to_object(sw->stmt));
    }
    sqlite3_reset(sw->stmt);
    return out;
}

void js_db_close(JSValue* dbv) {
    DbWrap* w = (DbWrap*)dbv->as.ptr;
    sqlite3_close(w->db);
}

/* ================== canvas (cairo-backed 2D context) ================== */
typedef struct { cairo_surface_t* surface; int w, h; } CanvasWrap;
typedef struct { cairo_t* cr; CanvasWrap* canvas; } CtxWrap;

JSValue* js_canvas_create(JSValue* wv, JSValue* hv) {
    int w = (int)to_number(wv), h = (int)to_number(hv);
    JSValue* r = alloc_val(T_CANVAS);
    CanvasWrap* cw = malloc(sizeof(CanvasWrap));
    cw->surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    cw->w = w; cw->h = h;
    r->as.ptr = cw;
    return r;
}

JSValue* js_canvas_get_context(JSValue* canvasv) {
    CanvasWrap* cw = (CanvasWrap*)canvasv->as.ptr;
    JSValue* r = alloc_val(T_CTX);
    CtxWrap* ctxw = malloc(sizeof(CtxWrap));
    ctxw->cr = cairo_create(cw->surface);
    ctxw->canvas = cw;
    /* default to opaque white background like a fresh <canvas> before drawing (node-canvas leaves it transparent; we keep transparent) */
    r->as.ptr = ctxw;
    return r;
}

static void parse_color(const char* s, double* r, double* g, double* b, double* a) {
    *a = 1.0;
    if (s[0] == '#') {
        unsigned int rr = 0, gg = 0, bb = 0;
        if (strlen(s) == 7) sscanf(s + 1, "%02x%02x%02x", &rr, &gg, &bb);
        else if (strlen(s) == 4) {
            unsigned int r1, g1, b1;
            sscanf(s + 1, "%1x%1x%1x", &r1, &g1, &b1);
            rr = r1 * 17; gg = g1 * 17; bb = b1 * 17;
        }
        *r = rr / 255.0; *g = gg / 255.0; *b = bb / 255.0;
        return;
    }
    struct { const char* name; double r, g, b; } table[] = {
        {"black",0,0,0}, {"white",1,1,1}, {"red",1,0,0}, {"green",0,0.5,0},
        {"blue",0,0,1}, {"yellow",1,1,0}, {"orange",1,0.65,0}, {"purple",0.5,0,0.5},
        {"gray",0.5,0.5,0.5}, {"grey",0.5,0.5,0.5}, {"cyan",0,1,1}, {"magenta",1,0,1},
        {"pink",1,0.75,0.8}, {"brown",0.65,0.16,0.16}, {"lime",0,1,0}, {"navy",0,0,0.5},
    };
    for (size_t i = 0; i < sizeof(table)/sizeof(table[0]); i++) {
        if (strcmp(table[i].name, s) == 0) { *r = table[i].r; *g = table[i].g; *b = table[i].b; return; }
    }
    *r = 0; *g = 0; *b = 0; /* default black */
}

void js_ctx_set_fill_style(JSValue* ctxv, JSValue* color) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    double r, g, b, a;
    parse_color(color->as.string, &r, &g, &b, &a);
    cairo_set_source_rgba(c->cr, r, g, b, a);
    /* stash so fill()/stroke() reuse whichever was set last isn't tracked separately;
       for simplicity fillStyle also becomes the active source immediately. */
}
void js_ctx_set_stroke_style(JSValue* ctxv, JSValue* color) {
    /* cairo has one "source color" at a time; we switch it right before stroke()/fill() calls
       by re-applying here too since our simplified model calls set_* then fill/stroke immediately. */
    js_ctx_set_fill_style(ctxv, color);
}
void js_ctx_set_line_width(JSValue* ctxv, JSValue* w) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    cairo_set_line_width(c->cr, to_number(w));
}
void js_ctx_fill_rect(JSValue* ctxv, JSValue* x, JSValue* y, JSValue* w, JSValue* h) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    cairo_rectangle(c->cr, to_number(x), to_number(y), to_number(w), to_number(h));
    cairo_fill(c->cr);
}
void js_ctx_stroke_rect(JSValue* ctxv, JSValue* x, JSValue* y, JSValue* w, JSValue* h) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    cairo_rectangle(c->cr, to_number(x), to_number(y), to_number(w), to_number(h));
    cairo_stroke(c->cr);
}
void js_ctx_clear_rect(JSValue* ctxv, JSValue* x, JSValue* y, JSValue* w, JSValue* h) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    cairo_save(c->cr);
    cairo_set_operator(c->cr, CAIRO_OPERATOR_CLEAR);
    cairo_rectangle(c->cr, to_number(x), to_number(y), to_number(w), to_number(h));
    cairo_fill(c->cr);
    cairo_restore(c->cr);
}
void js_ctx_begin_path(JSValue* ctxv) { cairo_new_path(((CtxWrap*)ctxv->as.ptr)->cr); }
void js_ctx_move_to(JSValue* ctxv, JSValue* x, JSValue* y) {
    cairo_move_to(((CtxWrap*)ctxv->as.ptr)->cr, to_number(x), to_number(y));
}
void js_ctx_line_to(JSValue* ctxv, JSValue* x, JSValue* y) {
    cairo_line_to(((CtxWrap*)ctxv->as.ptr)->cr, to_number(x), to_number(y));
}
void js_ctx_arc(JSValue* ctxv, JSValue* x, JSValue* y, JSValue* r, JSValue* a0, JSValue* a1) {
    cairo_arc(((CtxWrap*)ctxv->as.ptr)->cr, to_number(x), to_number(y), to_number(r), to_number(a0), to_number(a1));
}
void js_ctx_fill(JSValue* ctxv) { cairo_fill_preserve(((CtxWrap*)ctxv->as.ptr)->cr); }
void js_ctx_stroke(JSValue* ctxv) { cairo_stroke_preserve(((CtxWrap*)ctxv->as.ptr)->cr); }
void js_ctx_close_path(JSValue* ctxv) { cairo_close_path(((CtxWrap*)ctxv->as.ptr)->cr); }
void js_ctx_fill_text(JSValue* ctxv, JSValue* text, JSValue* x, JSValue* y) {
    CtxWrap* c = (CtxWrap*)ctxv->as.ptr;
    cairo_move_to(c->cr, to_number(x), to_number(y));
    cairo_show_text(c->cr, text->as.string);
}
void js_canvas_save_png(JSValue* canvasv, JSValue* path) {
    CanvasWrap* cw = (CanvasWrap*)canvasv->as.ptr;
    cairo_surface_write_to_png(cw->surface, path->as.string);
}
