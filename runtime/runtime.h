#ifndef JSC_RUNTIME_H
#define JSC_RUNTIME_H

typedef enum {
    T_UNDEFINED, T_NULL, T_BOOL, T_NUMBER, T_STRING, T_ARRAY, T_OBJECT,
    T_FUNCTION, T_DB, T_STMT, T_CANVAS, T_CTX
} JSType;

typedef struct JSValue JSValue;
typedef JSValue* (*JSFnPtr)(JSValue** args, int argc);

typedef struct {
    JSValue** items;
    int length;
    int capacity;
} JSArray;

typedef struct JSProp {
    char* key;
    JSValue* value;
    struct JSProp* next;
} JSProp;

typedef struct {
    JSProp* head;
} JSObject;

struct JSValue {
    JSType type;
    union {
        int boolean;
        double number;
        char* string;
        JSArray* array;
        JSObject* object;
        JSFnPtr fn;
        void* ptr;
    } as;
};

/* value constructors */
JSValue* js_new_number(double v);
JSValue* js_new_string(const char* s);
JSValue* js_new_bool(int b);
JSValue* js_undefined(void);
JSValue* js_null(void);
JSValue* js_new_function(void* fnptr);

/* arrays */
JSValue* js_new_array(void);
JSValue* js_array_push(JSValue* arr, JSValue* v);
JSValue* js_array_pop(JSValue* arr);
JSValue* js_array_get(JSValue* arr, JSValue* idx);
void js_array_set(JSValue* arr, JSValue* idx, JSValue* v);
JSValue* js_array_length(JSValue* arr);
JSValue* js_array_map(JSValue* arr, JSValue* fn);
JSValue* js_array_filter(JSValue* arr, JSValue* fn);
JSValue* js_array_forEach(JSValue* arr, JSValue* fn);
JSValue* js_array_join(JSValue* arr, JSValue* sep);
JSValue* js_array_indexOf(JSValue* arr, JSValue* v);
JSValue* js_array_includes(JSValue* arr, JSValue* v);
JSValue* js_array_slice(JSValue* arr, JSValue* start, JSValue* end);
JSValue* js_generic_indexOf(JSValue* v, JSValue* needle);
JSValue* js_generic_includes(JSValue* v, JSValue* needle);
JSValue* js_generic_slice(JSValue* v, JSValue* start, JSValue* end);

/* objects */
JSValue* js_object_new(void);
void js_object_set(JSValue* obj, const char* key, JSValue* v);
JSValue* js_object_get(JSValue* obj, const char* key);
JSValue* js_object_keys(JSValue* obj);
JSValue* js_object_values(JSValue* obj);

/* strings */
JSValue* js_string_length(JSValue* s);
JSValue* js_string_slice(JSValue* s, JSValue* start, JSValue* end);
JSValue* js_string_indexOf(JSValue* s, JSValue* needle);
JSValue* js_string_split(JSValue* s, JSValue* sep);
JSValue* js_string_toUpperCase(JSValue* s);
JSValue* js_string_toLowerCase(JSValue* s);
JSValue* js_string_includes(JSValue* s, JSValue* needle);
JSValue* js_string_charAt(JSValue* s, JSValue* idx);
JSValue* js_string_trim(JSValue* s);

/* numbers */
JSValue* js_number_toFixed(JSValue* n, JSValue* digits);
JSValue* js_number_toString(JSValue* n);

/* operators */
JSValue* js_add(JSValue* a, JSValue* b);
JSValue* js_sub(JSValue* a, JSValue* b);
JSValue* js_mul(JSValue* a, JSValue* b);
JSValue* js_div(JSValue* a, JSValue* b);
JSValue* js_mod(JSValue* a, JSValue* b);
JSValue* js_lt(JSValue* a, JSValue* b);
JSValue* js_gt(JSValue* a, JSValue* b);
JSValue* js_le(JSValue* a, JSValue* b);
JSValue* js_ge(JSValue* a, JSValue* b);
JSValue* js_eq(JSValue* a, JSValue* b);
JSValue* js_neq(JSValue* a, JSValue* b);
JSValue* js_not(JSValue* a);
JSValue* js_neg(JSValue* a);
int js_truthy(JSValue* v);

/* misc */
void js_console_log(JSValue** args, int argc);
JSValue* js_call(JSValue* fn, JSValue** args, int argc);
JSValue* js_arg_or_undef(JSValue** args, int argc, int i);
void js_runtime_init(void);
char* js_to_display_string(JSValue* v);
char* js_to_cstring(JSValue* v);

/* sqlite (better-sqlite3-like) */
JSValue* js_db_open(JSValue* path);
JSValue* js_db_exec(JSValue* db, JSValue* sql);
JSValue* js_db_prepare(JSValue* db, JSValue* sql);
JSValue* js_stmt_run(JSValue* stmt, JSValue** args, int argc);
JSValue* js_stmt_get(JSValue* stmt, JSValue** args, int argc);
JSValue* js_stmt_all(JSValue* stmt, JSValue** args, int argc);
void js_db_close(JSValue* db);

/* canvas (2D context, cairo-backed) */
JSValue* js_canvas_create(JSValue* w, JSValue* h);
JSValue* js_canvas_get_context(JSValue* canvas);
void js_ctx_set_fill_style(JSValue* ctx, JSValue* color);
void js_ctx_set_stroke_style(JSValue* ctx, JSValue* color);
void js_ctx_set_line_width(JSValue* ctx, JSValue* w);
void js_ctx_fill_rect(JSValue* ctx, JSValue* x, JSValue* y, JSValue* w, JSValue* h);
void js_ctx_stroke_rect(JSValue* ctx, JSValue* x, JSValue* y, JSValue* w, JSValue* h);
void js_ctx_clear_rect(JSValue* ctx, JSValue* x, JSValue* y, JSValue* w, JSValue* h);
void js_ctx_begin_path(JSValue* ctx);
void js_ctx_move_to(JSValue* ctx, JSValue* x, JSValue* y);
void js_ctx_line_to(JSValue* ctx, JSValue* x, JSValue* y);
void js_ctx_arc(JSValue* ctx, JSValue* x, JSValue* y, JSValue* r, JSValue* a0, JSValue* a1);
void js_ctx_fill(JSValue* ctx);
void js_ctx_stroke(JSValue* ctx);
void js_ctx_close_path(JSValue* ctx);
void js_ctx_fill_text(JSValue* ctx, JSValue* text, JSValue* x, JSValue* y);
void js_canvas_save_png(JSValue* canvas, JSValue* path);

#endif
