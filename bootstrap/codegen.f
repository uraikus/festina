// festina/codegen.py, ported to Festina -- the fourth and last stage.
//
// The other three ports needed a canonical dump invented for them.
// This one does not: codegen's output IS text, so the comparison is
// the LLVM IR itself, line for line (bootstrap/irdump.py). That makes
// it the strongest oracle in the project and the only one with no
// design decisions in it.
//
// It also makes the coverage number the most misleading, and the
// reason is measured rather than guessed: 382 of every module's lines
// are the identical runtime declaration block, which is 94% of the
// smallest file in the corpus. A port that could do nothing but print
// that block would already be within a couple of dozen lines of
// `benchmarks/hello.f`. So what counts here is file-specific lines
// reproduced, not files matched -- see bootstrap/irdiff.py's
// line_budget().
//
// Anything not yet ported emits an UNPORTED record and irdiff.py
// classifies the file as unported rather than as a match or a
// difference, the same rule astdiff.py and semdiff.py use. Coverage
// therefore only moves when something is really implemented.

// escape.f rather than semantic.f: it imports semantic.f itself, and
// codegen needs its answer before it can bind a single parameter
// (claude.md #74).
import escape.f

// ---------------------------------------------------------------------
// Output state.

arr[text] CG_IR = []
arr[text] CG_STRS = []
int CG_STR_N = 0

// String constants are INTERNED, not merely counted. codegen.py's
// `string_const` keys a dict on the literal's own text and names each
// one `@.str.<len(dict)>`, so a literal used twice is one global used
// twice. Counting instead would agree with the original on every
// program where no literal repeats -- which every file matching before
// this happened to be -- and then renumber every constant from the
// first repeat onward.
map[text] CG_STR_MAP = {}
bool CG_UNPORTED = false

// Whether the statement currently being walked has given up.
//
// CG_UNPORTED is sticky for the whole file -- it says "do not print
// this module" and never clears. This one clears before every
// statement, and it is what every "my subexpression failed, stop"
// guard reads.
//
// The distinction exists because collapsing the two made irdiff's
// blocker table a first-blocker histogram for the THIRD time. #291
// found it when cgUnported kept only the earliest reason; #292 found
// it again when early-return guards stopped the walk outright; and it
// survived both fixes at the expression level, because cgExpr returned
// immediately whenever CG_UNPORTED was already set -- so after one
// statement failed, no expression in the file was ever examined again.
// examples/ascii_scan.f reported `declaration of a non-scalar type` as
// its ONLY blocker while also needing method calls on every line of
// its loop.
//
// A per-statement flag gets both: the failing statement abandons its
// own walk (so no cascade of nonsense reasons from a subexpression
// that returned nothing), and the next statement starts clean.
bool CG_STUCK = false
text CG_WHY = ''
arr[text] CG_WHYS = []

// Output stops once something is unported, but the WALK does not: the
// module will not be printed either way, and continuing is what lets
// irdiff report every distinct blocker rather than only the first
// (decisions.md #291). Stopping the walk here is exactly the mistake
// that turned that table's two columns back into one.
void func cgEmit(line:text) {
    if CG_UNPORTED { return }
    CG_IR.push(line)
}

// EVERY distinct reason, not just the first.
//
// It used to keep only the earliest, which made irdiff's blocker table
// a histogram of FIRST blockers -- and that reads as a promise it
// cannot keep. Closing `StructDecl`, listed against 22 files, unlocked
// exactly zero of them: each one simply hit whatever was behind it.
// Collecting them all lets the table distinguish "blocked by this" from
// "blocked ONLY by this", and only the second number predicts what
// implementing something will actually buy.
void func cgUnported(what:text) {
    CG_UNPORTED = true
    CG_STUCK = true
    int i = 0
    while i < CG_WHYS.length {
        if CG_WHYS[i] == what { return }
        i++
    }
    CG_WHYS.push(what)
    if CG_WHY == '' { CG_WHY = what }
}

// ---------------------------------------------------------------------
// String constants.
//
// codegen.py emits `@.str.N = private unnamed_addr constant [L x i8]
// c"..."` where L is the BYTE length plus one for the terminator. A
// text's .length is code points, not bytes (specification.md 8.4), so
// a non-ASCII literal would compute the wrong L here. Rather than
// guess, a literal with any byte above 127 is reported unported --
// conservative in the one safe direction: a missing construct shows up
// as "not done yet" rather than as a silently wrong module.

// The C-string form of one BYTE, and the byte count it costs.
//
// The rule is per byte, not per character: a printable ASCII byte other
// than `"` and `\\` goes in literally, and every other byte -- control
// characters, and every byte of a multi-byte UTF-8 sequence -- goes in
// as `\\XX` with uppercase hex. Anything narrower than that diverges on
// the first literal that is not plain ASCII text.
text func cgCByte(b:int) {
    if b >= 32 && b < 127 && b != 34 && b != 92 { return b.toChar() }
    return `\\${cgHexN(b, 2)}`
}

// A text's bytes, escaped. Festina has no byte-level access to a
// `text` and no bitwise operators, so each code point is re-encoded to
// UTF-8 here arithmetically -- the shifts and masks spelled as
// divisions and remainders by powers of two.
text func cgCEscape(s:text) {
    text out = ''
    int i = 0
    while i < s.length {
        int c = s.charCodeAt(i)
        if c < 128 {
            out = out + cgCByte(c)
        } else if c < 2048 {
            out = out + cgCByte(192 + Math.floorDiv(c, 64))
            out = out + cgCByte(128 + c % 64)
        } else if c < 65536 {
            out = out + cgCByte(224 + Math.floorDiv(c, 4096))
            out = out + cgCByte(128 + Math.floorDiv(c, 64) % 64)
            out = out + cgCByte(128 + c % 64)
        } else {
            out = out + cgCByte(240 + Math.floorDiv(c, 262144))
            out = out + cgCByte(128 + Math.floorDiv(c, 4096) % 64)
            out = out + cgCByte(128 + Math.floorDiv(c, 64) % 64)
            out = out + cgCByte(128 + c % 64)
        }
        i++
    }
    return out
}

// The DECLARED length is a byte count, and `text.length` is a code
// point count -- so a literal with any multi-byte character would get
// an array too short for its own contents if this used `.length`.
int func cgUtf8Bytes(s:text) {
    int n = 0
    int i = 0
    while i < s.length {
        int c = s.charCodeAt(i)
        if c < 128 { n = n + 1 }
        else if c < 2048 { n = n + 2 }
        else if c < 65536 { n = n + 3 }
        else { n = n + 4 }
        i++
    }
    return n
}

text func cgStringConst(v:text) {
    if CG_STR_MAP[v] != null { return CG_STR_MAP[v] }
    text name = `@.str.${CG_STR_N}`
    CG_STR_N++
    CG_STR_MAP[v] = name
    int bytes = cgUtf8Bytes(v) + 1
    CG_STRS.push(`${name} = private unnamed_addr constant [${bytes} x i8] c"${cgCEscape(v)}\\00"`)
    return name
}

// ---------------------------------------------------------------------
// The runtime declaration block, verbatim.
//
// Identical in every module festina/codegen.py produces -- confirmed as
// the longest common prefix of all 83 compilable corpus files once each
// one's own source-path comment is set aside.

arr[text] CG_PRE = [
    '',
    'declare void @festina_runtime_init()',
    'declare void @festina_log_int(i64)',
    'declare void @festina_log_float(double)',
    'declare void @festina_log_bool(i8)',
    'declare i32 @_setjmp(ptr, ptr) returns_twice',
    'declare void @festina_try_push(ptr)',
    'declare void @festina_try_pop()',
    'declare ptr @festina_try_error()',
    'declare void @festina_cleanup_push(ptr, ptr)',
    'declare void @festina_cleanup_pop()',
    'declare void @festina_cleanup_pop_n(i64)',
    'declare void @festina_throw(ptr)',
    'declare void @festina_log_text(ptr)',
    'declare void @festina_fail(ptr)',
    'declare void @festina_troubleshoot(ptr, ptr)',
    'declare void @festina_fail_structured(ptr, ptr)',
    'declare ptr @festina_json_cursor_new(ptr)',
    'declare void @festina_json_cursor_free(ptr)',
    'declare void @festina_json_expect_end(ptr)',
    'declare void @festina_json_object_start(ptr)',
    'declare void @festina_json_array_start(ptr)',
    'declare i8 @festina_json_object_next(ptr, ptr)',
    'declare i8 @festina_json_array_next(ptr, ptr)',
    'declare ptr @festina_json_read_key(ptr)',
    'declare i8 @festina_json_key_matches(ptr, ptr)',
    'declare void @festina_json_skip_field_value(ptr)',
    'declare i64 @festina_json_read_int(ptr)',
    'declare double @festina_json_read_float(ptr)',
    'declare i8 @festina_json_read_bool(ptr)',
    'declare ptr @festina_json_read_text(ptr)',
    'declare ptr @festina_str_from_int(i64)',
    'declare ptr @festina_str_from_float(double)',
    'declare ptr @festina_str_from_bool(i8)',
    'declare ptr @festina_str_concat(ptr, ptr)',
    'declare i64 @festina_ascii_length(ptr)',
    'declare ptr @festina_ascii_alloc(i64)',
    'declare void @festina_ascii_release(ptr)',
    'declare ptr @festina_ascii_char_at(ptr, i64)',
    'declare ptr @festina_ascii_concat(ptr, ptr)',
    'declare i8 @festina_ascii_eq(ptr, ptr)',
    'declare ptr @festina_ascii_slice(ptr, i64, i64)',
    'declare ptr @festina_ascii_to_text(ptr)',
    'declare ptr @festina_ascii_from_text(ptr)',
    'declare ptr @festina_ascii_clone(ptr)',
    'declare ptr @festina_text_append(ptr, i64, ptr, i64, ptr)',
    'declare ptr @festina_text_own(ptr)',
    'declare double @acos(double)',
    'declare double @asin(double)',
    'declare double @atan(double)',
    'declare double @llvm.cos.f64(double)',
    'declare double @llvm.exp.f64(double)',
    'declare double @llvm.fabs.f64(double)',
    'declare double @llvm.log.f64(double)',
    'declare double @llvm.log10.f64(double)',
    'declare double @llvm.log2.f64(double)',
    'declare double @llvm.sin.f64(double)',
    'declare double @llvm.sqrt.f64(double)',
    'declare double @tan(double)',
    'declare double @atan2(double, double)',
    'declare double @llvm.maxnum.f64(double, double)',
    'declare double @llvm.minnum.f64(double, double)',
    'declare double @llvm.pow.f64(double, double)',
    'declare double @festina_random()',
    'declare ptr @festina_blob_open(ptr)',
    'declare ptr @festina_blob_load_dispatch(ptr, ptr)',
    'declare void @festina_register_async_io_hooks()',
    'declare ptr @festina_thread_register(ptr, ptr, ptr, ptr, ptr)',
    'declare void @festina_thread_spawn(ptr)',
    'declare ptr @festina_thread_get_main_handle()',
    'declare i8 @festina_thread_is_main(ptr)',
    'declare void @festina_thread_post(ptr, ptr, ptr, i64)',
    'declare ptr @festina_thread_pool_select(ptr, i64, i64)',
    'declare void @festina_thread_post_outbound(ptr, ptr, i64)',
    'declare i64 @festina_thread_alloc_txn_id()',
    'declare void @festina_thread_register_callback(ptr, i64, ptr, ptr, i8)',
    'declare void @festina_thread_reply(ptr, ptr, ptr, ptr)',
    'declare void @festina_set_global_message_handler(ptr)',
    'declare void @festina_thread_set_db_close(ptr, ptr)',
    'declare void @festina_thread_db_close(ptr)',
    'declare void @festina_thread_set_http_context(ptr, ptr, ptr, ptr)',
    'declare void @festina_thread_http_service_pass(i32)',
    'declare void @festina_thread_http_teardown()',
    'declare ptr @festina_conn_detach(ptr)',
    'declare void @festina_thread_give_request(ptr, ptr, ptr)',
    'declare void @festina_thread_deliver_given_request(ptr)',
    'declare void @festina_thread_kill(ptr)',
    'declare void @festina_thread_wait_drained(ptr)',
    'declare void @festina_thread_live(ptr, ptr)',
    'declare i8 @festina_thread_is_alive(ptr)',
    'declare void @festina_register_thread_hooks()',
    'declare ptr @festina_blob_clone(ptr)',
    'declare ptr @festina_image_clone(ptr)',
    'declare ptr @festina_audio_clone(ptr)',
    'declare ptr @festina_url_clone(ptr)',
    'declare void @festina_noop_release(ptr)',
    'declare ptr @festina_blob_from_bytes(ptr, i64)',
    'declare void @festina_blob_release(ptr)',
    'declare ptr @festina_blob_to_text(ptr)',
    'declare ptr @festina_blob_bytes(ptr, ptr)',
    'declare i64 @festina_blob_length(ptr)',
    'declare i64 @festina_blob_byte_at(ptr, i64)',
    'declare ptr @festina_blob_slice(ptr, i64, i64)',
    'declare i8 @festina_blob_write(ptr, ptr)',
    'declare i8 @festina_blob_append(ptr, ptr)',
    'declare i8 @festina_blob_exists(ptr)',
    'declare i8 @festina_blob_delete(ptr)',
    'declare i8 @festina_blob_save(ptr, ptr)',
    'declare i8 @festina_blob_save_copy(ptr, ptr)',
    'declare i8 @festina_map_delete(ptr, ptr, i64, ptr, ptr, ptr)',
    'declare void @festina_regex_mark_cached(ptr)',
    'declare ptr @festina_read_file(ptr)',
    'declare i8 @festina_write_file(ptr, ptr)',
    'declare i8 @festina_append_file(ptr, ptr)',
    'declare i8 @festina_file_exists(ptr)',
    'declare i8 @festina_delete_file(ptr)',
    'declare i64 @festina_now_ms()',
    'declare ptr @festina_format_time(i64, ptr)',
    'declare i8 @festina_mkdir(ptr)',
    'declare ptr @festina_ls(ptr)',
    'declare i8 @festina_str_eq(ptr, ptr)',
    'declare i64 @festina_text_to_int(ptr)',
    'declare ptr @festina_text_trim(ptr)',
    'declare ptr @festina_text_char_at(ptr, i64)',
    'declare i64 @festina_text_char_code_at(ptr, i64)',
    'declare ptr @festina_int_to_char(i64)',
    'declare i64 @festina_text_length(ptr)',
    'declare ptr @festina_argv_array(i32, ptr)',
    'declare i64 @festina_process_exec(ptr)',
    'declare i64 @strlen(ptr)',
    'declare void @festina_open_port(i64)',
    'declare void @festina_close_port(i64)',
    'declare void @festina_open_secure_port(i64, ptr, i64)',
    'declare void @festina_register_tls_hooks()',
    'declare ptr @festina_parse_url(ptr)',
    'declare ptr @festina_url_protocol(ptr)',
    'declare ptr @festina_url_username(ptr)',
    'declare ptr @festina_url_password(ptr)',
    'declare ptr @festina_url_hostname(ptr)',
    'declare i64 @festina_url_port(ptr)',
    'declare ptr @festina_url_pathname(ptr)',
    'declare ptr @festina_url_hash(ptr)',
    'declare ptr @festina_url_search_params(ptr)',
    'declare void @festina_release_url(ptr)',
    'declare void @festina_register_request_handler(ptr)',
    'declare void @festina_register_upgrade_handler(ptr)',
    'declare void @festina_register_message_handler(ptr)',
    'declare void @festina_register_socketclose_handler(ptr)',
    'declare void @festina_run_http_loop()',
    'declare void @festina_register_http_service_hooks()',
    'declare ptr @festina_http_literal_new(ptr, ptr, i64, ptr, ptr, i64, ptr)',
    'declare ptr @festina_http_url(ptr)',
    'declare ptr @festina_http_method(ptr)',
    'declare i64 @festina_http_code(ptr)',
    'declare ptr @festina_http_headers(ptr)',
    'declare ptr @festina_http_callback(ptr)',
    'declare void @festina_http_ok(ptr)',
    'declare void @festina_http_redirect(ptr, ptr)',
    'declare void @festina_http_upgrade(ptr)',
    'declare ptr @festina_http_to_blob(ptr)',
    'declare ptr @festina_http_to_img(ptr)',
    'declare ptr @festina_http_to_aud(ptr)',
    'declare ptr @festina_http_to_text(ptr)',
    'declare void @festina_http_send(ptr, ptr)',
    'declare void @festina_http_send_client_dispatch(ptr)',
    'declare void @festina_release_http(ptr)',
    'declare ptr @festina_socket_state(ptr)',
    'declare void @festina_socket_send_text(ptr, ptr)',
    'declare void @festina_socket_send_binary(ptr, ptr, i64)',
    'declare void @festina_socket_close(ptr)',
    'declare void @festina_release_conn_handle(ptr)',
    'declare ptr @festina_db_open(ptr)',
    'declare void @festina_sync_table(ptr, ptr, ptr, ptr, i32)',
    'declare void @festina_db_close(ptr)',
    'declare ptr @festina_sqlite_prepare(ptr, ptr)',
    'declare ptr @festina_sqlite_prepare_cached(ptr, ptr, ptr)',
    'declare ptr @festina_sb_new()',
    'declare void @festina_sb_append(ptr, ptr)',
    'declare void @festina_sb_append_n(ptr, ptr, i64)',
    'declare void @festina_sb_append_json_text(ptr, ptr)',
    'declare void @festina_sb_append_json_int(ptr, i64)',
    'declare void @festina_sb_append_json_float(ptr, double)',
    'declare void @festina_sb_append_json_bool(ptr, i8)',
    'declare void @festina_sb_append_json_bool64(ptr, i64)',
    'declare void @festina_sb_append_handle(ptr, ptr, ptr)',
    'declare ptr @festina_sb_finish(ptr)',
    'declare ptr @festina_text_split(ptr, ptr)',
    'declare ptr @festina_regex_split(ptr, ptr)',
    'declare ptr @festina_arr_join(ptr, ptr, ptr)',
    'declare void @festina_sqlite_bind_int(ptr, i32, i64)',
    'declare void @festina_sqlite_bind_float(ptr, i32, double)',
    'declare void @festina_sqlite_bind_text(ptr, i32, ptr)',
    'declare void @festina_sqlite_bind_null(ptr, i32)',
    'declare void @festina_sqlite_bind_blob(ptr, i32, ptr, i64)',
    'declare void @festina_set_audio_decoder(ptr)',
    'declare void @festina_set_image_decoder(ptr)',
    'declare ptr @festina_audio_bytes(ptr, ptr)',
    'declare ptr @festina_image_bytes(ptr, ptr)',
    'declare ptr @festina_audio_from_bytes(ptr, i64, ptr)',
    'declare void @festina_audio_free(ptr)',
    'declare ptr @festina_image_from_bytes(ptr, i64, ptr)',
    'declare void @festina_sqlite_exec(ptr)',
    'declare void @festina_sqlite_collect_rows(ptr, i32, ptr, ptr, ptr, ptr, i8)',
    'declare i8 @festina_row_undefined(ptr, ptr, i32, ptr)',
    'declare ptr @festina_regex_compile(ptr, ptr)',
    'declare ptr @festina_regex_compile_memo(ptr, ptr, ptr)',
    'declare void @festina_regex_free(ptr)',
    'declare i8 @festina_regex_test(ptr, ptr)',
    'declare ptr @festina_regex_match(ptr, ptr)',
    'declare ptr @festina_str_replace(ptr, ptr, ptr)',
    'declare ptr @festina_regex_replace(ptr, ptr, ptr)',
    'declare void @festina_graphics_init()',
    'declare void @festina_run_event_loop()',
    'declare void @festina_draw_rect(i64, i64, i64, i64)',
    'declare void @festina_draw_rect_color(i64, i64, i64, i64, i64)',
    'declare void @festina_draw_rect_colors(i64, i64, i64, i64, i64, i64)',
    'declare void @festina_draw_pixel(i64, i64)',
    'declare void @festina_draw_pixel_color(i64, i64, i64)',
    'declare void @festina_draw_circle(i64, i64, i64)',
    'declare void @festina_draw_circle_color(i64, i64, i64, i64)',
    'declare void @festina_draw_circle_colors(i64, i64, i64, i64, i64)',
    'declare void @festina_draw_text(ptr, i64, i64)',
    'declare void @festina_set_fill_rgb(i64, i64, i64)',
    'declare void @festina_set_border_rgb(i64, i64, i64)',
    'declare void @festina_set_fill_color(i64)',
    'declare void @festina_set_border_color(i64)',
    'declare void @festina_set_font_value(ptr)',
    'declare void @festina_set_line_width(i64)',
    'declare void @festina_set_font(i64, ptr, ptr)',
    'declare i64 @festina_measure_text_width(ptr)',
    'declare i64 @festina_measure_text_height(ptr)',
    'declare ptr @festina_load_image(ptr)',
    'declare ptr @festina_image_load_dispatch(ptr, ptr)',
    'declare i8 @festina_save_canvas(ptr)',
    'declare ptr @festina_canvas_to_image()',
    'declare void @festina_render()',
    'declare void @festina_clear_canvas()',
    'declare void @festina_clear_rect(i64, i64, i64, i64)',
    'declare void @festina_clear_circle(i64, i64, i64)',
    'declare void @festina_clear_pixel(i64, i64)',
    'declare void @festina_set_alpha(double)',
    'declare void @festina_fill_linear_gradient(i64, i64, i64, i64, i64, i64)',
    'declare void @festina_fill_radial_gradient(i64, i64, i64, i64, i64)',
    'declare void @festina_translate(i64, i64)',
    'declare void @festina_rotate(double)',
    'declare void @festina_scale(double, double)',
    'declare void @festina_reset_transform()',
    'declare void @festina_save_state()',
    'declare void @festina_restore_state()',
    'declare void @festina_begin_path()',
    'declare void @festina_move_to(i64, i64)',
    'declare void @festina_line_to(i64, i64)',
    'declare void @festina_curve_to(i64, i64, i64, i64, i64, i64)',
    'declare void @festina_close_path()',
    'declare void @festina_fill_path()',
    'declare void @festina_stroke_path()',
    'declare i64 @festina_image_width(ptr)',
    'declare i64 @festina_image_height(ptr)',
    'declare ptr @festina_image_clip(ptr, i64, i64, i64, i64)',
    'declare ptr @festina_blank_image(i64, i64)',
    'declare i64 @festina_get_pixel_color(i64, i64)',
    'declare i64 @festina_image_get_pixel_color(ptr, i64, i64)',
    'declare void @festina_image_resize(ptr, i64, i64)',
    'declare void @festina_image_draw_rect(ptr, i64, i64, i64, i64)',
    'declare void @festina_image_draw_rect_color(ptr, i64, i64, i64, i64, i64)',
    'declare void @festina_image_draw_rect_colors(ptr, i64, i64, i64, i64, i64, i64)',
    'declare void @festina_image_draw_pixel(ptr, i64, i64)',
    'declare void @festina_image_draw_pixel_color(ptr, i64, i64, i64)',
    'declare void @festina_image_draw_circle(ptr, i64, i64, i64)',
    'declare void @festina_image_draw_circle_color(ptr, i64, i64, i64, i64)',
    'declare void @festina_image_draw_circle_colors(ptr, i64, i64, i64, i64, i64)',
    'declare void @festina_image_draw_text(ptr, ptr, i64, i64)',
    'declare void @festina_image_translate(ptr, i64, i64)',
    'declare void @festina_image_rotate(ptr, double)',
    'declare void @festina_image_scale(ptr, double, double)',
    'declare void @festina_image_reset_transform(ptr)',
    'declare void @festina_image_save_state(ptr)',
    'declare void @festina_image_restore_state(ptr)',
    'declare void @festina_image_clear(ptr)',
    'declare void @festina_image_clear_rect(ptr, i64, i64, i64, i64)',
    'declare void @festina_image_clear_circle(ptr, i64, i64, i64)',
    'declare void @festina_image_clear_pixel(ptr, i64, i64)',
    'declare void @festina_image_draw_image(ptr, ptr, i64, i64)',
    'declare void @festina_image_draw_image_scaled(ptr, ptr, i64, i64, i64, i64)',
    'declare void @festina_image_free(ptr)',
    'declare i8 @festina_image_save(ptr, ptr)',
    'declare i8 @festina_image_save_copy(ptr, ptr)',
    'declare void @festina_draw_image(ptr, i64, i64)',
    'declare void @festina_draw_image_scaled(ptr, i64, i64, i64, i64)',
    'declare void @festina_draw_image_region(ptr, i64, i64, i64, i64, i64, i64, i64, i64)',
    'declare void @festina_register_mouse_down_handler(ptr)',
    'declare void @festina_register_mouse_up_handler(ptr)',
    'declare void @festina_register_mouse_handler(ptr)',
    'declare void @festina_register_mouse_wheel_up_handler(ptr)',
    'declare void @festina_register_mouse_wheel_down_handler(ptr)',
    'declare void @festina_register_key_down_handler(ptr)',
    'declare void @festina_register_key_up_handler(ptr)',
    'declare void @festina_register_resize_handler(ptr)',
    'declare void @festina_register_close_handler(ptr)',
    'declare void @festina_register_exit_handler(ptr)',
    'declare void @festina_program_exit(i64)',
    'declare void @festina_install_shutdown_handler()',
    'declare i64 @festina_client_width()',
    'declare i64 @festina_client_height()',
    'declare i64 @festina_screen_width()',
    'declare i64 @festina_screen_height()',
    'declare double @festina_device_pixel_ratio()',
    'declare void @festina_set_client_width(i64)',
    'declare void @festina_set_client_height(i64)',
    'declare void @festina_enter_fullscreen()',
    'declare void @festina_exit_fullscreen()',
    'declare void @festina_show_cursor()',
    'declare void @festina_hide_cursor()',
    'declare i64 @festina_set_timeout(ptr, i64)',
    'declare i64 @festina_set_interval(ptr, i64)',
    'declare void @festina_clear_timeout(i64)',
    'declare void @festina_clear_interval(i64)',
    'declare void @festina_run_timer_loop()',
    'declare ptr @festina_load_audio(ptr)',
    'declare ptr @festina_audio_load_dispatch(ptr, ptr)',
    'declare i64 @festina_audio_play_on(ptr, i64, i8, i8)',
    'declare void @festina_audio_stop_clip(ptr)',
    'declare i8 @festina_audio_save(ptr, ptr)',
    'declare i8 @festina_audio_save_copy(ptr, ptr)',
    'declare void @festina_stop_audio_player(i64)',
    'declare i8 @festina_audio_is_playing(ptr)',
    'declare i8 @festina_channel_is_playing(i64)',
    'declare void @festina_set_max_audio_players(i64)',
    'declare i64 @festina_get_max_audio_players()',
    'declare ptr @malloc(i64)',
    'declare ptr @calloc(i64, i64)',
    'declare void @free(ptr)',
    'declare void @festina_clear_text(ptr)',
    'declare void @festina_begin_clearing()',
    'declare void @festina_end_clearing()',
    'declare void @festina_free_z(ptr)',
    'declare void @festina_retain(ptr)',
    'declare void @festina_release(ptr)',
    'declare i8 @festina_cycle_candidate(ptr)',
    'declare i8 @festina_cycle_begin_gray(ptr)',
    'declare void @festina_cycle_dec(ptr)',
    'declare void @festina_cycle_inc(ptr)',
    'declare i64 @festina_cycle_begin_scan(ptr)',
    'declare void @festina_cycle_set_black(ptr)',
    'declare i8 @festina_cycle_needs_black(ptr)',
    'declare i8 @festina_cycle_begin_white(ptr)',
    'declare void @festina_cycle_visit_array(ptr, ptr)',
    'declare void @festina_cycle_visit_map(ptr, ptr)',
    'declare void @festina_cycle_dispose_array(ptr)',
    'declare void @festina_cycle_dispose_map(ptr)',
    'declare i8 @festina_release_check(ptr)',
    'declare void @festina_release_array(ptr)',
    'declare void @festina_array_push(ptr, ptr, i64, ptr)',
    'declare void @festina_array_unshift(ptr, ptr, i64, ptr)',
    'declare i8 @festina_array_pop(ptr, ptr, i64, ptr)',
    'declare i8 @festina_array_shift(ptr, ptr, i64, ptr)',
    'declare void @festina_array_splice(ptr, ptr, i64, i64, i64, ptr)',
    'declare void @festina_array_splice_insert(ptr, ptr, i64, i64, i64, ptr, i64, ptr)',
    'declare i64 @festina_array_index_of(ptr, i64, ptr, i8)',
    'declare void @festina_array_sort(ptr, i64, ptr, ptr)',
    'declare void @festina_release_map(ptr)',
    'declare ptr @festina_getenv(ptr)',
    'declare void @festina_map_set(ptr, ptr, ptr, ptr, ptr, i64)',
    'declare i64 @festina_map_get(ptr, i64, ptr, i64)',
    'declare void @festina_map_for_each(ptr, i64, ptr)',
    'declare void @festina_map_keys(ptr, i64, ptr)',
    'declare void @festina_map_values(ptr, i64, i64, i8, i8, ptr)',
    'declare void @festina_map_free_entries(ptr, i64)',
    'declare void @festina_map_clone(ptr, i64, ptr, ptr, ptr, ptr, ptr)',
    'declare i64 @llvm.fptosi.sat.i64.f64(double)',
    'declare double @llvm.floor.f64(double)',
    'declare double @llvm.ceil.f64(double)',
    'declare double @llvm.round.f64(double)',
    'declare double @llvm.trunc.f64(double)',
    '',
    '@__festina_db = global ptr null',
    '',
    '%struct._FestinaArray = type { i64, ptr }',
    '%struct._FestinaAmorArray = type { i64, ptr, i64 }',
    '%struct._FestinaMap = type { i64, ptr, i64, i64 }',
    '%struct._FestinaFont = type { i64, i64, i64, ptr }',
    '%struct._FestinaEnumBox = type { ptr, i64 }'
]

// ---------------------------------------------------------------------
// Numeric literals.
//
// Two conversions LLVM needs and Festina has no primitive for. Neither
// required a language change; both are exact-or-unported, never
// approximate, which is the same rule semantic.f's inferExpr follows:
// a partial implementation inside a differential test has to be wrong
// in one direction only.

text HEXD = '0123456789ABCDEF'

text func cgHexN(value:int, digits:int) {
    text out = ''
    int v = value
    int i = 0
    while i < digits {
        int d = v % 16
        out = HEXD.charCodeAt(d).toChar() + out
        // Math.floorDiv, not `/`: `/` always answers float (claude.md
        // #61), so plain division here would turn the running value
        // into a double and lose the low bits of a 52-bit mantissa.
        v = Math.floorDiv(v, 16)
        i++
    }
    return out
}

// A decimal literal's text to the double it denotes, by the classic
// exact fast path: all the digits as an integer, divided by a power of
// ten. Both operands are exactly representable while the digits fit in
// 2^53 and the scale in 10^22, and IEEE division is correctly rounded,
// so the result is the correctly-rounded double -- verified against
// Python's own strtod over 50,000 generated literals with no
// mismatch. Outside that window it refuses rather than approximating;
// a real strtod is what a literal like 0.30000000000000004 needs.
bool CG_NUM_OK = true

// Festina's `text` has no `.slice()`, so dropping the last character
// is a rebuild. Only ever called on a digit string, so a code-point
// walk is a byte walk here.
text func cgDropLast(s:text) {
    text out = ''
    int i = 0
    while i < s.length - 1 {
        out = out + s.charCodeAt(i).toChar()
        i++
    }
    return out
}

float func cgParseFloat(s:text) {
    CG_NUM_OK = true
    arr[text] parts = s.split('.')
    if parts.length == 1 {
        if parts[0].length > 18 { CG_NUM_OK = false  return 0.0 }
        return parts[0].toInt().toFloat()
    }
    if parts.length != 2 { CG_NUM_OK = false  return 0.0 }
    // Trailing zeros in the FRACTION are stripped first, and that is
    // exact rather than approximate: dropping one divides both the
    // digit integer and the power of ten by the same 10, so the
    // quotient is unchanged. Without it the window is decided by the
    // SPELLING rather than the value, and `4503599627370496.0` -- 2^52,
    // exactly representable -- is refused for a zero that carries no
    // information. That literal is in this port's own source, so the
    // difference is between self-hosting and not.
    text frac = parts[1]
    while frac.length > 0 {
        if frac.charCodeAt(frac.length - 1) != 48 { break }
        frac = cgDropLast(frac)
    }
    int k = frac.length
    if k > 22 { CG_NUM_OK = false  return 0.0 }
    text digits = parts[0] + frac
    if digits.length > 18 { CG_NUM_OK = false  return 0.0 }
    int d = digits.toInt()
    if d >= 9007199254740992 { CG_NUM_OK = false  return 0.0 }
    float p = 1.0
    int i = 0
    while i < k {
        p = p * 10.0
        i++
    }
    return d.toFloat() / p
}

// The IEEE-754 bit pattern, as LLVM's `0x` double form wants it.
//
// Festina has no bitwise operators and no float-bit access, so the
// fields are recovered by arithmetic: normalize into [1, 2) counting
// the exponent, then scale the mantissa by 2^52. The sign and exponent
// are printed as three hex digits and the mantissa as thirteen, rather
// than assembled into one integer, because setting bit 63 would
// overflow a signed i64.
//
// Verified against Python's struct.pack over 60,000 values: exact
// everywhere except subnormals, which the normalize loop cannot reach
// and which are refused here. Negative zero would also be wrong (it
// compares equal to zero), but it is unreachable: Festina has no
// negative literal -- unary minus is a separate operator, which
// festina/codegen.py emits as a runtime negation rather than folding
// into a constant -- so _format_double never sees one.
text func cgDoubleHex(v:float) {
    if v == 0.0 { return '0x0000000000000000' }
    int sign = 0
    float av = v
    if v < 0.0 {
        sign = 1
        av = 0.0 - v
    }
    int e = 0
    while av >= 2.0 {
        av = av / 2.0
        e++
    }
    while av < 1.0 {
        av = av * 2.0
        e = e - 1
    }
    if e < 0 - 1022 {
        cgUnported('subnormal float literal')
        return '0x0000000000000000'
    }
    int frac = Math.round((av - 1.0) * 4503599627370496.0)
    return `0x${cgHexN(sign * 2048 + e + 1023, 3)}${cgHexN(frac, 13)}`
}

// ---------------------------------------------------------------------
// Scalar globals.
//
// A top-level declaration is a global. int/float/bool only for now:
// `text` needs three globals and an own-then-free dance at every
// assignment, and every other type needs a header allocation, so both
// report unported rather than being half-emitted.

text func cgLlvmType(t:Ty) {
    if t == null { return '' }
    if t.kind != 'prim' { return '' }
    if t.name == 'int' { return 'i64' }
    if t.name == 'float' { return 'double' }
    if t.name == 'bool' { return 'i8' }
    return ''
}

// The LLVM type of a STRUCT FIELD, which unlike cgLlvmType always has
// an answer: every type that is not int/float/bool/color is passed as a
// pointer to its own storage.
//
// `color` is the trap. It is the only type outside the int/float/bool
// primitives that does NOT lower to `ptr` -- a packed RGBA value living
// in an i64 -- so treating "not a scalar primitive" as "pointer" gets
// every struct with a color field silently wrong in its layout.
text func cgFieldType(t:Ty) {
    if t == null { return 'ptr' }
    if t.kind == 'prim' {
        if t.name == 'int' { return 'i64' }
        if t.name == 'float' { return 'double' }
        if t.name == 'bool' { return 'i8' }
        if t.name == 'color' { return 'i64' }
    }
    return 'ptr'
}

text func cgZeroFor(ty:text) {
    if ty == 'double' { return '0.0' }
    if ty == 'ptr' { return 'null' }
    return '0'
}

// The constant a store writes, or '' when the initializer is not one
// this slice understands.
text func cgConstFor(ty:text, init:Node) {
    if init.kind == 'NumberLit' {
        text raw = fieldOf(init, 'value').raw
        if ty == 'double' {
            float f = cgParseFloat(raw)
            if CG_NUM_OK == false {
                cgUnported('float literal outside the exact-conversion window')
                return ''
            }
            return cgDoubleHex(f)
        }
        if raw.length > 18 {
            cgUnported('integer literal too long to convert')
            return ''
        }
        return `${raw.toInt()}`
    }
    if init.kind == 'BoolLit' {
        if fieldOf(init, 'value').raw == 'true' { return '1' }
        return '0'
    }
    cgUnported(`initializer ${init.kind}`)
    return ''
}
// ---------------------------------------------------------------------
// Name generators.
//
// Three separate counters, all module-wide, because that is what
// festina/codegen.py has: `tmp_counter` and `label_counter` are
// instance state and `_uid` is a CLASS attribute (decisions.md #289).
// A port that made any of them per-function would produce structurally
// identical IR with every generated name shifted, which reads as a
// difference on every line rather than as one wrong decision.

int CG_TMP = 0
int CG_LABEL = 0
int CG_UID = 0

text func cgTmp() {
    CG_TMP++
    return `%t${CG_TMP}`
}

text func cgLabel(prefix:text) {
    CG_LABEL++
    return `${prefix}${CG_LABEL}`
}

int func cgUid() {
    CG_UID++
    return CG_UID
}

// ---------------------------------------------------------------------
// Values.
//
// An emitted expression is an LLVM operand plus both of its types: the
// LLVM one to spell instructions with, and the Festina one to decide
// things LLVM cannot see -- which `log` overload to call, whether a
// comparison is signed-integer or floating-point, whether a mix needs
// an sitofp. `fty` is '' for anything not yet understood.

struct Val {
    v:text
    lty:text
    fty:text
    sname:text
    // An `arr[T]` value's own element field-type. Needed the moment
    // indexing exists: the header is the same shape whatever T is, so
    // the element type is not recoverable from the value itself and has
    // to travel with it.
    ety:text
    // Whether a COERCION just minted this value, so it already owns a
    // reference nothing else holds. Normally the source expression
    // answers that -- see cgIsOwningRefcountedSource -- and the two
    // disagree only where a coercion turns a borrowed value into a
    // fresh one.
    fresh:bool
}

Val func cgVal(v:text, lty:text, fty:text) {
    Val r
    r.v = v
    r.lty = lty
    r.fty = fty
    return r
}

// A struct-typed value has to carry WHICH struct, or a field access
// through it has no layout to GEP against.
Val func cgArrVal(v:text, ety:text) {
    Val r
    r.v = v
    r.lty = 'ptr'
    r.fty = 'arr'
    r.ety = ety
    return r
}

// claude.md #141: a first-class function value. The `ety` slot carries
// the encoded SIGNATURE, for the same reason a container's carries its
// element type -- an indirect call has to spell every argument's LLVM
// type and the pointer itself says nothing about them.
Val func cgFuncVal(v:text, sig:text) {
    Val r
    r.v = v
    r.lty = 'ptr'
    r.fty = 'func'
    r.ety = sig
    return r
}

// The same for a map: its value type is no more recoverable from the
// header than an array's element type is.
Val func cgMapVal(v:text, ety:text) {
    Val r
    r.v = v
    r.lty = 'ptr'
    r.fty = 'map'
    r.ety = ety
    return r
}

// A sqlite result ROW. Deliberately not a struct Val with a different
// name: a row is not laid out the way a struct is -- flat 8-byte slots
// with no LLVM type of its own -- so every place that reaches for
// %struct.<name> has to miss it. The table name rides in `sname`
// because that is the one slot a value's own type name has, and
// TBL_COLS is what tells a table name from a struct one.
Val func cgTableVal(v:text, tname:text) {
    Val r
    r.v = v
    r.lty = 'ptr'
    r.fty = 'table'
    r.sname = tname
    return r
}

Val func cgStructVal(v:text, sname:text) {
    Val r
    r.v = v
    r.lty = 'ptr'
    r.fty = 'struct'
    r.sname = sname
    return r
}

text func cgLtyOf(fty:text) {
    if fty == 'int' { return 'i64' }
    // claude.md #91: a colour is a PACKED 0xRRGGBB integer, so it costs
    // one register and comparing two is one integer compare.
    if fty == 'color' { return 'i64' }
    if fty == 'float' { return 'double' }
    if fty == 'blob' { return 'ptr' }
    if fty == 'bool' { return 'i8' }
    if fty == 'text' { return 'ptr' }
    // arr[T], map[T] and struct values are all a pointer to their own
    // storage -- never the aggregate inline (claude.md #79), which is
    // what gives two bindings a shared identity on assignment.
    if fty == 'arr' { return 'ptr' }
    if fty == 'map' { return 'ptr' }
    if fty == 'struct' { return 'ptr' }
    // A row is one pointer too, to storage the RUNTIME laid out rather
    // than codegen -- which is why it has no payload type of its own.
    if fty == 'table' { return 'ptr' }
    // claude.md #141: a first-class function value is a bare LLVM
    // function pointer. It is never allocated and never freed -- a
    // declared function is immortal for the process's lifetime -- so
    // it rides every generic SCALAR-shaped path (declaration, struct
    // field, array element, map value, argument passing) unchanged,
    // with no refcount, no tracking and no release.
    if fty == 'func' { return 'ptr' }
    // A regex is a handle, like a blob: one pointer, no payload of its
    // own that codegen lays out.
    if fty == 'regex' { return 'ptr' }
    // claude.md #256: an ascii is one pointer too, but unlike text it
    // is REFCOUNTED -- the pointer handed around is the payload, with
    // a {length, refcount} header sitting 16 bytes before it.
    if fty == 'ascii' { return 'ptr' }
    // claude.md #118: img and aud are handles carrying the same
    // refcount header a blob does -- one pointer, no payload codegen
    // lays out.
    if fty == 'img' { return 'ptr' }
    if fty == 'aud' { return 'ptr' }
    return ''
}

// A function type flattened to text, because the port keys everything
// on strings and a `func[int,int]:int` has to survive in the one
// element-type slot a binding gets. Each parameter is `fty:key` -- the
// key carrying a struct's name or a container's element type, empty
// for a scalar -- parameters joined by `;`, and the return type after
// a `|`. A void return is spelled `void:`.
//
// Parseable with `.split()` alone, which is the constraint: Festina's
// `text` has no `.slice()`, so an encoding that needed index
// arithmetic to decode would need a character walk instead.
text func cgTyKeyOf(t:Ty) {
    if t == null { return '' }
    if t.kind == 'struct' { return t.name }
    if t.kind == 'arr' || t.kind == 'map' {
        if t.elem == null { return '' }
        if t.elem.kind == 'prim' { return t.elem.name }
        if t.elem.kind == 'struct' { return t.elem.name }
        return ''
    }
    return ''
}

text func cgFtyOfTy(t:Ty) {
    if t == null { return 'void' }
    if t.kind == 'prim' {
        if t.name == 'int' || t.name == 'float' || t.name == 'bool'
                || t.name == 'text' || t.name == 'blob' { return t.name }
        return ''
    }
    if t.kind == 'arr' { return 'arr' }
    if t.kind == 'map' { return 'map' }
    if t.kind == 'struct' { return 'struct' }
    if t.kind == 'func' { return 'func' }
    return ''
}

text func cgFuncSig(t:Ty) {
    if t == null { return '' }
    if t.kind != 'func' { return '' }
    text out = ''
    int i = 0
    while i < t.params.length {
        text pf = cgFtyOfTy(t.params[i])
        if pf == '' || pf == 'void' { return '' }
        if i > 0 { out = out + ';' }
        out = out + `${pf}:${cgTyKeyOf(t.params[i])}`
        i++
    }
    text rf = cgFtyOfTy(t.elem)
    if rf == '' { return '' }
    return `${out}|${rf}:${cgTyKeyOf(t.elem)}`
}

// The same encoding built from a DECLARATION's own parameter list, so
// a bare reference to a function's name produces a value
// indistinguishable from one that arrived through a `func[...]`
// binding. Kept in a table of its own rather than widened out of
// FN_PARAMS, which a direct call reads and which deliberately spells
// a non-scalar parameter as '' (see its own comment).
text func cgSigOfDeclNode(d:Node) {
    arr[Node] ps = listOf(d, 'params')
    text out = ''
    int i = 0
    while i < ps.length {
        Ty pt = resolveTypeField(ps[i], 'type_expr')
        text pf = cgFtyOfTy(pt)
        if pf == '' || pf == 'void' { return '' }
        if i > 0 { out = out + ';' }
        out = out + `${pf}:${cgTyKeyOf(pt)}`
        i++
    }
    text rf = 'void'
    text rkey = ''
    Ty rt = resolveTypeField(d, 'return_type')
    // A void function's own return type resolves to a `prim` named
    // `void` rather than to nothing, so the check is on the NAME and
    // not on the pointer -- which cost an afternoon the first time.
    if rt != null {
        if rt.kind != 'prim' || rt.name != 'void' {
            rf = cgFtyOfTy(rt)
            if rf == '' || rf == 'void' { return '' }
            rkey = cgTyKeyOf(rt)
        }
    }
    return `${out}|${rf}:${rkey}`
}

// One parameter or the return of an encoded signature, as (fty, key).
text func cgSigFty(part:text) {
    arr[text] bits = part.split(':')
    return bits[0]
}

text func cgSigKey(part:text) {
    arr[text] bits = part.split(':')
    if bits.length < 2 { return '' }
    return bits[1]
}

arr[text] func cgSigParams(sig:text) {
    arr[text] halves = sig.split('|')
    arr[text] none = []
    if halves.length != 2 { return none }
    if halves[0] == '' { return none }
    return halves[0].split(';')
}

text func cgSigRet(sig:text) {
    arr[text] halves = sig.split('|')
    if halves.length != 2 { return '' }
    return halves[1]
}

// The LLVM payload type sitting behind a managed value's pointer, or
// '' when the type is not one of them. This is the shape the
// {refcount, payload} global header wraps -- the same layout
// festina_retain/festina_release expect, with the count at payload-8.
// A fresh, uniquely-owned heap block for a refcounted value: its own
// i64 refcount prefix set to 1, with the visible pointer GEP'd past it
// so every downstream GEP is unaffected. calloc zeroes the payload, so
// every field starts at its own zero exactly as a global's
// zeroinitializer storage does.
//
// sizeof comes from getelementptr-on-null, which is LLVM's own layout
// rule rather than a reimplementation of it.
//
// Untagged only. A struct that is a member of a pure-struct enum needs
// the wider {tag, refcount} header of claude.md #176; that is safe to
// ignore here only because EnumDecl is itself unported, so no program
// reaching this code has an enum at all.
text func cgFreshHeader(payload:text) {
    text sz = cgTmp()
    cgOut(`  ${sz} = getelementptr ${payload}, ptr null, i64 1`)
    text szi = cgTmp()
    cgOut(`  ${szi} = ptrtoint ptr ${sz} to i64`)
    text total = cgTmp()
    cgOut(`  ${total} = add i64 ${szi}, 8`)
    text raw = cgTmp()
    cgOut(`  ${raw} = call ptr @calloc(i64 1, i64 ${total})`)
    cgOut(`  store i64 1, ptr ${raw}`)
    text made = cgTmp()
    cgOut(`  ${made} = getelementptr i8, ptr ${raw}, i64 8`)
    return made
}

text func cgPayloadFor(t:Ty) {
    if t == null { return '' }
    if t.amortized { return '' }
    if t.kind == 'arr' { return '%struct._FestinaArray' }
    if t.kind == 'map' { return '%struct._FestinaMap' }
    if t.kind == 'struct' { return `%struct.${t.name}` }
    return ''
}

// The Festina-level tag for a managed type, matching cgLtyOf above.
text func cgManagedFty(t:Ty) {
    if t == null { return '' }
    if t.amortized { return '' }
    if t.kind == 'arr' { return 'arr' }
    if t.kind == 'map' { return 'map' }
    if t.kind == 'struct' { return 'struct' }
    if t.kind == 'table' { return 'table' }
    // claude.md #109: a blob carries the ordinary refcount header, so
    // the only thing the generic release cannot do for it is free the
    // path and byte buffer hanging off the payload -- exactly the shape
    // of a per-type cascade, except the runtime writes this one once
    // instead of codegen generating it per type.
    if t.kind == 'prim' {
        if t.name == 'blob' { return 'blob' }
        if t.name == 'regex' { return 'regex' }
        if t.name == 'ascii' { return 'ascii' }
        if t.name == 'img' { return 'img' }
        if t.name == 'aud' { return 'aud' }
    }
    return ''
}

// ---------------------------------------------------------------------
// Scopes.
//
// Globals live in @name; a parameter or local lives in an alloca slot
// named %<name>.<uid>. Locals shadow globals, and a function's locals
// are cleared on entry -- there is no nested block scoping here yet,
// which is safe because semantic.py has already rejected any program
// where that would matter.

map[text] G_SLOT = {}
map[text] G_FTY = {}
map[text] L_SLOT = {}
map[text] L_FTY = {}
map[text] FN_RET = {}

// Each function's parameter types, joined by `|` -- see the
// registration site for why a call needs them.
map[text] FN_PARAMS = {}

// The second half of a refcounted return's release key: a struct's
// name, or a container's element type.
map[text] FN_RETKEY = {}

// Each function's whole signature, encoded by cgSigOfDeclNode, so the
// name can be read as a first-class value (claude.md #141).
map[text] FN_SIG = {}

// Struct field layout, keyed '<Struct>.<field>'. codegen.py reads this
// off `analyzed.structs`; here it is collected while the type
// definitions are emitted, which is the same information in the same
// order.
map[int] SF_IDX = {}
map[text] SF_LTY = {}
map[text] SF_FTY = {}
map[text] SF_SNAME = {}
map[text] SF_ETY = {}

// Structs every one of whose fields is a scalar. Only those can have a
// LOCAL yet: a struct with a struct/arr/map/text field is released
// through a generated per-type cascade wrapper rather than the plain
// festina_release, and a non-escaping one with a struct-typed field
// needs its FIELDS released even though its own storage is in the
// frame (festina/codegen.py's _StackStructFieldsOnly). Both are their
// own mechanisms; until they are ported, refusing is the honest
// answer.
map[int] SF_PLAIN = {}

// Each struct's field names in declaration order, joined by `|`.
map[text] SF_NAMES = {}

// Lazily-generated per-struct release cascades, cached by struct name
// -- registered BEFORE the field walk so a struct that reaches itself
// gets its own name back instead of generating a second wrapper.
map[text] CG_STRUCT_REL = {}

// The receiver cgFieldPtr last emitted. A field read through a base the
// expression OWNS is a borrowed pointer INTO something about to be
// released, so it is copied out first and the base released after --
// claude.md #117/#119. Set on every cgFieldPtr and read immediately by
// cgMemberRead, so the window in which it is live is one call wide.
Val CG_FIELD_BASE

bool func cgIsLocal(name:text) {
    return L_SLOT[name] != null
}

text func cgSlotOf(name:text) {
    if L_SLOT[name] != null { return L_SLOT[name] }
    if G_SLOT[name] != null { return G_SLOT[name] }
    return ''
}

map[text] G_SNAME = {}
map[text] G_ETY = {}

// Lazily-generated per-element-type release cascades, cached by the
// element type so one is generated per type and not per site.
map[text] CG_ARR_REL = {}
// The ascii literals interned so far, and how many -- numbered
// separately from the text constants because they are a different
// section with a different shape.
map[text] CG_ASTR_MAP = {}
int CG_ASTR_N = 0
map[text] CG_ROW_REL = {}
map[text] CG_MAP_REL = {}

// Comparator trampolines, cached per element type (claude.md #184).
map[text] CG_SORT_TRAMP = {}

// JSON builders, cached by the target type's own spelling.
map[text] CG_FROMJSON = {}

// JSON walkers, cached by the type's own spelling.
map[text] CG_JSON = {}
map[text] L_SNAME = {}
map[text] L_ETY = {}

text func cgEtyOf(name:text) {
    if L_ETY[name] != null { return L_ETY[name] }
    if G_ETY[name] != null { return G_ETY[name] }
    return ''
}

text func cgSnameOf(name:text) {
    if L_SNAME[name] != null { return L_SNAME[name] }
    if G_SNAME[name] != null { return G_SNAME[name] }
    return ''
}

text func cgFtyOf(name:text) {
    if L_FTY[name] != null { return L_FTY[name] }
    if G_FTY[name] != null { return G_FTY[name] }
    return ''
}

// ---------------------------------------------------------------------
// Output buffers.
//
// festina/codegen.py assembles the module from sections, and the order
// they are GENERATED in is not the order they are printed in: every
// function body is emitted before __festina_main, so the shared
// counters give function temps the lower numbers. Two buffers keep
// that straight; `CUR` aliases whichever one is being filled (an
// arr[T] is a reference, claude.md #79).

// Whether the block just emitted ended in a terminator of its own. A
// `return` ends its block, so the enclosing `if`/`while`/`for` must NOT
// then emit its usual fall-through branch -- LLVM allows exactly one
// terminator per basic block, and festina/codegen.py tracks the same
// thing as ctx["terminated"].
bool CG_TERM = false

// The label of the block currently being emitted into --
// festina/codegen.py's `cur_block`, kept for the same reason its own
// _start_block docstring gives: a phi's predecessor is the block the
// incoming value was actually computed in, which is NOT the label that
// block started with once the arm contains control flow of its own.
//
// Every phi here reads this rather than the label it branched to. The
// earlier version named the branch labels directly, which agreed with
// the original for every construct the port could then emit -- no arm
// contained a nested branch -- and would have started diverging
// silently the moment one did. Nested struct-field access is exactly
// that case: reading `b.origin.x` twice puts the second read's `load`
// in `field.done2`, not in `entry`.
text CG_BLOCK = ''

void func cgBlockLabel(l:text) {
    CG_BLOCK = l
    cgOut(`${l}:`)
}

// Whether statements are being emitted into a function body rather
// than into __festina_main's top level. A declaration's storage and
// its lifetime both depend on this.
bool CG_IN_FUNC = false

// claude.md #236: whether the program contains a `try` ANYWHERE. It is
// a whole-program property rather than a per-function one, because a
// throw unwinds through frames that know nothing about it -- every
// tracked binding in the program has to be registered as it is bound,
// or the throw walks past it. A program with none of them pays
// literally nothing: the IR is unchanged.
bool CG_HAS_TRY = false

// Set only while a THROW emits its own scope walk, so a try-frame
// marker in the range is left alone. See cgFreeOne.
bool CG_SKIP_TRY_POP = false

// Module-level globals a call SITE needs -- a regex literal's cached
// compilation, a dynamic regex()'s memo slot. They are emitted in
// their own section between the ordinary globals and the function
// definitions, and numbered in the order the sites are reached.
arr[text] CG_EXTRA = []
int CG_REGEX_CACHES = 0
int CG_REGEX_MEMOS = 0

// claude.md #116: whether any setTimeout/setInterval call was emitted.
// Only the SCHEDULING pair sets it -- clearing alone schedules
// nothing, so a program that only ever clears needs no loop to wait
// in, the same "only pay for what you use" rule loadImage() follows.
bool CG_USES_TIMERS = false

// claude.md #242: whether any sqlite() call was emitted. Together with
// a declared table it is what opens the database at all -- for a
// program that does neither, the close call at the end of main would
// be the ONLY live reference into the runtime's SQLite code, and on
// wasm the one thing keeping the whole vendored engine from being
// dropped by the linker.
bool CG_USES_SQLITE = false

// claude.md #95: whether any graphics CODE was emitted -- drawing,
// measuring, loading -- as opposed to anything needing a real window.
// The distinction is the whole point of that entry: painting the
// offscreen canvas and saving it needs no X server at all, so a
// headless program should not have a window opened on it. This flag
// registers the image decoder in main's prologue; the separate,
// narrower one below is what opens a window.
bool CG_USES_GRAPHICS_CODE = false

// claude.md #101: whether any audio value exists, which is what
// registers the audio decoder in main's prologue -- the same
// only-pay-for-what-you-use gate the image one has.
bool CG_USES_AUDIO = false

// claude.md #91: every CSS colour name this language understands, and
// the hex its components come from. Kept as DATA rather than derived,
// because it is data -- the same 148 names the original resolves
// against, and a port that recognized a different set would compile a
// different language rather than merely a different compiler.
//
// The table is the whole of it: `resolve` below is a case fold, a
// `none` check, a hex branch and a lookup.
map[text] CG_CSS_COLORS = {
    'aliceblue': 'f0f8ff', 'antiquewhite': 'faebd7', 'aqua': '00ffff',
    'aquamarine': '7fffd4', 'azure': 'f0ffff', 'beige': 'f5f5dc',
    'bisque': 'ffe4c4', 'black': '000000', 'blanchedalmond': 'ffebcd',
    'blue': '0000ff', 'blueviolet': '8a2be2', 'brown': 'a52a2a',
    'burlywood': 'deb887', 'cadetblue': '5f9ea0', 'chartreuse': '7fff00',
    'chocolate': 'd2691e', 'coral': 'ff7f50', 'cornflowerblue': '6495ed',
    'cornsilk': 'fff8dc', 'crimson': 'dc143c', 'cyan': '00ffff',
    'darkblue': '00008b', 'darkcyan': '008b8b',
    'darkgoldenrod': 'b8860b', 'darkgray': 'a9a9a9',
    'darkgreen': '006400', 'darkgrey': 'a9a9a9', 'darkkhaki': 'bdb76b',
    'darkmagenta': '8b008b', 'darkolivegreen': '556b2f',
    'darkorange': 'ff8c00', 'darkorchid': '9932cc', 'darkred': '8b0000',
    'darksalmon': 'e9967a', 'darkseagreen': '8fbc8f',
    'darkslateblue': '483d8b', 'darkslategray': '2f4f4f',
    'darkslategrey': '2f4f4f', 'darkturquoise': '00ced1',
    'darkviolet': '9400d3', 'deeppink': 'ff1493',
    'deepskyblue': '00bfff', 'dimgray': '696969', 'dimgrey': '696969',
    'dodgerblue': '1e90ff', 'firebrick': 'b22222',
    'floralwhite': 'fffaf0', 'forestgreen': '228b22',
    'fuchsia': 'ff00ff', 'gainsboro': 'dcdcdc', 'ghostwhite': 'f8f8ff',
    'gold': 'ffd700', 'goldenrod': 'daa520', 'gray': '808080',
    'green': '008000', 'greenyellow': 'adff2f', 'grey': '808080',
    'honeydew': 'f0fff0', 'hotpink': 'ff69b4', 'indianred': 'cd5c5c',
    'indigo': '4b0082', 'ivory': 'fffff0', 'khaki': 'f0e68c',
    'lavender': 'e6e6fa', 'lavenderblush': 'fff0f5',
    'lawngreen': '7cfc00', 'lemonchiffon': 'fffacd',
    'lightblue': 'add8e6', 'lightcoral': 'f08080', 'lightcyan': 'e0ffff',
    'lightgoldenrodyellow': 'fafad2', 'lightgray': 'd3d3d3',
    'lightgreen': '90ee90', 'lightgrey': 'd3d3d3', 'lightpink': 'ffb6c1',
    'lightsalmon': 'ffa07a', 'lightseagreen': '20b2aa',
    'lightskyblue': '87cefa', 'lightslategray': '778899',
    'lightslategrey': '778899', 'lightsteelblue': 'b0c4de',
    'lightyellow': 'ffffe0', 'lime': '00ff00', 'limegreen': '32cd32',
    'linen': 'faf0e6', 'magenta': 'ff00ff', 'maroon': '800000',
    'mediumaquamarine': '66cdaa', 'mediumblue': '0000cd',
    'mediumorchid': 'ba55d3', 'mediumpurple': '9370db',
    'mediumseagreen': '3cb371', 'mediumslateblue': '7b68ee',
    'mediumspringgreen': '00fa9a', 'mediumturquoise': '48d1cc',
    'mediumvioletred': 'c71585', 'midnightblue': '191970',
    'mintcream': 'f5fffa', 'mistyrose': 'ffe4e1', 'moccasin': 'ffe4b5',
    'navajowhite': 'ffdead', 'navy': '000080', 'oldlace': 'fdf5e6',
    'olive': '808000', 'olivedrab': '6b8e23', 'orange': 'ffa500',
    'orangered': 'ff4500', 'orchid': 'da70d6', 'palegoldenrod': 'eee8aa',
    'palegreen': '98fb98', 'paleturquoise': 'afeeee',
    'palevioletred': 'db7093', 'papayawhip': 'ffefd5',
    'peachpuff': 'ffdab9', 'peru': 'cd853f', 'pink': 'ffc0cb',
    'plum': 'dda0dd', 'powderblue': 'b0e0e6', 'purple': '800080',
    'rebeccapurple': '663399', 'red': 'ff0000', 'rosybrown': 'bc8f8f',
    'royalblue': '4169e1', 'saddlebrown': '8b4513', 'salmon': 'fa8072',
    'sandybrown': 'f4a460', 'seagreen': '2e8b57', 'seashell': 'fff5ee',
    'sienna': 'a0522d', 'silver': 'c0c0c0', 'skyblue': '87ceeb',
    'slateblue': '6a5acd', 'slategray': '708090', 'slategrey': '708090',
    'snow': 'fffafa', 'springgreen': '00ff7f', 'steelblue': '4682b4',
    'tan': 'd2b48c', 'teal': '008080', 'thistle': 'd8bfd8',
    'tomato': 'ff6347', 'turquoise': '40e0d0', 'violet': 'ee82ee',
    'wheat': 'f5deb3', 'white': 'ffffff', 'whitesmoke': 'f5f5f5',
    'yellow': 'ffff00', 'yellowgreen': '9acd32'
}

// claude.md #91: a colour LITERAL resolved to the packed 0xRRGGBB
// integer a `color` value IS. Negative means "no colour at all".
//
// Packing is what makes a colour cost one register instead of three,
// and `a == b` a single integer compare; unpacking is three shift and
// mask pairs in the runtime, paid once per fillStyle rather than per
// pixel. Spelled as arithmetic here because Festina has no bitwise
// operators -- r * 65536 + g * 256 + b is the same number
// `(r << 16) | (g << 8) | b` names.
//
// Answers '' for anything this language does not recognize, which the
// caller turns into an error naming the offending value: there is
// deliberately no runtime resolver to fall back on, so a colour that
// cannot be resolved at compile time cannot be resolved at all.
text func cgColorValue(lit:text) {
    text s = cgLowerAscii(lit.trim())
    if s == '' { return '' }
    if s == 'none' || s == 'transparent' { return '-1' }
    // Read through charCodeAt rather than `s[0]`, because this file is
    // compiled by the compiler it defines and that one does not index
    // a text yet. Writing the shorter spelling here stops the whole
    // module self-hosting, which is a far louder failure than it looks
    // -- it was measured as a 160,000-line coverage collapse, not as a
    // syntax error.
    if s.charCodeAt(0) == 35 {
        text h = ''
        int i = 1
        while i < s.length {
            h = h + s.charCodeAt(i).toChar()
            i++
        }
        // `#abc` expands to `#aabbcc`, the same doubling CSS does.
        if h.length == 3 {
            int d0 = h.charCodeAt(0)
            int d1 = h.charCodeAt(1)
            int d2 = h.charCodeAt(2)
            h = d0.toChar() + d0.toChar() + d1.toChar() + d1.toChar()
                + d2.toChar() + d2.toChar()
        }
        if h.length != 6 { return '' }
        return cgPackHex(h)
    }
    if CG_CSS_COLORS[s] == null { return '' }
    return cgPackHex(CG_CSS_COLORS[s])
}

// Six hex digits to the packed integer, or '' if any of them is not a
// hex digit at all.
text func cgPackHex(h:text) {
    int acc = 0
    int i = 0
    while i < 6 {
        int d = cgHexDigit(h.charCodeAt(i))
        if d < 0 { return '' }
        acc = acc * 16 + d
        i++
    }
    return `${acc}`
}

int func cgHexDigit(code:int) {
    if code >= 48 && code <= 57 { return code - 48 }
    if code >= 97 && code <= 102 { return code - 87 }
    return 0 - 1
}

// A to Z folded to a to z, and nothing else touched. Festina has no
// case-folding method of its own, and a colour name is matched
// case-insensitively, so the fold is written out here rather than
// assumed away.
text func cgLowerAscii(v:text) {
    text out = ''
    int i = 0
    while i < v.length {
        int code = v.charCodeAt(i)
        if code >= 65 && code <= 90 { code = code + 32 }
        out = out + code.toChar()
        i++
    }
    return out
}

// claude.md #134/#234: the img-method counterparts of the canvas
// operations, each retargeted at the RECEIVER image's own surface
// rather than the shared canvas. Spelled '<arity>:<fn>:<ltys>' and
// joined by ';', because several of them take more than one argument
// count and the function differs per count.
map[text] CG_IMAGE_OPS = {
    'translate': '2:festina_image_translate:i64,i64',
    'rotate': '1:festina_image_rotate:double',
    'scale': '2:festina_image_scale:double,double',
    'resetTransform': '0:festina_image_reset_transform:',
    'saveState': '0:festina_image_save_state:',
    'restoreState': '0:festina_image_restore_state:',
    'clear': '0:festina_image_clear:',
    'clearRect': '4:festina_image_clear_rect:i64,i64,i64,i64',
    'clearCircle': '3:festina_image_clear_circle:i64,i64,i64',
    'clearPixel': '2:festina_image_clear_pixel:i64,i64',
    'drawImage': '3:festina_image_draw_image:ptr,i64,i64;5:festina_image_draw_image_scaled:ptr,i64,i64,i64,i64'
}

// claude.md #94/#180: the canvas operations that are a name, a runtime
// function and a fixed argument list -- nothing else. Spelled
// '<fn>|<lty>,<lty>,...', with an empty tail for the no-argument ones,
// because a map of two parallel lists would let the two drift.
//
// render(), enterFullscreen() and exitFullscreen() are deliberately
// ABSENT: they are the three here that need a real GUI, which changes
// main's own shape, and a port that emitted the call without that
// change would be silently wrong rather than merely incomplete.
map[text] CG_CANVAS_OPS = {
    'clearCanvas': 'festina_clear_canvas|',
    'clearRect': 'festina_clear_rect|i64,i64,i64,i64',
    'clearCircle': 'festina_clear_circle|i64,i64,i64',
    'clearPixel': 'festina_clear_pixel|i64,i64',
    'setClientWidth': 'festina_set_client_width|i64',
    'setClientHeight': 'festina_set_client_height|i64',
    'beginPath': 'festina_begin_path|',
    'moveTo': 'festina_move_to|i64,i64',
    'lineTo': 'festina_line_to|i64,i64',
    'curveTo': 'festina_curve_to|i64,i64,i64,i64,i64,i64',
    'closePath': 'festina_close_path|',
    'fillPath': 'festina_fill_path|',
    'strokePath': 'festina_stroke_path|',
    'translate': 'festina_translate|i64,i64',
    'rotate': 'festina_rotate|double',
    'scale': 'festina_scale|double,double',
    'resetTransform': 'festina_reset_transform|',
    'saveState': 'festina_save_state|',
    'restoreState': 'festina_restore_state|',
    'fillAlpha': 'festina_set_alpha|double',
    'fillLinearGradient': 'festina_fill_linear_gradient|i64,i64,i64,i64,i64,i64',
    'fillRadialGradient': 'festina_fill_radial_gradient|i64,i64,i64,i64,i64',
    'showCursor': 'festina_show_cursor|',
    'hideCursor': 'festina_hide_cursor|'
}

// claude.md #29-31: every declared `table`, in declaration order, and
// its columns -- names and SQL types, each '|'-joined, neither of
// which an identifier can contain. Recorded at declaration and spent
// in main's own prologue, where one festina_sync_table call per table
// brings the real database's schema up to the source's.
//
// Declaration ORDER is what the separate array is for. A map's own
// iteration order is not the source's, and the sync calls -- and the
// two globals each one needs -- have to come out in the order the
// original's own `analyzed.tables` does, which is registration order.
arr[text] TBL_ORDER = []
map[text] TBL_COLS = {}
map[text] TBL_TYPES = {}
// Kept separately rather than counted back out of the joined strings,
// because a table with no columns at all joins to the empty text and a
// split of that answers one, not zero.
map[int] TBL_NCOLS = {}
// Per-column, keyed '<Table>.<column>': the slot index and the
// festina-level type. A row's slots are flat 8-byte cells in
// declaration order, so the index IS the offset divided by eight --
// unlike a struct, whose fields are laid out by LLVM.
map[int] TBL_ARRAYS = {}
// claude.md #70: the `DatabaseURL = <expr>` directive's own value
// expression, or null. Not a statement the program runs: it is lifted
// out of the body and evaluated in main's prologue instead, ahead of
// festina_db_open -- and so ahead of every ordinary global's own
// initializer, which is why referencing another global from it would
// read a zero rather than that global's value.
Node CG_DB_URL = null
map[int] TB_IDX = {}
map[text] TB_FTY = {}

// The function currently being emitted, and its return type. A
// `return null` takes its type from the signature rather than from
// anything at the return site, which is the only thing these are for.
text CG_FUNC_NAME = ''
text CG_FUNC_RET = ''

// Values needing a free when their scope ends, and where each scope
// began. festina/codegen.py keeps the same thing as a frame stack and
// frees "down to" a given frame; this is that, with the frames as
// indices into one flat list.
//
// Frees are emitted in DECLARATION order, not reverse -- read off the
// original's output for two locals in one block, not assumed.
arr[text] CG_LIVE = []
arr[int] CG_FRAME = []

// A function's own escaping `text` parameters, freed AFTER every
// block-scoped local rather than before.
//
// That order is measured, not reasoned about, and it is the opposite of
// what the flat list above would give: a function with an escaping text
// parameter `a` and a body local `loc` frees `loc` first and `a`
// second, on the return path and the fall-through path alike. A
// parameter is bound outside the body's own scope, so the body's scope
// ends first -- obvious in hindsight and easy to get backwards, which
// is why it was read off the original's output before anything was
// written.
arr[text] CG_PARAM_LIVE = []

// The innermost loop's `continue` label, `break` label, and the frame
// depth to unwind to, joined -- festina/codegen.py's own
// `_loop_targets`. The depth is recorded BEFORE the body's frame is
// pushed, and a break frees down to exactly that: an outer local
// merely USED inside the loop, rather than declared inside it, is not
// this loop's to free.
arr[text] CG_LOOPS = []

// The escaping-name set for the body currently being emitted --
// festina/codegen.py's `_current_escaping_names`. Saved and restored
// around a nested function rather than reset, for the reason that one
// documents: a FuncDecl inside another body re-enters the emitter one
// level deeper while the outer body is still being walked, and a bare
// reset would silently turn the outer function's tracked locals back
// into leaks for everything left to emit after it.
map[int] CG_ESC = {}

bool func cgEscapes(name:text) {
    return CG_ESC[name] != null
}

void func cgFreeParams() {
    int popped = 0
    int i = 0
    while i < CG_PARAM_LIVE.length {
        if cgFreeOne(CG_PARAM_LIVE[i]) { popped++ }
        i++
    }
    if popped > 0 {
        if CG_HAS_TRY {
            cgOut(`  call void @festina_cleanup_pop_n(i64 ${popped})`)
        }
    }
}

arr[text] CG_FUNCS = []
arr[text] CG_MAIN = []
arr[text] CUR = []

void func cgOut(line:text) {
    if CG_UNPORTED { return }
    CUR.push(line)
}

// A block's statements. parseBlock wraps them in a Block node rather
// than storing a bare list, so a statement body is reached through two
// hops, not one.
arr[Node] func cgBlockStmts(b:Node) {
    arr[Node] empty = []
    if b == null { return empty }
    if b.kind != 'Block' { return empty }
    return listOf(b, 'body')
}

// ---------------------------------------------------------------------
// Alloca hoisting.
//
// claude.md #191: every static alloca moves to the top of its
// function's entry block, so a slot declared inside a loop is
// allocated once per call instead of once per iteration. This is a
// post-pass over the finished text in festina/codegen.py and it is one
// here too, for the same reason: it is a property of the module, not of
// any one statement, and doing it inline would mean knowing a local's
// existence before reaching its declaration.
//
// A dynamic alloca -- one whose element count is an SSA value rather
// than a constant -- is left alone, because moving it would change how
// much stack a single execution reserves. Neither generator emits one
// today; the guard keeps a future one safe by construction.
bool func cgIsStaticAlloca(line:text) {
    if line.length < 4 { return false }
    if line.charCodeAt(0) != 32 { return false }
    if line.charCodeAt(1) != 32 { return false }
    if line.charCodeAt(2) != 37 { return false }
    arr[text] halves = line.split(' = alloca ')
    if halves.length != 2 { return false }
    arr[text] chunks = halves[1].split(',')
    int i = 1
    while i < chunks.length {
        int j = 0
        while j < chunks[i].length {
            if chunks[i].charCodeAt(j) == 37 { return false }
            j++
        }
        i++
    }
    return true
}

bool func cgOpensDefine(line:text) {
    if line.length < 8 { return false }
    if line.split(' ')[0] != 'define' { return false }
    return line.charCodeAt(line.length - 1) == 123
}

bool func cgIsLabelLine(line:text) {
    if line.length < 2 { return false }
    if line.charCodeAt(0) == 32 { return false }
    return line.charCodeAt(line.length - 1) == 58
}

arr[text] func cgHoistAllocas(src:arr[text]) {
    arr[text] out = []
    arr[text] body = []
    bool inDefine = false
    int i = 0
    while i < src.length {
        text line = src[i]
        if inDefine == false {
            out.push(line)
            if cgOpensDefine(line) {
                inDefine = true
                arr[text] fresh = []
                body = fresh
            }
            i++
            continue
        }
        if line == '}' {
            arr[text] hoisted = []
            arr[text] kept = []
            int b = 0
            while b < body.length {
                if cgIsStaticAlloca(body[b]) { hoisted.push(body[b]) }
                else { kept.push(body[b]) }
                b++
            }
            // Insertion point: after the entry label when the block has
            // one, which this generator always emits.
            int insertAt = 0
            if kept.length > 0 {
                if cgIsLabelLine(kept[0]) { insertAt = 1 }
            }
            int k = 0
            while k < insertAt {
                out.push(kept[k])
                k++
            }
            int h = 0
            while h < hoisted.length {
                out.push(hoisted[h])
                h++
            }
            int k2 = insertAt
            while k2 < kept.length {
                out.push(kept[k2])
                k2++
            }
            out.push(line)
            inDefine = false
            i++
            continue
        }
        body.push(line)
        i++
    }
    return out
}

// ---------------------------------------------------------------------
// Expressions.

Val func cgExpr(e:Node) {
    Val none
    if CG_STUCK { return none }
    if e == null {
        cgUnported('missing expression')
        return none
    }

    if e.kind == 'NumberLit' {
        // The parser keeps only the literal's text, not the lexer's
        // int/float tag -- and a '.' is exactly what the lexer used to
        // decide, with its float normalization always leaving one digit
        // after the point, so the same test recovers the same answer.
        text raw = fieldOf(e, 'value').raw
        bool isFloat = false
        int i = 0
        while i < raw.length {
            if raw.charCodeAt(i) == 46 { isFloat = true }
            i++
        }
        if isFloat {
            float f = cgParseFloat(raw)
            if CG_NUM_OK == false {
                cgUnported('float literal outside the exact-conversion window')
                return none
            }
            return cgVal(cgDoubleHex(f), 'double', 'float')
        }
        if raw.length > 18 {
            cgUnported('integer literal too long to convert')
            return none
        }
        return cgVal(`${raw.toInt()}`, 'i64', 'int')
    }

    if e.kind == 'BoolLit' {
        if fieldOf(e, 'value').raw == 'true' { return cgVal('1', 'i8', 'bool') }
        return cgVal('0', 'i8', 'bool')
    }

    if e.kind == 'StringLit' {
        return cgVal(cgStringConst(rawText(e, 'value')), 'ptr', 'text')
    }

    // A `null` with no declared type in reach. `null` is only valid IR
    // for a pointer type, which covers every Festina type that can get
    // here at all -- int/float/bool always have a context, because
    // their nulls are ordinary constants and there is nowhere to put
    // one without knowing which. See cgExprExpecting for that half.
    if e.kind == 'NullLit' {
        return cgVal('null', 'ptr', 'null')
    }

    if e.kind == 'Identifier' {
        text name = rawText(e, 'name')
        text slot = cgSlotOf(name)
        text fty = cgFtyOf(name)
        // claude.md #141: a bare reference to a FUNCTION's own name,
        // not immediately called. The global symbol IS the value --
        // LLVM already treats a function symbol as a plain `ptr`
        // constant, so there is no address-of step and nothing to
        // load, unlike a variable's own storage. Checked only when no
        // binding shadows the name, which is the original's order.
        if slot == '' {
            if FN_SIG[name] != null {
                if FN_SIG[name] != '' {
                    Val fv = cgVal(`@${name}`, 'ptr', 'func')
                    fv.ety = FN_SIG[name]
                    return fv
                }
            }
        }
        if slot == '' || cgLtyOf(fty) == '' {
            cgUnported(`read of ${name}`)
            return none
        }
        text lty = cgLtyOf(fty)
        text t = cgTmp()
        cgOut(`  ${t} = load ${lty}, ptr ${slot}`)
        if fty == 'struct' { return cgStructVal(t, cgSnameOf(name)) }
        if fty == 'table' { return cgTableVal(t, cgSnameOf(name)) }
        if fty == 'arr' { return cgArrVal(t, cgEtyOf(name)) }
        if fty == 'map' { return cgMapVal(t, cgEtyOf(name)) }
        if fty == 'func' { return cgFuncVal(t, cgEtyOf(name)) }
        return cgVal(t, lty, fty)
    }

    // claude.md #67: a /pattern/flags literal compiles to the same
    // automaton every time this line is reached, so the compilation is
    // cached in a private global filled on first arrival. The cached
    // value is also MARKED, because `free` on a binding aliasing it
    // must not free something every later execution of this line
    // shares -- the value carries the answer, so festina_regex_free
    // can no-op on it.
    //
    // Deliberately NOT extended to `regex(p, f)`: there the pattern is
    // an arbitrary runtime expression, so the same site can
    // legitimately see a different one each time, and caching by site
    // would silently keep serving the first pattern forever.
    if e.kind == 'RegexLit' { return cgRegexLit(e) }

    if e.kind == 'BinOp' { return cgBinOp(e) }
    if e.kind == 'LogicalOp' { return cgLogical(e) }
    if e.kind == 'UnaryOp' { return cgUnary(e) }
    if e.kind == 'Ternary' { return cgTernary(e) }
    if e.kind == 'Member' { return cgMemberAccess(e) }
    if e.kind == 'Call' { return cgCall(e, true) }
    if e.kind == 'TemplateLit' { return cgTemplate(e) }

    cgUnported(`expression ${e.kind}`)
    return none
}

Val func cgRegexLit(e:Node) {
    text cache = `@.regex.cache.${CG_REGEX_CACHES}`
    CG_REGEX_CACHES = CG_REGEX_CACHES + 1
    CG_EXTRA.push(`${cache} = private global ptr null`)

    text loaded = cgTmp()
    cgOut(`  ${loaded} = load ptr, ptr ${cache}`)
    text isNull = cgTmp()
    cgOut(`  ${isNull} = icmp eq ptr ${loaded}, null`)
    text compileL = cgLabel('regex.compile')
    text doneL = cgLabel('regex.done')
    cgOut(`  br i1 ${isNull}, label %${compileL}, label %${doneL}`)
    text loadPred = CG_BLOCK

    cgBlockLabel(compileL)
    text pat = cgStringConst(rawText(e, 'pattern'))
    text flg = cgStringConst(rawText(e, 'flags'))
    text compiled = cgTmp()
    cgOut(`  ${compiled} = call ptr @festina_regex_compile(ptr ${pat}, ptr ${flg})`)
    cgOut(`  call void @festina_regex_mark_cached(ptr ${compiled})`)
    cgOut(`  store ptr ${compiled}, ptr ${cache}`)
    text compilePred = CG_BLOCK
    cgOut(`  br label %${doneL}`)

    cgBlockLabel(doneL)
    text out = cgTmp()
    cgOut(`  ${out} = phi ptr [ ${loaded}, %${loadPred} ], [ ${compiled}, %${compilePred} ]`)
    // claude.md #118: a literal's compilation is FRESH for the purpose
    // of a store -- not because it was allocated here, but because it
    // is immortal, so retain and release are both no-ops on it and the
    // cheaper answer is the right one. It is deliberately NOT an
    // owning source for a RELEASE: the original keeps those two
    // predicates apart, and only a `regex(p, f)` CALL result is
    // released where it is used (cgFreeRegexTemp).
    Val rv = cgVal(out, 'ptr', 'regex')
    rv.fresh = true
    return rv
}

Val func cgBinOp(e:Node) {
    Val none
    Node ln = childOf(e, 'left')
    Node rn = childOf(e, 'right')
    Val l
    Val r

    // `x == null` takes its type from the OTHER side, which means the
    // other side has to be emitted first -- and for `null == x` that
    // reverses the order the two operands are evaluated in. Observable
    // whenever the non-null side has effects of its own, so it is the
    // original's order rather than left-to-right by default.
    //
    // `null == null` has no context on either side and stays
    // unresolved, exactly as in the original: an exceedingly rare
    // expression with no obvious meaning, left alone under claude.md
    // #54's ambiguity rule rather than guessed at.
    if rn.kind == 'NullLit' && ln.kind != 'NullLit' {
        l = cgExpr(ln)
        if CG_STUCK { return none }
        r = cgExprExpecting(rn, l.fty, '')
        if CG_STUCK { return none }
    } else if ln.kind == 'NullLit' && rn.kind != 'NullLit' {
        r = cgExpr(rn)
        if CG_STUCK { return none }
        l = cgExprExpecting(ln, r.fty, '')
        if CG_STUCK { return none }
    } else {
        l = cgExpr(ln)
        if CG_STUCK { return none }
        r = cgExpr(rn)
        if CG_STUCK { return none }
    }
    text op = rawText(e, 'op')

    // `text` has its own branch, ahead of everything numeric: `==`/`!=`
    // through festina_str_eq, `+` through festina_str_concat, and
    // nothing else.
    //
    // Both operands are freed here when they were freshly allocated,
    // and that placement is the point rather than a detail. Concat
    // COPIES from both operands and keeps neither, so the `a + b`
    // inside `a + b + c` is dead the moment the outer concat returns;
    // freeing at the eventual binding site instead would leak one
    // buffer per `+`. Equality is the same: a bool is not a text
    // reference, so `f() == g()` has no later owner for either result.
    // claude.md #256: an ascii on either side makes the OTHER side an
    // ascii too, before anything is compared or joined. A text literal
    // converts at compile time into an immortal .rodata constant, so
    // the common lexer comparison allocates nothing at all; a
    // non-literal text operand pays a real festina_ascii_from_text,
    // which is the honest cost of validating that it is representable.
    if l.fty == 'ascii' || r.fty == 'ascii' {
        if l.fty == 'text' {
            l = cgTextToAscii(ln, l)
            if CG_STUCK { return none }
        }
        if r.fty == 'text' {
            r = cgTextToAscii(rn, r)
            if CG_STUCK { return none }
        }
        if op == '==' || op == '!=' {
            text aeq = cgTmp()
            cgOut(`  ${aeq} = call i8 @festina_ascii_eq(ptr ${l.v}, ptr ${r.v})`)
            text ares2 = aeq
            if op == '!=' {
                text neg = cgTmp()
                cgOut(`  ${neg} = xor i8 ${aeq}, 1`)
                ares2 = neg
            }
            cgReleaseOwnedReceiver(ln, l)
            cgReleaseOwnedReceiver(rn, r)
            return cgVal(ares2, 'i8', 'bool')
        }
        if op == '+' {
            text acat = cgTmp()
            cgOut(`  ${acat} = call ptr @festina_ascii_concat(ptr ${l.v}, ptr ${r.v})`)
            cgReleaseOwnedReceiver(ln, l)
            cgReleaseOwnedReceiver(rn, r)
            Val acr = cgVal(acat, 'ptr', 'ascii')
            acr.fresh = true
            return acr
        }
        cgUnported(`operator ${op} between ascii values`)
        return none
    }
    if l.fty == 'text' || r.fty == 'text' {
        if l.fty != 'text' || r.fty != 'text' {
            cgUnported(`operator ${op} between ${l.fty} and ${r.fty}`)
            return none
        }
        if op == '==' || op == '!=' {
            text eq = cgTmp()
            // i8 straight out of festina_str_eq, which only ever
            // answers 0 or 1 -- already the final bool, no zext, unlike
            // the icmp path below.
            cgOut(`  ${eq} = call i8 @festina_str_eq(ptr ${l.v}, ptr ${r.v})`)
            text res = eq
            if op == '!=' {
                text neg = cgTmp()
                cgOut(`  ${neg} = xor i8 ${eq}, 1`)
                res = neg
            }
            cgFreeTextTemp(childOf(e, 'left'), l)
            cgFreeTextTemp(childOf(e, 'right'), r)
            return cgVal(res, 'i8', 'bool')
        }
        if op == '+' {
            text cat = cgTmp()
            cgOut(`  ${cat} = call ptr @festina_str_concat(ptr ${l.v}, ptr ${r.v})`)
            cgFreeTextTemp(childOf(e, 'left'), l)
            cgFreeTextTemp(childOf(e, 'right'), r)
            return cgVal(cat, 'ptr', 'text')
        }
        cgUnported(`operator ${op} on text`)
        return none
    }

    // A comparison against `null` is its own branch, and it differs
    // from the generic one twice over: `icmp ... ptr X, null` rather
    // than an i64 compare, and the two temps in the opposite order --
    // the comparison's own first, then the widening. `text == null`
    // never reaches here, because the text branch above claims it for
    // festina_str_eq.
    if op == '==' || op == '!=' {
        bool rNull = rn.kind == 'NullLit'
        bool lNull = ln.kind == 'NullLit'
        if rNull || lNull {
            text other = r.lty
            text value = r.v
            if rNull { other = l.lty  value = l.v }
            if other == 'ptr' {
                text cmp = cgTmp()
                text pred = 'eq'
                if op == '!=' { pred = 'ne' }
                cgOut(`  ${cmp} = icmp ${pred} ptr ${value}, null`)
                text out = cgTmp()
                cgOut(`  ${out} = zext i1 ${cmp} to i8`)
                return cgVal(out, 'i8', 'bool')
            }
        }
    }

    // Identity, for a value that IS a reference. The original compares
    // the two pointers as i64 rather than as ptr -- they go through the
    // ordinary integer comparison path, which never learns they were
    // pointers. Same answer, different text.
    if cgIsRefcounted(l.fty) || cgIsRefcounted(r.fty) {
        if op != '==' && op != '!=' {
            cgUnported(`operator ${op} on ${l.fty}`)
            return none
        }
        if cgIsRefcounted(l.fty) == false || cgIsRefcounted(r.fty) == false {
            cgUnported(`operator ${op} between ${l.fty} and ${r.fty}`)
            return none
        }
        text res = cgTmp()
        text cmp = cgTmp()
        text pred = 'eq'
        if op == '!=' { pred = 'ne' }
        cgOut(`  ${cmp} = icmp ${pred} i64 ${l.v}, ${r.v}`)
        cgOut(`  ${res} = zext i1 ${cmp} to i8`)
        return cgVal(res, 'i8', 'bool')
    }


    // int/float mix: whichever side is int gets an sitofp, exactly as
    // though .toFloat() had been written on it (claude.md #143).
    if l.fty == 'int' && r.fty == 'float' {
        text c = cgTmp()
        cgOut(`  ${c} = sitofp i64 ${l.v} to double`)
        l = cgVal(c, 'double', 'float')
    }
    if l.fty == 'float' && r.fty == 'int' {
        text c = cgTmp()
        cgOut(`  ${c} = sitofp i64 ${r.v} to double`)
        r = cgVal(c, 'double', 'float')
    }
    bool useFloat = l.fty == 'float'

    // What each operator class actually accepts. Without this guard
    // `s + 'x'` on two `text` operands emitted `add i64` over two
    // POINTERS -- valid LLVM, catastrophically wrong, and a silent
    // difference rather than an honest "not ported yet". `text` now has
    // its own branch above; everything else pointer-backed (struct,
    // arr[T], map[T], blob, img, aud, regex, a table row) still reaches
    // here and is still refused, which is what keeps the next type
    // added an honest gap rather than wrong arithmetic.
    //
    // Equality admits bool as well as the numbers, which is what makes
    // `done == true` work -- the first version of this guard required
    // int/float on both sides and broke exactly that.
    bool isEquality = op == '==' || op == '!='
    bool lOk = l.fty == 'int' || l.fty == 'float'
    bool rOk = r.fty == 'int' || r.fty == 'float'
    if isEquality {
        if l.fty == 'bool' { lOk = true }
        if r.fty == 'bool' { rOk = true }
    }
    if lOk == false {
        cgUnported(`operator ${op} on ${l.fty}`)
        return none
    }
    if rOk == false {
        cgUnported(`operator ${op} on ${r.fty}`)
        return none
    }

    if op == '/' || op == '%' {
        // claude.md #57: division or modulo by zero answers null rather
        // than trapping, and for int that has to be real control flow
        // -- sdiv/srem by zero is undefined at the hardware level, so
        // checking afterwards is too late and a `select` would still
        // execute the trapping instruction.
        //
        // claude.md #143: `/` always answers float, so two int operands
        // are BOTH converted first and the whole thing goes through the
        // float path.
        bool asFloat = useFloat
        if op == '/' { asFloat = true }
        text lv = l.v
        text rv = r.v
        if asFloat && useFloat == false {
            text lc = cgTmp()
            cgOut(`  ${lc} = sitofp i64 ${lv} to double`)
            text rc = cgTmp()
            cgOut(`  ${rc} = sitofp i64 ${rv} to double`)
            lv = lc
            rv = rc
        }
        return cgDivMod(op, lv, rv, asFloat)
    }

    // The result temp is taken BEFORE the comparison temp, which is
    // what festina/codegen.py does and therefore what the numbering
    // has to be: `out = self.tmp()` runs above the icmp branch.
    text out = cgTmp()

    if op == '+' || op == '-' || op == '*' {
        text ins = 'add'
        if op == '-' { ins = 'sub' }
        if op == '*' { ins = 'mul' }
        if useFloat {
            if op == '+' { ins = 'fadd' }
            if op == '-' { ins = 'fsub' }
            if op == '*' { ins = 'fmul' }
        }
        text ty = 'i64'
        if useFloat { ty = 'double' }
        cgOut(`  ${out} = ${ins} ${ty} ${l.v}, ${r.v}`)
        if useFloat { return cgVal(out, 'double', 'float') }
        return cgVal(out, 'i64', 'int')
    }

    if op == '<' || op == '>' || op == '<=' || op == '>=' || op == '==' || op == '!=' {
        text cmpName = 'icmp'
        text pred = ''
        if useFloat {
            cmpName = 'fcmp'
            if op == '<' { pred = 'olt' }
            if op == '>' { pred = 'ogt' }
            if op == '<=' { pred = 'ole' }
            if op == '>=' { pred = 'oge' }
            if op == '==' { pred = 'oeq' }
            if op == '!=' { pred = 'one' }
        } else {
            if op == '<' { pred = 'slt' }
            if op == '>' { pred = 'sgt' }
            if op == '<=' { pred = 'sle' }
            if op == '>=' { pred = 'sge' }
            if op == '==' { pred = 'eq' }
            if op == '!=' { pred = 'ne' }
        }
        text cmpOut = cgTmp()
        text ty = 'i64'
        if useFloat { ty = 'double' }
        if useFloat == false && l.fty == 'bool' { ty = 'i8' }
        cgOut(`  ${cmpOut} = ${cmpName} ${pred} ${ty} ${l.v}, ${r.v}`)
        cgOut(`  ${out} = zext i1 ${cmpOut} to i8`)
        return cgVal(out, 'i8', 'bool')
    }

    cgUnported(`operator ${op}`)
    return none
}

// `test ? cons : alt`, as two real blocks joined by a phi. Each arm's
// value is computed INSIDE its own block, which is what makes the
// short-circuit real rather than decorative.
// The address of a struct field: evaluate the object, then GEP to the
// field's own index. Answers an empty `v` when the shape is not one
// this understands, having already reported why.
//
// The local here is `fp`, not the obvious `at`: bootstrap/parser.f
// exports `bool func at(kind:text)`, and a local that shadows a
// function name passes semantic analysis but resolves to the FUNCTION
// inside a template, so `${at}` failed the whole compile with
// `cannot interpolate a value of type func[text]:bool` and no line
// number. Recorded in todo.md -- the shadowing itself is legal and
// should either resolve to the local everywhere or be rejected at the
// declaration, not at an unrelated interpolation.
Val func cgFieldPtr(e:Node) {
    Val none
    if e.fields.length > 0 {
        if fieldOf(e, 'computed').raw == 'true' {
            cgUnported('computed member access')
            return none
        }
    }
    Val obj = cgExpr(childOf(e, 'obj'))
    if CG_STUCK { return none }
    // A ROW's columns are flat 8-byte cells the runtime laid out, so
    // the address is plain byte arithmetic rather than a typed
    // getelementptr into a named struct -- there is no LLVM type here
    // to index into.
    if obj.fty == 'table' {
        text ckey = `${obj.sname}.${rawText(e, 'prop')}`
        if TB_FTY[ckey] == null {
            cgUnported(`column ${rawText(e, 'prop')} of ${obj.sname}`)
            return none
        }
        text cfty = TB_FTY[ckey]
        if cgLtyOf(cfty) == '' {
            cgUnported(`column of type ${cfty}`)
            return none
        }
        int coff = TB_IDX[ckey] * 8
        text cfp = cgTmp()
        cgOut(`  ${cfp} = getelementptr i8, ptr ${obj.v}, i64 ${coff}`)
        Val cr = cgVal(cfp, cgLtyOf(cfty), cfty)
        CG_FIELD_BASE = obj
        return cr
    }
    if obj.fty != 'struct' {
        cgUnported(`member access on ${obj.fty}`)
        return none
    }
    text key = `${obj.sname}.${rawText(e, 'prop')}`
    if SF_LTY[key] == null {
        cgUnported(`field ${rawText(e, 'prop')} of ${obj.sname}`)
        return none
    }
    text fp = cgTmp()
    cgOut(`  ${fp} = getelementptr %struct.${obj.sname}, ptr ${obj.v}, i32 0, i32 ${SF_IDX[key]}`)
    Val r = cgVal(fp, SF_LTY[key], SF_FTY[key])
    if SF_FTY[key] == 'struct' { r.sname = SF_SNAME[key] }
    if SF_ETY[key] != null { r.ety = SF_ETY[key] }
    // The base travels with the pointer, because a READ through a base
    // this expression owns has to mint the field's own ownership
    // before that base is released -- see cgMemberRead. A WRITE uses
    // the pointer and nothing else.
    CG_FIELD_BASE = obj
    return r
}

// The LLVM payload type behind a managed field, which is what the
// auto-vivify path calloc's. Derivable from the field type rather than
// recorded: an `amor arr[T]` field has no managed field type at all
// (cgManagedFty answers '' for it), so `arr` here is always the one
// plain %struct._FestinaArray shape and there is no amortized variant
// to confuse it with.
text func cgFieldPayload(fp:Val) {
    if fp.fty == 'struct' { return `%struct.${fp.sname}` }
    if fp.fty == 'arr' { return '%struct._FestinaArray' }
    if fp.fty == 'map' { return '%struct._FestinaMap' }
    return ''
}

// Loads one field, giving a struct/arr[T]/map[T]-typed one real storage
// the first time it is reached.
//
// claude.md #97: those three field types start as a null pointer --
// calloc/zeroinitializer gives them no value of their own, unlike an
// int field whose zero IS 0. So the storage is created on first use,
// stored back through the same slot so every later read sees the same
// one, and the two paths join in a phi.
//
// Every struct here is untagged. A tagged one -- a member of a
// pure-struct enum, claude.md #176 -- needs a wider {tag, refcount}
// header, and this emits the plain one; that is safe only because an
// EnumDecl is itself unported, so no program reaching here has an enum
// at all. Porting enums means porting the tagged header with them.
Val func cgLoadFieldValue(fp:Val) {
    text payload = cgFieldPayload(fp)
    if payload == '' {
        text plain = cgTmp()
        cgOut(`  ${plain} = load ${fp.lty}, ptr ${fp.v}`)
        return cgVal(plain, fp.lty, fp.fty)
    }

    text loaded = cgTmp()
    cgOut(`  ${loaded} = load ptr, ptr ${fp.v}`)
    text isNull = cgTmp()
    cgOut(`  ${isNull} = icmp eq ptr ${loaded}, null`)
    text makeL = cgLabel('field.make')
    text doneL = cgLabel('field.done')
    cgOut(`  br i1 ${isNull}, label %${makeL}, label %${doneL}`)
    text loadPred = CG_BLOCK

    cgBlockLabel(makeL)
    text made = cgFreshHeader(payload)
    cgOut(`  store ptr ${made}, ptr ${fp.v}`)
    text makePred = CG_BLOCK
    cgOut(`  br label %${doneL}`)

    cgBlockLabel(doneL)
    text out = cgTmp()
    cgOut(`  ${out} = phi ptr [ ${loaded}, %${loadPred} ], [ ${made}, %${makePred} ]`)
    if fp.fty == 'struct' { return cgStructVal(out, fp.sname) }
    if fp.fty == 'arr' { return cgArrVal(out, fp.ety) }
    if fp.fty == 'map' { return cgMapVal(out, fp.ety) }
    return cgVal(out, 'ptr', fp.fty)
}

// `[a, b, c]` -- a fresh header, then a malloc'd data buffer.
//
// **Every element is evaluated BEFORE the header is allocated**, not
// interleaved with the stores. Read off the original's output rather
// than assumed, and it is the kind of ordering that changes every temp
// number downstream without changing what the program does.
//
// An empty literal is its own shape: length 0 and a bare `malloc(0)`,
// with no element-size computation at all -- there is nothing to
// multiply.
//
// Scalar element types only. A refcounted or text element needs its own
// retain-or-copy per slot, which is a different piece of work.
//
// `header` is claude.md #81: a non-escaping local declared directly
// from a literal knows its own element count right here, so its buffer
// size is known too and the header can be built straight into the
// frame slot the declaration already allocated. '' means "allocate a
// fresh heap one", which is every other position a literal can appear
// in.
Val func cgArrayLit(e:Node, ety:text, header:text) {
    Val none
    if ety == '' || cgElemLty(ety) == '' {
        cgUnported('array literal of a non-scalar type')
        return none
    }
    if cgStorableRefcounted('arr', ety) == false {
        cgUnported(`array literal of ${ety}`)
        return none
    }
    text elemLty = cgElemLty(ety)
    arr[Node] elems = listOf(e, 'elements')

    arr[text] vals = []
    arr[int] owned = []
    int i = 0
    while i < elems.length {
        Val v = cgExprExpectingElem(elems[i], ety)
        if CG_STUCK { return none }
        if cgValIsElem(v, ety) == false {
            cgUnported(`array literal element of type ${v.fty} in an array of ${ety}`)
            return none
        }
        vals.push(v.v)
        bool isOwning = cgOwnsText(elems[i], v)
        if cgElemIsRefcounted(ety) { isOwning = cgIsOwningRefcountedSource(elems[i]) }
        if isOwning { owned.push(1) } else { owned.push(0) }
        i++
    }

    text into = header
    if into == '' { into = cgFreshHeader('%struct._FestinaArray') }
    text lenP = cgTmp()
    cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr ${into}, i32 0, i32 0`)
    cgOut(`  store i64 ${elems.length}, ptr ${lenP}`)

    text total = '0'
    if elems.length > 0 {
        text sz = cgTmp()
        cgOut(`  ${sz} = getelementptr ${elemLty}, ptr null, i64 1`)
        text szi = cgTmp()
        cgOut(`  ${szi} = ptrtoint ptr ${sz} to i64`)
        text mul = cgTmp()
        cgOut(`  ${mul} = mul i64 ${szi}, ${elems.length}`)
        total = mul
    }
    text data = cgTmp()
    cgOut(`  ${data} = call ptr @malloc(i64 ${total})`)

    int k = 0
    while k < vals.length {
        text slot = cgTmp()
        cgOut(`  ${slot} = getelementptr ${elemLty}, ptr ${data}, i64 ${k}`)
        text v = vals[k]
        // claude.md #83: a text element is COPIED into its slot unless
        // the expression already owns a buffer nothing else holds, and
        // a refcounted one takes its own reference on the same terms.
        // No release-old here, unlike `xs[i] = v` on a built array:
        // this buffer is fresh malloc'd memory, so every slot is
        // written exactly once and there is no stale value to reclaim.
        if SF_NAMES[ety] != null {
            if owned[k] == 0 { cgOut(`  call void @festina_retain(ptr ${v})`) }
        } else if ety == 'text' && owned[k] == 0 {
            text o = cgTmp()
            cgOut(`  ${o} = call ptr @festina_text_own(ptr ${v})`)
            v = o
        }
        cgOut(`  store ${elemLty} ${v}, ptr ${slot}`)
        k++
    }

    text dataP = cgTmp()
    cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr ${into}, i32 0, i32 1`)
    cgOut(`  store ptr ${data}, ptr ${dataP}`)
    return cgArrVal(into, ety)
}

// An expression in a position whose type is already known, which is
// what festina/codegen.py's own _emit_value_for is for. Only an array
// literal actually needs it -- `[]` and `[1, 2]` carry no element type
// of their own, so a bare cgExpr could not know what to emit -- but
// routing every typed position through one function is what keeps the
// two from drifting.
Val func cgExprExpecting(e:Node, fty:text, ety:text) {
    // `null` has no type of its own, so it can only be emitted where
    // one is already known. Each Festina type spells its own null
    // differently -- i64's minimum, a NaN, 2 for a bool, the LLVM null
    // pointer for everything else -- and cgNullValue is the single
    // place that decides which.
    if e.kind == 'NullLit' {
        // No type in reach either (an argument to a function whose
        // signature this port has not registered, say): the untyped
        // `null` pointer, which is what the original answers too.
        if cgLtyOf(fty) == '' { return cgExpr(e) }
        return cgVal(cgNullValue(fty), cgLtyOf(fty), fty)
    }
    if e.kind == 'ArrayLit' {
        if fty != 'arr' {
            Val none
            cgUnported(`array literal in a ${fty} position`)
            return none
        }
        return cgArrayLit(e, ety, '')
    }
    // claude.md #32-34: a `sqlite()` whose rows are COLLECTED, which
    // only the destination's declared type can say. Placed here beside
    // the literal cases for the same reason they are here: the
    // expression's own shape is not enough to emit it.
    if e.kind == 'Call' {
        if fty == 'arr' {
            if TBL_COLS[ety] != null {
                Node scal = childOf(e, 'callee')
                if scal != null {
                    if scal.kind == 'Identifier' {
                        if rawText(scal, 'name') == 'sqlite' {
                            return cgSqliteCollect(e, ety)
                        }
                    }
                }
            }
        }
    }
    if e.kind == 'MapLit' {
        if fty != 'map' {
            Val none
            cgUnported(`map literal in a ${fty} position`)
            return none
        }
        return cgMapLit(e, ety, '')
    }
    // claude.md #91: a colour must come from a LITERAL, so the
    // compiler resolves it once -- and there is deliberately no runtime
    // resolver to fall back on, which is why anything else in a colour
    // position is an error rather than a deferred lookup. Placed before
    // the expression is emitted at all, because a literal here produces
    // no code: it IS the packed integer.
    if fty == 'color' {
        if e.kind == 'StringLit' {
            text packed = cgColorValue(rawText(e, 'value'))
            if packed == '' {
                cgUnported(`colour literal ${rawText(e, 'value')}`)
                Val none2
                return none2
            }
            return cgVal(packed, 'i64', 'color')
        }
        if e.kind == 'NullLit' {
            // `null` is not a text literal to resolve: it is colour's
            // own 'none' sentinel, the same value an unassigned colour
            // already reads as.
            return cgVal('-1', 'i64', 'color')
        }
    }
    Val v = cgExpr(e)
    if CG_STUCK { return v }
    // claude.md #109: a text in a blob position OPENS it. The handle
    // is fresh -- the count starts at 1 -- so nothing retains it, and
    // the path text is freed here if the expression allocated it.
    if fty == 'blob' && v.fty == 'text' {
        text out = cgTmp()
        cgOut(`  ${out} = call ptr @festina_blob_open(ptr ${v.v})`)
        cgFreeTextTemp(e, v)
        Val opened = cgVal(out, 'ptr', 'blob')
        // The handle is a fresh +1 however the SOURCE expression
        // reads: `blob b = 'path'` has a string literal for a node,
        // which owns nothing, and a value that owns everything. That
        // is the one place the node and the value disagree, so the
        // answer travels with the value.
        opened.fresh = true
        return opened
    }
    // claude.md #118: a text in an img position LOADS it, exactly as a
    // text in a blob position opens one -- and the handle is fresh for
    // the same reason, so nothing retains it and the path is freed
    // here if this expression allocated it. Loading needs no window:
    // decoding a PNG is Cairo's own in-memory decoder, which is why
    // this sets the CODE flag and not the one that opens a canvas.
    if fty == 'img' && v.fty == 'text' {
        CG_USES_GRAPHICS_CODE = true
        text iout = cgTmp()
        cgOut(`  ${iout} = call ptr @festina_load_image(ptr ${v.v})`)
        cgFreeTextTemp(e, v)
        Val loaded = cgVal(iout, 'ptr', 'img')
        loaded.fresh = true
        return loaded
    }
    if fty == 'aud' && v.fty == 'text' {
        CG_USES_AUDIO = true
        text aout3 = cgTmp()
        cgOut(`  ${aout3} = call ptr @festina_load_audio(ptr ${v.v})`)
        cgFreeTextTemp(e, v)
        Val loadedA = cgVal(aout3, 'ptr', 'aud')
        loadedA.fresh = true
        return loadedA
    }
    // claude.md #256: a text in an ascii position. A LITERAL is folded
    // into .rodata with its own inline header and needs no call at
    // all; anything else is a real runtime conversion that validates
    // and answers null for non-ascii input, the way toInt() answers
    // null for unparseable text.
    if fty == 'ascii' && v.fty == 'text' {
        if e.kind == 'StringLit' {
            return cgVal(cgAsciiConst(rawText(e, 'value')), 'ptr', 'ascii')
        }
        text aout = cgTmp()
        cgOut(`  ${aout} = call ptr @festina_ascii_from_text(ptr ${v.v})`)
        cgFreeTextTemp(e, v)
        Val made = cgVal(aout, 'ptr', 'ascii')
        made.fresh = true
        return made
    }
    // The other direction is always a real copy, since a text is a bare
    // char* with no header in front of it to share.
    if fty == 'text' && v.fty == 'ascii' {
        text tout = cgTmp()
        cgOut(`  ${tout} = call ptr @festina_ascii_to_text(ptr ${v.v})`)
        Val asText = cgVal(tout, 'ptr', 'text')
        asText.fresh = true
        return asText
    }
    return v
}

// claude.md #256: a text literal in an ascii position, emitted
// straight into .rodata with its own INLINE {length, refcount} header
// -- the same `{i64 -1, T}`-plus-getelementptr shape every managed
// global already uses. The count is the immortal sentinel, so retain,
// release and `free` on a literal are all no-ops through the very
// checks every other immortal value goes through, and the payload
// pointer handed out is indistinguishable from a heap ascii's.
//
// That is the whole reason ascii can carry a header where text cannot:
// every ascii in existence is built by this compiler or by
// festina_ascii_alloc, so there are none of text's four provenances to
// make a header invalid for.
text func cgAsciiConst(v:text) {
    if CG_ASTR_MAP[v] != null { return CG_ASTR_MAP[v] }
    text name = `@.astr.${CG_ASTR_N}`
    CG_ASTR_N++
    int bytes = cgUtf8Bytes(v) + 1
    text ty = `{i64, i64, [${bytes} x i8]}`
    CG_EXTRA.push(`${name} = private unnamed_addr constant ${ty} {i64 ${bytes - 1}, i64 -1, [${bytes} x i8] c"${cgCEscape(v)}\\00"}`)
    text ref = `getelementptr inbounds (${ty}, ptr ${name}, i32 0, i32 2)`
    CG_ASTR_MAP[v] = ref
    return ref
}

// ---------------------------------------------------------------------
// Maps.
//
// Every map runtime function deals in a raw i64 payload whatever T is
// -- `festina_map_get` has no idea what a given map's values are -- so
// the compiler reinterprets in both directions at every boundary, and
// picks the "key not present" answer itself at compile time.

text func cgMapToI64(v:text, vlty:text) {
    if vlty == 'i64' { return v }
    text out = cgTmp()
    if vlty == 'double' { cgOut(`  ${out} = bitcast double ${v} to i64`) }
    else if vlty == 'i8' { cgOut(`  ${out} = zext i8 ${v} to i64`) }
    else { cgOut(`  ${out} = ptrtoint ptr ${v} to i64`) }
    return out
}

text func cgMapFromI64(raw:text, vlty:text) {
    if vlty == 'i64' { return raw }
    text out = cgTmp()
    if vlty == 'double' { cgOut(`  ${out} = bitcast i64 ${raw} to double`) }
    else if vlty == 'i8' { cgOut(`  ${out} = trunc i64 ${raw} to i8`) }
    else { cgOut(`  ${out} = inttoptr i64 ${raw} to ptr`) }
    return out
}

// claude.md #72: "if the key is not present, the result is null" --
// and which bit pattern that is depends on T, which only the compiler
// knows. The float one is a NaN; the bool one is 2, a value no real
// `bool` can hold.
// The null of a Festina type, as a constant in its own LLVM type --
// distinct from cgMapMissing, which answers the same question in the
// raw i64 every map call deals in.
text func cgNullValue(fty:text) {
    // claude.md #91: an unset colour is 'none', not 0 -- 0 is a real
    // colour (opaque black), so a zero default would silently paint.
    if fty == 'color' { return '-1' }
    if fty == 'float' { return '0x7FF8000000000000' }
    if fty == 'bool' { return '2' }
    if fty == 'int' { return '-9223372036854775808' }
    return 'null'
}

text func cgMapMissing(vlty:text) {
    if vlty == 'double' { return '9221120237041090560' }
    if vlty == 'i8' { return '2' }
    if vlty == 'ptr' { return '0' }
    return '-9223372036854775808'
}

// A key expression and whether the buffer it produced has an owner.
// The two cannot be recovered from each other: claude.md #302 renders
// a non-text key, which makes a fresh buffer out of an expression that
// looks borrowed.
struct MapKey {
    v:text
    owned:bool
}

// claude.md #302: a key may be any type with a text form, rendered
// exactly as `log()` and `${...}` render it. A `text` key is used as
// it stands and is owned only if its own expression owns it.
MapKey func cgMapKey(e:Node) {
    MapKey k
    Val v = cgExpr(e)
    if CG_STUCK { return k }
    if v.fty == 'text' {
        k.v = v.v
        k.owned = cgOwnsText(e, v)
        return k
    }
    Val r = cgToText(v)
    if CG_STUCK { return k }
    k.v = r.v
    k.owned = true
    return k
}

// `m[k]` -- entries and capacity read straight out of the map's own
// storage. No `count`: festina_map_get scans buckets by capacity, not
// a dense range, so a read never needed it.
Val func cgMapGet(objV:text, vty:text, keyV:text) {
    text vlty = cgElemLty(vty)
    text entP = cgTmp()
    cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr ${objV}, i32 0, i32 1`)
    text ent = cgTmp()
    cgOut(`  ${ent} = load ptr, ptr ${entP}`)
    text capP = cgTmp()
    cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr ${objV}, i32 0, i32 2`)
    text cap = cgTmp()
    cgOut(`  ${cap} = load i64, ptr ${capP}`)
    text raw = cgTmp()
    cgOut(`  ${raw} = call i64 @festina_map_get(ptr ${ent}, i64 ${cap}, ptr ${keyV}, i64 ${cgMapMissing(vlty)})`)
    text out = cgMapFromI64(raw, vlty)
    if SF_NAMES[vty] != null { return cgStructVal(out, vty) }
    // A row read out of a map, on exactly a struct's terms: the value
    // type here is the TABLE name, so a plain cgVal would hand back a
    // value whose fty was 'People' -- a type nothing downstream knows
    // -- instead of a row that remembers which table it came from.
    if TBL_COLS[vty] != null { return cgTableVal(out, vty) }
    return cgVal(out, vlty, vty)
}

// `m[k] = v`, and every entry of a map literal, which is the same
// call. Unlike a read this needs the map's own header ADDRESS: a set
// can rehash the whole table and has to write the new
// count/entries/capacity/tombstones back for the change to stick,
// which is why all four fields are passed as pointers.
//
// Scalar value types only. A refcounted or `text` value has to find
// and release whatever the key mapped to before -- there is no fixed
// address to load an old value from, so it takes a festina_map_get of
// its own first -- and that is its own piece of work.
void func cgMapSet(mapPtr:text, vty:text, keyV:text, valV:text, keyOwned:bool, valOwning:bool) {
    text vlty = cgElemLty(vty)
    text countP = cgTmp()
    cgOut(`  ${countP} = getelementptr %struct._FestinaMap, ptr ${mapPtr}, i32 0, i32 0`)
    text entP = cgTmp()
    cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr ${mapPtr}, i32 0, i32 1`)
    text capP = cgTmp()
    cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr ${mapPtr}, i32 0, i32 2`)
    text tombP = cgTmp()
    cgOut(`  ${tombP} = getelementptr %struct._FestinaMap, ptr ${mapPtr}, i32 0, i32 3`)
    // claude.md #80: a refcounted value has to give back whatever the
    // key mapped to before. There is no fixed address to load an old
    // value from -- a key may or may not be present and
    // festina_map_set does not say which -- so festina_map_get with a
    // null default finds it, which is safe unconditionally: 0 can
    // never be a real heap pointer, so it only ever means "nothing to
    // release".
    text oldPtr = ''
    text oldFn = ''
    text stored = valV
    if cgElemOwnsSomething(vty) {
        text ent = cgTmp()
        cgOut(`  ${ent} = load ptr, ptr ${entP}`)
        text cap = cgTmp()
        cgOut(`  ${cap} = load i64, ptr ${capP}`)
        text oldRaw = cgTmp()
        cgOut(`  ${oldRaw} = call i64 @festina_map_get(ptr ${ent}, i64 ${cap}, ptr ${keyV}, i64 0)`)
        text old = cgTmp()
        cgOut(`  ${old} = inttoptr i64 ${oldRaw} to ptr`)
        if vty == 'text' {
            // claude.md #83: text is copied rather than retained, and
            // the old buffer is freed BEFORE the set rather than after
            // -- there is no cycle trial behind a free, so nothing
            // needs the entry to be updated first.
            if valOwning == false {
                text o = cgTmp()
                cgOut(`  ${o} = call ptr @festina_text_own(ptr ${stored})`)
                stored = o
            }
            cgOut(`  call void @free(ptr ${old})`)
        } else {
            if valOwning == false {
                cgOut(`  call void @festina_retain(ptr ${stored})`)
            }
            // Resolved HERE, before the value's own reinterpretation
            // takes a temp -- generating the value type's cascade is
            // what this call may do, and those temps come first.
            oldPtr = old
            // Through the ELEMENT dispatch, not a hardcoded 'struct'.
            // The two agree for a struct value type, which is why the
            // narrower spelling survived this long -- but a map of
            // rows, of handles, or of containers all need their own
            // release here, and the generic one would drop a row's
            // columns, a blob's buffer or an inner array's elements on
            // the floor.
            oldFn = cgElemReleaseFn(vty)
        }
    }
    text raw = cgMapToI64(stored, vlty)
    cgOut(`  call void @festina_map_set(ptr ${countP}, ptr ${entP}, ptr ${capP}, ptr ${tombP}, ptr ${keyV}, i64 ${raw})`)
    // claude.md #120: DEFERRED until after the set. A cycle trial run
    // by that release must never find the entry still pointing at the
    // value whose count it has just dropped.
    if oldPtr != '' {
        cgOut(`  call void ${oldFn}(ptr ${oldPtr})`)
    }
    // claude.md #97: festina_map_set strdups the key, so a key the
    // caller allocated has no owner left once this returns. Freed here
    // rather than at each call site so the literal path and the
    // assignment path get it from one place.
    if keyOwned { cgOut(`  call void @free(ptr ${keyV})`) }
}

// `{ 'a': 1, 'b': 2 }`. Unlike an array literal the header comes
// FIRST: festina_map_set mutates it in place once per entry, in source
// order, so a repeated key ends up last-one-wins with no dedup pass.
Val func cgMapLit(e:Node, vty:text, header:text) {
    Val none
    if vty == '' || cgElemLty(vty) == '' {
        cgUnported('map literal of a non-scalar type')
        return none
    }
    text into = header
    if into == '' { into = cgFreshHeader('%struct._FestinaMap') }
    // Each entry is a `#pair` node -- festina/parser.py stores them as
    // tuples, which the canonical dump renders exactly like a list, so
    // the port models them as a node with an `a` and a `b`.
    arr[Node] entries = listOf(e, 'entries')
    int i = 0
    while i < entries.length {
        MapKey k = cgMapKey(childOf(entries[i], 'a'))
        if CG_STUCK { return none }
        Val v = cgExprExpecting(childOf(entries[i], 'b'), vty, '')
        if CG_STUCK { return none }
        if cgValIsElem(v, vty) == false {
            cgUnported(`map literal value of type ${v.fty} in a map of ${vty}`)
            return none
        }
        cgMapSet(into, vty, k.v, v.v, k.owned, cgMapValOwns(vty, childOf(entries[i], 'b'), v))
        i++
    }
    return cgMapVal(into, vty)
}

// claude.md #111: `delete m[k]`, JS-shaped -- the entry stops existing,
// the count drops and forEach skips it.
//
// count and tombstones are out-params (a delete either removes a live
// entry or converts it into a tombstone); capacity is read by VALUE,
// because a delete never grows the table. That asymmetry is the whole
// difference between this call and a set's.
//
// The last argument is a per-value-type release trampoline, needed only
// when the values themselves own something. A scalar map passes null.
// claude.md #111: `free name` releases whatever the binding holds and
// then NULLS the binding. The null store is half the design rather
// than tidying: every release in this runtime is null-safe, so the
// automatic scope-exit release that may later visit this same binding
// finds null and does nothing. Manual and automatic reclamation
// coexist with no bookkeeping between them, `free x` twice is a no-op
// rather than a double free, and use-after-free THROUGH THIS BINDING
// is impossible -- reading x afterwards reads null, the ordinary
// absent value.
//
// A refcounted binding is DECREMENTED, not forcibly freed: an aliased
// value survives until its other references drop. A text buffer is
// exclusively owned (claude.md #83) and so is freed outright. A scalar
// has nothing to release and `free` degenerates to `x = null` -- which
// is why the null store is emitted for every type and the release for
// only some.
//
// decisions.md #283/#284: `clear` is the same statement with
// `zeroing`, and the intent cannot ride on this call site, because a
// release runs a cascade this site does not walk. It travels as
// runtime state set around the whole cascade instead, and every free
// inside consults it -- so a value still referenced elsewhere is
// neither freed nor zeroed, the flag being read only at a free that
// actually happens.
void func cgFree(s:Node) {
    text name = rawText(s, 'name')
    text slot = cgSlotOf(name)
    if slot == '' {
        cgUnported(`free of ${name}`)
        return
    }
    text fty = cgFtyOf(name)
    text lty = cgLtyOf(fty)
    if lty == '' {
        cgUnported(`free of a ${fty}`)
        return
    }
    bool zeroing = fieldOf(s, 'zeroing').raw == 'true'
    if lty == 'ptr' {
        text old = cgTmp()
        cgOut(`  ${old} = load ptr, ptr ${slot}`)
        if cgIsRefcounted(fty) {
            // Resolved before the calls are emitted, because resolving
            // may GENERATE the cascade and its temps come first.
            text relFn = cgReleaseFnFor(fty, cgRelKeyOf(name))
            if zeroing { cgOut('  call void @festina_begin_clearing()') }
            cgOut(`  call void ${relFn}(ptr ${old})`)
            if zeroing { cgOut('  call void @festina_end_clearing()') }
        } else if fty == 'text' {
            if zeroing {
                cgOut(`  call void @festina_clear_text(ptr ${old})`)
            } else {
                cgOut(`  call void @free(ptr ${old})`)
            }
        }
        cgOut(`  store ptr null, ptr ${slot}`)
        return
    }
    cgOut(`  store ${lty} ${cgNullValue(fty)}, ptr ${slot}`)
}

// claude.md #157/#235: `try { A } catch (name:text) { B }`.
//
// The setjmp call is emitted RIGHT HERE, into the enclosing function's
// own IR, rather than behind a runtime helper -- setjmp only captures
// a valid jump target while its OWN calling frame is still live, and a
// helper that calls it and returns has already invalidated that frame
// by the time a later throw tries to jump back into it. Emitting it
// here makes the "calling function" the one containing the try, which
// by construction cannot have returned yet.
//
// libc's setjmp rather than the llvm.eh.sjlj intrinsics: those have no
// lowering at all on wasm32 or AArch64 and a broken one on x86_64
// Windows. Structurally this is a plain two-way branch, exactly like an
// `if` -- 0 is the first, normal arrival and runs A; nonzero means a
// throw's longjmp landed straight back here and runs B.
//
// A's frame gets one extra entry, a try-frame MARKER, in a wrapper
// frame of its own, so that any exit from A -- fallthrough, return,
// break, continue, however deeply nested -- pops the runtime's catch
// frame through the same walk that frees every other local.
void func cgTry(s:Node) {
    text bufp = cgTmp()
    cgOut(`  ${bufp} = alloca [1024 x i8], align 16`)
    text rc = cgTmp()
    cgOut(`  ${rc} = call i32 @_setjmp(ptr ${bufp}, ptr null)`)
    text isCatch = cgTmp()
    cgOut(`  ${isCatch} = icmp ne i32 ${rc}, 0`)
    text tryL = cgLabel('try.body')
    text catchL = cgLabel('try.catch')
    text endL = cgLabel('try.end')
    cgOut(`  br i1 ${isCatch}, label %${catchL}, label %${tryL}`)

    cgBlockLabel(tryL)
    CG_TERM = false
    cgOut(`  call void @festina_try_push(ptr ${bufp})`)
    cgPushFrame()
    cgTrackTryFrame()
    cgBlockInto(childOf(s, 'try_body'))
    cgPopFrame()
    bool tryTerm = CG_TERM
    if tryTerm == false { cgOut(`  br label %${endL}`) }

    cgBlockLabel(catchL)
    CG_TERM = false
    // festina_try_error hands over an OWNED text value -- the runtime's
    // own copy, made when the throw happened -- bound as an ordinary
    // local, so its scope-exit cleanup is the same generic text-local
    // handling every other text local gets rather than anything
    // special-cased for this one.
    text errVal = cgTmp()
    cgOut(`  ${errVal} = call ptr @festina_try_error()`)
    text errSlot = cgTmp()
    cgOut(`  ${errSlot} = alloca ptr`)
    cgOut(`  store ptr ${errVal}, ptr ${errSlot}`)
    text cvar = rawText(s, 'catch_var')
    text savedSlot = ''
    text savedFty = ''
    if L_SLOT[cvar] != null { savedSlot = L_SLOT[cvar] }
    if L_FTY[cvar] != null { savedFty = L_FTY[cvar] }
    L_SLOT[cvar] = errSlot
    L_FTY[cvar] = 'text'
    cgPushFrame()
    cgTrackLive('text', errSlot, '')
    cgBlockInto(childOf(s, 'catch_body'))
    cgPopFrame()
    if savedSlot != '' { L_SLOT[cvar] = savedSlot } else { L_SLOT[cvar] = null }
    if savedFty != '' { L_FTY[cvar] = savedFty } else { L_FTY[cvar] = null }
    bool catchTerm = CG_TERM
    if catchTerm == false { cgOut(`  br label %${endL}`) }

    if tryTerm && catchTerm {
        CG_TERM = true
        return
    }
    cgBlockLabel(endL)
    CG_TERM = false
}

// claude.md #157/#236: `throw expr`. The message is coerced to text
// exactly as `fail()` coerces its own, and -- unlike fail -- an
// ALIASED text needs an owning copy first, because festina_throw is
// handed a pointer the unwinding is about to release.
//
// With a `try` anywhere in the program, no scope walk is emitted here
// at all: every tracked binding is already on the runtime's cleanup
// stack, and festina_throw releases exactly the entries above the
// catching frame -- this function's locals since the try, and every
// intermediate frame's, which no generated code here could reach. The
// two designs must not be combined; freeing here as well would be a
// double free.
void func cgThrow(s:Node) {
    Node ex = childOf(s, 'expr')
    Val v = cgExpr(ex)
    if CG_STUCK { return }
    Val t = cgToText(v)
    if CG_STUCK { return }
    text val = t.v
    if v.fty == 'text' {
        if cgOwnsText(ex, v) == false {
            text owned = cgTmp()
            cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${val})`)
            val = owned
        }
    }
    if CG_HAS_TRY == false {
        // A throw with nothing to unwind to is fail(), and the old
        // walk stays purely so the IR is unchanged there.
        CG_SKIP_TRY_POP = true
        cgFreeFrom(0)
        CG_SKIP_TRY_POP = false
    }
    cgOut(`  call void @festina_throw(ptr ${val})`)
}

void func cgDelete(s:Node) {
    Node target = childOf(s, 'target')
    if target == null || target.kind != 'Member' {
        cgUnported('delete of a non-member target')
        return
    }
    Val obj = cgExpr(childOf(target, 'obj'))
    if CG_STUCK { return }
    if obj.fty != 'map' {
        cgUnported(`delete through a ${obj.fty}`)
        return
    }
    if obj.ety == '' {
        cgUnported('delete from a map of a non-scalar type')
        return
    }
    MapKey k
    if fieldOf(target, 'computed').raw == 'true' {
        k = cgMapKey(childOf(target, 'prop'))
        if CG_STUCK { return }
    } else {
        // `delete m.name` -- the property is a bare name, so the key is
        // a string constant rather than an expression, and nothing owns
        // it.
        k.v = cgStringConst(rawText(target, 'prop'))
        k.owned = false
    }
    text countP = cgTmp()
    cgOut(`  ${countP} = getelementptr %struct._FestinaMap, ptr ${obj.v}, i32 0, i32 0`)
    text entP = cgTmp()
    cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr ${obj.v}, i32 0, i32 1`)
    text capP = cgTmp()
    cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr ${obj.v}, i32 0, i32 2`)
    text cap = cgTmp()
    cgOut(`  ${cap} = load i64, ptr ${capP}`)
    text tombP = cgTmp()
    cgOut(`  ${tombP} = getelementptr %struct._FestinaMap, ptr ${obj.v}, i32 0, i32 3`)
    // A deleted entry's VALUE has to be given back, and the runtime
    // cannot do it: a map's buckets are opaque to codegen's element
    // walk and codegen's types are opaque to the runtime, so the two
    // meet at a generated trampoline, exactly as the release cascade
    // does. A scalar value owns nothing and passes null.
    text delFn = 'null'
    if cgElemOwnsSomething(obj.ety) { delFn = cgMapReleaseTrampoline(obj.ety) }
    cgOut(`  call i8 @festina_map_delete(ptr ${countP}, ptr ${entP}, i64 ${cap}, ptr ${tombP}, ptr ${k.v}, ptr ${delFn})`)
    if k.owned { cgOut(`  call void @free(ptr ${k.v})`) }
}

Val func cgMemberRead(e:Node) {
    Val none
    Val fp = cgFieldPtr(e)
    if CG_STUCK { return none }
    Val base = CG_FIELD_BASE
    if cgLtyOf(fp.fty) == '' {
        cgUnported(`read of a ${fp.fty} field`)
        return none
    }
    Val out = cgLoadFieldValue(fp)
    if CG_STUCK { return none }
    // claude.md #117: the field's value points INTO a base this
    // expression owns and is about to release, so its own ownership is
    // minted first -- a refcounted field retains, a text one copies --
    // and only then is the base released. Its cascade then decrements
    // the just-retained value back to exactly the one reference this
    // expression holds. A scalar needs no minting: its loaded value
    // survives the base by copy.
    if cgIsRefcounted(base.fty) && cgOwnsRefcounted(childOf(e, 'obj'), base) {
        if cgIsRefcounted(out.fty) {
            cgOut(`  call void @festina_retain(ptr ${out.v})`)
            out.fresh = true
        } else if out.fty == 'text' {
            text owned = cgTmp()
            cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
            out.v = owned
            out.fresh = true
        }
        cgOut(`  call void ${cgReleaseFnFor(base.fty, cgRelKeyVal(base))}(ptr ${base.v})`)
    }
    return out
}

// `.length` on a `text` or an `arr[T]`. Two different mechanisms behind
// one spelling: a text length is a runtime call (a UTF-8 code-point
// walk -- claude.md #253's own reason for `ascii` existing), while an
// array's is a field of the header it already has.
Val func cgLengthOf(e:Node, obj:Val) {
    Val none
    if obj.fty == 'text' {
        text out = cgTmp()
        cgOut(`  ${out} = call i64 @festina_text_length(ptr ${obj.v})`)
        // A receiver this expression allocated -- `f().length` -- has
        // no owner left once the length is taken.
        cgFreeTextTemp(childOf(e, 'obj'), obj)
        return cgVal(out, 'i64', 'int')
    }
    // claude.md #256: an ascii's length is already sitting in its own
    // header at payload-16 -- a LOAD, not a call, and certainly not
    // text's code-point walk. This is the entire reason the type
    // exists.
    if obj.fty == 'ascii' {
        text lenP = cgTmp()
        cgOut(`  ${lenP} = getelementptr i8, ptr ${obj.v}, i64 -16`)
        text out = cgTmp()
        cgOut(`  ${out} = load i64, ptr ${lenP}`)
        cgReleaseOwnedReceiver(childOf(e, 'obj'), obj)
        return cgVal(out, 'i64', 'int')
    }
    // A blob's length is a runtime call, not a header field: unlike an
    // array, a blob handle does not carry one.
    if obj.fty == 'blob' {
        text out = cgTmp()
        cgOut(`  ${out} = call i64 @festina_blob_length(ptr ${obj.v})`)
        cgReleaseOwnedReceiver(childOf(e, 'obj'), obj)
        return cgVal(out, 'i64', 'int')
    }
    if obj.fty == 'arr' {
        text lenP = cgTmp()
        cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 0`)
        text out = cgTmp()
        cgOut(`  ${out} = load i64, ptr ${lenP}`)
        cgReleaseOwnedReceiver(childOf(e, 'obj'), obj)
        return cgVal(out, 'i64', 'int')
    }
    cgUnported(`.length on ${obj.fty}`)
    return none
}

// claude.md #119: the COMPUTED half of #117's chain ownership.
// `getRows()[0]`, `line.split(' ')[0]` -- a computed member whose
// receiver this expression owns has the same dilemma a field read
// does: releasing the container before the element escapes would free
// the element too, and not releasing it at all leaks the whole
// container, because nothing else will ever own it. The answer is the
// same one instruction: mint the element's own ownership FIRST --
// retain a refcounted one, copy a text one -- and only then release
// the container, whose element cascade decrements the just-retained
// value back to exactly the one reference this expression holds.
//
// A scalar element needs no minting: its loaded value survives the
// container by copy, so the container is simply released.
Val func cgMintAndReleaseComputed(e:Node, out:Val, obj:Val) {
    if cgIsRefcounted(obj.fty) == false { return out }
    if cgIsOwningRefcountedSource(childOf(e, 'obj')) == false { return out }
    if cgIsRefcounted(out.fty) {
        cgOut(`  call void @festina_retain(ptr ${out.v})`)
        out.fresh = true
    } else if out.fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    cgOut(`  call void ${cgReleaseFnFor(obj.fty, cgRelKeyVal(obj))}(ptr ${obj.v})`)
    return out
}

// `xs[i]` on an array: the object, then the INDEX, then the data
// pointer. That order is the original's and it is observable -- an
// index expression with side effects of its own runs before the data
// pointer is loaded, not after.
Val func cgIndexRead(e:Node, obj:Val) {
    Val none
    if obj.fty == 'map' {
        if obj.ety == '' {
            cgUnported('indexing a map of a non-scalar type')
            return none
        }
        MapKey k = cgMapKey(childOf(e, 'prop'))
        if CG_STUCK { return none }
        Val r = cgMapGet(obj.v, obj.ety, k.v)
        // A key this expression rendered or allocated has no owner once
        // the lookup is done -- festina_map_get only reads it.
        if k.owned { cgOut(`  call void @free(ptr ${k.v})`) }
        return cgMintAndReleaseComputed(e, r, obj)
    }
    // claude.md #256: one of the 128 immortal single-character
    // singletons -- O(1) and no allocation, where text[i] both walks
    // and mallocs. Deliberately NOT treated as an owning temporary:
    // the result is immortal, so a release for it would be a no-op at
    // best and misleading at worst.
    if obj.fty == 'ascii' {
        Val aidx2 = cgExpr(childOf(e, 'prop'))
        if CG_STUCK { return none }
        text aout2 = cgTmp()
        cgOut(`  ${aout2} = call ptr @festina_ascii_char_at(ptr ${obj.v}, i64 ${aidx2.v})`)
        cgReleaseOwnedReceiver(childOf(e, 'obj'), obj)
        return cgVal(aout2, 'ptr', 'ascii')
    }
    if obj.fty != 'arr' {
        cgUnported(`indexing a ${obj.fty}`)
        return none
    }
    if obj.ety == '' {
        cgUnported('indexing an array of a non-scalar type')
        return none
    }
    Val idx = cgExpr(childOf(e, 'prop'))
    if CG_STUCK { return none }
    if idx.fty != 'int' {
        cgUnported(`array index of type ${idx.fty}`)
        return none
    }
    text elemLty = cgElemLty(obj.ety)
    text dataP = cgTmp()
    cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 1`)
    text dataV = cgTmp()
    cgOut(`  ${dataV} = load ptr, ptr ${dataP}`)
    text slot = cgTmp()
    cgOut(`  ${slot} = getelementptr ${elemLty}, ptr ${dataV}, i64 ${idx.v}`)
    text out = cgTmp()
    cgOut(`  ${out} = load ${elemLty}, ptr ${slot}`)
    return cgMintAndReleaseComputed(e, cgElemVal(out, obj.ety), obj)
}

// Every member access, computed or not, dispatched on the receiver's
// own type once it has been emitted exactly once.
Val func cgMemberAccess(e:Node) {
    Val none
    bool computed = fieldOf(e, 'computed').raw == 'true'
    // claude.md #71: `environment.NAME` and `environment[keyExpr]`.
    // Intercepted before the object is emitted at all, because
    // `environment` is not a value -- there is nothing to load, and
    // evaluating it would report an unknown name. The dot form's key
    // is a compile-time constant; the bracket form's is an ordinary
    // text expression, and both end at the same call.
    Node envObj = childOf(e, 'obj')
    if envObj != null {
        if envObj.kind == 'Identifier' {
            if rawText(envObj, 'name') == 'environment' {
                if cgSlotOf('environment') == '' {
                    return cgEnvironmentGet(e, computed)
                }
            }
        }
    }
    if computed == false {
        // A struct field still goes the long way round, because
        // cgFieldPtr is shared with the assignment path and emits the
        // object itself.
        if rawText(e, 'prop') != 'length' { return cgMemberRead(e) }
        // claude.md #262: `.length` read off a member CHAIN whose base
        // this expression owns -- `mkInner().b.length` -- takes a
        // different path in the original than an ordinary field read
        // does. The chain's owning bases are PARKED and released after
        // the length is taken, with nothing minted: the receiver type
        // (blob/text/ascii) is not the one whose value escapes, so
        // there is nothing for a mint to protect, and retaining the
        // field the way a field READ does would be a reference nobody
        // ever gives back.
        //
        // Refused rather than approximated. Going through the ordinary
        // member read emits a retain where the original emits the
        // length call itself, which is a differing line in a real
        // program -- and half a mechanism is worse here than none,
        // because the harness cannot tell a deliberate omission from a
        // wrong answer.
        Node lenObj = childOf(e, 'obj')
        if lenObj != null {
            if lenObj.kind == 'Member' {
                if fieldOf(lenObj, 'computed').raw != 'true' {
                    if cgIsOwningRefcountedSource(childOf(lenObj, 'obj')) {
                        cgUnported('.length off an owning member chain')
                        return none
                    }
                }
            }
        }
    }
    Val obj = cgExpr(childOf(e, 'obj'))
    if CG_STUCK { return none }
    if computed { return cgIndexRead(e, obj) }
    return cgLengthOf(e, obj)
}

// The one reader of the process environment. The result is NOT an
// owning text: festina_getenv hands back a pointer into the
// environment block rather than a fresh buffer, so a binding copies it
// and nothing ever frees what came back.
Val func cgEnvironmentGet(e:Node, computed:bool) {
    Val none
    text key = ''
    if computed {
        Val k = cgExprExpecting(childOf(e, 'prop'), 'text', '')
        if CG_STUCK { return none }
        key = k.v
    } else {
        key = cgStringConst(rawText(e, 'prop'))
    }
    text out = cgTmp()
    cgOut(`  ${out} = call ptr @festina_getenv(ptr ${key})`)
    return cgVal(out, 'ptr', 'text')
}

// Numbers and bools only. A text ternary owns its value inside each
// arm -- observed once, in one program -- and one observation of a
// two-branch construct is not enough to port from, so it reports
// unported rather than being generalized from a single sample.
Val func cgTernary(e:Node) {
    Val none
    Val c = cgExpr(childOf(e, 'test'))
    if CG_STUCK { return none }
    if c.fty != 'bool' {
        cgUnported(`ternary condition of type ${c.fty}`)
        return none
    }
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${c.v}, 0`)
    text thenL = cgLabel('tern.then')
    text elseL = cgLabel('tern.else')
    text endL = cgLabel('tern.end')
    cgOut(`  br i1 ${cond}, label %${thenL}, label %${elseL}`)

    // claude.md #173: a null branch takes its type from the OTHER one,
    // so when the CONSEQUENT is null the two arms are emitted in the
    // opposite order -- there is nothing to emit for a null until the
    // type is known. Observable whenever the non-null arm has effects.
    Node consN = childOf(e, 'cons')
    Node altN = childOf(e, 'alt')
    bool consNull = consN.kind == 'NullLit'
    bool altNull = altN.kind == 'NullLit'
    Val a
    Val b
    text thenPred = ''
    text elsePred = ''
    if consNull && altNull == false {
        cgBlockLabel(elseL)
        b = cgExpr(altN)
        if CG_STUCK { return none }
        b = cgOwnTernaryBranch(b, altN)
        elsePred = CG_BLOCK
        cgOut(`  br label %${endL}`)
        cgBlockLabel(thenL)
        a = cgExprExpecting(consN, b.fty, cgRelKeyVal(b))
        if CG_STUCK { return none }
        thenPred = CG_BLOCK
        cgOut(`  br label %${endL}`)
    } else {
        cgBlockLabel(thenL)
        a = cgExpr(consN)
        if CG_STUCK { return none }
        a = cgOwnTernaryBranch(a, consN)
        thenPred = CG_BLOCK
        cgOut(`  br label %${endL}`)
        cgBlockLabel(elseL)
        if altNull {
            b = cgExprExpecting(altN, a.fty, cgRelKeyVal(a))
        } else {
            b = cgExpr(altN)
            if CG_STUCK { return none }
            b = cgOwnTernaryBranch(b, altN)
        }
        if CG_STUCK { return none }
        elsePred = CG_BLOCK
        cgOut(`  br label %${endL}`)
    }
    cgBlockLabel(endL)
    Val shape = a
    if consNull { shape = b }
    if cgLtyOf(shape.fty) == '' {
        cgUnported(`ternary of type ${shape.fty}`)
        return none
    }
    text out = cgTmp()
    cgOut(`  ${out} = phi ${cgLtyOf(shape.fty)} [ ${a.v}, %${thenPred} ], [ ${b.v}, %${elsePred} ]`)
    if shape.fty == 'struct' { return cgStructVal(out, shape.sname) }
    if shape.fty == 'arr' { return cgArrVal(out, shape.ety) }
    if shape.fty == 'map' { return cgMapVal(out, shape.ety) }
    return cgVal(out, cgLtyOf(shape.fty), shape.fty)
}

// claude.md #173: a ternary ARM is normalized to something genuinely
// owned before the phi, rather than the whole ternary being treated as
// aliasing afterwards. The old rule was right only when BOTH arms were
// aliasing, and silently leaked the moment either was fresh: the
// caller copied or retained the result exactly once whichever arm
// ran, so a fresh arm's own correct ownership got an extra claim with
// nothing to balance it. Found by a sanitizer, not by inspection.
Val func cgOwnTernaryBranch(v:Val, src:Node) {
    if v.fty == 'text' {
        if cgOwnsText(src, v) == false {
            text owned = cgTmp()
            cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${v.v})`)
            Val o = cgVal(owned, 'ptr', 'text')
            o.fresh = true
            return o
        }
        return v
    }
    if cgIsRefcounted(v.fty) {
        if cgOwnsRefcounted(src, v) == false {
            cgOut(`  call void @festina_retain(ptr ${v.v})`)
        }
    }
    return v
}

// claude.md #114's implicit `.toText()`: what a non-text value becomes
// inside `${...}` or `log()`. int/float/bool only here --
// struct/arr/map render through a generated JSON walk, `blob` and
// `ascii` through their own runtime conversions, and img/aud/thread are
// compile errors in the original rather than conversions.
//
// A `text` value is returned UNCHANGED, and that is load-bearing: the
// caller distinguishes a piece it must free from one it must not by
// asking whether the source expression owns it, and a conversion would
// make every piece owned.
Val func cgToText(a:Val) {
    Val none
    if a.fty == 'text' { return a }
    text out = cgTmp()
    if a.fty == 'int' {
        cgOut(`  ${out} = call ptr @festina_str_from_int(i64 ${a.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    if a.fty == 'float' {
        cgOut(`  ${out} = call ptr @festina_str_from_float(double ${a.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    if a.fty == 'bool' {
        cgOut(`  ${out} = call ptr @festina_str_from_bool(i8 ${a.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    // claude.md #256: the characters, as a fresh text buffer -- always
    // total, since every ascii byte is a valid single-byte UTF-8 code
    // point. This is also what ascii.toText() compiles to, and the two
    // must not disagree.
    if a.fty == 'ascii' {
        cgOut(`  ${out} = call ptr @festina_ascii_to_text(ptr ${a.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    // claude.md #114: a struct or container renders as JSON, through
    // the walker generated for its own type. The builder is a runtime
    // value that owns its buffer until it is finished, which is what
    // makes the recursive append cheap: one allocation for the whole
    // rendering rather than one per piece.
    if a.fty == 'struct' || a.fty == 'arr' || a.fty == 'map' {
        text key = cgTypeKey(a.fty, cgRelKeyVal(a))
        text fn = cgJsonFn(key)
        text sb = cgTmp()
        cgOut(`  ${sb} = call ptr @festina_sb_new()`)
        cgOut(`  call void ${fn}(ptr ${a.v}, ptr ${sb}, i64 0)`)
        cgOut(`  ${out} = call ptr @festina_sb_finish(ptr ${sb})`)
        return cgVal(out, 'ptr', 'text')
    }
    // claude.md #115: a blob renders its CONTENTS, which is exactly
    // what its explicit toText() does -- the two must not disagree.
    if a.fty == 'blob' {
        cgOut(`  ${out} = call ptr @festina_blob_to_text(ptr ${a.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    cgUnported(`interpolation of ${a.fty}`)
    return none
}

// A template literal: alternating literal parts and interpolations,
// folded left to right with @festina_str_concat.
//
// Three details, all of them observable in the output:
//
//   1. **An EMPTY literal part emits no concat at all.** `` `${x}` ``
//      has an empty leading AND trailing part, and `` `${a}${b}` `` an
//      empty one between them; concatenating with "" allocates and
//      copies for nothing. Emitting those calls would double the
//      concat count for the commonest template shape.
//   2. **Every intermediate buffer is freed the moment the next
//      concat has copied out of it.** claude.md #83: concat mallocs a
//      fresh buffer and leaves both operands alone, so a chain leaks
//      every intermediate otherwise. `resultOwned`/`pieceOwned` track
//      whether the pointer in hand is a buffer THIS template allocated
//      -- a `@.str.N` constant or another binding's buffer must never
//      be freed here.
//   3. **The result is always a fresh buffer.** A bare `` `${name}` ``
//      concatenates nothing and would otherwise hand back `name`'s own
//      pointer, indistinguishable from it, so freeing either would
//      leave the other dangling. That case alone takes a
//      festina_text_own copy on the way out.
//
// `exprs` is never empty -- the parser builds a TemplateLit only once
// at least one `${...}` is seen, and a template with none is a plain
// StringLit -- so the loop always runs and `result` is always set.
//
// festina/codegen.py also releases an interpolated value the template
// OWNS (claude.md #192): `` `${make()}` `` renders a fresh container to
// text and then must release the container itself. That call is a
// no-op for int/float/bool/text, which is all cgToText accepts, so it
// has no port here yet -- and must arrive with container interpolation
// rather than after it.
Val func cgTemplate(e:Node) {
    Val none
    arr[Node] parts = listOf(e, 'parts')
    arr[Node] exprs = listOf(e, 'exprs')

    text result = ''
    bool resultOwned = false
    if parts.length > 0 {
        text head = rawText(parts[0], 'v')
        if head != '' { result = cgStringConst(head) }
    }

    int i = 0
    while i < exprs.length {
        Val a = cgExpr(exprs[i])
        if CG_STUCK { return none }
        bool pieceOwned = a.fty != 'text'
        if a.fty == 'text' {
            // The VALUE can own a buffer its own node does not -- a
            // text field read through an owning base was copied out
            // (claude.md #117) -- so the value is asked first.
            pieceOwned = a.fresh
            if pieceOwned == false { pieceOwned = cgOwnsText(exprs[i], a) }
        }
        Val piece = cgToText(a)
        if CG_STUCK { return none }

        if result == '' {
            result = piece.v
            resultOwned = pieceOwned
        } else {
            text out = cgTmp()
            cgOut(`  ${out} = call ptr @festina_str_concat(ptr ${result}, ptr ${piece.v})`)
            if resultOwned { cgOut(`  call void @free(ptr ${result})`) }
            if pieceOwned { cgOut(`  call void @free(ptr ${piece.v})`) }
            result = out
            resultOwned = true
        }

        text tail = ''
        if i + 1 < parts.length { tail = rawText(parts[i + 1], 'v') }
        if tail != '' {
            text ts = cgStringConst(tail)
            text out2 = cgTmp()
            cgOut(`  ${out2} = call ptr @festina_str_concat(ptr ${result}, ptr ${ts})`)
            if resultOwned { cgOut(`  call void @free(ptr ${result})`) }
            result = out2
            resultOwned = true
        }
        i++
    }

    if resultOwned == false {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${result})`)
        result = owned
    }
    return cgVal(result, 'ptr', 'text')
}

Val func cgUnary(e:Node) {
    Val none
    Val v = cgExpr(childOf(e, 'operand'))
    if CG_STUCK { return none }
    text op = rawText(e, 'op')

    if op == '-' {
        if v.fty == 'int' {
            text out = cgTmp()
            cgOut(`  ${out} = sub i64 0, ${v.v}`)
            return cgVal(out, 'i64', 'int')
        }
        if v.fty == 'float' {
            text out = cgTmp()
            cgOut(`  ${out} = fneg double ${v.v}`)
            return cgVal(out, 'double', 'float')
        }
        cgUnported(`unary - on ${v.fty}`)
        return none
    }

    if op == '!' {
        if v.fty != 'bool' {
            cgUnported(`unary ! on ${v.fty}`)
            return none
        }
        // The result temp is taken before the two intermediates, the
        // same inversion the comparison path has: LLVM's xor works on
        // a genuine i1, so the i8 the language uses for bool is a zext
        // of it rather than the thing itself.
        text out = cgTmp()
        text cond = cgTmp()
        text notted = cgTmp()
        cgOut(`  ${cond} = icmp ne i8 ${v.v}, 0`)
        cgOut(`  ${notted} = xor i1 ${cond}, 1`)
        cgOut(`  ${out} = zext i1 ${notted} to i8`)
        return cgVal(out, 'i8', 'bool')
    }

    cgUnported(`unary ${op}`)
    return none
}

Val func cgDivMod(op:text, lv:text, rv:text, asFloat:bool) {
    text ty = 'i64'
    text zero = '0'
    text sentinel = '-9223372036854775808'
    if asFloat {
        ty = 'double'
        zero = '0.0'
        // The float null sentinel is a quiet NaN (see the module
        // docstring in festina/codegen.py on null encodings).
        sentinel = '0x7FF8000000000000'
    }
    text isZero = cgTmp()
    if asFloat { cgOut(`  ${isZero} = fcmp oeq ${ty} ${rv}, ${zero}`) }
    else { cgOut(`  ${isZero} = icmp eq ${ty} ${rv}, ${zero}`) }
    text zeroL = cgLabel('divzero')
    text nonzeroL = cgLabel('divnonzero')
    text endL = cgLabel('divend')
    cgOut(`  br i1 ${isZero}, label %${zeroL}, label %${nonzeroL}`)
    cgBlockLabel(zeroL)
    text zeroPred = CG_BLOCK
    cgOut(`  br label %${endL}`)
    cgBlockLabel(nonzeroL)
    text res = cgTmp()
    text ins = 'srem'
    if op == '/' { ins = 'fdiv' }
    else {
        if asFloat { ins = 'frem' }
    }
    cgOut(`  ${res} = ${ins} ${ty} ${lv}, ${rv}`)
    text nonzeroPred = CG_BLOCK
    cgOut(`  br label %${endL}`)
    cgBlockLabel(endL)
    text out = cgTmp()
    cgOut(`  ${out} = phi ${ty} [ ${sentinel}, %${zeroPred} ], [ ${res}, %${nonzeroPred} ]`)
    if asFloat { return cgVal(out, 'double', 'float') }
    return cgVal(out, 'i64', 'int')
}

// `&&` and `||`, short-circuiting through real blocks and joining with
// a phi.
//
// The labels are allocated rhs, end, START -- in that order, not in the
// order they are emitted. Taking them in emission order would number
// every one of them differently and change nothing else, which is the
// kind of difference that looks like a mystery until you read the
// original's own sequence of label() calls.
Val func cgLogical(e:Node) {
    Val none
    Val l = cgExpr(childOf(e, 'left'))
    if CG_STUCK { return none }
    if l.fty != 'bool' {
        cgUnported(`logical operator on ${l.fty}`)
        return none
    }
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${l.v}, 0`)
    text rhsL = cgLabel('logic.rhs')
    text endL = cgLabel('logic.end')
    text startL = cgLabel('logic.start')
    cgOut(`  br label %${startL}`)
    cgBlockLabel(startL)
    if rawText(e, 'op') == '&&' {
        cgOut(`  br i1 ${cond}, label %${rhsL}, label %${endL}`)
    } else {
        cgOut(`  br i1 ${cond}, label %${endL}, label %${rhsL}`)
    }
    cgBlockLabel(rhsL)
    Val r = cgExpr(childOf(e, 'right'))
    if CG_STUCK { return none }
    if r.fty != 'bool' {
        cgUnported(`logical operator on ${r.fty}`)
        return none
    }
    text rhsPred = CG_BLOCK
    cgOut(`  br label %${endL}`)
    cgBlockLabel(endL)
    text out = cgTmp()
    // The left edge always leaves startL, which is why startL exists at
    // all -- evaluating the left operand may have opened blocks of its
    // own, but this branch is emitted from a block the generator
    // controls. The right edge is wherever the right operand finished.
    cgOut(`  ${out} = phi i8 [ ${l.v}, %${startL} ], [ ${r.v}, %${rhsPred} ]`)
    return cgVal(out, 'i8', 'bool')
}

// A call to a user-declared function. `wantValue` distinguishes an
// expression position from a bare statement, because a void call has no
// result temp to take.
// ---------------------------------------------------------------------
// Method calls.
//
// A call whose callee is a `Member` -- `x.f()`. Two unrelated things
// wear that shape: a METHOD on a value, whose receiver is emitted and
// handed to a runtime function, and a `Math.*` call, whose "receiver"
// is a namespace that is never emitted at all.
//
// The original distinguishes them by spelling: the identifier `Math`,
// with no check that it is unbound. A local named `Math` would still
// take the namespace path. Reproduced exactly rather than tidied --
// this port's job is to agree.

// claude.md #93: float -> float. LLVM has real intrinsics for most of
// these; the rest come straight from libm, which is on every link line
// already. Both are emitted identically, so the split is only about
// which name to use.
text func cgMathFloatFn(m:text) {
    if m == 'sqrt' { return 'llvm.sqrt.f64' }
    if m == 'sin' { return 'llvm.sin.f64' }
    if m == 'cos' { return 'llvm.cos.f64' }
    if m == 'exp' { return 'llvm.exp.f64' }
    if m == 'log' { return 'llvm.log.f64' }
    if m == 'log2' { return 'llvm.log2.f64' }
    if m == 'log10' { return 'llvm.log10.f64' }
    if m == 'abs' { return 'llvm.fabs.f64' }
    if m == 'tan' { return 'tan' }
    if m == 'asin' { return 'asin' }
    if m == 'acos' { return 'acos' }
    if m == 'atan' { return 'atan' }
    return ''
}

text func cgMathFloat2Fn(m:text) {
    if m == 'pow' { return 'llvm.pow.f64' }
    if m == 'min' { return 'llvm.minnum.f64' }
    if m == 'max' { return 'llvm.maxnum.f64' }
    if m == 'atan2' { return 'atan2' }
    return ''
}

text func cgMathIntrinsic(m:text) {
    if m == 'floor' { return 'llvm.floor.f64' }
    if m == 'ceil' { return 'llvm.ceil.f64' }
    if m == 'round' { return 'llvm.round.f64' }
    if m == 'trunc' { return 'llvm.trunc.f64' }
    return ''
}

// claude.md #102: a double to an i64 without the undefined behaviour a
// bare `fptosi` has. `fptosi` is genuinely undefined for a NaN, an
// infinity, or anything outside i64's range -- not "some unspecified
// integer" -- so the three cases are tested for and answered with the
// int null, the same claim claude.md #57 already makes for division by
// zero. The conversion itself uses the saturating intrinsic, which is
// fully defined, so no UB is left anywhere for the optimizer to fold
// two identical sites differently.
text func cgFloatToInt(v:text) {
    text isNan = cgTmp()
    cgOut(`  ${isNan} = fcmp uno double ${v}, ${v}`)
    text hi = cgTmp()
    cgOut(`  ${hi} = fcmp oge double ${v}, 0x43E0000000000000`)
    text lo = cgTmp()
    cgOut(`  ${lo} = fcmp olt double ${v}, 0xC3E0000000000000`)
    text oor = cgTmp()
    cgOut(`  ${oor} = or i1 ${hi}, ${lo}`)
    text bad = cgTmp()
    cgOut(`  ${bad} = or i1 ${isNan}, ${oor}`)
    text conv = cgTmp()
    cgOut(`  ${conv} = call i64 @llvm.fptosi.sat.i64.f64(double ${v})`)
    text out = cgTmp()
    cgOut(`  ${out} = select i1 ${bad}, i64 -9223372036854775808, i64 ${conv}`)
    return out
}

bool func cgIsMathMethod(m:text) {
    if cgMathFloatFn(m) != '' { return true }
    if cgMathFloat2Fn(m) != '' { return true }
    if cgMathIntrinsic(m) != '' { return true }
    return m == 'random' || m == 'floorDiv'
}

Val func cgMathCall(e:Node, m:text) {
    Val none
    arr[Node] args = listOf(e, 'args')
    if cgMathFloatFn(m) != '' {
        Val a = cgExpr(args[0])
        if CG_STUCK { return none }
        text out = cgTmp()
        cgOut(`  ${out} = call double @${cgMathFloatFn(m)}(double ${a.v})`)
        return cgVal(out, 'double', 'float')
    }
    if cgMathFloat2Fn(m) != '' {
        Val a = cgExpr(args[0])
        if CG_STUCK { return none }
        Val b = cgExpr(args[1])
        if CG_STUCK { return none }
        text out = cgTmp()
        cgOut(`  ${out} = call double @${cgMathFloat2Fn(m)}(double ${a.v}, double ${b.v})`)
        return cgVal(out, 'double', 'float')
    }
    // claude.md #188: Math.floorDiv(a, b) -- floor rather than
    // truncation, so a negative quotient rounds away from zero. It
    // shares claude.md #57's by-zero convention (null, through real
    // control flow) rather than inventing a second one: sdiv and srem
    // by zero are undefined at the hardware level, so a `select` alone
    // would not do.
    if m == 'floorDiv' {
        Val a = cgExpr(args[0])
        if CG_STUCK { return none }
        Val b = cgExpr(args[1])
        if CG_STUCK { return none }
        text isZero = cgTmp()
        cgOut(`  ${isZero} = icmp eq i64 ${b.v}, 0`)
        text zeroL = cgLabel('floordivzero')
        text nonzeroL = cgLabel('floordivnonzero')
        text endL = cgLabel('floordivend')
        cgOut(`  br i1 ${isZero}, label %${zeroL}, label %${nonzeroL}`)
        cgBlockLabel(zeroL)
        text zeroPred = CG_BLOCK
        cgOut(`  br label %${endL}`)
        cgBlockLabel(nonzeroL)
        text q = cgTmp()
        cgOut(`  ${q} = sdiv i64 ${a.v}, ${b.v}`)
        text r2 = cgTmp()
        cgOut(`  ${r2} = srem i64 ${a.v}, ${b.v}`)
        text rNonzero = cgTmp()
        cgOut(`  ${rNonzero} = icmp ne i64 ${r2}, 0`)
        text rNeg = cgTmp()
        cgOut(`  ${rNeg} = icmp slt i64 ${r2}, 0`)
        text bNeg = cgTmp()
        cgOut(`  ${bNeg} = icmp slt i64 ${b.v}, 0`)
        text differ = cgTmp()
        cgOut(`  ${differ} = xor i1 ${rNeg}, ${bNeg}`)
        text adjust = cgTmp()
        cgOut(`  ${adjust} = and i1 ${rNonzero}, ${differ}`)
        text lower = cgTmp()
        cgOut(`  ${lower} = sub i64 ${q}, 1`)
        text picked = cgTmp()
        cgOut(`  ${picked} = select i1 ${adjust}, i64 ${lower}, i64 ${q}`)
        text nonzeroPred = CG_BLOCK
        cgOut(`  br label %${endL}`)
        cgBlockLabel(endL)
        text out = cgTmp()
        cgOut(`  ${out} = phi i64 [ -9223372036854775808, %${zeroPred} ], [ ${picked}, %${nonzeroPred} ]`)
        return cgVal(out, 'i64', 'int')
    }
    if m == 'random' {
        text out = cgTmp()
        cgOut(`  ${out} = call double @festina_random()`)
        return cgVal(out, 'double', 'float')
    }
    if cgMathIntrinsic(m) != '' {
        Val a = cgExpr(args[0])
        if CG_STUCK { return none }
        if a.fty != 'float' {
            cgUnported(`Math.${m}() of ${a.fty}`)
            return none
        }
        text rounded = cgTmp()
        cgOut(`  ${rounded} = call double @${cgMathIntrinsic(m)}(double ${a.v})`)
        return cgVal(cgFloatToInt(rounded), 'i64', 'int')
    }
    cgUnported(`Math.${m}()`)
    return none
}

// A method on a VALUE. The receiver is emitted first and, when it is a
// text the expression itself allocated, freed once the call has read
// it -- exactly what `.length` already does for the same reason.
// One drawing or transform call against an image's own surface. Split
// out of cgMethodCall because the receiver is already emitted by the
// time this runs -- the same four drawing names are canvas builtins
// when called bare, and only the receiver's type tells the two apart.
//
// Every form is picked purely by ARGUMENT COUNT, exactly as the
// canvas-level ones are: a trailing `color` overrides the current fill
// for this call alone, and semantic analysis has already confirmed
// that exactly one form matches.
Val func cgImageMethod(e:Node, m:text, args:arr[Node], recv:Node, obj:Val) {
    Val none
    CG_USES_GRAPHICS_CODE = true
    if m == 'drawRect' || m == 'drawPixel' || m == 'drawCircle' {
        int plain = 4
        if m == 'drawCircle' { plain = 3 }
        if m == 'drawPixel' { plain = 2 }
        // cgSnakeOf already turns `drawRect` into `draw_rect`, so the
        // prefix stops at `festina_image_` -- spelling it
        // `festina_image_draw_` gives `festina_image_draw_draw_rect`.
        text fn = `festina_image_${cgSnakeOf(m)}`
        int extra = args.length - plain
        if extra == 1 { fn = fn + '_color' }
        else if extra == 2 { fn = fn + '_colors' }
        else if extra != 0 {
            cgUnported(`.${m}() with ${args.length} arguments`)
            return none
        }
        text joined = ''
        int i = 0
        while i < args.length {
            text want = 'int'
            // The trailing arguments past the plain form are colours,
            // which are i64-shaped too but resolve from a literal.
            if i >= plain { want = 'color' }
            Val av = cgExprExpecting(args[i], want, '')
            if CG_STUCK { return none }
            joined = joined + `, i64 ${av.v}`
            i++
        }
        cgOut(`  call void @${fn}(ptr ${obj.v}${joined})`)
        cgReleaseOwnedReceiver(recv, obj)
        return cgVal('0', 'void', 'void')
    }
    if m == 'drawText' {
        if args.length != 3 {
            cgUnported(`.drawText() with ${args.length} arguments`)
            return none
        }
        Val tv = cgExprExpecting(args[0], 'text', '')
        if CG_STUCK { return none }
        Val xv = cgExprExpecting(args[1], 'int', '')
        if CG_STUCK { return none }
        Val yv = cgExprExpecting(args[2], 'int', '')
        if CG_STUCK { return none }
        cgOut(`  call void @festina_image_draw_text(ptr ${obj.v}, ptr ${tv.v}, i64 ${xv.v}, i64 ${yv.v})`)
        cgFreeTextTemp(args[0], tv)
        cgReleaseOwnedReceiver(recv, obj)
        return cgVal('0', 'void', 'void')
    }
    // The table-driven ones. Each entry lists every arity it accepts,
    // because several of these name a different runtime function per
    // argument count rather than one function with optional trailing
    // arguments.
    arr[text] forms = CG_IMAGE_OPS[m].split(';')
    Node srcNode = null
    Val srcVal
    int fi = 0
    while fi < forms.length {
        arr[text] spec = forms[fi].split(':')
        if spec[0].toInt() == args.length {
            arr[text] ltys = []
            if spec[2] != '' { ltys = spec[2].split(',') }
            text joined2 = ''
            int q = 0
            while q < args.length {
                text want2 = 'int'
                if ltys[q] == 'double' { want2 = 'float' }
                if ltys[q] == 'ptr' { want2 = 'img' }
                Val av2 = cgExprExpecting(args[q], want2, '')
                if CG_STUCK { return none }
                joined2 = joined2 + `, ${ltys[q]} ${av2.v}`
                if ltys[q] == 'ptr' {
                    // Parked, not released here: an owning SOURCE is
                    // done with once PAINTED, and releasing it inside
                    // the argument loop would emit the free before the
                    // call that reads it.
                    srcNode = args[q]
                    srcVal = av2
                }
                q++
            }
            cgOut(`  call void @${spec[1]}(ptr ${obj.v}${joined2})`)
            if srcNode != null {
                // An owning source -- a blankImage() result passed
                // straight in -- gets the same release an owning
                // receiver does, and in the same place.
                cgReleaseOwnedReceiver(srcNode, srcVal)
            }
            cgReleaseOwnedReceiver(recv, obj)
            return cgVal('0', 'void', 'void')
        }
        fi++
    }
    cgUnported(`.${m}() with ${args.length} arguments`)
    return none
}

// drawRect -> draw_rect. Only the three drawing names go through this,
// so a single capital is all it ever has to find.
text func cgSnakeOf(m:text) {
    text out = ''
    int i = 0
    while i < m.length {
        int code = m.charCodeAt(i)
        if code >= 65 && code <= 90 {
            out = out + '_' + (code + 32).toChar()
        } else {
            out = out + code.toChar()
        }
        i++
    }
    return out
}

Val func cgMethodCall(e:Node, callee:Node) {
    Val none
    text m = rawText(callee, 'prop')
    Node recv = childOf(callee, 'obj')
    arr[Node] args = listOf(e, 'args')

    // claude.md #150: a LITERAL receiver is parsed at compile time, so
    // `'42'.toInt()` costs the compiled program nothing at all. The
    // fold and the runtime function must agree exactly; they do here
    // because `festina_text_to_int` is itself what evaluates this
    // `.toInt()` while the port is running.
    // claude.md #134/#234: a drawing or transform METHOD on an img.
    // The receiver decides: the same four names are canvas builtins
    // when called bare, so this only claims them once the receiver has
    // been emitted and turns out to be an image.
    if CG_IMAGE_OPS[m] != null || m == 'drawRect' || m == 'drawPixel'
            || m == 'drawCircle' || m == 'drawText' {
        Val imgRecv = cgExpr(recv)
        if CG_STUCK { return none }
        if imgRecv.fty == 'img' { return cgImageMethod(e, m, args, recv, imgRecv) }
        cgUnported(`.${m}() on ${imgRecv.fty}`)
        return none
    }
    if m == 'toInt' && args.length == 0 && recv.kind == 'StringLit' {
        return cgVal(`${rawText(recv, 'value').toInt()}`, 'i64', 'int')
    }

    // claude.md #96: push/pop/shift/unshift. The runtime moves elements
    // by BYTES with the element size passed in, so one set of helpers
    // covers every arr[T]; what happens HERE is the ownership half, and
    // it is the rule `xs[i] = v` already follows. Without it,
    // `xs.push(s)` would leave the array and `s` sharing one buffer and
    // whichever was freed first would leave the other dangling.
    //
    // Nothing is released on REMOVAL: pop and shift hand the element
    // back, so ownership transfers rather than ending.
    // claude.md #96/#130: `.splice(start, count)` removes a run and
    // hands it back as a fresh array; `.splice(start, count, insertArr)`
    // is JavaScript's splice(start, deleteCount, ...items), with the
    // variadic items spelled as one arr[T] because this language has no
    // variadic parameters.
    //
    // The removed elements are NOT released: they are handed to the
    // returned array, so ownership transfers rather than ending --
    // exactly what pop and shift do with the one element they remove.
    if m == 'splice' {
        Val obj = cgExpr(recv)
        if CG_STUCK { return none }
        if obj.fty != 'arr' {
            cgUnported(`.splice() on ${obj.fty}`)
            return none
        }
        if cgStorableRefcounted('arr', obj.ety) == false {
            cgUnported(`.splice() on an array of ${obj.ety}`)
            return none
        }
        if args.length != 2 && args.length != 3 {
            cgUnported(`.splice() with ${args.length} arguments`)
            return none
        }
        text spElemLty = cgElemLty(obj.ety)
        text spSize = '8'
        if obj.ety == 'bool' { spSize = '1' }
        Val spStart = cgExprExpecting(args[0], 'int', '')
        if CG_STUCK { return none }
        Val spCount = cgExprExpecting(args[1], 'int', '')
        if CG_STUCK { return none }
        text dst = cgFreshHeader('%struct._FestinaArray')
        if args.length == 2 {
            cgOut(`  call void @festina_array_splice(ptr ${obj.v}, ptr null, i64 ${spSize}, i64 ${spStart.v}, i64 ${spCount.v}, ptr ${dst})`)
            Val spr = cgArrVal(dst, obj.ety)
            spr.fresh = true
            return spr
        }
        Val ins = cgExprExpecting(args[2], 'arr', obj.ety)
        if CG_STUCK { return none }
        if ins.fty != 'arr' {
            cgUnported(`.splice() insert of type ${ins.fty}`)
            return none
        }
        text insLenP = cgTmp()
        cgOut(`  ${insLenP} = getelementptr %struct._FestinaArray, ptr ${ins.v}, i32 0, i32 0`)
        text insLen = cgTmp()
        cgOut(`  ${insLen} = load i64, ptr ${insLenP}`)
        text insDataP = cgTmp()
        cgOut(`  ${insDataP} = getelementptr %struct._FestinaArray, ptr ${ins.v}, i32 0, i32 1`)
        text insData = cgTmp()
        cgOut(`  ${insData} = load ptr, ptr ${insDataP}`)
        cgOut(`  call void @festina_array_splice_insert(ptr ${obj.v}, ptr null, i64 ${spSize}, i64 ${spStart.v}, i64 ${spCount.v}, ptr ${insData}, i64 ${insLen}, ptr ${dst})`)
        // The call may have realloc'd this array's own data buffer, so
        // its pointer is reloaded AFTER it rather than reused.
        text nowP = cgTmp()
        cgOut(`  ${nowP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 1`)
        text nowV = cgTmp()
        cgOut(`  ${nowV} = load ptr, ptr ${nowP}`)
        cgSpliceOwnRange(nowV, spElemLty, obj.ety, spStart.v, insLen)
        // The inserted array is read only for its raw BYTES and goes on
        // managing its own elements, so a receiver this expression owns
        // -- a literal, a call result -- is released here.
        if cgIsRefcounted('arr') {
            if cgIsOwningRefcountedSource(args[2]) {
                cgOut(`  call void ${cgReleaseFnFor('arr', obj.ety)}(ptr ${ins.v})`)
            }
        }
        Val spr2 = cgArrVal(dst, obj.ety)
        spr2.fresh = true
        return spr2
    }
    if m == 'push' || m == 'unshift' || m == 'pop' || m == 'shift' {
        Val obj = cgExpr(recv)
        if CG_STUCK { return none }
        if obj.fty != 'arr' {
            cgUnported(`.${m}() on ${obj.fty}`)
            return none
        }
        if cgStorableRefcounted('arr', obj.ety) == false {
            cgUnported(`.${m}() on an array of ${obj.ety}`)
            return none
        }
        text elemLty = cgElemLty(obj.ety)
        text elemSize = '8'
        if obj.ety == 'bool' { elemSize = '1' }
        if m == 'push' || m == 'unshift' {
            Val v = cgExprExpecting(args[0], obj.ety, '')
            if CG_STUCK { return none }
            if cgValIsElem(v, obj.ety) == false {
                cgUnported(`.${m}() of ${v.fty} onto an array of ${obj.ety}`)
                return none
            }
            text stored = v.v
            // The same rule `xs[i] = v` follows: a text element is
            // COPIED unless its source already owns a buffer, and a
            // refcounted one takes its own reference unless the source
            // already holds a fresh +1 nothing else references.
            if SF_NAMES[obj.ety] != null {
                if cgIsOwningRefcountedSource(args[0]) == false {
                    cgOut(`  call void @festina_retain(ptr ${stored})`)
                }
            } else if obj.ety == 'text' && cgOwnsText(args[0], v) == false {
                text o = cgTmp()
                cgOut(`  ${o} = call ptr @festina_text_own(ptr ${stored})`)
                stored = o
            }
            text slot = cgTmp()
            cgOut(`  ${slot} = alloca ${elemLty}`)
            cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)
            text fn = 'festina_array_push'
            if m == 'unshift' { fn = 'festina_array_unshift' }
            cgOut(`  call void @${fn}(ptr ${obj.v}, ptr null, i64 ${elemSize}, ptr ${slot})`)
            // JS hands back the new length, and reading it costs one
            // load -- emitted whether or not anything wants it.
            text lenP = cgTmp()
            cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 0`)
            text out = cgTmp()
            cgOut(`  ${out} = load i64, ptr ${lenP}`)
            return cgVal(out, 'i64', 'int')
        }
        text slot = cgTmp()
        cgOut(`  ${slot} = alloca ${elemLty}`)
        // Pre-seeded with this element type's own NULL rather than its
        // zero value: for an int, zero is a perfectly ordinary element,
        // and the runtime leaves the slot alone when there is nothing
        // to remove -- so an empty pop() would be indistinguishable
        // from popping a real 0.
        cgOut(`  store ${elemLty} ${cgNullValue(obj.ety)}, ptr ${slot}`)
        text fn = 'festina_array_pop'
        if m == 'shift' { fn = 'festina_array_shift' }
        cgOut(`  call i8 @${fn}(ptr ${obj.v}, ptr null, i64 ${elemSize}, ptr ${slot})`)
        text out = cgTmp()
        cgOut(`  ${out} = load ${elemLty}, ptr ${slot}`)
        if obj.ety == 'text' { return cgVal(out, 'ptr', 'text') }
        return cgVal(out, elemLty, obj.ety)
    }

    // claude.md #109/#272: a blob's byte-level readers. The receiver
    // is a handle that already holds the path, so these are single
    // runtime calls with no path to thread through.
    //
    // **`.slice()` emits its receiver TWICE**, and that is the
    // original's output rather than a slip here. `slice` is first
    // claimed by the `ascii` branch, which emits the receiver, finds it
    // is not an ascii, releases it if it owned one, and falls THROUGH
    // -- so the blob branch below emits it again from scratch. The
    // first value is simply unused. Reproducing it matters more than
    // tidying it: this port's job is to agree.
    // claude.md #109: a blob's path-shaped methods, all of one shape --
    // the receiver is a handle that already holds the path, so none of
    // them threads one through. `toText` belongs to this family too
    // but is not here: the conversion family claims the NAME first,
    // emits the receiver, finds a blob matches none of its cases and
    // falls through -- so a blob's toText emits its receiver twice,
    // exactly as `.slice()` does. Reproduced rather than tidied.
    if m == 'write' || m == 'append' || m == 'exists' || m == 'delete' {
        Val r = cgExpr(recv)
        if CG_STUCK { return none }
        if r.fty != 'blob' {
            cgUnported(`.${m}() on ${r.fty}`)
            return none
        }
        text fn = 'festina_blob_exists'
        text retIr = 'i8'
        text retF = 'bool'
        if m == 'write' { fn = 'festina_blob_write' }
        else if m == 'append' { fn = 'festina_blob_append' }
        else if m == 'delete' { fn = 'festina_blob_delete' }
        text out = cgTmp()
        if args.length > 0 {
            Val a = cgExpr(args[0])
            if CG_STUCK { return none }
            cgOut(`  ${out} = call ${retIr} @${fn}(ptr ${r.v}, ptr ${a.v})`)
            cgFreeTextTemp(args[0], a)
        } else {
            cgOut(`  ${out} = call ${retIr} @${fn}(ptr ${r.v})`)
        }
        // toText hands back an owned copy and the rest return scalars,
        // so nothing here points into the handle.
        cgReleaseOwnedReceiver(recv, r)
        if retF == 'text' { return cgVal(out, 'ptr', 'text') }
        return cgVal(out, 'i8', 'bool')
    }

    if m == 'byteAt' || m == 'slice' {
        if m == 'slice' {
            Val first = cgExpr(recv)
            if CG_STUCK { return none }
            // claude.md #256: clamped at both ends by the runtime, so
            // nothing here checks. Answered before the blob path below
            // rather than falling through it, because an ascii
            // receiver has already been emitted by this point.
            if first.fty == 'ascii' {
                Val sa = cgExprExpecting(args[0], 'int', '')
                if CG_STUCK { return none }
                Val sb = cgExprExpecting(args[1], 'int', '')
                if CG_STUCK { return none }
                text sout = cgTmp()
                cgOut(`  ${sout} = call ptr @festina_ascii_slice(ptr ${first.v}, i64 ${sa.v}, i64 ${sb.v})`)
                cgReleaseOwnedReceiver(recv, first)
                Val sres = cgVal(sout, 'ptr', 'ascii')
                sres.fresh = true
                return sres
            }
            cgReleaseOwnedReceiver(recv, first)
            cgFreeTextTemp(recv, first)
        }
        Val r = cgExpr(recv)
        if CG_STUCK { return none }
        if r.fty != 'blob' {
            cgUnported(`.${m}() on ${r.fty}`)
            return none
        }
        arr[text] avs = []
        int ai = 0
        while ai < args.length {
            Val a = cgExpr(args[ai])
            if CG_STUCK { return none }
            avs.push(a.v)
            ai++
        }
        text out = cgTmp()
        text rty = 'text'
        if m == 'byteAt' {
            cgOut(`  ${out} = call i64 @festina_blob_byte_at(ptr ${r.v}, i64 ${avs[0]})`)
            rty = 'int'
        } else {
            cgOut(`  ${out} = call ptr @festina_blob_slice(ptr ${r.v}, i64 ${avs[0]}, i64 ${avs[1]})`)
        }
        // byteAt answers a scalar and slice an owned copy -- neither
        // points into the handle, so the receiver is released exactly
        // as toText()'s is.
        cgReleaseOwnedReceiver(recv, r)
        if rty == 'int' { return cgVal(out, 'i64', 'int') }
        return cgVal(out, 'ptr', 'text')
    }

    // text.split(sep) -> arr[text], and its inverse arr.join(sep).
    // Both free the SEPARATOR when the expression allocated it, and
    // split frees its receiver too -- an array receiver is a binding
    // rather than a temporary, so join has nothing to free there.
    if m == 'split' && args.length == 1 {
        Val r = cgExpr(recv)
        if CG_STUCK { return none }
        if r.fty != 'text' {
            cgUnported(`.split() on ${r.fty}`)
            return none
        }
        Val sep = cgExpr(args[0])
        if CG_STUCK { return none }
        if sep.fty != 'text' && sep.fty != 'regex' {
            cgUnported(`.split() by ${sep.fty}`)
            return none
        }
        text out = cgTmp()
        // A regex separator is a different call with its arguments the
        // other way round -- the pattern first, the subject second --
        // and a different temporary to reclaim.
        if sep.fty == 'regex' {
            cgOut(`  ${out} = call ptr @festina_regex_split(ptr ${sep.v}, ptr ${r.v})`)
        } else {
            cgOut(`  ${out} = call ptr @festina_text_split(ptr ${r.v}, ptr ${sep.v})`)
        }
        cgFreeTextTemp(recv, r)
        cgFreeTextTemp(args[0], sep)
        cgFreeRegexTemp(args[0], sep)
        return cgArrVal(out, 'text')
    }
    if m == 'join' && args.length == 1 {
        Val r = cgExpr(recv)
        if CG_STUCK { return none }
        if r.fty != 'arr' {
            cgUnported(`.join() on ${r.fty}`)
            return none
        }
        if r.ety == '' {
            cgUnported('.join() on an array of a non-scalar type')
            return none
        }
        Val sep = cgExpr(args[0])
        if CG_STUCK { return none }
        text out = cgTmp()
        // claude.md #116: one runtime function, with the element KIND
        // riding along as a constant -- only the compiler knows an
        // arr[T]'s T, the same reason the JSON renderers are generated
        // per type.
        cgOut(`  ${out} = call ptr @festina_arr_join(ptr ${r.v}, ptr ${sep.v}, ptr ${cgStringConst(r.ety)})`)
        cgFreeTextTemp(args[0], sep)
        return cgVal(out, 'ptr', 'text')
    }

    // claude.md #159/#233: `text.toStruct(T)` / `text.toArr(T)`. The
    // whole call site is bracketed by cleanup-stack registrations,
    // because a parse can still throw at any point: a temporary
    // receiver text, the cursor, and -- once the builder has returned
    // it -- the finished value itself, which festina_json_expect_end
    // can still reject for trailing data. That last one is the one
    // that is easy to miss: by then the value is off the builder's own
    // frame and nothing else owns it yet.
    if m == 'toStruct' || m == 'toArr' {
        if args.length != 1 || args[0].kind != 'TypeArg' {
            cgUnported(`.${m}() without a type argument`)
            return none
        }
        Val rv = cgExpr(recv)
        if CG_STUCK { return none }
        if rv.fty != 'text' {
            cgUnported(`.${m}() on ${rv.fty}`)
            return none
        }
        Ty tt = resolveTypeField(args[0], 'type_expr')
        text tfty = cgFtyOfTy(tt)
        text tkey = cgTyKeyOf(tt)
        if tfty == '' || tfty == 'void' {
            cgUnported(`.${m}() into an unsupported type`)
            return none
        }
        bool owningRecv = cgOwnsText(recv, rv)
        if owningRecv {
            cgOut(`  call void @festina_cleanup_push(ptr ${rv.v}, ptr @free)`)
        }
        text cursor = cgTmp()
        cgOut(`  ${cursor} = call ptr @festina_json_cursor_new(ptr ${rv.v})`)
        cgOut(`  call void @festina_cleanup_push(ptr ${cursor}, ptr @festina_json_cursor_free)`)
        text builder = ''
        text resFty = ''
        text resKey = ''
        if m == 'toStruct' {
            if tfty != 'struct' {
                cgUnported(`.toStruct() into a ${tfty}`)
                return none
            }
            builder = cgFromJsonStructFn(tkey)
            resFty = 'struct'
            resKey = tkey
        } else {
            text ety = tfty
            if tfty == 'struct' { ety = tkey }
            else if tfty == 'arr' || tfty == 'map' { ety = cgTypeKey(tfty, tkey) }
            builder = cgFromJsonArrFn(ety)
            resFty = 'arr'
            resKey = ety
        }
        text jout = cgTmp()
        cgOut(`  ${jout} = call ptr ${builder}(ptr ${cursor})`)
        cgOut(`  call void @festina_cleanup_push(ptr ${jout}, ptr ${cgReleaseFnFor(resFty, resKey)})`)
        cgOut(`  call void @festina_json_expect_end(ptr ${cursor})`)
        cgOut('  call void @festina_cleanup_pop()')
        cgOut('  call void @festina_cleanup_pop()')
        if owningRecv { cgOut('  call void @festina_cleanup_pop()') }
        cgOut(`  call void @festina_json_cursor_free(ptr ${cursor})`)
        cgFreeTextTemp(recv, rv)
        if resFty == 'struct' { return cgStructVal(jout, resKey) }
        return cgArrVal(jout, resKey)
    }

    // claude.md #67/#68/#107: the regex trio. `pattern.test(value)`,
    // `value.match(pattern)` and `value.replace(search, replacement)`
    // -- note that test's receiver is the PATTERN and match's is the
    // text, which is the original's asymmetry rather than a slip.
    //
    // How many matches a replace touches is not decided here: a regex
    // carries its own `g` flag and the runtime reads it, while a text
    // search has no flags and replaces the first match only. There is
    // nothing left for codegen to pass, which is the point -- the old
    // constant argument could never have said "global" for a pattern
    // built at runtime.
    if m == 'test' && args.length == 1 {
        Val rx = cgExpr(recv)
        if CG_STUCK { return none }
        if rx.fty == 'regex' {
            Val sv = cgExpr(args[0])
            if CG_STUCK { return none }
            text tout = cgTmp()
            cgOut(`  ${tout} = call i8 @festina_regex_test(ptr ${rx.v}, ptr ${sv.v})`)
            cgFreeTextTemp(args[0], sv)
            cgFreeRegexTemp(recv, rx)
            return cgVal(tout, 'i8', 'bool')
        }
        cgUnported(`.test() on ${rx.fty}`)
        return none
    }
    if m == 'match' && args.length == 1 {
        Val sv = cgExpr(recv)
        if CG_STUCK { return none }
        if sv.fty == 'text' {
            Val rx = cgExpr(args[0])
            if CG_STUCK { return none }
            text mout = cgTmp()
            cgOut(`  ${mout} = call ptr @festina_regex_match(ptr ${rx.v}, ptr ${sv.v})`)
            cgFreeTextTemp(recv, sv)
            cgFreeRegexTemp(args[0], rx)
            return cgVal(mout, 'ptr', 'text')
        }
        cgUnported(`.match() on ${sv.fty}`)
        return none
    }
    if m == 'replace' && args.length == 2 {
        Val sv = cgExpr(recv)
        if CG_STUCK { return none }
        if sv.fty == 'text' {
            Val needle = cgExpr(args[0])
            if CG_STUCK { return none }
            Val repl = cgExpr(args[1])
            if CG_STUCK { return none }
            text rout2 = cgTmp()
            if needle.fty == 'regex' {
                cgOut(`  ${rout2} = call ptr @festina_regex_replace(ptr ${needle.v}, ptr ${sv.v}, ptr ${repl.v})`)
            } else {
                cgOut(`  ${rout2} = call ptr @festina_str_replace(ptr ${sv.v}, ptr ${needle.v}, ptr ${repl.v})`)
            }
            cgFreeTextTemp(recv, sv)
            cgFreeTextTemp(args[0], needle)
            cgFreeRegexTemp(args[0], needle)
            cgFreeTextTemp(args[1], repl)
            return cgVal(rout2, 'ptr', 'text')
        }
        cgUnported(`.replace() on ${sv.fty}`)
        return none
    }

    // claude.md #109: `.save()` and `.saveCopy()` on a handle. The
    // receiver already holds its own path, so the no-argument form
    // passes a NULL path and the runtime uses it -- which is why these
    // take a `ptr` either way rather than having two shapes.
    if m == 'save' || m == 'saveCopy' {
        Val h = cgExpr(recv)
        if CG_STUCK { return none }
        text base = ''
        if h.fty == 'blob' { base = 'festina_blob' }
        if base == '' {
            cgUnported(`.${m}() on ${h.fty}`)
            return none
        }
        text suffix = '_save'
        if m == 'saveCopy' { suffix = '_save_copy' }
        text pathV = 'null'
        Val pathArg
        if args.length > 0 {
            pathArg = cgExpr(args[0])
            if CG_STUCK { return none }
            pathV = pathArg.v
        }
        text sout = cgTmp()
        cgOut(`  ${sout} = call i8 @${base}${suffix}(ptr ${h.v}, ptr ${pathV})`)
        if args.length > 0 { cgFreeTextTemp(args[0], pathArg) }
        cgReleaseOwnedReceiver(recv, h)
        return cgVal(sout, 'i8', 'bool')
    }

    // claude.md #184: `.sort(cmp)` -- an in-place, stable sort whose
    // comparator is a first-class function value. Nothing here is
    // retained, copied or released: a function value is already a bare
    // pointer (claude.md #141) rather than a heap value, and the sort
    // itself only repositions each element's raw slot within the same
    // buffer. The runtime's comparator ABI takes two `void*` slots and
    // an opaque userdata, so a generated trampoline decodes the slots
    // into this element type and calls through -- and `userdata` IS
    // the callback pointer, not a payload to read one out of, because
    // festina_array_sort hands back unchanged whatever it was given.
    if m == 'sort' {
        if args.length != 1 {
            cgUnported('.sort() with other than one argument')
            return none
        }
        Val sobj = cgExpr(recv)
        if CG_STUCK { return none }
        if sobj.fty != 'arr' {
            cgUnported(`.sort() on ${sobj.fty}`)
            return none
        }
        if cgStorableRefcounted('arr', sobj.ety) == false {
            cgUnported(`.sort() on an array of ${sobj.ety}`)
            return none
        }
        Val cmp = cgExpr(args[0])
        if CG_STUCK { return none }
        if cmp.fty != 'func' {
            cgUnported(`.sort() with a comparator of type ${cmp.fty}`)
            return none
        }
        text tramp = cgSortTrampoline(sobj.ety)
        text ssize = '8'
        if sobj.ety == 'bool' { ssize = '1' }
        cgOut(`  call void @festina_array_sort(ptr ${sobj.v}, i64 ${ssize}, ptr ${tramp}, ptr ${cmp.v})`)
        return cgVal('0', 'void', 'void')
    }

    // claude.md #72: `m.forEach(fn)` visits every live entry as
    // (value, key). The runtime walks buckets it understands and knows
    // nothing about T, so a generated trampoline reinterprets the raw
    // i64 into this map's value type and calls the callback. Semantic
    // analysis has already established the argument is a declared
    // function of the right shape, so its own symbol is the callback.
    if m == 'forEach' {
        if args.length != 1 {
            cgUnported('.forEach() with other than one argument')
            return none
        }
        Val fobj = cgExpr(recv)
        if CG_STUCK { return none }
        if fobj.fty != 'map' {
            cgUnported(`.forEach() on ${fobj.fty}`)
            return none
        }
        if fobj.ety == '' || cgElemLty(fobj.ety) == '' {
            cgUnported('.forEach() on a map of a non-scalar type')
            return none
        }
        if args[0].kind != 'Identifier' {
            cgUnported('.forEach() with a non-identifier callback')
            return none
        }
        text cbName = `@${rawText(args[0], 'name')}`
        text ftramp = cgMapForEachTrampoline(fobj.ety, cbName)
        text fEntP = cgTmp()
        cgOut(`  ${fEntP} = getelementptr %struct._FestinaMap, ptr ${fobj.v}, i32 0, i32 1`)
        text fEnt = cgTmp()
        cgOut(`  ${fEnt} = load ptr, ptr ${fEntP}`)
        text fCapP = cgTmp()
        cgOut(`  ${fCapP} = getelementptr %struct._FestinaMap, ptr ${fobj.v}, i32 0, i32 2`)
        text fCap = cgTmp()
        cgOut(`  ${fCap} = load i64, ptr ${fCapP}`)
        cgOut(`  call void @festina_map_for_each(ptr ${fEnt}, i64 ${fCap}, ptr ${ftramp})`)
        return cgVal('0', 'void', 'void')
    }

    // claude.md #186: `m.keys()` answers an arr[text] and `m.values()`
    // an arr[T], both built by the runtime into a header this call
    // allocates. The receiver is NOT released, matching forEach's own
    // precedent: every call site is a plain named map, never a chained
    // call-result temporary.
    //
    // keys needs nothing but the entries and the capacity, because a
    // key is always text. values needs the element type spelled out as
    // three constants -- stride, "is refcounted", "is text" -- since
    // the runtime walks a map's buckets without knowing what is in
    // them, and only the compiler does.
    if m == 'keys' || m == 'values' {
        if args.length != 0 {
            cgUnported(`.${m}() with arguments`)
            return none
        }
        Val mv = cgExpr(recv)
        if CG_STUCK { return none }
        if mv.fty != 'map' {
            cgUnported(`.${m}() on ${mv.fty}`)
            return none
        }
        if mv.ety == '' || cgElemLty(mv.ety) == '' {
            cgUnported(`.${m}() on a map of a non-scalar type`)
            return none
        }
        text kEntP = cgTmp()
        cgOut(`  ${kEntP} = getelementptr %struct._FestinaMap, ptr ${mv.v}, i32 0, i32 1`)
        text kEnt = cgTmp()
        cgOut(`  ${kEnt} = load ptr, ptr ${kEntP}`)
        text kCapP = cgTmp()
        cgOut(`  ${kCapP} = getelementptr %struct._FestinaMap, ptr ${mv.v}, i32 0, i32 2`)
        text kCap = cgTmp()
        cgOut(`  ${kCap} = load i64, ptr ${kCapP}`)
        text dst = cgFreshHeader('%struct._FestinaArray')
        if m == 'keys' {
            cgOut(`  call void @festina_map_keys(ptr ${kEnt}, i64 ${kCap}, ptr ${dst})`)
            return cgArrVal(dst, 'text')
        }
        text vSize = '8'
        if mv.ety == 'bool' { vSize = '1' }
        text vRef = '0'
        if SF_NAMES[mv.ety] != null { vRef = '1' }
        text vText = '0'
        if mv.ety == 'text' { vText = '1' }
        cgOut(`  call void @festina_map_values(ptr ${kEnt}, i64 ${kCap}, i64 ${vSize}, i8 ${vRef}, i8 ${vText}, ptr ${dst})`)
        return cgArrVal(dst, mv.ety)
    }

    // Everything this slice does not implement is refused BEFORE the
    // receiver is emitted, so a refusal never leaves half an
    // expression behind for the next statement to trip over.
    bool known = false
    if m == 'toFloat' && args.length == 0 { known = true }
    if m == 'toInt' && args.length == 0 { known = true }
    if m == 'trim' && args.length == 0 { known = true }
    if m == 'toChar' && args.length == 0 { known = true }
    if m == 'toText' && args.length == 0 { known = true }
    if m == 'charCodeAt' && args.length == 1 { known = true }
    if m == 'toAscii' && args.length == 0 { known = true }
    if known == false {
        cgUnported(`method .${m}()`)
        return none
    }

    Val r = cgExpr(recv)
    if CG_STUCK { return none }

    if m == 'toFloat' {
        if r.fty != 'int' {
            cgUnported(`.toFloat() on ${r.fty}`)
            return none
        }
        text out = cgTmp()
        cgOut(`  ${out} = sitofp i64 ${r.v} to double`)
        return cgVal(out, 'double', 'float')
    }
    if m == 'toInt' {
        if r.fty != 'text' {
            cgUnported(`.toInt() on ${r.fty}`)
            return none
        }
        text out = cgTmp()
        cgOut(`  ${out} = call i64 @festina_text_to_int(ptr ${r.v})`)
        cgFreeTextTemp(recv, r)
        return cgVal(out, 'i64', 'int')
    }
    if m == 'trim' {
        if r.fty != 'text' {
            cgUnported(`.trim() on ${r.fty}`)
            return none
        }
        text out = cgTmp()
        cgOut(`  ${out} = call ptr @festina_text_trim(ptr ${r.v})`)
        cgFreeTextTemp(recv, r)
        return cgVal(out, 'ptr', 'text')
    }
    if m == 'toAscii' {
        if r.fty != 'text' {
            cgUnported(`.toAscii() on ${r.fty}`)
            return none
        }
        // claude.md #256: validating, and null for anything not
        // representable one byte per character. Unlike the COERCION of
        // a literal, this is always a real call -- the receiver is an
        // arbitrary expression whose bytes are not known here.
        text aout = cgTmp()
        cgOut(`  ${aout} = call ptr @festina_ascii_from_text(ptr ${r.v})`)
        cgFreeTextTemp(recv, r)
        Val ares = cgVal(aout, 'ptr', 'ascii')
        ares.fresh = true
        return ares
    }
    if m == 'charCodeAt' {
        // claude.md #258: on an ASCII this is emitted INLINE, with no
        // call at all. One byte per character means the byte at offset
        // i IS the code point, so the whole operation is a bounds check
        // and a load -- and a scan loop is the only place it is ever
        // hot, which is exactly where a call per character showed up in
        // the char_scan benchmark.
        if r.fty == 'ascii' {
            Val aidx = cgExpr(args[0])
            if CG_STUCK { return none }
            text acode = cgAsciiCharCodeAt(r.v, aidx.v)
            cgReleaseOwnedReceiver(recv, r)
            return cgVal(acode, 'i64', 'int')
        }
        if r.fty != 'text' {
            cgUnported(`.charCodeAt() on ${r.fty}`)
            return none
        }
        Val idx = cgExpr(args[0])
        if CG_STUCK { return none }
        text out = cgTmp()
        cgOut(`  ${out} = call i64 @festina_text_char_code_at(ptr ${r.v}, i64 ${idx.v})`)
        cgFreeTextTemp(recv, r)
        return cgVal(out, 'i64', 'int')
    }
    if m == 'toChar' {
        if r.fty != 'int' {
            cgUnported(`.toChar() on ${r.fty}`)
            return none
        }
        text out = cgTmp()
        cgOut(`  ${out} = call ptr @festina_int_to_char(i64 ${r.v})`)
        return cgVal(out, 'ptr', 'text')
    }
    // .toText() is the explicit spelling of exactly what a template
    // interpolation already does, so it shares cgToText rather than
    // having a copy that could drift from it.
    if r.fty == 'blob' {
        // The fall-through the original takes, receiver and all: this
        // second emission is the one the call actually uses.
        Val again = cgExpr(recv)
        if CG_STUCK { return none }
        text bt = cgTmp()
        cgOut(`  ${bt} = call ptr @festina_blob_to_text(ptr ${again.v})`)
        cgReleaseOwnedReceiver(recv, again)
        return cgVal(bt, 'ptr', 'text')
    }
    // A struct or container renders as JSON, through the same
    // cgToText a template interpolation uses -- one path rather than
    // two that could drift apart.
    if r.fty == 'struct' || r.fty == 'arr' || r.fty == 'map' {
        Val t = cgToText(r)
        if CG_STUCK { return none }
        cgReleaseOwnedReceiver(recv, r)
        return t
    }
    // claude.md #256: the characters, through that same path -- which
    // is what keeps `${a}`, `log(a)` and `a.toText()` from ever
    // disagreeing about what an ascii looks like.
    if r.fty == 'ascii' {
        Val at = cgToText(r)
        if CG_STUCK { return none }
        cgReleaseOwnedReceiver(recv, r)
        return at
    }
    if r.fty != 'int' && r.fty != 'float' && r.fty != 'bool' {
        cgUnported(`.toText() on ${r.fty}`)
        return none
    }
    return cgToText(r)
}

// claude.md #108: a call BORROWS every argument for its duration, so
// an argument the expression itself allocated is the caller's to
// reclaim once the call returns. An owning refcounted argument is
// released and an owning text one is freed -- sound because a callee
// that KEPT anything took its own reference on the way to wherever it
// stored it (an escaping parameter retains at binding, a global or
// field store retains, a returned alias is retained by the return
// path), so the caller's is provably the last one nothing else will
// drop.
// claude.md #236: the call-site half of throw unwinding. A call site
// owns whatever fresh argument values it built -- a literal, a
// template text, a call result, a handle a coercion minted -- and
// releases them right after the call returns. A callee that THROWS
// never returns here, so those temporaries were the one thing left
// leaking once every frame's locals were covered. Registered as
// (value, release) pairs for the duration of the call only; no slot is
// needed, which is the same shape the JSON builders use.
//
// The predicate is deliberately the same one cgFreeCallArgs uses:
// exactly what that function would free is exactly what a throw has to
// free instead, and two predicates that could drift apart would leak
// on one path or double-free on the other.
int func cgGuardCallArgs(args:arr[Node], vals:arr[Val]) {
    if CG_HAS_TRY == false { return 0 }
    int pushed = 0
    int i = 0
    while i < vals.length {
        Val a = vals[i]
        if cgIsRefcounted(a.fty) {
            if cgOwnsRefcounted(args[i], a) {
                cgOut(`  call void @festina_cleanup_push(ptr ${a.v}, ptr ${cgReleaseFnFor(a.fty, cgRelKeyVal(a))})`)
                pushed++
            }
        } else if a.fty == 'text' {
            if cgOwnsText(args[i], a) {
                cgOut(`  call void @festina_cleanup_push(ptr ${a.v}, ptr @free)`)
                pushed++
            }
        }
        i++
    }
    return pushed
}

void func cgUnguardCallArgs(pushed:int) {
    if pushed > 0 { cgOut(`  call void @festina_cleanup_pop_n(i64 ${pushed})`) }
}

void func cgFreeCallArgs(args:arr[Node], vals:arr[Val]) {
    int i = 0
    while i < vals.length {
        Val a = vals[i]
        if cgIsRefcounted(a.fty) {
            if cgIsOwningRefcountedSource(args[i]) || a.fresh {
                cgOut(`  call void ${cgReleaseFnFor(a.fty, cgRelKeyVal(a))}(ptr ${a.v})`)
            }
        } else {
            cgFreeTextTemp(args[i], a)
        }
        i++
    }
}

// claude.md #141: calling through a function VALUE. The pointer is
// loaded from the binding, every argument is emitted against the
// signature's own parameter type, and the call spells each argument's
// LLVM type explicitly -- an indirect callee carries none of that
// itself.
Val func cgIndirectCall(e:Node, name:text) {
    Val none
    text sig = cgEtyOf(name)
    if sig == '' {
        cgUnported(`call through ${name}`)
        return none
    }
    arr[text] ptys = cgSigParams(sig)
    arr[Node] args = listOf(e, 'args')
    if args.length != ptys.length {
        cgUnported(`call through ${name} with ${args.length} arguments`)
        return none
    }
    text fnPtr = cgTmp()
    cgOut(`  ${fnPtr} = load ptr, ptr ${cgSlotOf(name)}`)
    arr[text] parts = []
    arr[Val] argVals = []
    int i = 0
    while i < args.length {
        text pf = cgSigFty(ptys[i])
        Val a = cgExprExpecting(args[i], pf, cgSigKey(ptys[i]))
        if CG_STUCK { return none }
        parts.push(`${cgLtyOf(pf)} ${a.v}`)
        argVals.push(a)
        i++
    }
    text joined = ''
    int j = 0
    while j < parts.length {
        if j > 0 { joined = joined + ', ' }
        joined = joined + parts[j]
        j++
    }
    text rf = cgSigFty(cgSigRet(sig))
    int guarded = cgGuardCallArgs(args, argVals)
    if rf == 'void' {
        cgOut(`  call void ${fnPtr}(${joined})`)
        cgUnguardCallArgs(guarded)
        cgFreeCallArgs(args, argVals)
        return cgVal('', 'void', 'void')
    }
    text out = cgTmp()
    cgOut(`  ${out} = call ${cgLtyOf(rf)} ${fnPtr}(${joined})`)
    cgUnguardCallArgs(guarded)
    cgFreeCallArgs(args, argVals)
    text rkey = cgSigKey(cgSigRet(sig))
    if rf == 'struct' { return cgStructVal(out, rkey) }
    if rf == 'arr' { return cgArrVal(out, rkey) }
    if rf == 'map' { return cgMapVal(out, rkey) }
    return cgVal(out, cgLtyOf(rf), rf)
}

Val func cgCall(e:Node, wantValue:bool) {
    Val none
    Node callee = childOf(e, 'callee')
    if callee != null && callee.kind == 'Member' {
        if fieldOf(callee, 'computed').raw == 'true' {
            cgUnported('call through a computed member')
            return none
        }
        Node recv = childOf(callee, 'obj')
        text prop = rawText(callee, 'prop')
        if recv != null && recv.kind == 'Identifier' {
            // The namespace path is taken per METHOD NAME, not per
            // receiver: `Math.sqrt()` is the namespace even when a
            // variable called `Math` is in scope, while `Math.toText()`
            // is that variable's own method, because `toText` is in no
            // Math table. Both halves are the original's, quirk
            // included.
            if rawText(recv, 'name') == 'Math' && cgIsMathMethod(prop) {
                return cgMathCall(e, prop)
            }
        }
        return cgMethodCall(e, callee)
    }
    if callee == null || callee.kind != 'Identifier' {
        cgUnported('call through a non-identifier callee')
        return none
    }
    text name = rawText(callee, 'name')
    // claude.md #131: `close(code)` ends the program, running a
    // declared `on exit(code:int)` handler on the way out --
    // festina_program_exit does both, because the registered handler
    // is a runtime concern rather than something codegen calls here.
    // Checked before the user-function table so a program that happens
    // to declare its own `close` still gets the builtin, which is the
    // original's own order.
    // claude.md #116: the timers. setTimeout/setInterval take a
    // declared function's own symbol -- semantic analysis has already
    // established the shape -- and answer an id the clear pair takes
    // back. Only the two SCHEDULING calls make a program "use timers";
    // clearing alone schedules nothing, the same "only pay for what
    // you use" rule loadImage() follows.
    // claude.md #118: `regex(pattern, flags)` -- memoized per call
    // SITE rather than cached. The runtime remembers what this site
    // compiled last time, reuses it on a match and recompiles on a
    // mismatch, so a fixed pattern built from config costs what a
    // literal does and a genuinely varying one is never served a stale
    // automaton. Evicting the superseded compilation is safe only
    // because a regex is refcounted: a binding still aliasing the old
    // one keeps it alive.
    if name == 'regex' {
        arr[Node] rargs = listOf(e, 'args')
        if rargs.length == 0 {
            cgUnported('regex() with no pattern')
            return none
        }
        Val pv = cgExpr(rargs[0])
        if CG_STUCK { return none }
        if pv.fty != 'text' {
            cgUnported(`regex() pattern of type ${pv.fty}`)
            return none
        }
        text flagsV = ''
        Val fv
        if rargs.length > 1 {
            fv = cgExpr(rargs[1])
            if CG_STUCK { return none }
            if fv.fty != 'text' {
                cgUnported(`regex() flags of type ${fv.fty}`)
                return none
            }
            flagsV = fv.v
        } else {
            flagsV = cgStringConst('')
        }
        text memo = `@.regex.memo.${CG_REGEX_MEMOS}`
        CG_REGEX_MEMOS = CG_REGEX_MEMOS + 1
        CG_EXTRA.push(`${memo} = private global [3 x ptr] zeroinitializer`)
        text rout = cgTmp()
        cgOut(`  ${rout} = call ptr @festina_regex_compile_memo(ptr ${pv.v}, ptr ${flagsV}, ptr ${memo})`)
        // claude.md #83: the memo strdups whatever it keeps and
        // regcomp reads its argument inline, so neither pointer is
        // held past this call and a temporary passed for either is the
        // caller's to free.
        cgFreeTextTemp(rargs[0], pv)
        if rargs.length > 1 { cgFreeTextTemp(rargs[1], fv) }
        return cgVal(rout, 'ptr', 'regex')
    }
    // claude.md #93/#132: time, and the two filesystem calls that
    // stayed free functions when the rest moved onto `blob` itself.
    // Each frees any text temporary it was handed: none of these
    // runtime functions keeps a pointer past the call -- the file
    // helpers read or write and close, strftime copies into its own
    // buffer.
    // claude.md #94: a canvas operation is a name, a runtime function
    // and a fixed argument list. Every one of them paints or configures
    // the OFFSCREEN canvas, which needs no window -- see the table's
    // own comment for the three that do and are therefore not in it.
    if CG_CANVAS_OPS[name] != null {
        arr[text] spec = CG_CANVAS_OPS[name].split('|')
        arr[text] argLtys = []
        if spec[1] != '' { argLtys = spec[1].split(',') }
        arr[Node] cargs2 = listOf(e, 'args')
        if cargs2.length != argLtys.length {
            cgUnported(`${name}() with ${cargs2.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        text cjoined = ''
        int cq = 0
        while cq < cargs2.length {
            text want = 'int'
            if argLtys[cq] == 'double' { want = 'float' }
            Val cv = cgExprExpecting(cargs2[cq], want, '')
            if CG_STUCK { return none }
            if cv.lty != argLtys[cq] {
                cgUnported(`${name}() argument of type ${cv.fty}`)
                return none
            }
            if cq > 0 { cjoined = cjoined + ', ' }
            cjoined = cjoined + `${argLtys[cq]} ${cv.v}`
            cq++
        }
        cgOut(`  call void @${spec[0]}(${cjoined})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #89: the style setters record state and draw nothing,
    // so they open no window either. One argument is an already-packed
    // `color`; three are raw channels, for a colour chosen at runtime.
    if name == 'fillStyle' || name == 'borderColor' {
        arr[Node] sargs = listOf(e, 'args')
        if sargs.length != 1 && sargs.length != 3 {
            cgUnported(`${name}() with ${sargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        // claude.md #91: ONE argument is an already-packed `color`,
        // whether it came from a declaration's own literal or from
        // another colour-typed binding. Three are raw channels, for a
        // colour chosen at runtime.
        if sargs.length == 1 {
            text cfn = 'festina_set_fill_color'
            if name == 'borderColor' { cfn = 'festina_set_border_color' }
            Val cv2 = cgExprExpecting(sargs[0], 'color', '')
            if CG_STUCK { return none }
            cgOut(`  call void @${cfn}(i64 ${cv2.v})`)
            return cgVal('0', 'void', 'void')
        }
        text sfn = 'festina_set_fill_rgb'
        if name == 'borderColor' { sfn = 'festina_set_border_rgb' }
        arr[text] schan = []
        int sq = 0
        while sq < 3 {
            Val sv = cgExprExpecting(sargs[sq], 'int', '')
            if CG_STUCK { return none }
            if sv.fty != 'int' {
                cgUnported(`${name}() channel of type ${sv.fty}`)
                return none
            }
            schan.push(sv.v)
            sq++
        }
        cgOut(`  call void @${sfn}(i64 ${schan[0]}, i64 ${schan[1]}, i64 ${schan[2]})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #95: drawing paints the OFFSCREEN canvas too. Each of
    // these has more than one form, picked purely by argument count --
    // a trailing `color` overrides the current fillStyle for this call
    // alone -- and semantic analysis has already confirmed exactly one
    // form matches, so the count is a safe discriminator here.
    if name == 'drawRect' || name == 'drawCircle' || name == 'drawPixel' {
        arr[Node] dargs = listOf(e, 'args')
        int plain = 4
        if name == 'drawCircle' { plain = 3 }
        if name == 'drawPixel' { plain = 2 }
        if dargs.length != plain {
            // A colour override is a `color` value, which this port
            // does not have a type for yet.
            cgUnported(`${name}() with ${dargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        text dfn = 'festina_draw_rect'
        if name == 'drawCircle' { dfn = 'festina_draw_circle' }
        if name == 'drawPixel' { dfn = 'festina_draw_pixel' }
        text djoined = ''
        int dq = 0
        while dq < dargs.length {
            Val dv = cgExprExpecting(dargs[dq], 'int', '')
            if CG_STUCK { return none }
            if dv.fty != 'int' {
                cgUnported(`${name}() argument of type ${dv.fty}`)
                return none
            }
            if dq > 0 { djoined = djoined + ', ' }
            djoined = djoined + `i64 ${dv.v}`
            dq++
        }
        cgOut(`  call void @${dfn}(${djoined})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #185: three shapes, picked by argument count --
    // semantic analysis has already confirmed exactly one matches.
    if name == 'drawImage' {
        arr[Node] iargs = listOf(e, 'args')
        text ifn = ''
        if iargs.length == 3 { ifn = 'festina_draw_image' }
        else if iargs.length == 5 { ifn = 'festina_draw_image_scaled' }
        else if iargs.length == 9 { ifn = 'festina_draw_image_region' }
        else {
            cgUnported(`drawImage() with ${iargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val iv = cgExprExpecting(iargs[0], 'img', '')
        if CG_STUCK { return none }
        text ijoined = ''
        int iq = 1
        while iq < iargs.length {
            Val nv = cgExprExpecting(iargs[iq], 'int', '')
            if CG_STUCK { return none }
            ijoined = ijoined + `, i64 ${nv.v}`
            iq++
        }
        cgOut(`  call void @${ifn}(ptr ${iv.v}${ijoined})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #189: reads the canvas's own backing store directly,
    // so it needs no window either -- only Cairo.
    if name == 'getPixelColor' {
        arr[Node] gargs = listOf(e, 'args')
        if gargs.length != 2 {
            cgUnported(`getPixelColor() with ${gargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val gx = cgExprExpecting(gargs[0], 'int', '')
        if CG_STUCK { return none }
        Val gy = cgExprExpecting(gargs[1], 'int', '')
        if CG_STUCK { return none }
        text gout = cgTmp()
        cgOut(`  ${gout} = call i64 @festina_get_pixel_color(i64 ${gx.v}, i64 ${gy.v})`)
        return cgVal(gout, 'i64', 'color')
    }
    if name == 'drawText' {
        arr[Node] targs2 = listOf(e, 'args')
        if targs2.length != 3 {
            cgUnported(`drawText() with ${targs2.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val tv = cgExprExpecting(targs2[0], 'text', '')
        if CG_STUCK { return none }
        Val txv = cgExprExpecting(targs2[1], 'int', '')
        if CG_STUCK { return none }
        Val tyv = cgExprExpecting(targs2[2], 'int', '')
        if CG_STUCK { return none }
        cgOut(`  call void @festina_draw_text(ptr ${tv.v}, i64 ${txv.v}, i64 ${tyv.v})`)
        // Cairo copies the glyphs it draws and keeps no pointer.
        cgFreeTextTemp(targs2[0], tv)
        return cgVal('0', 'void', 'void')
    }
    if name == 'measureTextWidth' || name == 'measureTextHeight' {
        arr[Node] margs2 = listOf(e, 'args')
        if margs2.length != 1 {
            cgUnported(`${name}() with ${margs2.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val mv = cgExprExpecting(margs2[0], 'text', '')
        if CG_STUCK { return none }
        text mfn = 'festina_measure_text_width'
        if name == 'measureTextHeight' { mfn = 'festina_measure_text_height' }
        text mout = cgTmp()
        cgOut(`  ${mout} = call i64 @${mfn}(ptr ${mv.v})`)
        cgFreeTextTemp(margs2[0], mv)
        return cgVal(mout, 'i64', 'int')
    }
    if name == 'lineWidth' {
        arr[Node] wargs = listOf(e, 'args')
        if wargs.length != 1 {
            cgUnported(`lineWidth() with ${wargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val wv = cgExprExpecting(wargs[0], 'int', '')
        if CG_STUCK { return none }
        cgOut(`  call void @festina_set_line_width(i64 ${wv.v})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #95/#135: writes the OFFSCREEN canvas, so it needs no
    // window -- this is the headless case the render() split exists
    // for. The no-argument form answers a fresh img SNAPSHOT instead of
    // writing a file, which is a different return TYPE and so a
    // different port.
    if name == 'saveCanvas' {
        arr[Node] vargs = listOf(e, 'args')
        if vargs.length > 1 {
            cgUnported(`saveCanvas() with ${vargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        // claude.md #135: with no path it answers a fresh img SNAPSHOT
        // of the canvas instead of writing a file -- a different return
        // TYPE, which is why it is its own branch rather than an
        // optional argument on the one below.
        if vargs.length == 0 {
            text snap = cgTmp()
            cgOut(`  ${snap} = call ptr @festina_canvas_to_image()`)
            Val snapv = cgVal(snap, 'ptr', 'img')
            snapv.fresh = true
            return snapv
        }
        Val pv = cgExprExpecting(vargs[0], 'text', '')
        if CG_STUCK { return none }
        text sout = cgTmp()
        cgOut(`  ${sout} = call i8 @festina_save_canvas(ptr ${pv.v})`)
        // Cairo reads the path inline and keeps no pointer, so a
        // temporary is the caller's to free.
        cgFreeTextTemp(vargs[0], pv)
        return cgVal(sout, 'i8', 'bool')
    }
    // claude.md #188: blankImage(w, h) -> img. Shares loadImage's own
    // reasoning for setting only the CODE flag: creating a Cairo
    // surface needs no X server, unlike drawing onto a window.
    if name == 'blankImage' {
        arr[Node] bargs = listOf(e, 'args')
        if bargs.length != 2 {
            cgUnported(`blankImage() with ${bargs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val bw = cgExprExpecting(bargs[0], 'int', '')
        if CG_STUCK { return none }
        Val bh = cgExprExpecting(bargs[1], 'int', '')
        if CG_STUCK { return none }
        text bout = cgTmp()
        cgOut(`  ${bout} = call ptr @festina_blank_image(i64 ${bw.v}, i64 ${bh.v})`)
        Val bres = cgVal(bout, 'ptr', 'img')
        bres.fresh = true
        return bres
    }
    if name == 'loadImage' {
        arr[Node] largs = listOf(e, 'args')
        if largs.length != 1 {
            cgUnported(`loadImage() with ${largs.length} arguments`)
            return none
        }
        CG_USES_GRAPHICS_CODE = true
        Val lp = cgExprExpecting(largs[0], 'text', '')
        if CG_STUCK { return none }
        text lout = cgTmp()
        cgOut(`  ${lout} = call ptr @festina_load_image(ptr ${lp.v})`)
        // Cairo reads the PNG inline and keeps no pointer.
        cgFreeTextTemp(largs[0], lp)
        Val lres = cgVal(lout, 'ptr', 'img')
        lres.fresh = true
        return lres
    }
    if name == 'now' && listOf(e, 'args').length == 0 {
        text nout = cgTmp()
        cgOut(`  ${nout} = call i64 @festina_now_ms()`)
        return cgVal(nout, 'i64', 'int')
    }
    if name == 'formatTime' || name == 'mkdir' || name == 'ls' {
        arr[Node] fargs = listOf(e, 'args')
        arr[Val] fvals = []
        arr[text] fparts = []
        int fi = 0
        while fi < fargs.length {
            Val fa = cgExpr(fargs[fi])
            if CG_STUCK { return none }
            text fl = 'ptr'
            if fa.fty == 'int' { fl = 'i64' }
            fparts.push(`${fl} ${fa.v}`)
            fvals.push(fa)
            fi++
        }
        text fjoined = ''
        int fj = 0
        while fj < fparts.length {
            if fj > 0 { fjoined = fjoined + ', ' }
            fjoined = fjoined + fparts[fj]
            fj++
        }
        text ffn = 'festina_format_time'
        text fret = 'ptr'
        text fty2 = 'text'
        if name == 'mkdir' { ffn = 'festina_mkdir'  fret = 'i8'  fty2 = 'bool' }
        if name == 'ls' { ffn = 'festina_ls' }
        text fout = cgTmp()
        cgOut(`  ${fout} = call ${fret} @${ffn}(${fjoined})`)
        int fk = 0
        while fk < fvals.length {
            cgFreeTextTemp(fargs[fk], fvals[fk])
            fk++
        }
        if name == 'ls' { return cgArrVal(fout, 'text') }
        return cgVal(fout, fret, fty2)
    }
    if name == 'setTimeout' || name == 'setInterval' {
        arr[Node] targs = listOf(e, 'args')
        if targs.length != 2 || targs[0].kind != 'Identifier' {
            cgUnported(`${name}() with an unexpected shape`)
            return none
        }
        text cb = `@${rawText(targs[0], 'name')}`
        Val delay = cgExprExpecting(targs[1], 'int', '')
        if CG_STUCK { return none }
        CG_USES_TIMERS = true
        text tfn = 'festina_set_timeout'
        if name == 'setInterval' { tfn = 'festina_set_interval' }
        text tout = cgTmp()
        cgOut(`  ${tout} = call i64 @${tfn}(ptr ${cb}, i64 ${delay.v})`)
        return cgVal(tout, 'i64', 'int')
    }
    if name == 'clearTimeout' || name == 'clearInterval' {
        arr[Node] targs = listOf(e, 'args')
        if targs.length != 1 {
            cgUnported(`${name}() with other than one argument`)
            return none
        }
        Val id = cgExprExpecting(targs[0], 'int', '')
        if CG_STUCK { return none }
        text cfn = 'festina_clear_timeout'
        if name == 'clearInterval' { cfn = 'festina_clear_interval' }
        cgOut(`  call void @${cfn}(i64 ${id.v})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #32-34. This is the form whose result nobody catches:
    // the statement is prepared, its parameters bound, and run to
    // completion -- every INSERT/UPDATE/DELETE and a SELECT nobody
    // keeps. Collecting rows is a different emission reached from
    // cgExprExpecting instead, because only the DECLARED type of where
    // the result flows can say which of the two a call is.
    if name == 'sqlite' {
        if wantValue {
            cgUnported('a sqlite() result in a non-arr[Table] position')
            return none
        }
        text stmt = cgSqliteStmt(e)
        if CG_STUCK { return none }
        cgOut(`  call void @festina_sqlite_exec(ptr ${stmt})`)
        return cgVal('0', 'void', 'void')
    }
    if name == 'close' {
        arr[Node] cargs = listOf(e, 'args')
        if cargs.length != 1 {
            cgUnported('close() with other than one argument')
            return none
        }
        Val cv = cgExprExpecting(cargs[0], 'int', '')
        if CG_STUCK { return none }
        cgOut(`  call void @festina_program_exit(i64 ${cv.v})`)
        return cgVal('0', 'void', 'void')
    }
    // claude.md #141: an INDIRECT call, through a `func[...]`-typed
    // binding rather than a declared function's own name. Checked
    // BEFORE the function table below, mirroring the original's
    // dispatch order, so a local that shadows a real global function
    // of the same name resolves to ITS OWN signature rather than
    // silently falling through to a direct call against the global it
    // shadows.
    if cgFtyOf(name) == 'func' {
        if cgSlotOf(name) != '' { return cgIndirectCall(e, name) }
    }
    if FN_RET[name] == null {
        cgUnported(`call to ${name}`)
        return none
    }
    text retF = FN_RET[name]
    arr[Node] args = listOf(e, 'args')
    arr[text] ptys = []
    if FN_PARAMS[name] != null {
        if FN_PARAMS[name] != '' { ptys = FN_PARAMS[name].split('|') }
    }
    // FN_PARAMS deliberately spells a non-scalar parameter as '' (see
    // its own comment: a `null` argument needs only enough type to
    // pick a null constant, and every non-scalar's is the same
    // pointer). A LITERAL argument needs more than that -- an array
    // literal has to know its own element type -- so the full encoded
    // signature is consulted where there is one. The two agree
    // everywhere they overlap: for a ptr-shaped parameter, an untyped
    // null and a typed one are the same constant.
    arr[text] sigPtys = []
    if FN_SIG[name] != null {
        if FN_SIG[name] != '' { sigPtys = cgSigParams(FN_SIG[name]) }
    }
    arr[text] parts = []
    arr[Val] argVals = []
    int i = 0
    while i < args.length {
        text want = ''
        text wantKey = ''
        if i < ptys.length { want = ptys[i] }
        if want == '' {
            if i < sigPtys.length {
                want = cgSigFty(sigPtys[i])
                wantKey = cgSigKey(sigPtys[i])
            }
        }
        Val a = cgExprExpecting(args[i], want, wantKey)
        if CG_STUCK { return none }
        parts.push(`${a.lty} ${a.v}`)
        argVals.push(a)
        i++
    }
    text joined = ''
    int j = 0
    while j < parts.length {
        if j > 0 { joined = joined + ', ' }
        joined = joined + parts[j]
        j++
    }
    int guarded = cgGuardCallArgs(args, argVals)
    if retF == 'void' {
        cgOut(`  call void @${name}(${joined})`)
        cgUnguardCallArgs(guarded)
        cgFreeCallArgs(args, argVals)
        return cgVal('', 'void', 'void')
    }
    text lty = cgLtyOf(retF)
    if lty == '' { lty = 'ptr' }
    text t = cgTmp()
    cgOut(`  ${t} = call ${lty} @${name}(${joined})`)
    cgUnguardCallArgs(guarded)
    cgFreeCallArgs(args, argVals)
    if retF == 'struct' { return cgStructVal(t, FN_RETKEY[name]) }
    if retF == 'arr' { return cgArrVal(t, FN_RETKEY[name]) }
    if retF == 'map' { return cgMapVal(t, FN_RETKEY[name]) }
    return cgVal(t, lty, retF)
}

// ---------------------------------------------------------------------
// Statements.

void func cgLog(args:arr[Node]) {
    if args.length != 1 {
        cgUnported('log() with other than one argument')
        return
    }
    Val a = cgExpr(args[0])
    if CG_STUCK { return }
    if a.fty == 'int' {
        cgOut(`  call void @festina_log_int(i64 ${a.v})`)
        return
    }
    if a.fty == 'float' {
        cgOut(`  call void @festina_log_float(double ${a.v})`)
        return
    }
    if a.fty == 'bool' {
        cgOut(`  call void @festina_log_bool(i8 ${a.v})`)
        return
    }
    if a.fty == 'text' {
        cgOut(`  call void @festina_log_text(ptr ${a.v})`)
        cgFreeTextTemp(args[0], a)
        return
    }
    // claude.md #256: an ascii is logged as its characters, through
    // the same rendering path ascii.toText() uses -- so the two can
    // never disagree, exactly as for blob and the containers. The
    // rendering is a fresh buffer this call owns and frees.
    if a.fty == 'ascii' {
        Val rendered = cgToText(a)
        if CG_STUCK { return }
        cgOut(`  call void @festina_log_text(ptr ${rendered.v})`)
        cgOut(`  call void @free(ptr ${rendered.v})`)
        cgReleaseOwnedReceiver(args[0], a)
        return
    }
    cgUnported(`log(${a.fty})`)
}

void func cgStmt(s:Node) {
    // Pure type information: the definition was emitted with the
    // module's type section and nothing reaches main.
    if s.kind == 'StructDecl' { return }
    if s.kind == 'FuncDecl' { return }
    // A `table` is pure schema too, but unlike a struct it reaches the
    // program at RUN time rather than at type-check time: the columns
    // recorded in cgProgram become a festina_sync_table call in
    // main's own prologue, long before __festina_main starts. Nothing
    // is owed here either way.
    if s.kind == 'TableDecl' { return }
    // And the DatabaseURL directive, which cgProgram already lifted:
    // it is spent in main's prologue, so there is nothing left to run
    // where it was written.
    if s.kind == 'ExprStmt' {
        Node dbex = childOf(s, 'expr')
        if dbex != null {
            if dbex.kind == 'Assign' {
                Node dbtg = childOf(dbex, 'target')
                if dbtg != null {
                    if dbtg.kind == 'Identifier' {
                        if rawText(dbtg, 'name') == 'DatabaseURL' { return }
                    }
                }
            }
        }
    }

    if s.kind == 'VarDecl' {
        if fieldOf(s, 'is_const').raw == 'true' {
            cgUnported('const declaration')
            return
        }
        if fieldOf(s, 'manually_managed').raw == 'true' {
            cgUnported('manually-managed declaration')
            return
        }
        text fty = cgDeclFty(s)
        if fty == '' {
            Ty dt = resolveTypeField(s, 'type_expr')
            text managed = cgManagedFty(dt)
            if managed == '' {
                cgUnported('declaration of a non-scalar type')
                return
            }
            // A managed GLOBAL with no initializer is already fully
            // described by its header in the globals section, so there
            // is nothing for main to do. With one, the declaration is
            // an ASSIGNMENT into that header: the empty one the globals
            // section already built is released and the initializer's
            // value stored over it, exactly as a later `ns = [...]`
            // would do.
            text gname = rawText(s, 'name')
            // Local or global, on exactly the rule the scalar path
            // above uses: a declaration INSIDE a function is local
            // even when it shadows a global of the same name. Reading
            // the global table alone gets that wrong, and silently --
            // `arr[Tok] toks` inside `tokenize` would resolve to
            // lexdump.f's own top-level `toks` and never get a slot at
            // all, while every later read of the name went to the
            // global. Found by porting the drivers, where a local and
            // a top-level binding first share a name.
            bool isGlobalDecl = false
            if CG_IN_FUNC == false {
                if G_SLOT[gname] != null { isGlobalDecl = true }
            }
            if isGlobalDecl {
                Node ginit = childOf(s, 'init')
                if ginit == null { return }
                if cgStorableRefcounted(managed, cgEtyOf(gname)) == false {
                    cgUnported(`${managed} global of a non-scalar type`)
                    return
                }
                Val gv = cgExprExpecting(ginit, managed, cgEtyOf(gname))
                if CG_STUCK { return }
                if gv.fty != managed {
                    cgUnported(`initializer of type ${gv.fty} for ${managed}`)
                    return
                }
                bool gOwning = cgIsOwningRefcountedSource(ginit)
                if gv.fresh { gOwning = true }
                cgStoreRefcounted(G_SLOT[gname], managed, cgRelKeyOf(gname),
                                  gv.v, gOwning)
                return
            }

            // A blob is a HANDLE, so its local is one `alloca ptr` and
            // nothing else -- no frame storage to zero, no header to
            // calloc, and no stack-versus-heap decision to make: there
            // is no payload that could live in the frame.
            // A regex is the same shape for the same reason -- and so
            // is a ROW, whose storage the runtime laid out and whose
            // local is therefore one pointer to it and nothing more.
            if managed == 'blob' || managed == 'regex' || managed == 'table'
                    || managed == 'ascii' || managed == 'img' || managed == 'aud' {
                Node binit = childOf(s, 'init')
                if binit == null {
                    cgUnported(`${managed} declaration with no initializer`)
                    return
                }
                Val bv = cgExprExpecting(binit, managed, '')
                if CG_STUCK { return }
                if bv.fty != managed {
                    cgUnported(`initializer of type ${bv.fty} for ${managed}`)
                    return
                }
                text bslot = `%${gname}.${cgUid()}`
                cgOut(`  ${bslot} = alloca ptr`)
                bool bOwning = cgIsOwningRefcountedSource(binit)
                if bv.fresh { bOwning = true }
                // claude.md #256: an ascii DECLARATION never retains,
                // and that asymmetry is the original's rather than a
                // slip here. `ascii` is listed among the types a scope
                // exit releases but NOT among the ones the declaration
                // branch claims a reference for, so its local is bound
                // exactly like a scalar -- alloca, store, nothing else
                // -- while still being released on the way out.
                if managed == 'ascii' { bOwning = true }
                if bOwning == false {
                    cgOut(`  call void @festina_retain(ptr ${bv.v})`)
                }
                cgOut(`  store ptr ${bv.v}, ptr ${bslot}`)
                // A row's release can only be generated from its TABLE
                // name, so that is what travels with the tracking
                // entry -- the slot a struct binding uses for the same
                // purpose.
                text bkey = ''
                if managed == 'table' { bkey = dt.name }
                cgTrackLive(managed, bslot, bkey)
                L_SLOT[gname] = bslot
                L_FTY[gname] = managed
                if managed == 'table' { L_SNAME[gname] = dt.name }
                return
            }
            text declEty = ''
            if managed == 'struct' { declEty = dt.name }
            if managed == 'arr' || managed == 'map' {
                // An `arr[T]`/`map[T]` whose T is itself refcounted
                // releases every element through a generated per-element
                // cascade wrapper rather than the plain release. That is
                // its own mechanism; a scalar element type needs none of
                // it.
                declEty = cgEtyOfTy(dt)
                if declEty == '' {
                    cgUnported(`${managed} local of a non-scalar type`)
                    return
                }
                if cgStorableRefcounted(managed, declEty) == false {
                    cgUnported(`${managed} local of ${declEty}`)
                    return
                }
            }
            Node linit = childOf(s, 'init')

            // claude.md #74: THE decision escape analysis exists for.
            // A struct local no one can reach any other way lives in
            // the frame -- no refcount header, nothing to release. One
            // that escapes gets the ordinary heap header and is
            // released at scope exit like any other struct value.
            //
            // The two must be indistinguishable to a Festina program,
            // which is why the stack path stores an explicit
            // zeroinitializer: alloca does not zero and calloc does,
            // and "an unassigned field reads as its zero" is a language
            // rule, not an allocation detail.
            //
            // claude.md #81 narrows that for a container declared WITH
            // an initializer: frame storage only when the initializer
            // is a literal written right here, because only then is the
            // element count -- and so the buffer size -- known at the
            // declaration. Any other initializer (another binding, a
            // call result) aliases a value whose history this cannot
            // see, so it is always refcounted, escaping or not. The uid
            // is taken first either way, which is what keeps the
            // numbering identical across the three shapes.
            text payload = cgPayloadFor(dt)
            int uid = cgUid()
            text slot = `%${gname}.${uid}`
            text backing = `%${gname}.storage.${uid}`
            bool stackable = cgEscapes(gname) == false
            if linit != null {
                // claude.md #81 covers both container literals, and
                // only a literal: the entry count of a `{ ... }` is as
                // knowable at the declaration as an array's length.
                if managed == 'arr' {
                    if linit.kind != 'ArrayLit' { stackable = false }
                } else if managed == 'map' {
                    if linit.kind != 'MapLit' { stackable = false }
                } else {
                    stackable = false
                }
            }
            if linit != null && stackable {
                // The header is built straight into the frame slot:
                // zeroed first (an empty literal never writes a data
                // pointer of its own), then filled by the literal, and
                // only then published to the binding's own slot.
                cgOut(`  ${backing} = alloca ${payload}`)
                cgOut(`  store ${payload} zeroinitializer, ptr ${backing}`)
                if managed == 'arr' { cgArrayLit(linit, declEty, backing) }
                else { cgMapLit(linit, declEty, backing) }
                if CG_STUCK { return }
                cgOut(`  ${slot} = alloca ptr`)
                cgOut(`  store ptr ${backing}, ptr ${slot}`)
                cgTrackLive(`${managed}.stack`, slot, declEty)
            } else if linit != null {
                Val lv = cgExprExpecting(linit, managed, declEty)
                if CG_STUCK { return }
                if lv.fty != managed {
                    cgUnported(`initializer of type ${lv.fty} for ${managed}`)
                    return
                }
                bool lOwning = cgIsOwningRefcountedSource(linit)
                if lv.fresh { lOwning = true }
                if lOwning == false {
                    cgOut(`  call void @festina_retain(ptr ${lv.v})`)
                }
                cgOut(`  ${slot} = alloca ptr`)
                cgOut(`  store ptr ${lv.v}, ptr ${slot}`)
                cgTrackLive(managed, slot, declEty)
            } else if stackable {
                cgOut(`  ${backing} = alloca ${payload}`)
                cgOut(`  store ${payload} zeroinitializer, ptr ${backing}`)
                cgOut(`  ${slot} = alloca ptr`)
                cgOut(`  store ptr ${backing}, ptr ${slot}`)
                // A struct's frame storage owns nothing of its OWN --
                // but its fields can: a text field's buffer is heap
                // whatever the header's storage is, so it still has to
                // be reclaimed even though nothing is ever released
                // here. A container's frame storage owns its heap data
                // buffer for the same reason.
                if managed == 'struct' {
                    if cgStructOwnsAnything(declEty) {
                        cgTrackLive('struct.stack', slot, declEty)
                    }
                }
                if managed == 'arr' { cgTrackLive('arr.stack', slot, declEty) }
                if managed == 'map' { cgTrackLive('map.stack', slot, declEty) }
            } else {
                text made = cgFreshHeader(payload)
                cgOut(`  ${slot} = alloca ptr`)
                cgOut(`  store ptr ${made}, ptr ${slot}`)
                cgTrackLive(managed, slot, declEty)
            }
            L_SLOT[gname] = slot
            L_FTY[gname] = managed
            if managed == 'struct' { L_SNAME[gname] = dt.name }
            if managed == 'arr' || managed == 'map' {
                if dt.elem != null {
                    L_ETY[gname] = cgEtyOfTy(dt)
                }
            }
            return
        }
        text name = rawText(s, 'name')
        text lty = cgLtyOf(fty)

        // Local or global? Not "inside a function": a `for` loop's
        // own variable at the TOP level is a local in
        // __festina_main, while a top-level `int n = 5` is a global.
        // What separates them is whether cgProgram registered the
        // name as a global, which it does only for declarations
        // directly in the program body. Inside a function every
        // declaration is local, even one shadowing a global name.
        bool isLocalDecl = CG_IN_FUNC
        if G_SLOT[name] == null { isLocalDecl = true }

        bool freshLocal = false
        text localSlot = ''
        if isLocalDecl {
            localSlot = `%${name}.${cgUid()}`
            cgOut(`  ${localSlot} = alloca ${lty}`)
            if fty == 'text' {
                // claude.md #243's append shadow gets storage of its
                // own and is initialized empty. The allocas are hoisted
                // to the entry block; these stores are not, so they run
                // once per execution of the declaration -- which is
                // what makes a declaration inside a loop correct.
                cgOut(`  ${localSlot}.ap = alloca ptr`)
                cgOut(`  ${localSlot}.aplen = alloca i64`)
                cgOut(`  store ptr null, ptr ${localSlot}.ap`)
                cgOut(`  store i64 0, ptr ${localSlot}.aplen`)
                cgTrackLiveLate('text', localSlot, '')
            }
            freshLocal = true
        }
        Node init = childOf(s, 'init')
        if init == null {
            // No initializer: the declaration IS the whole statement,
            // so this is already "after the store" (there is none).
            if isLocalDecl { cgBindLocalDecl(s, name, fty, localSlot) }
            if fty == 'text' {
                if freshLocal { cgCleanupPush('text', cgSlotOf(name), '') }
            }
            return
        }
        Val v = cgExprExpecting(init, fty, '')
        if CG_STUCK { return }
        if v.fty != fty && cgNumericPair(v.fty, fty) == false {
            cgUnported(`initializer of type ${v.fty} for ${fty}`)
            return
        }
        // claude.md #298's mirror image: the name becomes visible only
        // now, AFTER its own initializer has been emitted, because an
        // initializer is resolved in the scope BEFORE this declaration.
        // Binding first only mattered for a name that genuinely
        // resolves to something else -- `func[int,int]:int cmp = cmp`
        // read the slot it was about to fill, stored that back into
        // itself, and then called it.
        if isLocalDecl { cgBindLocalDecl(s, name, fty, localSlot) }
        if fty == 'text' {
            // A fresh local's slot holds nothing yet, so there is no
            // old buffer to free and no stale append shadow to null --
            // only the owning copy and the store. A global's slot may
            // already hold a value from an earlier execution, which is
            // why it goes the long way round.
            if freshLocal {
                text owned = v.v
                if cgOwnsText(init, v) == false {
                    text o = cgTmp()
                    cgOut(`  ${o} = call ptr @festina_text_own(ptr ${owned})`)
                    owned = o
                }
                cgOut(`  store ptr ${owned}, ptr ${cgSlotOf(name)}`)
                cgCleanupPush('text', cgSlotOf(name), '')
                return
            }
            cgStoreText(cgSlotOf(name), `${cgSlotOf(name)}.ap`, v,
                        cgOwnsText(init, v))
            return
        }
        cgOut(`  store ${lty} ${v.v}, ptr ${cgSlotOf(name)}`)
        return
    }

    if s.kind == 'ExprStmt' {
        cgEvalForEffect(childOf(s, 'expr'))
        return
    }

    if s.kind == 'WhileStmt' { cgWhile(s)  return }
    if s.kind == 'ForStmt' { cgFor(s)  return }
    if s.kind == 'IfStmt' { cgIf(s)  return }
    if s.kind == 'Return' { cgReturn(s)  return }
    if s.kind == 'DeleteStmt' { cgDelete(s)  return }
    if s.kind == 'FreeStmt' { cgFree(s)  return }
    if s.kind == 'TryStmt' { cgTry(s)  return }
    if s.kind == 'ThrowStmt' { cgThrow(s)  return }
    if s.kind == 'BreakStmt' || s.kind == 'ContinueStmt' {
        if CG_LOOPS.length == 0 {
            // semantic.py rejects this outside a loop, so reaching it
            // would be a compiler bug rather than bad source.
            cgUnported(`${s.kind} outside a loop`)
            return
        }
        arr[text] target = CG_LOOPS[CG_LOOPS.length - 1].split('|')
        // claude.md #74: everything declared since this loop's body
        // began is freed BEFORE leaving, exactly as reaching the body's
        // natural end would. Continuing still exits this iteration's
        // own scopes, even though the loop itself carries on.
        cgFreeFrom(target[2].toInt())
        text to = target[0]
        if s.kind == 'BreakStmt' { to = target[1] }
        cgOut(`  br label %${to}`)
        CG_TERM = true
        return
    }

    cgUnported(`statement ${s.kind}`)
}

// An expression evaluated for its effect and not its value -- a bare
// statement, or a `for` loop's update clause, which is an expression
// in the grammar rather than a statement.
void func cgEvalForEffect(ex:Node) {
    if ex == null {
        cgUnported('empty expression statement')
        return
    }
    if ex.kind == 'Call' {
        Node callee = childOf(ex, 'callee')
        if callee != null && callee.kind == 'Identifier'
                && rawText(callee, 'name') == 'log' {
            cgLog(listOf(ex, 'args'))
            return
        }
        Val r = cgCall(ex, false)
        if CG_STUCK { return }
        // A discarded result this statement OWNS is provably the
        // value's only reference -- nothing else can hold a call's
        // fresh +1 yet -- so releasing it here frees it outright and is
        // correct rather than merely conservative. Only an owning
        // source reaches this: a bare identifier or field read as a
        // statement allocates nothing of its own.
        if cgIsRefcounted(r.fty) {
            if cgIsOwningRefcountedSource(ex) || r.fresh {
                cgOut(`  call void ${cgReleaseFnFor(r.fty, cgRelKeyVal(r))}(ptr ${r.v})`)
            }
        } else {
            cgFreeTextTemp(ex, r)
        }
        return
    }
    if ex.kind == 'Assign' {
        cgAssign(ex)
        return
    }
    if ex.kind == 'PostfixOp' {
        cgPostfix(ex)
        return
    }
    cgUnported(`expression statement ${ex.kind}`)
}

// Opens the database and brings every declared table's schema up to
// the source's. Nothing at all for a program with no `table`, which is
// what keeps a program that never queries free of SQLite entirely.
//
// The order the three string constants are interned in is observable,
// because they are numbered: each table's column NAMES first, then its
// column TYPES, and only then the table's own name -- which is the
// order the original's own call site evaluates them in, the arrays
// being built by a helper called before the name is asked for.
// Everything a sqlite() call does before its two forms diverge: the
// SQL evaluated, the connection loaded, the statement prepared, the
// caller's temporary freed, and the parameters bound.
text func cgSqliteStmt(e:Node) {
    arr[Node] qargs = listOf(e, 'args')
    if qargs.length == 0 || qargs.length > 2 {
        cgUnported('sqlite() with an unexpected argument count')
        return ''
    }
    CG_USES_SQLITE = true
    Val sql = cgExprExpecting(qargs[0], 'text', '')
    if CG_STUCK { return '' }
    text db = cgTmp()
    cgOut(`  ${db} = load ptr, ptr @__festina_db`)
    text stmt = cgSqlitePrepare(qargs[0], sql.v, db)
    // claude.md #83: prepare COMPILES the SQL rather than keeping the
    // string, so a template built for this call is the caller's to
    // free the moment the statement exists.
    cgFreeTextTemp(qargs[0], sql)
    if qargs.length == 2 {
        cgSqliteBind(qargs[1], stmt)
        if CG_STUCK { return '' }
    }
    return stmt
}

// The collecting form. The runtime hands back a count and a plain
// buffer of row pointers -- one 8-byte pointer per row, which is
// already exactly the layout an arr[T] data pointer expects when T is
// pointer-shaped -- so there is nothing to repack: a fresh header is
// built around the buffer as it stands.
//
// The result is FRESH in the strongest sense: nothing else references
// it yet, the same way an array literal's own header is fresh.
Val func cgSqliteCollect(e:Node, tname:text) {
    Val none
    text stmt = cgSqliteStmt(e)
    if CG_STUCK { return none }
    cgTableArrays(tname)
    int n = TBL_NCOLS[tname]
    text nSlot = cgTmp()
    cgOut(`  ${nSlot} = alloca i64`)
    text dataSlot = cgTmp()
    cgOut(`  ${dataSlot} = alloca ptr`)
    // claude.md #188: want_rowid is 1 here and only here -- a
    // table-shaped row carries a `.rowid` slot, a struct-query row
    // does not.
    cgOut(`  call void @festina_sqlite_collect_rows(ptr ${stmt}, i32 ${n}, ptr @${tname}.types, ptr @${tname}.cols, ptr ${nSlot}, ptr ${dataSlot}, i8 1)`)
    text nv = cgTmp()
    cgOut(`  ${nv} = load i64, ptr ${nSlot}`)
    text dv = cgTmp()
    cgOut(`  ${dv} = load ptr, ptr ${dataSlot}`)
    text header = cgFreshHeader('%struct._FestinaArray')
    text lenP = cgTmp()
    cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr ${header}, i32 0, i32 0`)
    cgOut(`  store i64 ${nv}, ptr ${lenP}`)
    text dataP = cgTmp()
    cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr ${header}, i32 0, i32 1`)
    cgOut(`  store ptr ${dv}, ptr ${dataP}`)
    Val r = cgArrVal(header, tname)
    r.fresh = true
    return r
}

// claude.md #113: a SQL string that cannot change is compiled into
// sqlite bytecode ONCE, through a private slot of this call site's
// own, exactly the way a regex literal's automaton is cached. Anything
// dynamic keeps the per-call prepare, because the same site can see
// different SQL each time.
text func cgSqlitePrepare(sqlNode:Node, sqlVal:text, dbVal:text) {
    text stmt = cgTmp()
    if sqlNode.kind == 'StringLit' {
        text slot = `@__festina_stmtcache_${cgUid()}`
        CG_EXTRA.push(`${slot} = private global ptr null`)
        cgOut(`  ${stmt} = call ptr @festina_sqlite_prepare_cached(ptr ${dbVal}, ptr ${sqlVal}, ptr ${slot})`)
        return stmt
    }
    cgOut(`  ${stmt} = call ptr @festina_sqlite_prepare(ptr ${dbVal}, ptr ${sqlVal})`)
    return stmt
}

// claude.md #33's own example binds [1, 'Patrick'] -- two types in one
// list, which no arr[T] value can hold. So the parameter list is call
// SYNTAX rather than an argument: a literal array whose elements are
// each bound by their own compile-time type, which sidesteps the
// conflict instead of loosening arr[T] itself.
void func cgSqliteBind(paramsNode:Node, stmt:text) {
    if paramsNode.kind != 'ArrayLit' {
        cgUnported('sqlite() parameters that are not a literal array')
        return
    }
    arr[Node] els = listOf(paramsNode, 'elements')
    int i = 0
    while i < els.length {
        // sqlite3_bind_* parameters are 1-indexed.
        int idx = i + 1
        if els[i].kind == 'NullLit' {
            cgOut(`  call void @festina_sqlite_bind_null(ptr ${stmt}, i32 ${idx})`)
            i++
            continue
        }
        Val pv = cgExpr(els[i])
        if CG_STUCK { return }
        if pv.fty == 'int' {
            cgOut(`  call void @festina_sqlite_bind_int(ptr ${stmt}, i32 ${idx}, i64 ${pv.v})`)
        } else if pv.fty == 'float' {
            cgOut(`  call void @festina_sqlite_bind_float(ptr ${stmt}, i32 ${idx}, double ${pv.v})`)
        } else if pv.fty == 'text' {
            cgOut(`  call void @festina_sqlite_bind_text(ptr ${stmt}, i32 ${idx}, ptr ${pv.v})`)
            // Bound with SQLITE_TRANSIENT, so sqlite has its own copy
            // by the time this returns and a temporary is both safe
            // and necessary to free right here.
            cgFreeTextTemp(els[i], pv)
        } else if pv.fty == 'bool' {
            // claude.md #30: bool is a SQLite INTEGER, same as int.
            text z = cgTmp()
            cgOut(`  ${z} = zext i8 ${pv.v} to i64`)
            cgOut(`  call void @festina_sqlite_bind_int(ptr ${stmt}, i32 ${idx}, i64 ${z})`)
        } else {
            cgUnported(`sqlite() parameter of type ${pv.fty}`)
            return
        }
        i++
    }
}

void func cgSyncTables() {
    if TBL_ORDER.length == 0 && CG_USES_SQLITE == false { return }
    // claude.md #70: the directive's own expression, evaluated HERE --
    // before festina_db_open, and so before __festina_main runs any
    // other global's initializer. Without one the default path is the
    // whole answer.
    text url = ''
    if CG_DB_URL != null {
        Val uv = cgExprExpecting(CG_DB_URL, 'text', '')
        if CG_STUCK { return }
        url = uv.v
    } else {
        url = cgStringConst('festina.sqlite')
    }
    cgOut(`  %db = call ptr @festina_db_open(ptr ${url})`)
    cgOut('  store ptr %db, ptr @__festina_db')
    int ti = 0
    while ti < TBL_ORDER.length {
        text tn = TBL_ORDER[ti]
        int n = TBL_NCOLS[tn]
        cgTableArrays(tn)
        text tnConst = cgStringConst(tn)
        cgOut(`  call void @festina_sync_table(ptr %db, ptr ${tnConst}, ptr @${tn}.cols, ptr @${tn}.types, i32 ${n})`)
        ti++
    }
}

// The two column globals a table needs, emitted ONCE however many
// places ask for them -- a second definition of the same LLVM global
// name would not link.
//
// Where they are emitted from is not fixed, and that is the point: a
// SELECT collected in the program's body asks first and interns the
// column strings there, leaving only the table's own name for the
// sync call in main's prologue to intern. A program that only declares
// a table interns everything in the prologue. Constants are numbered,
// so the difference is visible in the output and cannot be papered
// over by emitting them somewhere convenient.
void func cgTableArrays(tname:text) {
    if TBL_ARRAYS[tname] != null { return }
    TBL_ARRAYS[tname] = 1
    int n = TBL_NCOLS[tname]
    arr[text] cnames = TBL_COLS[tname].split('|')
    arr[text] ctypes = TBL_TYPES[tname].split('|')
    text namePtrs = ''
    text typePtrs = ''
    int ci = 0
    while ci < n {
        if ci > 0 { namePtrs = namePtrs + ', ' }
        namePtrs = namePtrs + `ptr ${cgStringConst(cnames[ci])}`
        ci++
    }
    int cj = 0
    while cj < n {
        if cj > 0 { typePtrs = typePtrs + ', ' }
        typePtrs = typePtrs + `ptr ${cgStringConst(ctypes[cj])}`
        cj++
    }
    CG_EXTRA.push(`@${tname}.cols = private constant [${n} x ptr] [${namePtrs}]`)
    CG_EXTRA.push(`@${tname}.types = private constant [${n} x ptr] [${typePtrs}]`)
}

void func cgPushFrame() {
    CG_FRAME.push(CG_LIVE.length)
}

// Makes a scalar local's name visible. Split out of the declaration
// path because WHEN it is called is the whole point: everything a
// declaration emits happens first, and only then does the name start
// resolving to this slot. See the caller's own comment.
void func cgBindLocalDecl(s:Node, name:text, fty:text, slot:text) {
    L_SLOT[name] = slot
    L_FTY[name] = fty
    // A func binding's signature rides in the element-type slot, the
    // same place a container's element type does.
    if fty == 'func' { L_ETY[name] = cgFuncSig(resolveTypeField(s, 'type_expr')) }
}

// Frees every live value from `downTo` onward. A `return` passes 0, so
// it unwinds every frame at once; a block's natural end passes its own
// base and unwinds just itself.
// A live entry is '<kind>|<slot>'. `text` is freed outright
// (claude.md #83: copy-on-alias, no refcount); a struct/arr[T]/map[T]
// binding holds a counted reference and is released instead.
// The element type travels with the entry, because the release a
// container gets depends on it: a container of a type that owns
// something needs a generated cascade rather than the generic call,
// and by scope-exit time the binding's own name is long gone.
void func cgTrackLive(kind:text, slot:text, ety:text) {
    cgTrackLiveLate(kind, slot, ety)
    cgCleanupPush(kind, slot, ety)
}

// The two halves, separately, for the one binding whose push is not
// emitted where its tracking is recorded. A text LOCAL's push comes
// after the initializer's own store, because the original tracks a
// local only once its whole declaration statement has been emitted --
// while a PARAMETER's comes before, its binding store being the last
// thing that happens. The difference is visible and neither order is
// derivable from the other, so both are spelled out.
void func cgTrackLiveLate(kind:text, slot:text, ety:text) {
    CG_LIVE.push(`${kind}|${slot}|${ety}`)
}

void func cgCleanupPush(kind:text, slot:text, ety:text) {
    // claude.md #236: when the program has a `try` to reach, every
    // binding is ALSO registered on the runtime's per-thread cleanup
    // stack as it is bound, paired with a generated function that
    // releases it THROUGH ITS SLOT. festina_throw then releases every
    // entry pushed since the catching frame -- this function's locals
    // since the try, and every intermediate frame's on the call chain,
    // which no generated code here could ever reach.
    //
    // The push happens after the slot holds its value, and the unwind
    // function reads the SLOT at throw time rather than the value at
    // binding time -- so a local reassigned before the throw releases
    // what it holds then, and one nulled by `free` releases nothing.
    //
    // A program with no `try` pays nothing: a throw there is fail(),
    // and the IR is unchanged.
    if CG_HAS_TRY {
        cgOut(`  call void @festina_cleanup_push(ptr ${slot}, ptr ${cgUnwindFn(kind, ety)})`)
    }
}

// claude.md #157: not a local at all -- a marker in the live list that
// pops the runtime's own catch-frame stack. Every exit from a try body
// (normal fallthrough, return, break, continue) reaches it through the
// same scope-exit walk every local does, which is what makes
// festina_try_pop need exactly one emission site.
void func cgTrackTryFrame() {
    CG_LIVE.push('try.frame||')
}

// The release of ONE live entry, through a slot -- shared by the
// ordinary scope-exit walk and by the per-kind unwind functions
// festina_throw calls from the runtime, so the two can never disagree
// about what releasing a binding means.
map[text] CG_UNWIND = {}

text func cgUnwindFn(kind:text, ety:text) {
    text key = `${kind}|${ety}`
    if CG_UNWIND[key] != null { return CG_UNWIND[key] }
    text name = `@__festina_unwind_${cgUid()}`
    CG_UNWIND[key] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %slot) {`)
    cgBlockLabel('entry')
    cgReleaseTracked(kind, '%slot', ety)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// Returns whether this entry was a real local -- a try-frame marker is
// not one, and the count of REAL releases is what the matching
// cleanup-stack pop is sized from.
bool func cgFreeOne(entry:text) {
    arr[text] parts = entry.split('|')
    if parts[0] == 'try.frame' {
        // claude.md #157: a throw must never pop the very catch frame
        // it may be about to unwind INTO -- festina_throw looks up and
        // pops exactly the frame it jumps to, at runtime. Popping it
        // here first, in code that always runs, would make a
        // perfectly-caught throw read as uncaught.
        if CG_SKIP_TRY_POP == false { cgOut('  call void @festina_try_pop()') }
        return false
    }
    cgReleaseTracked(parts[0], parts[1], parts[2])
    return true
}

// The release of ONE tracked binding, given its kind, its slot and the
// element/struct key that travels with it. Split out of cgFreeOne so a
// generated unwind function can run the IDENTICAL release through the
// same slot -- if the two ever disagreed about what releasing a
// binding means, a throw would free something differently from the way
// an ordinary scope exit does.
void func cgReleaseTracked(kind:text, slot:text, ety:text) {
    arr[text] parts = [kind, slot, ety]
    text t = cgTmp()
    cgOut(`  ${t} = load ptr, ptr ${slot}`)

    // claude.md #83: text is copied on alias and freed outright.
    if kind == 'text' {
        cgOut(`  call void @free(ptr ${t})`)
        return
    }
    // A heap-backed binding hands back its counted reference. Each
    // container type has its own release, because each knows a
    // different thing about what it owns.
    if cgIsRefcounted(kind) {
        cgOut(`  call void ${cgReleaseFnFor(kind, parts[2])}(ptr ${t})`)
        return
    }

    // A frame-allocated container still owns a HEAP data buffer: the
    // header lives in the frame, the elements never do. So there is no
    // reference to release and exactly one buffer to free.
    //
    // The array path loads the length it does not use. That is the
    // original's output, not an oversight of this port's -- the same
    // sequence releases each element first when the element type is
    // refcounted, and the load is hoisted above that branch.
    // claude.md #78: the storage is in the frame and is never freed,
    // and there is no refcount to drop -- but every field reference it
    // holds still has an owner going away.
    if kind == 'struct.stack' {
        cgReleaseStructFields(t, parts[2])
        return
    }
    if kind == 'arr.stack' {
        text lenP = cgTmp()
        cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr ${t}, i32 0, i32 0`)
        text lenV = cgTmp()
        cgOut(`  ${lenV} = load i64, ptr ${lenP}`)
        text dataP = cgTmp()
        cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr ${t}, i32 0, i32 1`)
        text dataV = cgTmp()
        cgOut(`  ${dataV} = load ptr, ptr ${dataP}`)
        // The header is in the frame and is never freed, but the
        // elements' own claims still have to be given back -- which is
        // what the length loaded just above is for. A scalar element
        // owns nothing, so for one the load really is unused
        // (decisions.md #301) and only the buffer is freed.
        if cgElemOwnsSomething(parts[2]) {
            cgReleaseArrayElements(dataV, lenV, cgElemReleaseFn(parts[2]), cgElemLty(parts[2]))
        }
        cgOut(`  call void @free(ptr ${dataV})`)
        return
    }
    if kind == 'map.stack' {
        text entP = cgTmp()
        cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr ${t}, i32 0, i32 1`)
        text entV = cgTmp()
        cgOut(`  ${entV} = load ptr, ptr ${entP}`)
        text nP = cgTmp()
        cgOut(`  ${nP} = getelementptr %struct._FestinaMap, ptr ${t}, i32 0, i32 2`)
        text nV = cgTmp()
        cgOut(`  ${nV} = load i64, ptr ${nP}`)
        // The header is in the frame, but the VALUES are not, and a
        // map's entries layout is opaque to codegen the way an array's
        // flat buffer is not -- there is nothing to walk. So the same
        // for_each-and-trampoline the generated cascade uses runs
        // inline here, before the entries buffer is freed. A scalar
        // value owns nothing and needs none of it.
        if cgElemOwnsSomething(parts[2]) {
            text tramp = cgMapReleaseTrampoline(parts[2])
            cgOut(`  call void @festina_map_for_each(ptr ${entV}, i64 ${nV}, ptr ${tramp})`)
        }
        cgOut(`  call void @festina_map_free_entries(ptr ${entV}, i64 ${nV})`)
        return
    }
}

// Frees every live value from the frame at `downTo` up to the
// innermost, INNERMOST FRAME FIRST -- and within each frame in
// declaration order.
//
// Both halves are measured rather than reasoned about, and they point
// opposite ways, which is exactly why guessing gets it wrong. Inside
// one frame the frees run in declaration order; ACROSS frames the
// innermost runs first, so an outer text local is freed after an inner
// struct one declared later than it. The original says the order never
// affects correctness -- each release is independent -- and that is
// true and beside the point: this port has to produce the same TEXT,
// and the two orders renumber every temp from the first divergence on.
void func cgFreeFrom(downTo:int) {
    // Frame boundaries at or above downTo, innermost last.
    arr[int] bounds = []
    int f = 0
    while f < CG_FRAME.length {
        if CG_FRAME[f] > downTo { bounds.push(CG_FRAME[f]) }
        f++
    }
    int popped = 0
    int hi = CG_LIVE.length
    int b = bounds.length - 1
    while b >= 0 {
        int lo = bounds[b]
        int i = lo
        while i < hi {
            if cgFreeOne(CG_LIVE[i]) { popped++ }
            i++
        }
        hi = lo
        b = b - 1
    }
    int i2 = downTo
    while i2 < hi {
        if cgFreeOne(CG_LIVE[i2]) { popped++ }
        i2++
    }
    // claude.md #236: these locals' own cleanup-stack entries go with
    // them. One count-based call for the whole walk: entries are pushed
    // in binding order and this walk releases frames newest-first, so
    // what it released is always the top of the runtime's stack.
    if popped > 0 {
        if CG_HAS_TRY {
            cgOut(`  call void @festina_cleanup_pop_n(i64 ${popped})`)
        }
    }
}

// Closes the innermost frame. No frees when the block already ended in
// a terminator: a `return` has freed everything itself, and emitting
// them again would be a double free.
void func cgPopFrame() {
    int base = CG_FRAME[CG_FRAME.length - 1]
    if CG_TERM == false { cgFreeFrom(base) }
    while CG_LIVE.length > base { CG_LIVE.pop() }
    CG_FRAME.pop()
}

// Runs a block's statements and leaves CG_TERM saying whether it ended
// in a terminator. Reset on entry so the answer is about THIS block and
// not one a sibling already closed.
void func cgBlockInto(b:Node) {
    CG_TERM = false
    cgPushFrame()
    arr[Node] body = cgBlockStmts(b)
    int i = 0
    while i < body.length {
        // Cleared per statement, not per file: see CG_STUCK's own
        // comment for why collapsing the two flags made the blocker
        // table lie three times over.
        CG_STUCK = false
        cgStmt(body[i])
        i++
    }
    cgPopFrame()
}

// An `if` always emits all three blocks, even with no `else` -- the
// else block then holds nothing but the branch to the end. Matching
// that matters: skipping it would renumber every label after it.
void func cgIf(s:Node) {
    Val c = cgExpr(childOf(s, 'test'))
    if CG_STUCK { return }
    text thenL = cgLabel('if.then')
    text elseL = cgLabel('if.else')
    text endL = cgLabel('if.end')
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${thenL}, label %${elseL}`)
    cgBlockLabel(thenL)
    cgBlockInto(childOf(s, 'then'))
    if CG_TERM == false { cgOut(`  br label %${endL}`) }
    cgBlockLabel(elseL)
    CG_TERM = false
    Node orelse = childOf(s, 'orelse')
    if orelse != null {
        if orelse.kind == 'Block' {
            cgBlockInto(orelse)
        } else {
            cgStmt(orelse)
        }
    }
    if CG_TERM == false { cgOut(`  br label %${endL}`) }
    cgBlockLabel(endL)
    // The end block itself falls through, so whatever follows the `if`
    // is reachable regardless of what the arms did.
    CG_TERM = false
}

// Labels in allocation order cond, body, update, end -- so the branch
// out of the condition names an `end` whose number is higher than the
// update block's.
void func cgFor(s:Node) {
    cgStmt(childOf(s, 'init'))
    if CG_STUCK { return }
    text condL = cgLabel('for.cond')
    text bodyL = cgLabel('for.body')
    text updateL = cgLabel('for.update')
    text endL = cgLabel('for.end')
    cgOut(`  br label %${condL}`)
    cgBlockLabel(condL)
    Val c = cgExpr(childOf(s, 'test'))
    if CG_STUCK { return }
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${bodyL}, label %${endL}`)
    cgBlockLabel(bodyL)
    CG_LOOPS.push(`${updateL}|${endL}|${CG_LIVE.length}`)
    cgBlockInto(childOf(s, 'body'))
    CG_LOOPS.pop()
    if CG_TERM == false { cgOut(`  br label %${updateL}`) }
    cgBlockLabel(updateL)
    cgEvalForEffect(childOf(s, 'update'))
    cgOut(`  br label %${condL}`)
    cgBlockLabel(endL)
    CG_TERM = false
}

bool func cgNumericPair(a:text, b:text) {
    if a == 'int' && b == 'float' { return true }
    if a == 'float' && b == 'int' { return true }
    return false
}

text func cgDeclFty(d:Node) {
    Ty t = resolveTypeField(d, 'type_expr')
    if t == null { return '' }
    // claude.md #141: a `func[...]` binding is scalar-shaped -- one
    // immortal pointer, never retained, tracked or released -- so it
    // belongs here with the primitives rather than with the managed
    // types, whatever its arity.
    if t.kind == 'func' {
        if cgFuncSig(t) == '' { return '' }
        return 'func'
    }
    if t.kind != 'prim' { return '' }
    if t.name == 'color' { return 'color' }
    if t.name == 'int' { return 'int' }
    if t.name == 'float' { return 'float' }
    if t.name == 'bool' { return 'bool' }
    if t.name == 'text' { return 'text' }
    return ''
}

// Storing into a `text` binding. claude.md #83: text is copied on
// alias and freed outright rather than refcounted, so a store owns a
// fresh buffer (festina_text_own) and frees whatever the slot held.
//
// The `.ap` shadow is claude.md #243's in-place append tracking: a
// plain store invalidates it, so it is nulled on the way through. The
// ordering is load-old, own-new, free-old, null-ap, store-new, and it
// is load-then-own-then-free rather than free-then-own because the new
// value may be derived from the old one.
void func cgStoreText(slot:text, apSlot:text, v:Val, owning:bool) {
    text old = cgTmp()
    cgOut(`  ${old} = load ptr, ptr ${slot}`)
    text val = v.v
    if owning == false {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${val})`)
        val = owned
    }
    cgOut(`  call void @free(ptr ${old})`)
    cgOut(`  store ptr null, ptr ${apSlot}`)
    cgOut(`  store ptr ${val}, ptr ${slot}`)
}

// ---------------------------------------------------------------------
// claude.md #243: in-place text append.
//
// `s = `${s}x`` and `s = s + x` used to compile like every other
// concatenation -- a fresh buffer of the combined length, both operands
// copied, the old one freed -- which is O(n^2) in bytes moved for a
// string built one piece at a time. Instead the assignment's own old
// value is handed to festina_text_append, which grows it in place. What
// makes that sound is the rule the whole text model rests on
// (claude.md #83): a text binding's buffer is exclusively its own, so
// consuming it inside the very assignment that was about to free it
// changes nothing anyone else can observe.
//
// The length is what the compiler carries. Every text slot has a
// {pointer, length} shadow beside it; an append writes the new length
// there and records which buffer it belongs to, and the next append
// trusts that length only if the binding STILL holds that exact
// pointer. Every other store clears the pointer shadow, so a remembered
// length can never outlive the buffer it described.

// Whether a piece is safe to evaluate in the middle of an append: it
// must not call user code (which could reassign the target underneath)
// and must not read the target again (whose buffer moves the moment it
// grows).
bool func cgAppendSimple(e:Node, targetName:text) {
    if e == null { return false }
    if e.kind == 'Identifier' { return rawText(e, 'name') != targetName }
    if e.kind == 'StringLit' { return true }
    if e.kind == 'NumberLit' { return true }
    if e.kind == 'BoolLit' { return true }
    if e.kind == 'Member' {
        if fieldOf(e, 'computed').raw == 'true' { return false }
        return cgAppendSimple(childOf(e, 'obj'), targetName)
    }
    return false
}

// The pieces to append, in order, when `target = v` is an append onto
// `target`. An EMPTY answer means "not an append" -- there is no shape
// this recognizes that would produce no pieces, so the two cases need
// no separate signal.
//
// A const piece is carried as parser.f's own '#str' marker node, the
// same way TemplateLit.parts already holds its literal halves, so one
// list can hold both kinds without a parallel array of tags.
arr[Node] func cgAppendPieces(targetName:text, v:Node) {
    arr[Node] no = []
    arr[Node] pieces = []
    if v == null { return no }

    if v.kind == 'TemplateLit' {
        arr[Node] parts = listOf(v, 'parts')
        arr[Node] exprs = listOf(v, 'exprs')
        if exprs.length == 0 { return no }
        if parts.length == 0 { return no }
        // The template must START with the target and nothing else:
        // `` `${s}x` `` appends, `` `a${s}` `` does not.
        if rawText(parts[0], 'v') != '' { return no }
        Node first = exprs[0]
        if first.kind != 'Identifier' { return no }
        if rawText(first, 'name') != targetName { return no }
        if parts.length > 1 {
            text p1 = rawText(parts[1], 'v')
            if p1 != '' { pieces.push(mkStr(p1)) }
        }
        int i = 1
        while i < exprs.length {
            if cgAppendSimple(exprs[i], targetName) == false { return no }
            pieces.push(exprs[i])
            if i + 1 < parts.length {
                text nx = rawText(parts[i + 1], 'v')
                if nx != '' { pieces.push(mkStr(nx)) }
            }
            i++
        }
        return pieces
    }

    if v.kind == 'BinOp' {
        if rawText(v, 'op') != '+' { return no }
        // `s + a + b` parses left-associatively, so the chain is
        // collected right to left and then walked back.
        arr[Node] chain = []
        Node cur = v
        while cur != null && cur.kind == 'BinOp' && rawText(cur, 'op') == '+' {
            chain.push(childOf(cur, 'right'))
            cur = childOf(cur, 'left')
        }
        if cur == null { return no }
        if cur.kind != 'Identifier' { return no }
        if rawText(cur, 'name') != targetName { return no }
        int k = chain.length - 1
        while k >= 0 {
            if cgAppendSimple(chain[k], targetName) == false { return no }
            pieces.push(chain[k])
            k--
        }
        return pieces
    }

    return no
}

void func cgEmitAppendAssign(slot:text, pieces:arr[Node]) {
    text ptrSlot = `${slot}.ap`
    text lenSlot = `${slot}.aplen`
    text old = cgTmp()
    cgOut(`  ${old} = load ptr, ptr ${slot}`)
    text remembered = cgTmp()
    cgOut(`  ${remembered} = load ptr, ptr ${ptrSlot}`)
    text rememberedLen = cgTmp()
    cgOut(`  ${rememberedLen} = load i64, ptr ${lenSlot}`)
    text same = cgTmp()
    cgOut(`  ${same} = icmp eq ptr ${old}, ${remembered}`)
    // -1 means "measure it": the runtime never trusts the length for
    // bounds -- capacity always comes from the allocator -- so a
    // remembered length that no longer applies can only ever
    // mis-position bytes inside the allocation, never outside it.
    // `knownLen`, not the obvious `known`: bootstrap/semantic.f
    // exports `bool func known(s:Scope, name:text)`, and a local that
    // shadows a function name misresolves inside a template -- the
    // same trap `at` set in cgFieldPtr, hit a second time in one
    // session. See todo.md.
    text knownLen = cgTmp()
    cgOut(`  ${knownLen} = select i1 ${same}, i64 ${rememberedLen}, i64 -1`)

    text cur = old
    text curLen = knownLen
    int i = 0
    while i < pieces.length {
        text pieceVal = ''
        bool owned = false
        if isStrType(pieces[i]) {
            pieceVal = cgStringConst(rawText(pieces[i], 'v'))
        } else {
            Val a = cgExpr(pieces[i])
            if CG_STUCK { return }
            owned = a.fty != 'text'
            if a.fty == 'text' { owned = cgOwnsText(pieces[i], a) }
            Val p = cgToText(a)
            if CG_STUCK { return }
            pieceVal = p.v
        }
        text grown = cgTmp()
        cgOut(`  ${grown} = call ptr @festina_text_append(ptr ${cur}, i64 ${curLen}, ptr ${pieceVal}, i64 -1, ptr ${lenSlot})`)
        if owned { cgOut(`  call void @free(ptr ${pieceVal})`) }
        text newLen = cgTmp()
        cgOut(`  ${newLen} = load i64, ptr ${lenSlot}`)
        cur = grown
        curLen = newLen
        i++
    }
    cgOut(`  store ptr ${cur}, ptr ${slot}`)
    cgOut(`  store ptr ${cur}, ptr ${ptrSlot}`)
}

// `xs[i] = v`: the slot, then the value, then the store. The slot is
// computed first because the index and the data pointer are part of the
// TARGET, and an index expression's own side effects run before the
// value's -- the same left-to-right rule every other assignment follows.
void func cgIndexAssign(e:Node, target:Node) {
    Val obj = cgExpr(childOf(target, 'obj'))
    if CG_STUCK { return }
    if obj.fty == 'map' {
        if obj.ety == '' {
            cgUnported('assignment into a map of a non-scalar type')
            return
        }
        // The key, then the value, and only then the header GEPs the
        // set itself needs -- the original resolves the target's own
        // parts first, so a key expression's side effects precede the
        // value's.
        MapKey k = cgMapKey(childOf(target, 'prop'))
        if CG_STUCK { return }
        Node mvalue = childOf(e, 'value')
        Val v = cgExprExpecting(mvalue, obj.ety, '')
        if CG_STUCK { return }
        if cgValIsElem(v, obj.ety) == false {
            cgUnported(`assigning ${v.fty} into a map of ${obj.ety}`)
            return
        }
        cgMapSet(obj.v, obj.ety, k.v, v.v, k.owned, cgMapValOwns(obj.ety, mvalue, v))
        return
    }
    if obj.fty != 'arr' {
        cgUnported(`assignment through an index on ${obj.fty}`)
        return
    }
    if obj.ety == '' {
        cgUnported('assignment into an array of a non-scalar type')
        return
    }
    Val idx = cgExpr(childOf(target, 'prop'))
    if CG_STUCK { return }
    if idx.fty != 'int' {
        cgUnported(`array index of type ${idx.fty}`)
        return
    }
    text elemLty = cgElemLty(obj.ety)
    text dataP = cgTmp()
    cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 1`)
    text dataV = cgTmp()
    cgOut(`  ${dataV} = load ptr, ptr ${dataP}`)
    text slot = cgTmp()
    cgOut(`  ${slot} = getelementptr ${elemLty}, ptr ${dataV}, i64 ${idx.v}`)
    Node valueNode = childOf(e, 'value')
    Val v = cgExprExpectingElem(valueNode, obj.ety)
    if CG_STUCK { return }
    if cgValIsElem(v, obj.ety) == false {
        cgUnported(`assigning ${v.fty} into an array of ${obj.ety}`)
        return
    }
    text stored = v.v
    if cgElemIsRefcounted(obj.ety) {
        // A refcounted element: the slot's old value is read, the new
        // one takes its reference, the store happens, and only THEN is
        // the old one released -- claude.md #120's store-before-release
        // again, so a cycle trial never finds the slot still pointing
        // at the value whose count it just dropped.
        text old = cgTmp()
        cgOut(`  ${old} = load ptr, ptr ${slot}`)
        bool eOwning = cgIsOwningRefcountedSource(valueNode)
        if v.fresh { eOwning = true }
        if eOwning == false {
            cgOut(`  call void @festina_retain(ptr ${stored})`)
        }
        cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)
        cgOut(`  call void ${cgElemReleaseFn(obj.ety)}(ptr ${old})`)
        return
    }
    if obj.ety == 'text' {
        // Unlike a literal's fresh buffer, this slot already holds
        // something, so the old value is reclaimed -- and read BEFORE
        // the copy is made, since `xs[i] = xs[i]` must not free the
        // buffer it is about to copy from.
        text old = cgTmp()
        cgOut(`  ${old} = load ptr, ptr ${slot}`)
        if cgOwnsText(valueNode, v) == false {
            text o = cgTmp()
            cgOut(`  ${o} = call ptr @festina_text_own(ptr ${stored})`)
            stored = o
        }
        cgOut(`  call void @free(ptr ${old})`)
    }
    cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)
}

void func cgAssign(e:Node) {
    Node target = childOf(e, 'target')
    if target != null && target.kind == 'Member' {
        if fieldOf(target, 'computed').raw == 'true' {
            cgIndexAssign(e, target)
            return
        }
        // The object and its GEP come first, then the value -- the
        // order festina/codegen.py's own _emit_assign uses, because it
        // resolves the target's type before emitting the value so an
        // array-literal right-hand side can pick its element type from
        // context.
        Val fp = cgFieldPtr(target)
        if CG_STUCK { return }
        if fp.fty == 'text' {
            // The slot already holds something, so the old buffer is
            // reclaimed -- and read BEFORE the copy is made, so
            // `p.name = p.name` cannot free what it is about to copy.
            // A plain free here, not festina_free_z: the zeroing form
            // is the SCOPE-EXIT rule (decisions.md #284), and an
            // overwrite is not a scope exit.
            // The VALUE first, then the old contents: the original
            // resolves the target's address, emits the value, and only
            // then reads what the slot held. Reading the old value
            // first gives identical behaviour and different temp
            // numbers from there on -- which is the whole diff.
            Node fvalue = childOf(e, 'value')
            Val fv = cgExprExpecting(fvalue, 'text', '')
            if CG_STUCK { return }
            text old = cgTmp()
            cgOut(`  ${old} = load ptr, ptr ${fp.v}`)
            text stored = fv.v
            if cgOwnsText(fvalue, fv) == false {
                text o = cgTmp()
                cgOut(`  ${o} = call ptr @festina_text_own(ptr ${stored})`)
                stored = o
            }
            cgOut(`  call void @free(ptr ${old})`)
            cgOut(`  store ptr ${stored}, ptr ${fp.v}`)
            return
        }
        if cgIsRefcounted(fp.fty) {
            Node rvalue = childOf(e, 'value')
            Val rv = cgExprExpecting(rvalue, fp.fty, fp.ety)
            if CG_STUCK { return }
            text old = cgTmp()
            cgOut(`  ${old} = load ptr, ptr ${fp.v}`)
            bool owning = cgIsOwningRefcountedSource(rvalue)
            if rv.fresh { owning = true }
            if owning == false {
                cgOut(`  call void @festina_retain(ptr ${rv.v})`)
            }
            // claude.md #120: the release of the overwritten value is
            // DEFERRED until after the store. A cycle trial run by that
            // release must never find the field still pointing at the
            // value whose count it has just dropped -- which is why
            // this one path stores first, where a plain binding
            // assignment releases first.
            cgOut(`  store ptr ${rv.v}, ptr ${fp.v}`)
            cgOut(`  call void ${cgReleaseFnFor(fp.fty, cgRelKeyVal(fp))}(ptr ${old})`)
            return
        }
        if fp.fty != 'int' && fp.fty != 'float' && fp.fty != 'bool' {
            cgUnported(`assignment to a ${fp.fty} field`)
            return
        }
        Val fv = cgExprExpecting(childOf(e, 'value'), fp.fty, '')
        if CG_STUCK { return }
        cgOut(`  store ${fp.lty} ${fv.v}, ptr ${fp.v}`)
        return
    }
    if target == null || target.kind != 'Identifier' {
        cgUnported('assignment to a non-identifier target')
        return
    }
    text name = rawText(target, 'name')
    text slot = cgSlotOf(name)
    text fty = cgFtyOf(name)
    if slot == '' || cgLtyOf(fty) == '' {
        cgUnported(`assignment to ${name}`)
        return
    }
    Node value = childOf(e, 'value')

    // Checked BEFORE the value is emitted: the append path consumes the
    // target's own old buffer instead of building a new one, so the
    // ordinary emission must not have happened yet.
    if fty == 'text' {
        arr[Node] pieces = cgAppendPieces(name, value)
        if pieces.length > 0 {
            cgEmitAppendAssign(slot, pieces)
            return
        }
    }

    Val v = cgExprExpecting(value, fty, cgEtyOf(name))
    if CG_STUCK { return }
    if fty == 'text' {
        cgStoreText(slot, `${slot}.ap`, v, cgOwnsText(value, v))
        return
    }
    if cgIsRefcounted(fty) {
        if cgStorableRefcounted(fty, cgEtyOf(name)) == false {
            cgUnported(`assignment to a ${fty} of a non-scalar type`)
            return
        }
        bool owning = cgIsOwningRefcountedSource(value)
        if v.fresh { owning = true }
        cgStoreRefcounted(slot, fty, cgRelKeyOf(name), v.v, owning)
        return
    }
    cgOut(`  store ${cgLtyOf(fty)} ${v.v}, ptr ${slot}`)
}

// claude.md #79/#80: a refcounted binding hands its old reference back
// and takes one on the new value. Retain BEFORE release, because the
// two can be the same object -- `g = g` releasing first would drop the
// last reference to the value it is about to store.
//
// An owning source already holds a fresh +1 that nothing else
// references, so it is stored directly; anything else (another
// binding, a field read) is shared and needs its own count.
//
// **A container whose elements own something is refused here**, not
// released with the generic call. `map[text]`'s release is a GENERATED
// per-type cascade (`@__festina_release_map_1`), not
// `@festina_release_map`, and the difference is invisible in the IR
// until the wrong one leaks every value in the table. A global of such
// a type was harmless while nothing could assign to it -- a global is
// never released -- and stopped being harmless the moment literals
// gave it an initializer.
//
// PURE, deliberately. Calling cgReleaseFnFor here instead would work
// and be wrong: generating a cascade takes a uid and a batch of temp
// numbers, so asking "could this be released?" would consume the names
// the original hands to whatever comes next. It cost an afternoon's
// worth of off-by-sixteen temp numbers to notice.
bool func cgStorableRefcounted(fty:text, ety:text) {
    if fty == 'struct' || fty == 'blob' || fty == 'regex' { return true }
    // A handle or a row: one pointer, and nothing to say about
    // elements, so there is no element type for the question below to
    // be about.
    if fty == 'ascii' || fty == 'table' || fty == 'img' || fty == 'aud' { return true }
    if ety == '' { return false }
    if ety == 'int' || ety == 'float' || ety == 'bool' { return true }
    return cgElemOwnsSomething(ety)
}

// The Val for one element read out of a container, given the element
// type. A nested container's element is itself a container, so it
// needs its own fty and its own element type rather than the scalar
// shape every other element read has.
Val func cgElemVal(v:text, ety:text) {
    if SF_NAMES[ety] != null { return cgStructVal(v, ety) }
    if TBL_COLS[ety] != null { return cgTableVal(v, ety) }
    if cgIsNestedElem(ety) {
        text kf = cgKeyFty(ety)
        if kf == 'arr' { return cgArrVal(v, cgKeyEty(ety)) }
        return cgMapVal(v, cgKeyEty(ety))
    }
    return cgVal(v, cgElemLty(ety), ety)
}

void func cgStoreRefcounted(slot:text, fty:text, ety:text, v:text, owning:bool) {
    text old = cgTmp()
    cgOut(`  ${old} = load ptr, ptr ${slot}`)
    if owning == false {
        cgOut(`  call void @festina_retain(ptr ${v})`)
    }
    cgOut(`  call void ${cgReleaseFnFor(fty, ety)}(ptr ${old})`)
    cgOut(`  store ptr ${v}, ptr ${slot}`)
}

// Whether an expression already hands back a buffer nothing else
// holds, so storing it needs no copy. A string literal is NOT one: it
// is a pointer into .rodata that every use of that literal shares.
// claude.md #79: whether an expression's value is a fresh reference
// nothing else holds yet, so a binding can take it without its own
// retain. A call result is -- every refcounted-value-returning path
// hands back a +1. Anything else (another binding, a field read) is
// shared.
// The release each refcounted kind gets. They are NOT
// interchangeable: an array's knows to reclaim its data buffer, a
// map's its entry table. Getting this wrong leaks the payload while
// looking perfectly correct, which is how it was caught -- a global
// arr[T] assignment released its old value with the struct one.
//
// And the generic one is only right when an ELEMENT has nothing of its
// own to give back. `@festina_release_array` frees the buffer and the
// header; it knows nothing about what the slots hold. A container of a
// type that owns something needs a cascade generated for it -- see
// cgReleaseArrayFn.
text func cgReleaseFn(fty:text) {
    if fty == 'arr' { return '@festina_release_array' }
    if fty == 'map' { return '@festina_release_map' }
    if fty == 'blob' { return '@festina_blob_release' }
    // claude.md #118: regfree on the last reference. A cached
    // /pattern/ literal is immortal and no-ops through here, which is
    // what lets `free` on a binding that aliases one be safe.
    if fty == 'regex' { return '@festina_regex_free' }
    // claude.md #256: frees at payload-16, the base of the
    // {length, refcount} header -- never at the payload the rest of
    // the program sees.
    if fty == 'ascii' { return '@festina_ascii_release' }
    // claude.md #118: destruction has real work to do for both -- an
    // img owns a Cairo surface, its bytes and its path; an aud stops
    // every channel still playing the clip before freeing its PCM.
    if fty == 'img' { return '@festina_image_free' }
    if fty == 'aud' { return '@festina_audio_free' }
    return '@festina_release'
}

// Whether a container of this element type can use the generic
// release. A scalar element owns nothing; a `text` one owns a buffer.
bool func cgIsRefcounted(fty:text) {
    if fty == 'regex' { return true }
    if fty == 'ascii' { return true }
    if fty == 'img' || fty == 'aud' { return true }
    // claude.md #265: a row is reference counted, so a row an array
    // gave out survives the array it came from.
    if fty == 'table' { return true }
    return fty == 'struct' || fty == 'arr' || fty == 'map' || fty == 'blob'
}

// Whether a container of this element type can use the generic
// release. A scalar element owns nothing; a `text` one owns a buffer;
// a STRUCT one owns a whole reference, whatever its own fields are.
bool func cgElemOwnsSomething(ety:text) {
    if ety == 'text' { return true }
    // A NESTED container element owns a whole reference, whatever it
    // holds -- an `arr[arr[int]]`'s slots are counted values even
    // though `int` owns nothing. The element type is spelled with the
    // same `arr:T` key #311 introduced, so a colon is what
    // distinguishes one from a scalar or a struct name.
    if cgIsNestedElem(ety) { return true }
    // A refcounted HANDLE element -- a blob, a regex -- owns a whole
    // reference exactly as a struct element does. It is retained on
    // store and has to be released here, and the plain container
    // release would drop every one of them on the floor: an
    // `arr[blob]` that escaped, was reassigned or was returned leaked
    // one open file handle per element, for as long as the array
    // lived.
    if cgIsRefcounted(ety) { return true }
    // A ROW element owns whatever its own text/blob columns hold, and
    // its allocation besides. Checked by name against the table
    // registry, since a table name and a struct name look alike here.
    if TBL_COLS[ety] != null { return true }
    return SF_NAMES[ety] != null
}

// Whether this element type is itself a container. The key spelling is
// the test: `arr:int`, `map:text`. A struct element is a bare name and
// a scalar is a bare word, so neither can be mistaken for one.
bool func cgIsNestedElem(ety:text) {
    if ety == '' { return false }
    return ety.split(':').length == 2
}

// The LLVM type of one element. Every non-scalar is a pointer to its
// own storage, so a struct element is a `ptr` whatever its layout.
// Whether an emitted value is of this element type. A struct value's
// own `fty` is the tag `struct` while an element type is the struct's
// NAME, so the two are never directly comparable.
bool func cgValIsElem(v:Val, ety:text) {
    if SF_NAMES[ety] != null { return v.fty == 'struct' && v.sname == ety }
    // A row's element type is its TABLE name, so the value's own fty
    // ('table') never equals it -- the name is in `sname`, exactly as
    // a struct's is.
    if TBL_COLS[ety] != null { return v.fty == 'table' && v.sname == ety }
    if cgIsNestedElem(ety) {
        return v.fty == cgKeyFty(ety) && v.ety == cgKeyEty(ety)
    }
    return v.fty == ety
}

// Emitting an expression in an ELEMENT position, where the type is
// spelled as an element type rather than as an (fty, key) pair. A
// nested container has to be split back into the two, because a
// literal in that position needs to know both which container it is
// building and what its own elements are.
Val func cgExprExpectingElem(e:Node, ety:text) {
    if cgIsNestedElem(ety) {
        return cgExprExpecting(e, cgKeyFty(ety), cgKeyEty(ety))
    }
    return cgExprExpecting(e, ety, '')
}

// Whether an element of this type holds a REFERENCE -- a struct or a
// nested container -- as opposed to a buffer (text) or nothing at all
// (a scalar). What separates them is which ownership question a store
// into the slot has to ask.
bool func cgElemIsRefcounted(ety:text) {
    if SF_NAMES[ety] != null { return true }
    if TBL_COLS[ety] != null { return true }
    if cgIsNestedElem(ety) { return true }
    // A handle element, on exactly the terms above: a store into one
    // of these slots is an alias and needs its own +1.
    return cgIsRefcounted(ety)
}

text func cgElemLty(ety:text) {
    if SF_NAMES[ety] != null { return 'ptr' }
    if TBL_COLS[ety] != null { return 'ptr' }
    if cgIsNestedElem(ety) { return 'ptr' }
    return cgLtyOf(ety)
}

text func cgReleaseFnFor(fty:text, ety:text) {
    // `ety` carries the struct NAME for a struct, and the element type
    // for a container -- one slot, because a value is never both.
    if fty == 'struct' && cgStructOwnsAnything(ety) { return cgReleaseStructFn(ety) }
    // claude.md #265: the same per-table wrapper an arr[Table]'s own
    // cascade uses. It is a release rather than an unconditional free,
    // which is what makes it safe to reach from an ordinary ownership
    // site and not only from a container tearing its elements down.
    if fty == 'table' { return cgTableRowReleaseFn(ety) }
    if fty == 'arr' && cgElemOwnsSomething(ety) { return cgReleaseArrayFn(ety) }
    if fty == 'map' && cgElemOwnsSomething(ety) { return cgReleaseMapFn(ety) }
    return cgReleaseFn(fty)
}

// The release of ONE element, given its type. A `text` is copied on
// alias and freed outright; a struct hands back its reference instead,
// through its own cascade -- the same shape with a different call.
text func cgElemReleaseFn(ety:text) {
    if cgIsNestedElem(ety) { return cgReleaseFnFor(cgKeyFty(ety), cgKeyEty(ety)) }
    if SF_NAMES[ety] != null { return cgReleaseFnFor('struct', ety) }
    if TBL_COLS[ety] != null { return cgTableRowReleaseFn(ety) }
    // A handle element gets its own destructor, never plain @free: a
    // blob owns its path and byte buffer, a regex its compiled
    // automaton, and neither is reachable from the pointer the slot
    // holds once that pointer is gone.
    if cgIsRefcounted(ety) { return cgReleaseFn(ety) }
    return '@free'
}

// A text operand beside an ascii one. Split out because the coercion
// has two completely different costs: a LITERAL is folded into an
// immortal .rodata constant with no call and no allocation, and
// anything else is a real validating conversion whose own temporary
// this expression then owns.
Val func cgTextToAscii(e:Node, v:Val) {
    if e != null {
        if e.kind == 'StringLit' {
            return cgVal(cgAsciiConst(rawText(e, 'value')), 'ptr', 'ascii')
        }
    }
    text out = cgTmp()
    cgOut(`  ${out} = call ptr @festina_ascii_from_text(ptr ${v.v})`)
    cgFreeTextTemp(e, v)
    Val made = cgVal(out, 'ptr', 'ascii')
    made.fresh = true
    return made
}

// claude.md #258: `s.charCodeAt(i)` on an ascii, emitted inline. There
// is no runtime function for it -- the one that used to exist was
// deleted, because this was its only caller and nothing about the
// operation needs a call.
//
// BRANCHLESS, deliberately: no new basic blocks, so the expression
// stays a straight-line value the surrounding emitter can keep treating
// as one, and so LLVM can hoist the loop-invariant length load without
// having to prove a guard. A null receiver is redirected to the empty
// literal -- whose one NUL byte makes even an empty ascii safe to load
// at 0 -- and the loaded byte is then discarded by the final select,
// which is also what answers null for an out-of-range index.
text func cgAsciiCharCodeAt(payload:text, idx:text) {
    text empty = cgAsciiConst('')
    text isNull = cgTmp()
    cgOut(`  ${isNull} = icmp eq ptr ${payload}, null`)
    text safeP = cgTmp()
    cgOut(`  ${safeP} = select i1 ${isNull}, ptr ${empty}, ptr ${payload}`)
    text lenP = cgTmp()
    cgOut(`  ${lenP} = getelementptr i8, ptr ${safeP}, i64 -16`)
    text len = cgTmp()
    cgOut(`  ${len} = load i64, ptr ${lenP}`)
    text low = cgTmp()
    cgOut(`  ${low} = icmp slt i64 ${idx}, 0`)
    text high = cgTmp()
    cgOut(`  ${high} = icmp sge i64 ${idx}, ${len}`)
    text oor = cgTmp()
    cgOut(`  ${oor} = or i1 ${low}, ${high}`)
    text safeI = cgTmp()
    cgOut(`  ${safeI} = select i1 ${oor}, i64 0, i64 ${idx}`)
    text byteP = cgTmp()
    cgOut(`  ${byteP} = getelementptr i8, ptr ${safeP}, i64 ${safeI}`)
    text byte = cgTmp()
    cgOut(`  ${byte} = load i8, ptr ${byteP}`)
    text code = cgTmp()
    cgOut(`  ${code} = zext i8 ${byte} to i64`)
    text out = cgTmp()
    cgOut(`  ${out} = select i1 ${oor}, i64 ${cgNullValue('int')}, i64 ${code}`)
    return out
}

// claude.md #130: `.splice(start, count, insertArr)` copies raw element
// BYTES out of a SEPARATE array's buffer -- a plain memcpy with no
// notion of a Festina type. Unlike push, whose single value has one
// source expression to ask about, there is no source expression here:
// the source is a whole array, read for its bytes, which goes on
// managing its own elements independently of what this array now does
// with the copies.
//
// So the newly-written range always takes its own reference,
// unconditionally and with no freshness check possible: a refcounted
// element is retained in place, a text one is REPLACED by a fresh copy
// (text has no shared representation to retain), and anything else
// needs nothing -- the bytes the runtime copied are already a complete
// independent value.
void func cgSpliceOwnRange(dataV:text, elemLty:text, ety:text, startV:text, countV:text) {
    if cgElemOwnsSomething(ety) == false { return }
    text idx = cgTmp()
    cgOut(`  ${idx} = alloca i64`)
    cgOut(`  store i64 0, ptr ${idx}`)
    text condL = cgLabel('spliceretain.loopcond')
    text bodyL = cgLabel('spliceretain.loopbody')
    text endL = cgLabel('spliceretain.loopend')
    cgOut(`  br label %${condL}`)
    cgBlockLabel(condL)
    text i = cgTmp()
    cgOut(`  ${i} = load i64, ptr ${idx}`)
    text go = cgTmp()
    cgOut(`  ${go} = icmp slt i64 ${i}, ${countV}`)
    cgOut(`  br i1 ${go}, label %${bodyL}, label %${endL}`)
    cgBlockLabel(bodyL)
    text abs = cgTmp()
    cgOut(`  ${abs} = add i64 ${startV}, ${i}`)
    text ep = cgTmp()
    cgOut(`  ${ep} = getelementptr ${elemLty}, ptr ${dataV}, i64 ${abs}`)
    text ev = cgTmp()
    cgOut(`  ${ev} = load ${elemLty}, ptr ${ep}`)
    if ety == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${ev})`)
        cgOut(`  store ptr ${owned}, ptr ${ep}`)
    } else {
        cgOut(`  call void @festina_retain(ptr ${ev})`)
    }
    text nx = cgTmp()
    cgOut(`  ${nx} = add i64 ${i}, 1`)
    cgOut(`  store i64 ${nx}, ptr ${idx}`)
    cgOut(`  br label %${condL}`)
    cgBlockLabel(endL)
}

// The counted loop both cascades share: the generated wrapper for a
// heap array about to be freed, and the scope exit of a
// frame-allocated one whose header is never freed but whose elements
// still hold something. It touches neither the data pointer nor the
// header -- the two callers need different things done with those.
void func cgReleaseArrayElements(dataV:text, lenV:text, elemFn:text, elemLty:text) {
    text idx = cgTmp()
    cgOut(`  ${idx} = alloca i64`)
    cgOut(`  store i64 0, ptr ${idx}`)
    text condL = cgLabel('relarr.loopcond')
    text bodyL = cgLabel('relarr.loopbody')
    text endL = cgLabel('relarr.loopend')
    cgOut(`  br label %${condL}`)
    cgBlockLabel(condL)
    text i = cgTmp()
    cgOut(`  ${i} = load i64, ptr ${idx}`)
    text go = cgTmp()
    cgOut(`  ${go} = icmp slt i64 ${i}, ${lenV}`)
    cgOut(`  br i1 ${go}, label %${bodyL}, label %${endL}`)
    cgBlockLabel(bodyL)
    text slot = cgTmp()
    cgOut(`  ${slot} = getelementptr ${elemLty}, ptr ${dataV}, i64 ${i}`)
    text v = cgTmp()
    cgOut(`  ${v} = load ${elemLty}, ptr ${slot}`)
    cgOut(`  call void ${elemFn}(ptr ${v})`)
    text nxt = cgTmp()
    cgOut(`  ${nxt} = add i64 ${i}, 1`)
    cgOut(`  store i64 ${nxt}, ptr ${idx}`)
    cgOut(`  br label %${condL}`)
    cgBlockLabel(endL)
}

// A generated function is built into a buffer of its own and pushed
// onto CG_FUNCS immediately, which puts it BEFORE the function whose
// body asked for it -- see cgFunc's own note. CG_BLOCK is saved and
// restored because the generated body has labels of its own and the
// interrupted body is still mid-block.
void func cgEmitGenerated(lines:arr[text]) {
    int i = 0
    while i < lines.length {
        CG_FUNCS.push(lines[i])
        i++
    }
}

// claude.md #80: the per-element-type cascade. It drops the refcount
// itself rather than calling festina_release_array, because the
// element loop has to run strictly BETWEEN the refcount check and the
// free -- the two cannot simply call each other.
// Whether this struct owns anything of its own -- a struct/arr/map
// field (refcounted) or a `text` one (an exclusively-owned buffer).
// Never transitive: a field's own cascade handles its own fields.
bool func cgStructOwnsAnything(sname:text) {
    if SF_NAMES[sname] == null { return false }
    if SF_NAMES[sname] == '' { return false }
    arr[text] names = SF_NAMES[sname].split('|')
    int i = 0
    while i < names.length {
        text f = SF_FTY[`${sname}.${names[i]}`]
        if f == 'text' { return true }
        if cgIsRefcounted(f) { return true }
        i++
    }
    return false
}

// The field walk both struct cascades share: the generated wrapper for
// a heap struct about to be freed, and the scope exit of a
// frame-allocated one whose own storage is never freed but whose
// fields still hold something. Touches neither the storage nor the
// header -- the callers need different things done with those.
void func cgReleaseStructFields(objPtr:text, sname:text) {
    arr[text] names = SF_NAMES[sname].split('|')
    int i = 0
    while i < names.length {
        text key = `${sname}.${names[i]}`
        text f = SF_FTY[key]
        bool managed = cgIsRefcounted(f)
        if f == 'text' { managed = true }
        if managed {
            // decisions.md #284: a text FIELD goes through
            // festina_free_z rather than plain free, and this is the
            // site that matters most for `clear` -- a struct holding a
            // secret holds it in a text field, and wiping only the
            // struct's own storage would leave it in the heap.
            //
            // Resolved BEFORE the field's own GEP temp, because
            // resolving it may GENERATE another cascade and that one's
            // temps come first. The same trap the array cascade has.
            text fn = '@festina_free_z'
            if f != 'text' { fn = cgReleaseFnFor(f, cgFieldEty(key)) }
            text fp = cgTmp()
            cgOut(`  ${fp} = getelementptr %struct.${sname}, ptr ${objPtr}, i32 0, i32 ${SF_IDX[key]}`)
            text fv = cgTmp()
            cgOut(`  ${fv} = load ptr, ptr ${fp}`)
            cgOut(`  call void ${fn}(ptr ${fv})`)
        }
        i++
    }
}

// The second half of a release key: a struct's NAME, or a container's
// element type. One slot, because a value is never both.
text func cgFieldEty(key:text) {
    if SF_SNAME[key] != null { return SF_SNAME[key] }
    if SF_ETY[key] != null { return SF_ETY[key] }
    return ''
}

text func cgRelKeyOf(name:text) {
    if cgFtyOf(name) == 'struct' { return cgSnameOf(name) }
    if cgFtyOf(name) == 'table' { return cgSnameOf(name) }
    return cgEtyOf(name)
}

text func cgRelKeyVal(v:Val) {
    if v.fty == 'struct' { return v.sname }
    // A row too: the table name is the only thing its release can be
    // generated from, and it rides in the same slot a struct's does.
    if v.fty == 'table' { return v.sname }
    return v.ety
}

// ---------------------------------------------------------------------
// claude.md #120: cycle collection.
//
// A reference cycle never reaches zero, so refcounting alone never
// frees one. The answer is a synchronous TRIAL DELETION: when a
// release leaves a value still referenced, try it as a cycle root --
// markGray (tentatively remove every edge internal to the subgraph),
// scan (decide survival from the counts that remain), then black
// (restore a surviving region) or white (free the garbage).
//
// The whole thing is gated on whether the TYPE can reach itself. A
// program with no self-referencing type generates none of these
// functions and its releases run no trial, so it pays literally
// nothing for the collector existing.
//
// A type is identified here by a key that describes itself: a struct
// by its name, a container as `arr[T]`/`map[T]`. That is the same
// spelling festina/codegen.py uses, and it is what lets a child be
// handed around as one string instead of a pair.

// Spelled `arr:T` rather than `arr[T]`: Festina's `text` has no
// `.slice()`, so a key has to be decodable by `.split()`, and a struct
// name never contains a colon.
// The element type of a container, as the port spells one: a scalar's
// own name, a struct's name, or -- for a NESTED container -- the same
// `arr:T`/`map:T` key a release is cached under. One slot, three
// shapes, distinguished by whether it carries a colon.
text func cgEtyOfTy(t:Ty) {
    if t == null { return '' }
    if t.elem == null { return '' }
    if t.elem.kind == 'prim' { return t.elem.name }
    if t.elem.kind == 'struct' { return t.elem.name }
    // A table name is spelled exactly like a struct name here, and
    // TBL_COLS is what separates them everywhere it matters. Both are
    // bare, which is deliberate: the colon in `arr:T` is reserved for
    // telling a NESTED container from a named type, and a row is not
    // one.
    if t.elem.kind == 'table' { return t.elem.name }
    if t.elem.kind == 'arr' || t.elem.kind == 'map' {
        text inner = cgEtyOfTy(t.elem)
        if inner == '' { return '' }
        // Only one level deep: `arr[arr[arr[int]]]` would need a key
        // that nests, and nothing reaches for one. Refused rather than
        // mis-spelled.
        if cgIsNestedElem(inner) { return '' }
        return cgTypeKey(t.elem.kind, inner)
    }
    return ''
}

text func cgTypeKey(fty:text, ety:text) {
    if fty == 'struct' { return ety }
    if fty == 'arr' { return `arr:${ety}` }
    if fty == 'map' { return `map:${ety}` }
    return ''
}

text func cgKeyFty(key:text) {
    arr[text] parts = key.split(':')
    if parts.length == 2 { return parts[0] }
    return 'struct'
}

text func cgKeyEty(key:text) {
    arr[text] parts = key.split(':')
    if parts.length == 2 { return parts[1] }
    return key
}

// The type-graph edges a trial walks: a struct's struct/arr/map fields,
// a container's element type when it is one of those. Every leaf --
// text, blob, a scalar -- has no outgoing edge, because none of them
// holds a reference to another managed value.
arr[text] func cgManagedChildren(key:text) {
    arr[text] out = []
    if cgKeyFty(key) != 'struct' {
        text e = cgKeyEty(key)
        if SF_NAMES[e] != null { out.push(e) }
        return out
    }
    if SF_NAMES[key] == null { return out }
    if SF_NAMES[key] == '' { return out }
    arr[text] names = SF_NAMES[key].split('|')
    int i = 0
    while i < names.length {
        text fk = `${key}.${names[i]}`
        text f = SF_FTY[fk]
        if f == 'struct' || f == 'arr' || f == 'map' {
            out.push(cgTypeKey(f, cgFieldEty(fk)))
        }
        i++
    }
    return out
}

map[int] CG_CYCLIC = {}
map[text] CG_CYCLE_FNS = {}

// Whether values of this type can sit on a cycle -- whether the type
// can reach ITSELF through managed edges. Purely a property of the
// declared type graph, so it is computed once per key and cached.
bool func cgIsCyclic(key:text) {
    if key == '' { return false }
    if CG_CYCLIC[key] != null { return CG_CYCLIC[key] == 1 }
    map[int] seen = {}
    arr[text] frontier = cgManagedChildren(key)
    bool result = false
    while frontier.length > 0 {
        text child = frontier.pop()
        if child == key {
            result = true
            frontier = []
        } else if seen[child] == null {
            seen[child] = 1
            arr[text] more = cgManagedChildren(child)
            int j = 0
            while j < more.length {
                frontier.push(more[j])
                j++
            }
        }
    }
    if result { CG_CYCLIC[key] = 1 } else { CG_CYCLIC[key] = 0 }
    return result
}

// The four traversals, plus the two per-element helpers a container
// hands to festina_cycle_visit_*. Registered in the cache BEFORE the
// body is generated, exactly like the release wrappers -- which is the
// only thing standing between a self-referencing type and infinite
// generation.
text func cgCycleFn(op:text, key:text) {
    text ck = `${op}|${key}`
    if CG_CYCLE_FNS[ck] != null { return CG_CYCLE_FNS[ck] }
    text name = `@__festina_cycle_${op}_${cgUid()}`
    CG_CYCLE_FNS[ck] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    if op == 'grayedge' {
        cgOut(`define void ${name}(ptr %c) {`)
        cgBlockLabel('entry')
        cgOut('  call void @festina_cycle_dec(ptr %c)')
        cgOut(`  call void ${cgCycleFn('gray', key)}(ptr %c)`)
        cgOut('  ret void')
        cgOut('}')
        cgOut('')
    } else if op == 'blackedge' {
        cgOut(`define void ${name}(ptr %c) {`)
        cgBlockLabel('entry')
        text recL = cgLabel('cyedge.rec')
        text doneL = cgLabel('cyedge.done')
        text nb = cgTmp()
        text cc = cgTmp()
        cgOut('  call void @festina_cycle_inc(ptr %c)')
        cgOut(`  ${nb} = call i8 @festina_cycle_needs_black(ptr %c)`)
        cgOut(`  ${cc} = icmp ne i8 ${nb}, 0`)
        cgOut(`  br i1 ${cc}, label %${recL}, label %${doneL}`)
        cgBlockLabel(recL)
        cgOut(`  call void ${cgCycleFn('black', key)}(ptr %c)`)
        cgOut(`  br label %${doneL}`)
        cgBlockLabel(doneL)
        cgOut('  ret void')
        cgOut('}')
        cgOut('')
    } else if cgKeyFty(key) == 'struct' {
        cgCycleStructBody(op, key, name)
    } else {
        cgCycleContainerBody(op, key, name)
    }
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// The fields a trial traverses: exactly the cyclic-typed ones.
// Everything else the struct owns -- text buffers, acyclic containers
// -- is handled by `white`'s disposal instead, released through the
// ordinary machinery, because it provably is not part of any cycle and
// its counts were never touched by the trial.
arr[text] func cgCycleStructChildren(sname:text) {
    arr[text] out = []
    if SF_NAMES[sname] == null { return out }
    if SF_NAMES[sname] == '' { return out }
    arr[text] names = SF_NAMES[sname].split('|')
    int i = 0
    while i < names.length {
        text fk = `${sname}.${names[i]}`
        text f = SF_FTY[fk]
        if f == 'struct' || f == 'arr' || f == 'map' {
            text ck = cgTypeKey(f, cgFieldEty(fk))
            if cgIsCyclic(ck) { out.push(`${SF_IDX[fk]}|${ck}`) }
        }
        i++
    }
    return out
}

text func cgCycleLoadField(sname:text, idx:text) {
    text fp = cgTmp()
    cgOut(`  ${fp} = getelementptr %struct.${sname}, ptr %p, i32 0, i32 ${idx}`)
    text fv = cgTmp()
    cgOut(`  ${fv} = load ptr, ptr ${fp}`)
    return fv
}

void func cgCycleStructBody(op:text, sname:text, name:text) {
    arr[text] children = cgCycleStructChildren(sname)
    cgOut(`define void ${name}(ptr %p) {`)
    cgBlockLabel('entry')
    if op == 'gray' {
        text go = cgTmp()
        text cond = cgTmp()
        text walk = cgLabel('cygray.walk')
        text done = cgLabel('cygray.done')
        cgOut(`  ${go} = call i8 @festina_cycle_begin_gray(ptr %p)`)
        cgOut(`  ${cond} = icmp ne i8 ${go}, 0`)
        cgOut(`  br i1 ${cond}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        int i = 0
        while i < children.length {
            arr[text] c = children[i].split('|')
            text fv = cgCycleLoadField(sname, c[0])
            cgOut(`  call void @festina_cycle_dec(ptr ${fv})`)
            cgOut(`  call void ${cgCycleFn('gray', c[1])}(ptr ${fv})`)
            i++
        }
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    } else if op == 'scan' {
        text r = cgTmp()
        text is1 = cgTmp()
        text blackL = cgLabel('cyscan.black')
        text chk2 = cgLabel('cyscan.chk2')
        text walk = cgLabel('cyscan.walk')
        text done = cgLabel('cyscan.done')
        cgOut(`  ${r} = call i64 @festina_cycle_begin_scan(ptr %p)`)
        cgOut(`  ${is1} = icmp eq i64 ${r}, 1`)
        cgOut(`  br i1 ${is1}, label %${blackL}, label %${chk2}`)
        cgBlockLabel(blackL)
        cgOut(`  call void ${cgCycleFn('black', sname)}(ptr %p)`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(chk2)
        text is2 = cgTmp()
        cgOut(`  ${is2} = icmp eq i64 ${r}, 2`)
        cgOut(`  br i1 ${is2}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        int i2 = 0
        while i2 < children.length {
            arr[text] c = children[i2].split('|')
            text fv = cgCycleLoadField(sname, c[0])
            cgOut(`  call void ${cgCycleFn('scan', c[1])}(ptr ${fv})`)
            i2++
        }
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    } else if op == 'black' {
        cgOut('  call void @festina_cycle_set_black(ptr %p)')
        int i3 = 0
        while i3 < children.length {
            arr[text] c = children[i3].split('|')
            text fv = cgCycleLoadField(sname, c[0])
            cgOut(`  call void @festina_cycle_inc(ptr ${fv})`)
            text nb = cgTmp()
            text cc = cgTmp()
            text rec = cgLabel('cyblack.rec')
            text nxt = cgLabel('cyblack.next')
            cgOut(`  ${nb} = call i8 @festina_cycle_needs_black(ptr ${fv})`)
            cgOut(`  ${cc} = icmp ne i8 ${nb}, 0`)
            cgOut(`  br i1 ${cc}, label %${rec}, label %${nxt}`)
            cgBlockLabel(rec)
            cgOut(`  call void ${cgCycleFn('black', c[1])}(ptr ${fv})`)
            cgOut(`  br label %${nxt}`)
            cgBlockLabel(nxt)
            i3++
        }
    } else {
        text go = cgTmp()
        text cond = cgTmp()
        text walk = cgLabel('cywhite.walk')
        text done = cgLabel('cywhite.done')
        cgOut(`  ${go} = call i8 @festina_cycle_begin_white(ptr %p)`)
        cgOut(`  ${cond} = icmp ne i8 ${go}, 0`)
        cgOut(`  br i1 ${cond}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        int i4 = 0
        while i4 < children.length {
            arr[text] c = children[i4].split('|')
            text fv = cgCycleLoadField(sname, c[0])
            cgOut(`  call void ${cgCycleFn('white', c[1])}(ptr ${fv})`)
            i4++
        }
        // Dispose: everything the node owns that the trial did NOT
        // traverse -- ordinary releases, whose counts the trial never
        // altered. Cyclic children are NOT released here: markGray
        // already removed those counts and the white recursion above
        // frees whichever of them are garbage.
        arr[text] names = SF_NAMES[sname].split('|')
        int k = 0
        while k < names.length {
            text fk = `${sname}.${names[k]}`
            text f = SF_FTY[fk]
            bool cyclic = false
            if f == 'struct' || f == 'arr' || f == 'map' {
                if cgIsCyclic(cgTypeKey(f, cgFieldEty(fk))) { cyclic = true }
            }
            if cyclic == false {
                if cgIsRefcounted(f) {
                    text dfn = cgReleaseFnFor(f, cgFieldEty(fk))
                    text fv = cgCycleLoadField(sname, `${SF_IDX[fk]}`)
                    cgOut(`  call void ${dfn}(ptr ${fv})`)
                } else if f == 'text' {
                    text fv = cgCycleLoadField(sname, `${SF_IDX[fk]}`)
                    cgOut(`  call void @festina_free_z(ptr ${fv})`)
                }
            }
            k++
        }
        text hdr = cgTmp()
        cgOut(`  ${hdr} = getelementptr i8, ptr %p, i64 -8`)
        cgOut(`  call void @free(ptr ${hdr})`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    }
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
}

// An arr[T]/map[T] with a cyclic T: the same four operations, with the
// per-element loop delegated to the runtime's visit helper handing
// each element to the element type's own function -- or to a
// grayedge/blackedge helper where the edge has count work of its own.
// Disposal is the runtime's, covering the container's buffers and keys
// and never its elements.
void func cgCycleContainerBody(op:text, key:text, name:text) {
    bool isMap = cgKeyFty(key) == 'map'
    text elem = cgKeyEty(key)
    text visit = '@festina_cycle_visit_array'
    text dispose = '@festina_cycle_dispose_array'
    if isMap {
        visit = '@festina_cycle_visit_map'
        dispose = '@festina_cycle_dispose_map'
    }
    cgOut(`define void ${name}(ptr %p) {`)
    cgBlockLabel('entry')
    if op == 'gray' {
        text go = cgTmp()
        text cond = cgTmp()
        text walk = cgLabel('cygray.walk')
        text done = cgLabel('cygray.done')
        cgOut(`  ${go} = call i8 @festina_cycle_begin_gray(ptr %p)`)
        cgOut(`  ${cond} = icmp ne i8 ${go}, 0`)
        cgOut(`  br i1 ${cond}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        cgOut(`  call void ${visit}(ptr %p, ptr ${cgCycleFn('grayedge', elem)})`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    } else if op == 'scan' {
        text r = cgTmp()
        text is1 = cgTmp()
        text blackL = cgLabel('cyscan.black')
        text chk2 = cgLabel('cyscan.chk2')
        text walk = cgLabel('cyscan.walk')
        text done = cgLabel('cyscan.done')
        cgOut(`  ${r} = call i64 @festina_cycle_begin_scan(ptr %p)`)
        cgOut(`  ${is1} = icmp eq i64 ${r}, 1`)
        cgOut(`  br i1 ${is1}, label %${blackL}, label %${chk2}`)
        cgBlockLabel(blackL)
        cgOut(`  call void ${cgCycleFn('black', key)}(ptr %p)`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(chk2)
        text is2 = cgTmp()
        cgOut(`  ${is2} = icmp eq i64 ${r}, 2`)
        cgOut(`  br i1 ${is2}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        cgOut(`  call void ${visit}(ptr %p, ptr ${cgCycleFn('scan', elem)})`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    } else if op == 'black' {
        cgOut('  call void @festina_cycle_set_black(ptr %p)')
        cgOut(`  call void ${visit}(ptr %p, ptr ${cgCycleFn('blackedge', elem)})`)
    } else {
        text go = cgTmp()
        text cond = cgTmp()
        text walk = cgLabel('cywhite.walk')
        text done = cgLabel('cywhite.done')
        cgOut(`  ${go} = call i8 @festina_cycle_begin_white(ptr %p)`)
        cgOut(`  ${cond} = icmp ne i8 ${go}, 0`)
        cgOut(`  br i1 ${cond}, label %${walk}, label %${done}`)
        cgBlockLabel(walk)
        cgOut(`  call void ${visit}(ptr %p, ptr ${cgCycleFn('white', elem)})`)
        cgOut(`  call void ${dispose}(ptr %p)`)
        cgOut(`  br label %${done}`)
        cgBlockLabel(done)
    }
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
}

// The still-referenced branch of a cyclic type's release wrapper: the
// value survives its own release, so try it as a cycle root. The
// candidate check keeps the trial off null and immortal values;
// everything else is at worst wasted work -- an externally-reachable
// subgraph scans black and comes out exactly as it went in -- never
// corruption.
void func cgCycleTrial(key:text, aliveL:text, doneL:text) {
    cgBlockLabel(aliveL)
    text cand = cgTmp()
    text cc = cgTmp()
    text trial = cgLabel('reltrial.run')
    cgOut(`  ${cand} = call i8 @festina_cycle_candidate(ptr %payload)`)
    cgOut(`  ${cc} = icmp ne i8 ${cand}, 0`)
    cgOut(`  br i1 ${cc}, label %${trial}, label %${doneL}`)
    cgBlockLabel(trial)
    cgOut(`  call void ${cgCycleFn('gray', key)}(ptr %payload)`)
    cgOut(`  call void ${cgCycleFn('scan', key)}(ptr %payload)`)
    cgOut(`  call void ${cgCycleFn('white', key)}(ptr %payload)`)
    cgOut(`  br label %${doneL}`)
}

// claude.md #78: the per-struct cascade. Registered in the cache
// BEFORE the field walk, which is the only thing standing between a
// self-referential struct and infinite generation -- one that reaches
// itself finds its own name already there and gets it back. The
// wrapper may then call ITSELF, which is correct: the recursion it
// performs is at runtime over the real object graph, bounded by
// refcounts reaching zero.
// claude.md #85/#265: frees exactly one sqlite result row -- each of
// its own text columns, then the allocation itself.
//
// A row is deliberately not shaped like any other Festina value. The
// runtime builds it as a flat `col_count * 8` block with each text
// column strdup'd into its slot, and the refcount header sits one i64
// BEFORE the payload every offset is measured from -- so the base, not
// the payload, is what is freed. Nothing generic could be pointed at
// one, which is why this is a bespoke function rather than a case
// inside the ordinary release dispatch.
//
// Which columns hold a heap pointer is decided by the identical rule
// the runtime used when BUILDING the row, read off the same declared
// column types: `text` was strdup'd, everything else is a plain i64.
// free(NULL) is a no-op, which covers a column that was SQL NULL and
// so was never strdup'd at all.
text func cgTableRowReleaseFn(tname:text) {
    if CG_ROW_REL[tname] != null { return CG_ROW_REL[tname] }
    // A blob/img/aud column is a heap pointer too, but not a plain
    // buffer -- the runtime decoded the stored BLOB into a real handle
    // and freeing it needs that type's own destructor. Refused rather
    // than freed wrongly, and refused HERE rather than at the
    // declaration, so a table with such a column can still be synced
    // by a program that never queries it.
    arr[text] pre = TBL_TYPES[tname].split('|')
    int pi = 0
    while pi < TBL_NCOLS[tname] {
        if cgLtyOf(pre[pi]) == '' {
            cgUnported(`table column of type ${pre[pi]}`)
            return ''
        }
        pi++
    }
    text name = `@__festina_release_row_${tname}_${cgUid()}`
    CG_ROW_REL[tname] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %row) {`)
    cgBlockLabel('entry')
    // Null first: a row-typed binding reads null until it is assigned,
    // and `free` nulls its slot again afterwards.
    text nullL = cgLabel('relrow.null')
    text checkL = cgLabel('relrow.check')
    text freeL = cgLabel('relrow.free')
    text isNull = cgTmp()
    cgOut(`  ${isNull} = icmp eq ptr %row, null`)
    cgOut(`  br i1 ${isNull}, label %${nullL}, label %${checkL}`)
    cgBlockLabel(checkL)
    text chk = cgTmp()
    cgOut(`  ${chk} = call i8 @festina_release_check(ptr %row)`)
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${chk}, 0`)
    cgOut(`  br i1 ${cond}, label %${freeL}, label %${nullL}`)
    cgBlockLabel(freeL)
    arr[text] ctypes = TBL_TYPES[tname].split('|')
    int n = TBL_NCOLS[tname]
    int i = 0
    while i < n {
        if ctypes[i] == 'text' {
            text slot = cgTmp()
            cgOut(`  ${slot} = getelementptr i64, ptr %row, i64 ${i}`)
            text v = cgTmp()
            cgOut(`  ${v} = load ptr, ptr ${slot}`)
            cgOut(`  call void @free(ptr ${v})`)
        }
        i++
    }
    text base = cgTmp()
    cgOut(`  ${base} = getelementptr i8, ptr %row, i64 -8`)
    cgOut(`  call void @free(ptr ${base})`)
    cgOut(`  br label %${nullL}`)
    cgBlockLabel(nullL)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgReleaseStructFn(sname:text) {
    if CG_STRUCT_REL[sname] != null { return CG_STRUCT_REL[sname] }
    text name = `@__festina_release_struct_${sname}`
    CG_STRUCT_REL[sname] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %payload) {`)
    cgBlockLabel('entry')
    text chk = cgTmp()
    cgOut(`  ${chk} = call i8 @festina_release_check(ptr %payload)`)
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${chk}, 0`)
    text freeL = cgLabel('relstruct.free')
    text doneL = cgLabel('relstruct.done')
    // claude.md #120: a type that can sit on a cycle takes a third
    // branch -- the not-last-reference case runs a trial deletion
    // instead of doing nothing, because this release may be the last
    // EXTERNAL reference to a cycle whose internal edges hold every
    // count above zero. An acyclic type keeps the plain two-way branch
    // and pays nothing.
    bool cyclic = cgIsCyclic(sname)
    text aliveL = doneL
    if cyclic { aliveL = cgLabel('relstruct.alive') }
    cgOut(`  br i1 ${cond}, label %${freeL}, label %${aliveL}`)
    cgBlockLabel(freeL)
    cgReleaseStructFields('%payload', sname)
    text hdr = cgTmp()
    cgOut(`  ${hdr} = getelementptr i8, ptr %payload, i64 -8`)
    cgOut(`  call void @festina_free_z(ptr ${hdr})`)
    cgOut(`  br label %${doneL}`)
    if cyclic { cgCycleTrial(sname, aliveL, doneL) }
    cgBlockLabel(doneL)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// claude.md #80: the map counterpart of the array cascade, and it
// needs one more piece. A map's ENTRIES are opaque to codegen in a way
// an array's flat buffer is not -- the entry layout lives in the
// runtime -- so the per-value release cannot be a loop emitted here.
// It goes through festina_map_for_each instead, which takes a callback
// of a fixed shape, so a small TRAMPOLINE is generated to discard the
// key and reinterpret the raw i64 value.
//
// The trampoline is never cached the way the wrappers are: it is
// needed exactly once, at the one site that generates it.
// claude.md #184: the bridge between festina_array_sort's comparator
// ABI -- `int(*)(const void*, const void*, void*)`, given a real
// userdata slot from the start so this never needs a global -- and a
// Festina `func[T,T]:int`. Cached per element type, because decoding
// the two raw slots needs THIS type's own LLVM type and the indirect
// call's argument types have to match the comparator exactly.
text func cgSortTrampoline(ety:text) {
    if CG_SORT_TRAMP[ety] != null { return CG_SORT_TRAMP[ety] }
    text name = `@__festina_sortcmp_${cgUid()}`
    CG_SORT_TRAMP[ety] = name
    text elemLty = cgElemLty(ety)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define i32 ${name}(ptr %a, ptr %b, ptr %userdata) {`)
    cgBlockLabel('entry')
    text av = cgTmp()
    cgOut(`  ${av} = load ${elemLty}, ptr %a`)
    text bv = cgTmp()
    cgOut(`  ${bv} = load ${elemLty}, ptr %b`)
    text r = cgTmp()
    cgOut(`  ${r} = call i64 %userdata(${elemLty} ${av}, ${elemLty} ${bv})`)
    text r32 = cgTmp()
    cgOut(`  ${r32} = trunc i64 ${r} to i32`)
    cgOut(`  ret i32 ${r32}`)
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// The same bridge for `map.forEach`: the runtime hands back a raw i64
// and the key, and only the compiler knows what the i64 means. Never
// cached -- the callback is part of the body, so two forEach calls
// with different callbacks need two trampolines.
text func cgMapForEachTrampoline(vty:text, cbName:text) {
    text name = `@__festina_maptrampoline_${cgUid()}`
    text vlty = cgElemLty(vty)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(i64 %raw, ptr %key) {`)
    cgBlockLabel('entry')
    text v = cgMapFromI64('%raw', vlty)
    cgOut(`  call void ${cbName}(${vlty} ${v}, ptr %key)`)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// ---------------------------------------------------------------------
// claude.md #159/#173/#233: parsing JSON into a declared type.
//
// `text.toStruct(T)` and `text.toArr(T)` generate a builder per target
// type, cached, that walks a cursor the runtime owns. Every read
// either returns a valid value or throws from inside the runtime and
// never returns, so nothing generated here branches on failure.
//
// What the generated code DOES have to handle is a throw from
// somewhere deeper: a half-built value is on this frame and nothing
// else owns it yet. Each builder registers what it holds on the
// runtime's cleanup stack -- the header it is filling in, and each
// key text between its read and its free -- so festina_throw releases
// them on the way to the catching try. Nested builders push and pop
// above their caller's entries, which is what makes the release order
// right: an inner half-built value goes before the outer one it would
// have been stored into.
//
// This is deliberately NOT the sjlj-per-builder design an earlier
// round used: that made any program using .toStruct() a "uses try"
// program, which has no lowering at all on wasm32 or AArch64.

// The builder for one target type, generated on first use and cached.
// The key is the type spelling, so `arr[int]` and a struct named `int`
// could never collide -- they are `arr:int` and `int`.
text func cgFromJsonFn(key:text) {
    if CG_FROMJSON[key] != null { return CG_FROMJSON[key] }
    if cgKeyFty(key) == 'arr' { return cgFromJsonArrFn(cgKeyEty(key)) }
    if cgKeyFty(key) == 'map' { return cgFromJsonMapFn(cgKeyEty(key)) }
    return cgFromJsonStructFn(key)
}

// Reading ONE value at the cursor. A scalar is a single runtime call;
// a nested struct or container recurses into its own builder, exactly
// as JSON rendering recurses.
text func cgJsonReadValue(fty:text, key:text) {
    if fty == 'struct' || fty == 'arr' || fty == 'map' {
        text fn = cgFromJsonFn(cgTypeKey(fty, key))
        text v = cgTmp()
        cgOut(`  ${v} = call ptr ${fn}(ptr %cursor)`)
        return v
    }
    text v2 = cgTmp()
    if fty == 'int' { cgOut(`  ${v2} = call i64 @festina_json_read_int(ptr %cursor)`) }
    else if fty == 'float' { cgOut(`  ${v2} = call double @festina_json_read_float(ptr %cursor)`) }
    else if fty == 'bool' { cgOut(`  ${v2} = call i8 @festina_json_read_bool(ptr %cursor)`) }
    else { cgOut(`  ${v2} = call ptr @festina_json_read_text(ptr %cursor)`) }
    return v2
}

text func cgFromJsonStructFn(sname:text) {
    text key = sname
    if CG_FROMJSON[key] != null { return CG_FROMJSON[key] }
    text name = `@__festina_from_json_struct_${cgUid()}`
    CG_FROMJSON[key] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define ptr ${name}(ptr %cursor) {`)
    cgBlockLabel('entry')
    text out = cgFreshHeader(`%struct.${sname}`)
    cgOut(`  call void @festina_cleanup_push(ptr ${out}, ptr ${cgReleaseFnFor('struct', sname)})`)
    cgOut('  call void @festina_json_object_start(ptr %cursor)')
    text first = cgTmp()
    cgOut(`  ${first} = alloca i8`)
    cgOut(`  store i8 1, ptr ${first}`)
    text loopL = cgLabel('fromjson.loop')
    text endL = cgLabel('fromjson.end')
    text readkeyL = cgLabel('fromjson.readkey')
    text keydoneL = cgLabel('fromjson.keydone')
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(loopL)
    text done = cgTmp()
    cgOut(`  ${done} = call i8 @festina_json_object_next(ptr %cursor, ptr ${first})`)
    text doneB = cgTmp()
    cgOut(`  ${doneB} = icmp ne i8 ${done}, 0`)
    cgOut(`  br i1 ${doneB}, label %${endL}, label %${readkeyL}`)
    cgBlockLabel(readkeyL)
    text keyReg = cgTmp()
    cgOut(`  ${keyReg} = call ptr @festina_json_read_key(ptr %cursor)`)
    cgOut(`  call void @festina_cleanup_push(ptr ${keyReg}, ptr @free)`)

    arr[text] names = SF_NAMES[sname].split('|')
    int i = 0
    while i < names.length {
        text fk = `${sname}.${names[i]}`
        text matchL = cgLabel(`fromjson.match${i}`)
        text nextL = cgLabel(`fromjson.check${i}`)
        text matches = cgTmp()
        cgOut(`  ${matches} = call i8 @festina_json_key_matches(ptr ${keyReg}, ptr ${cgStringConst(names[i])})`)
        text matchesB = cgTmp()
        cgOut(`  ${matchesB} = icmp ne i8 ${matches}, 0`)
        cgOut(`  br i1 ${matchesB}, label %${matchL}, label %${nextL}`)
        cgBlockLabel(matchL)
        text slot = cgTmp()
        cgOut(`  ${slot} = getelementptr %struct.${sname}, ptr ${out}, i32 0, i32 ${SF_IDX[fk]}`)
        text ffty = SF_FTY[fk]
        text fkey = ''
        if ffty == 'struct' { fkey = SF_SNAME[fk] }
        if ffty == 'arr' || ffty == 'map' { fkey = SF_ETY[fk] }
        text oldVal = ''
        if ffty == 'text' || cgIsRefcounted(ffty) {
            oldVal = cgTmp()
            cgOut(`  ${oldVal} = load ptr, ptr ${slot}`)
        }
        text v = cgJsonReadValue(ffty, fkey)
        cgOut(`  store ${SF_LTY[fk]} ${v}, ptr ${slot}`)
        // A duplicate key overwrites, last one wins -- which means
        // whatever the earlier one stored has to be given back, the
        // same convention a map literal's own repeated key follows.
        if oldVal != '' {
            if ffty == 'text' {
                cgOut(`  call void @free(ptr ${oldVal})`)
            } else {
                cgOut(`  call void ${cgReleaseFnFor(ffty, fkey)}(ptr ${oldVal})`)
            }
        }
        cgOut(`  br label %${keydoneL}`)
        cgBlockLabel(nextL)
        i++
    }
    // An unrecognized key's value is skipped rather than refused:
    // lenient, forward-compatible parsing.
    cgOut('  call void @festina_json_skip_field_value(ptr %cursor)')
    cgOut(`  br label %${keydoneL}`)
    cgBlockLabel(keydoneL)
    cgOut('  call void @festina_cleanup_pop()')
    cgOut(`  call void @free(ptr ${keyReg})`)
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(endL)
    cgOut('  call void @festina_cleanup_pop()')
    cgOut(`  ret ptr ${out}`)
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

// claude.md #173: the map[T] counterpart. Unlike the struct builder's
// own loop -- which matches each key against a FIXED set of field
// names and skips anything else -- every key here becomes an entry.
// That is the whole difference: arbitrary keys are what a map target
// is for.
// ---------------------------------------------------------------------
// claude.md #114/#190: rendering a value as JSON.
//
// One generated walker per type, cached, registered BEFORE its body is
// generated so a self-referencing struct produces ONE function that
// calls itself rather than recursing forever at compile time -- the
// same load-bearing cache write the release wrappers use.
//
// The recursion is depth-capped at 32 and a value past the cap renders
// as null. A cyclic value is constructible, and a DEBUG rendering that
// crashed the program it is debugging would be worse than an honest
// truncation.

// A compile-time-known string, appended with its length rather than
// through a runtime strlen the compiler already knows the answer to.
// A literal double quote. Festina's single-quoted strings take `\'`
// for their own delimiter, so a bare `"` is ordinary -- but a template
// literal is the only place this is ever interpolated, and spelling it
// once keeps the JSON key construction below readable.
text func cgDq() {
    return 34.toChar()
}

void func cgSbConst(sb:text, lit:text) {
    cgOut(`  call void @festina_sb_append_n(ptr ${sb}, ptr ${cgStringConst(lit)}, i64 ${cgUtf8Bytes(lit)})`)
}

// ONE value, at a slot, of a given type -- the shared field, element
// and entry emitter every generated walker is built from.
void func cgJsonSlot(sb:text, fty:text, key:text, slot:text, depth:text) {
    text v = cgTmp()
    if fty == 'int' {
        cgOut(`  ${v} = load i64, ptr ${slot}`)
        cgOut(`  call void @festina_sb_append_json_int(ptr ${sb}, i64 ${v})`)
        return
    }
    if fty == 'float' {
        cgOut(`  ${v} = load double, ptr ${slot}`)
        cgOut(`  call void @festina_sb_append_json_float(ptr ${sb}, double ${v})`)
        return
    }
    if fty == 'bool' {
        cgOut(`  ${v} = load i8, ptr ${slot}`)
        cgOut(`  call void @festina_sb_append_json_bool(ptr ${sb}, i8 ${v})`)
        return
    }
    if fty == 'text' {
        cgOut(`  ${v} = load ptr, ptr ${slot}`)
        cgOut(`  call void @festina_sb_append_json_text(ptr ${sb}, ptr ${v})`)
        return
    }
    if fty == 'struct' || fty == 'arr' || fty == 'map' {
        cgOut(`  ${v} = load ptr, ptr ${slot}`)
        text inner = cgJsonFn(cgTypeKey(fty, key))
        text d = cgTmp()
        cgOut(`  ${d} = add i64 ${depth}, 1`)
        cgOut(`  call void ${inner}(ptr ${v}, ptr ${sb}, i64 ${d})`)
        return
    }
    // Anything with no text form of its own renders as a labelled
    // handle rather than as nothing.
    cgOut(`  ${v} = load ptr, ptr ${slot}`)
    text q = cgDq()
    text label = cgStringConst(`${q}<${fty}>${q}`)
    cgOut(`  call void @festina_sb_append_handle(ptr ${sb}, ptr ${v}, ptr ${label})`)
}

text func cgJsonFn(key:text) {
    if CG_JSON[key] != null { return CG_JSON[key] }
    text name = `@__festina_json_${cgUid()}`
    CG_JSON[key] = name
    text kf = cgKeyFty(key)
    text ke = cgKeyEty(key)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %v, ptr %sb, i64 %depth) {`)
    cgBlockLabel('entry')
    text nullL = cgLabel('json.null')
    text deepL = cgLabel('json.deep')
    text goL = cgLabel('json.go')
    text isnull = cgTmp()
    cgOut(`  ${isnull} = icmp eq ptr %v, null`)
    cgOut(`  br i1 ${isnull}, label %${nullL}, label %${deepL}`)
    cgBlockLabel(nullL)
    cgSbConst('%sb', 'null')
    cgOut('  ret void')
    cgBlockLabel(deepL)
    text toodeep = cgTmp()
    cgOut(`  ${toodeep} = icmp sgt i64 %depth, 32`)
    cgOut(`  br i1 ${toodeep}, label %${nullL}, label %${goL}`)
    cgBlockLabel(goL)

    if kf == 'struct' {
        arr[text] fnames = []
        if SF_NAMES[ke] != '' { fnames = SF_NAMES[ke].split('|') }
        int i = 0
        while i < fnames.length {
            text prefix = ',"'
            if i == 0 { prefix = '{"' }
            cgSbConst('%sb', `${prefix}${fnames[i]}${cgDq()}:`)
            text fk = `${ke}.${fnames[i]}`
            text fp = cgTmp()
            cgOut(`  ${fp} = getelementptr %struct.${ke}, ptr %v, i32 0, i32 ${SF_IDX[fk]}`)
            text ffty = SF_FTY[fk]
            text fkey = ''
            if ffty == 'struct' { fkey = SF_SNAME[fk] }
            if ffty == 'arr' || ffty == 'map' { fkey = SF_ETY[fk] }
            cgJsonSlot('%sb', ffty, fkey, fp, '%depth')
            i++
        }
        if fnames.length == 0 { cgSbConst('%sb', '{') }
        cgSbConst('%sb', '}')
    } else if kf == 'arr' {
        text elemLty = cgElemLty(ke)
        text esize = '8'
        if ke == 'bool' { esize = '1' }
        cgSbConst('%sb', '[')
        text lenP = cgTmp()
        cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr %v, i32 0, i32 0`)
        text n = cgTmp()
        cgOut(`  ${n} = load i64, ptr ${lenP}`)
        text dataP = cgTmp()
        cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr %v, i32 0, i32 1`)
        text dataV = cgTmp()
        cgOut(`  ${dataV} = load ptr, ptr ${dataP}`)
        text iSlot = cgTmp()
        cgOut(`  ${iSlot} = alloca i64`)
        cgOut(`  store i64 0, ptr ${iSlot}`)
        text condL = cgLabel('json.acond')
        text loopL = cgLabel('json.abody')
        text sepL = cgLabel('json.asep')
        text elemL = cgLabel('json.aelem')
        text doneL = cgLabel('json.adone')
        cgOut(`  br label %${condL}`)
        cgBlockLabel(condL)
        text iv = cgTmp()
        cgOut(`  ${iv} = load i64, ptr ${iSlot}`)
        text more = cgTmp()
        cgOut(`  ${more} = icmp slt i64 ${iv}, ${n}`)
        cgOut(`  br i1 ${more}, label %${loopL}, label %${doneL}`)
        cgBlockLabel(loopL)
        text nonfirst = cgTmp()
        cgOut(`  ${nonfirst} = icmp sgt i64 ${iv}, 0`)
        cgOut(`  br i1 ${nonfirst}, label %${sepL}, label %${elemL}`)
        cgBlockLabel(sepL)
        cgSbConst('%sb', ',')
        cgOut(`  br label %${elemL}`)
        cgBlockLabel(elemL)
        text off = cgTmp()
        cgOut(`  ${off} = mul i64 ${iv}, ${esize}`)
        text ep = cgTmp()
        cgOut(`  ${ep} = getelementptr i8, ptr ${dataV}, i64 ${off}`)
        text efty = ke
        text ekey = ''
        if SF_NAMES[ke] != null { efty = 'struct'  ekey = ke }
        else if cgIsNestedElem(ke) { efty = cgKeyFty(ke)  ekey = cgKeyEty(ke) }
        cgJsonSlot('%sb', efty, ekey, ep, '%depth')
        text nx = cgTmp()
        cgOut(`  ${nx} = add i64 ${iv}, 1`)
        cgOut(`  store i64 ${nx}, ptr ${iSlot}`)
        cgOut(`  br label %${condL}`)
        cgBlockLabel(doneL)
        cgSbConst('%sb', ']')
    } else {
        // A map walks its BUCKETS by capacity rather than a dense
        // range, so a tombstone and an empty slot both have to be
        // recognized and skipped -- and whether a comma is owed cannot
        // be read off the index the way an array's can, because the
        // live entries are not contiguous.
        text capP = cgTmp()
        cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr %v, i32 0, i32 2`)
        text cap = cgTmp()
        cgOut(`  ${cap} = load i64, ptr ${capP}`)
        text entP = cgTmp()
        cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr %v, i32 0, i32 1`)
        text ents = cgTmp()
        cgOut(`  ${ents} = load ptr, ptr ${entP}`)
        cgSbConst('%sb', '{')
        text iSlot = cgTmp()
        cgOut(`  ${iSlot} = alloca i64`)
        cgOut(`  store i64 0, ptr ${iSlot}`)
        text emittedSlot = cgTmp()
        cgOut(`  ${emittedSlot} = alloca i8`)
        cgOut(`  store i8 0, ptr ${emittedSlot}`)
        text condL = cgLabel('json.mcond')
        text loopL = cgLabel('json.mbody')
        text liveL = cgLabel('json.mlive')
        text sepL = cgLabel('json.msep')
        text kvL = cgLabel('json.mkv')
        text nextL = cgLabel('json.mnext')
        text doneL = cgLabel('json.mdone')
        cgOut(`  br label %${condL}`)
        cgBlockLabel(condL)
        text iv = cgTmp()
        cgOut(`  ${iv} = load i64, ptr ${iSlot}`)
        text more = cgTmp()
        cgOut(`  ${more} = icmp slt i64 ${iv}, ${cap}`)
        cgOut(`  br i1 ${more}, label %${loopL}, label %${doneL}`)
        cgBlockLabel(loopL)
        text off = cgTmp()
        cgOut(`  ${off} = mul i64 ${iv}, 16`)
        text entp = cgTmp()
        cgOut(`  ${entp} = getelementptr i8, ptr ${ents}, i64 ${off}`)
        text keyv = cgTmp()
        cgOut(`  ${keyv} = load ptr, ptr ${entp}`)
        text isNull2 = cgTmp()
        cgOut(`  ${isNull2} = icmp eq ptr ${keyv}, null`)
        text isTomb = cgTmp()
        cgOut(`  ${isTomb} = icmp eq ptr ${keyv}, inttoptr (i64 1 to ptr)`)
        text skip = cgTmp()
        cgOut(`  ${skip} = or i1 ${isNull2}, ${isTomb}`)
        cgOut(`  br i1 ${skip}, label %${nextL}, label %${liveL}`)
        cgBlockLabel(liveL)
        text emitted = cgTmp()
        cgOut(`  ${emitted} = load i8, ptr ${emittedSlot}`)
        text nonfirst = cgTmp()
        cgOut(`  ${nonfirst} = icmp ne i8 ${emitted}, 0`)
        cgOut(`  br i1 ${nonfirst}, label %${sepL}, label %${kvL}`)
        cgBlockLabel(sepL)
        cgSbConst('%sb', ',')
        cgOut(`  br label %${kvL}`)
        cgBlockLabel(kvL)
        cgOut(`  call void @festina_sb_append_json_text(ptr %sb, ptr ${keyv})`)
        cgSbConst('%sb', ':')
        text vslot = cgTmp()
        cgOut(`  ${vslot} = getelementptr i8, ptr ${entp}, i64 8`)
        text vfty = ke
        text vkey = ''
        if SF_NAMES[ke] != null { vfty = 'struct'  vkey = ke }
        else if cgIsNestedElem(ke) { vfty = cgKeyFty(ke)  vkey = cgKeyEty(ke) }
        cgJsonSlot('%sb', vfty, vkey, vslot, '%depth')
        cgOut(`  store i8 1, ptr ${emittedSlot}`)
        cgOut(`  br label %${nextL}`)
        cgBlockLabel(nextL)
        text nx = cgTmp()
        cgOut(`  ${nx} = add i64 ${iv}, 1`)
        cgOut(`  store i64 ${nx}, ptr ${iSlot}`)
        cgOut(`  br label %${condL}`)
        cgBlockLabel(doneL)
        cgSbConst('%sb', '}')
    }
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgFromJsonMapFn(vty:text) {
    text key = `map:${vty}`
    if CG_FROMJSON[key] != null { return CG_FROMJSON[key] }
    text name = `@__festina_from_json_map_${cgUid()}`
    CG_FROMJSON[key] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define ptr ${name}(ptr %cursor) {`)
    cgBlockLabel('entry')
    text out = cgFreshHeader('%struct._FestinaMap')
    cgOut(`  call void @festina_cleanup_push(ptr ${out}, ptr ${cgReleaseFnFor('map', vty)})`)
    cgOut('  call void @festina_json_object_start(ptr %cursor)')
    text first = cgTmp()
    cgOut(`  ${first} = alloca i8`)
    cgOut(`  store i8 1, ptr ${first}`)
    text loopL = cgLabel('fromjson.mloop')
    text endL = cgLabel('fromjson.mend')
    text entryL = cgLabel('fromjson.mentry')
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(loopL)
    text done = cgTmp()
    cgOut(`  ${done} = call i8 @festina_json_object_next(ptr %cursor, ptr ${first})`)
    text doneB = cgTmp()
    cgOut(`  ${doneB} = icmp ne i8 ${done}, 0`)
    cgOut(`  br i1 ${doneB}, label %${endL}, label %${entryL}`)
    cgBlockLabel(entryL)
    text keyReg = cgTmp()
    cgOut(`  ${keyReg} = call ptr @festina_json_read_key(ptr %cursor)`)
    cgOut(`  call void @festina_cleanup_push(ptr ${keyReg}, ptr @free)`)
    text vfty = vty
    text vkey = ''
    if SF_NAMES[vty] != null { vfty = 'struct'  vkey = vty }
    else if cgIsNestedElem(vty) { vfty = cgKeyFty(vty)  vkey = cgKeyEty(vty) }
    text v = cgJsonReadValue(vfty, vkey)

    // The entry is stored WITHOUT a retain: every JSON read hands back
    // a fresh value rather than an alias, the same reasoning the array
    // builder's push relies on. A duplicate key -- the only way this
    // is ever asked to overwrite -- still releases what the key
    // already mapped to, AFTER the set, which is claude.md #120's own
    // ordering.
    text countP = cgTmp()
    cgOut(`  ${countP} = getelementptr %struct._FestinaMap, ptr ${out}, i32 0, i32 0`)
    text entP = cgTmp()
    cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr ${out}, i32 0, i32 1`)
    text capP = cgTmp()
    cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr ${out}, i32 0, i32 2`)
    text tombP = cgTmp()
    cgOut(`  ${tombP} = getelementptr %struct._FestinaMap, ptr ${out}, i32 0, i32 3`)
    text entV = cgTmp()
    cgOut(`  ${entV} = load ptr, ptr ${entP}`)
    text capV = cgTmp()
    cgOut(`  ${capV} = load i64, ptr ${capP}`)
    text oldRaw = cgTmp()
    cgOut(`  ${oldRaw} = call i64 @festina_map_get(ptr ${entV}, i64 ${capV}, ptr ${keyReg}, i64 0)`)
    text oldPtr = cgTmp()
    cgOut(`  ${oldPtr} = inttoptr i64 ${oldRaw} to ptr`)
    text raw = cgMapToI64(v, cgElemLty(vty))
    cgOut(`  call void @festina_map_set(ptr ${countP}, ptr ${entP}, ptr ${capP}, ptr ${tombP}, ptr ${keyReg}, i64 ${raw})`)
    if cgElemIsRefcounted(vty) {
        cgOut(`  call void ${cgElemReleaseFn(vty)}(ptr ${oldPtr})`)
    } else if vty == 'text' {
        cgOut(`  call void @free(ptr ${oldPtr})`)
    }
    cgOut('  call void @festina_cleanup_pop()')
    cgOut(`  call void @free(ptr ${keyReg})`)
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(endL)
    cgOut('  call void @festina_cleanup_pop()')
    cgOut(`  ret ptr ${out}`)
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgFromJsonArrFn(ety:text) {
    text key = `arr:${ety}`
    if CG_FROMJSON[key] != null { return CG_FROMJSON[key] }
    text name = `@__festina_from_json_arr_${cgUid()}`
    CG_FROMJSON[key] = name

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define ptr ${name}(ptr %cursor) {`)
    cgBlockLabel('entry')
    text out = cgFreshHeader('%struct._FestinaArray')
    cgOut(`  call void @festina_cleanup_push(ptr ${out}, ptr ${cgReleaseFnFor('arr', ety)})`)
    cgOut('  call void @festina_json_array_start(ptr %cursor)')
    text first = cgTmp()
    cgOut(`  ${first} = alloca i8`)
    cgOut(`  store i8 1, ptr ${first}`)
    text elemLty = cgElemLty(ety)
    text esize = '8'
    if ety == 'bool' { esize = '1' }
    text slot = cgTmp()
    cgOut(`  ${slot} = alloca ${elemLty}`)
    text loopL = cgLabel('fromjson.aloop')
    text endL = cgLabel('fromjson.aend')
    text elemL = cgLabel('fromjson.aelem')
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(loopL)
    text done = cgTmp()
    cgOut(`  ${done} = call i8 @festina_json_array_next(ptr %cursor, ptr ${first})`)
    text doneB = cgTmp()
    cgOut(`  ${doneB} = icmp ne i8 ${done}, 0`)
    cgOut(`  br i1 ${doneB}, label %${endL}, label %${elemL}`)
    cgBlockLabel(elemL)
    // Pushed with festina_array_push, the same helper `.push()` uses,
    // and never needing an ownership copy first: every JSON read
    // returns an already-fresh value rather than an alias.
    text efty = ety
    text ekey = ''
    if SF_NAMES[ety] != null { efty = 'struct'  ekey = ety }
    else if cgIsNestedElem(ety) { efty = cgKeyFty(ety)  ekey = cgKeyEty(ety) }
    text v = cgJsonReadValue(efty, ekey)
    cgOut(`  store ${elemLty} ${v}, ptr ${slot}`)
    cgOut(`  call void @festina_array_push(ptr ${out}, ptr null, i64 ${esize}, ptr ${slot})`)
    cgOut(`  br label %${loopL}`)
    cgBlockLabel(endL)
    cgOut('  call void @festina_cleanup_pop()')
    cgOut(`  ret ptr ${out}`)
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgMapReleaseTrampoline(vty:text) {
    text name = `@__festina_maprelease_${cgUid()}`
    // Resolved before the body's temps, because resolving it may
    // generate the value type's own cascade -- whose temps come first.
    // A text value is freed rather than released, exactly as an array
    // element of the same type is.
    text releaseFn = cgElemReleaseFn(vty)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(i64 %raw, ptr %key) {`)
    cgBlockLabel('entry')
    text p = cgTmp()
    cgOut(`  ${p} = inttoptr i64 %raw to ptr`)
    cgOut(`  call void ${releaseFn}(ptr ${p})`)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgReleaseMapFn(vty:text) {
    if CG_MAP_REL[vty] != null { return CG_MAP_REL[vty] }
    text name = `@__festina_release_map_${cgUid()}`
    CG_MAP_REL[vty] = name
    text tramp = cgMapReleaseTrampoline(vty)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %payload) {`)
    cgBlockLabel('entry')
    text chk = cgTmp()
    cgOut(`  ${chk} = call i8 @festina_release_check(ptr %payload)`)
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${chk}, 0`)
    text freeL = cgLabel('relmap.free')
    text doneL = cgLabel('relmap.done')
    bool cyclic = cgIsCyclic(cgTypeKey('map', vty))
    text aliveL = doneL
    if cyclic { aliveL = cgLabel('relmap.alive') }
    cgOut(`  br i1 ${cond}, label %${freeL}, label %${aliveL}`)
    cgBlockLabel(freeL)
    text entP = cgTmp()
    cgOut(`  ${entP} = getelementptr %struct._FestinaMap, ptr %payload, i32 0, i32 1`)
    text ent = cgTmp()
    cgOut(`  ${ent} = load ptr, ptr ${entP}`)
    text capP = cgTmp()
    cgOut(`  ${capP} = getelementptr %struct._FestinaMap, ptr %payload, i32 0, i32 2`)
    text cap = cgTmp()
    cgOut(`  ${cap} = load i64, ptr ${capP}`)
    cgOut(`  call void @festina_map_for_each(ptr ${ent}, i64 ${cap}, ptr ${tramp})`)
    cgOut(`  call void @festina_map_free_entries(ptr ${ent}, i64 ${cap})`)
    text hdr = cgTmp()
    cgOut(`  ${hdr} = getelementptr i8, ptr %payload, i64 -8`)
    cgOut(`  call void @festina_free_z(ptr ${hdr})`)
    cgOut(`  br label %${doneL}`)
    if cyclic { cgCycleTrial(cgTypeKey('map', vty), aliveL, doneL) }
    cgBlockLabel(doneL)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

text func cgReleaseArrayFn(ety:text) {
    if CG_ARR_REL[ety] != null { return CG_ARR_REL[ety] }
    text name = `@__festina_release_array_${cgUid()}`
    CG_ARR_REL[ety] = name
    // The element's own release is resolved BEFORE this body starts,
    // because resolving it may GENERATE another cascade -- a struct
    // element's -- and that one's temps come first. Asking for it from
    // inside the element loop instead numbers the two functions the
    // other way round.
    text elemFn = cgElemReleaseFn(ety)

    arr[text] saved = CUR
    text savedBlock = CG_BLOCK
    arr[text] gen = []
    CUR = gen
    cgOut(`define void ${name}(ptr %payload) {`)
    cgBlockLabel('entry')
    text chk = cgTmp()
    cgOut(`  ${chk} = call i8 @festina_release_check(ptr %payload)`)
    text cond = cgTmp()
    cgOut(`  ${cond} = icmp ne i8 ${chk}, 0`)
    text freeL = cgLabel('relarr.free')
    text doneL = cgLabel('relarr.done')
    // The same trial branch the struct wrapper grows, for an arr[T]
    // whose T sits on a cycle.
    bool cyclic = cgIsCyclic(cgTypeKey('arr', ety))
    text aliveL = doneL
    if cyclic { aliveL = cgLabel('relarr.alive') }
    cgOut(`  br i1 ${cond}, label %${freeL}, label %${aliveL}`)
    cgBlockLabel(freeL)
    text lenP = cgTmp()
    cgOut(`  ${lenP} = getelementptr %struct._FestinaArray, ptr %payload, i32 0, i32 0`)
    text lenV = cgTmp()
    cgOut(`  ${lenV} = load i64, ptr ${lenP}`)
    text dataP = cgTmp()
    cgOut(`  ${dataP} = getelementptr %struct._FestinaArray, ptr %payload, i32 0, i32 1`)
    text dataV = cgTmp()
    cgOut(`  ${dataV} = load ptr, ptr ${dataP}`)
    cgReleaseArrayElements(dataV, lenV, elemFn, cgElemLty(ety))
    cgOut(`  call void @festina_free_z(ptr ${dataV})`)
    text hdr = cgTmp()
    cgOut(`  ${hdr} = getelementptr i8, ptr %payload, i64 -8`)
    cgOut(`  call void @festina_free_z(ptr ${hdr})`)
    cgOut(`  br label %${doneL}`)
    if cyclic { cgCycleTrial(cgTypeKey('arr', ety), aliveL, doneL) }
    cgBlockLabel(doneL)
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    CUR = saved
    CG_BLOCK = savedBlock
    cgEmitGenerated(gen)
    return name
}

bool func cgIsOwningRefcountedSource(e:Node) {
    if e == null { return false }
    if e.kind == 'ArrayLit' { return true }
    if e.kind == 'MapLit' { return true }
    // claude.md #173: see cgIsOwningTextSource's own note -- a ternary
    // is owning because cgTernary already normalized whichever arm ran.
    if e.kind == 'Ternary' { return true }
    return e.kind == 'Call'
}

// Whether a text VALUE owns its buffer. The expression usually
// answers, but not always: a text field read through an owning base is
// copied out (claude.md #117), so the node reads as a plain member
// access while the value holds a fresh buffer. Ask the value first.
bool func cgOwnsText(e:Node, v:Val) {
    if v.fresh { return true }
    return cgIsOwningTextSource(e)
}

// Whether a value about to be stored into a map entry already owns
// what it points at. The two entry families ask DIFFERENT questions: a
// refcounted entry wants to know whether a retain is owed, a `text`
// one whether a copy is -- and the predicates disagree, because a
// concatenation is an owning text source (claude.md #97: every `+` in
// a text context mallocs) while it is no kind of refcounted source at
// all. Asking the refcounted question about a text value copies a
// buffer that was already exclusively owned and drops the original,
// which is exactly the shape that leaked here before this existed.
bool func cgMapValOwns(vty:text, e:Node, v:Val) {
    if vty == 'text' { return cgOwnsText(e, v) }
    return cgOwnsRefcounted(e, v)
}

// The refcounted counterpart of cgOwnsText, and the same lesson a
// third time: the VALUE can own a reference its own expression does
// not. `makeRows()[0]` is a plain member access as a NODE, while the
// value it produced holds the +1 claude.md #119 minted for it -- so a
// `.n` off it has a reference to give back that no amount of looking
// at the syntax would reveal. Ask the value first.
bool func cgOwnsRefcounted(e:Node, v:Val) {
    if v.fresh { return true }
    return cgIsOwningRefcountedSource(e)
}

bool func cgIsOwningTextSource(e:Node) {
    if e == null { return false }
    // A call's result is a buffer nothing else holds, so storing it
    // needs no copy -- and, symmetrically, dropping it needs a free
    // (see cgFreeTextTemp). A string literal is the opposite: a
    // pointer into .rodata that every use of that literal shares.
    if e.kind == 'Call' { return true }
    // A template always hands back a fresh buffer -- cgTemplate
    // guarantees it, taking a festina_text_own copy in the one case
    // (a bare `${x}`) where it otherwise would not.
    if e.kind == 'TemplateLit' { return true }
    // claude.md #173: a ternary too -- NOT because both arms are
    // somehow guaranteed fresh (most are not), but because cgTernary
    // normalizes whichever arm actually ran into a genuinely owned
    // value BEFORE this is ever asked. Treating one as aliasing after
    // that is what leaked: the caller claimed the result once
    // whichever arm ran, so a fresh arm's own correct ownership got an
    // extra claim with nothing to balance it.
    if e.kind == 'Ternary' { return true }
    // In a text context `+` is concatenation, which is exactly one
    // @festina_str_concat and mallocs unconditionally: there is no
    // operand-passthrough path, not even for an empty operand. Leaving
    // it out means every binding of a concatenation copies a buffer
    // that was already exclusively owned and drops the original.
    if e.kind == 'BinOp' { return rawText(e, 'op') == '+' }
    if e.kind == 'Ternary' { return true }
    return false
}

// A text value that was produced fresh by an expression and is not
// owned by any binding has to be freed once it has been used, or every
// such call leaks. Only a call reaches this today.
// The refcounted counterpart of cgFreeTextTemp: a receiver the
// expression itself owns and is now finished with. `xs.length` on a
// BINDING reads and leaves it alone; on `s.split(sep).length` the
// array has no owner once the length is taken, and nothing else will
// ever release it.
void func cgReleaseOwnedReceiver(e:Node, v:Val) {
    if cgIsRefcounted(v.fty) == false { return }
    if cgOwnsRefcounted(e, v) == false { return }
    cgOut(`  call void ${cgReleaseFnFor(v.fty, cgRelKeyVal(v))}(ptr ${v.v})`)
}

// claude.md #118: a regex that this expression COMPILED is released
// where it is used. `regex(p, f)` hands back the memo's own +1; a
// /pattern/ literal's cached compilation is immortal, so releasing it
// here is a harmless no-op. A regex bound to a VARIABLE is an
// identifier at its use sites and is not released here -- its own
// binding's scope exit owns that reference.
void func cgFreeRegexTemp(e:Node, v:Val) {
    if v.fty != 'regex' { return }
    if e == null { return }
    if e.kind != 'Call' { return }
    cgOut(`  call void @festina_regex_free(ptr ${v.v})`)
}

void func cgFreeTextTemp(e:Node, v:Val) {
    if v.fty != 'text' { return }
    // The VALUE can own a buffer its own expression does not: a text
    // field read through an owning base was copied out (claude.md
    // #117), so the node reads as a plain member access and the value
    // holds a fresh buffer.
    if v.fresh == false {
        if cgIsOwningTextSource(e) == false { return }
    }
    cgOut(`  call void @free(ptr ${v.v})`)
}

void func cgPostfix(e:Node) {
    Node operand = childOf(e, 'operand')
    if operand == null || operand.kind != 'Identifier' {
        cgUnported('postfix on a non-identifier')
        return
    }
    text name = rawText(operand, 'name')
    text slot = cgSlotOf(name)
    if slot == '' || cgFtyOf(name) != 'int' {
        cgUnported(`postfix on ${name}`)
        return
    }
    text op = rawText(e, 'op')
    text ins = 'add'
    if op == '--' { ins = 'sub' }
    text cur = cgTmp()
    cgOut(`  ${cur} = load i64, ptr ${slot}`)
    text nxt = cgTmp()
    cgOut(`  ${nxt} = ${ins} i64 ${cur}, 1`)
    cgOut(`  store i64 ${nxt}, ptr ${slot}`)
}

void func cgWhile(s:Node) {
    // Labels are allocated cond, body, end -- the order
    // festina/codegen.py takes them in, and therefore the numbering.
    text condL = cgLabel('while.cond')
    text bodyL = cgLabel('while.body')
    text endL = cgLabel('while.end')
    cgOut(`  br label %${condL}`)
    cgBlockLabel(condL)
    Val c = cgExpr(childOf(s, 'test'))
    if CG_STUCK { return }
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${bodyL}, label %${endL}`)
    cgBlockLabel(bodyL)
    // A `while`'s continue goes to the CONDITION; a `for`'s goes to
    // the update, so the step still runs.
    CG_LOOPS.push(`${condL}|${endL}|${CG_LIVE.length}`)
    cgBlockInto(childOf(s, 'body'))
    CG_LOOPS.pop()
    if CG_TERM == false { cgOut(`  br label %${condL}`) }
    cgBlockLabel(endL)
    CG_TERM = false
}

void func cgReturn(s:Node) {
    Node v = childOf(s, 'value')
    if v == null {
        cgFreeFrom(0)
        cgFreeParams()
        cgOut('  ret void')
        CG_TERM = true
        return
    }
    // Through cgExprExpecting, not cgExpr: `return null` has no type
    // of its own and takes the FUNCTION's. Without that it stays the
    // untyped null pointer and the refcounted branch below never
    // fires -- which shows up as a missing `festina_retain(ptr null)`,
    // a runtime no-op that is nonetheless in the original's IR.
    text retKey = ''
    if FN_RETKEY[CG_FUNC_NAME] != null { retKey = FN_RETKEY[CG_FUNC_NAME] }
    Val r = cgExprExpecting(v, CG_FUNC_RET, retKey)
    if CG_STUCK { return }
    // Returning text hands the caller ownership, so the value is
    // copied BEFORE the locals are freed -- returning a local's own
    // buffer and then freeing it would hand back a dangling pointer.
    // The order here (own, free, ret) is the original's.
    text val = r.v
    // The refcounted counterpart: a struct/container handed back takes
    // its own reference BEFORE the scope frees run, because a returned
    // local is no longer excluded from that list. Retain-then-release-
    // everything nets out to exactly one surviving reference on every
    // path, which is what makes the exclusion unnecessary -- and it
    // covers a ternary or field read a name-based exclusion never
    // could.
    if cgIsRefcounted(r.fty) {
        if cgIsOwningRefcountedSource(v) == false {
            cgOut(`  call void @festina_retain(ptr ${val})`)
        }
    } else if r.fty == 'text' {
        if cgOwnsText(v, r) == false {
            text o = cgTmp()
            cgOut(`  ${o} = call ptr @festina_text_own(ptr ${val})`)
            val = o
        }
    }
    cgFreeFrom(0)
    cgFreeParams()
    cgOut(`  ret ${r.lty} ${val}`)
    CG_TERM = true
}

// ---------------------------------------------------------------------
// Functions.
//
// Emitted into CG_FUNCS before main is emitted at all, because the
// shared temp/label/uid counters must reach them first.

void func cgFunc(d:Node) {
    text name = rawText(d, 'name')
    text retF = FN_RET[name]
    text retL = 'void'
    if retF != 'void' { retL = cgLtyOf(retF) }
    if retF != 'void' && retL == '' { retL = 'ptr' }

    arr[Node] params = listOf(d, 'params')
    arr[text] sig = []
    arr[text] pnames = []
    arr[text] pftys = []
    arr[text] pltys = []
    arr[text] psnames = []
    arr[text] petys = []
    int i = 0
    while i < params.length {
        text pn = rawText(params[i], 'name')
        text pf = cgDeclFty(params[i])
        text psname = ''
        text pety = ''
        text plty = cgLtyOf(pf)
        if pf == '' {
            // A struct/arr[T]/map[T] parameter: one `ptr` in the
            // signature whatever it holds, so the caller's side needs
            // nothing special. What it needs is the same restrictions a
            // LOCAL of that type has, because the binding is released
            // by the same machinery.
            Ty pt = resolveTypeField(params[i], 'type_expr')
            pf = cgManagedFty(pt)
            if pf == '' {
                cgUnported('parameter of a non-scalar type')
                return
            }
            plty = 'ptr'
            if pf == 'blob' || pf == 'regex' || pf == 'ascii'
                    || pf == 'img' || pf == 'aud' {
                // A handle: one ptr, and nothing to say about elements
                // or fields. Its release is the runtime's own.
            } else if pf == 'struct' {
                psname = pt.name
            } else if pf == 'table' {
                // A row parameter is a borrowed pointer, like a struct
                // one -- but its release is generated from the table
                // name, which is what `sname` carries.
                psname = pt.name
            } else {
                pety = cgEtyOfTy(pt)
                if pety == '' {
                    cgUnported(`${pf} parameter of a non-scalar type`)
                    return
                }
                if cgStorableRefcounted(pf, pety) == false {
                    cgUnported(`${pf} parameter of ${pety}`)
                    return
                }
            }
        }
        sig.push(`${plty} %arg.${pn}`)
        pnames.push(pn)
        pftys.push(pf)
        pltys.push(plty)
        psnames.push(psname)
        petys.push(pety)
        i++
    }
    text joined = ''
    int j = 0
    while j < sig.length {
        if j > 0 { joined = joined + ', ' }
        joined = joined + sig[j]
        j++
    }

    // A fresh local scope per function. Festina has no way to clear a
    // map in place, so the maps are replaced outright.
    map[text] freshSlot = {}
    map[text] freshFty = {}
    L_SLOT = freshSlot
    L_FTY = freshFty

    // claude.md #74: the whole body's escaping-name set, computed
    // BEFORE any parameter is bound, because binding is the first thing
    // that needs it -- a text parameter the body lets escape takes an
    // owning copy, one it only reads does not.
    map[int] escSet = findEscapingNames(cgBlockStmts(childOf(d, 'body')))
    if ESC_UNKNOWN {
        cgUnported(`escape analysis: expression ${ESC_UNKNOWN_KIND}`)
        return
    }
    map[int] savedEsc = CG_ESC
    CG_ESC = escSet

    // Into a buffer of this function's OWN, appended to CG_FUNCS only
    // once the body is finished. That is not tidiness: a per-type
    // release cascade is generated LAZILY, at the first release site
    // that needs it, which is somewhere inside a body -- and it must
    // land in the module BEFORE the function whose body asked for it,
    // because the original builds each body in a local list and
    // extends the shared one only at the end. Streaming straight into
    // CG_FUNCS would drop the generated function into the middle of
    // the definition that triggered it.
    arr[text] body = []
    CUR = body
    CG_IN_FUNC = true
    text savedFn = CG_FUNC_NAME
    text savedRet = CG_FUNC_RET
    CG_FUNC_NAME = name
    CG_FUNC_RET = retF
    cgOut(`define ${retL} @${name}(${joined}) {`)
    cgBlockLabel(cgLabel('entry'))

    // A fresh live-value list per function: a frame left over from a
    // previous function would be freed inside this one. Both are reset
    // before the parameter stores below, because an escaping text
    // parameter is tracked there.
    arr[text] freshLive = []
    arr[int] freshFrames = []
    arr[text] freshParams = []
    CG_LIVE = freshLive
    CG_FRAME = freshFrames
    CG_PARAM_LIVE = freshParams

    // One pass, alloca and binding together per parameter, because the
    // UID ORDER is observable: an escaping parameter's binding may
    // GENERATE a function of its own (the unwind wrapper claude.md
    // #236 registers it with), and that generation happens between
    // this parameter's slot and the next one's. Two separate loops
    // took every slot's uid first and left the generated function
    // numbered after all of them. The allocas still come out grouped
    // at the top -- hoisting does that, not this loop.
    int q = 0
    while q < pnames.length {
        text slot = `%${pnames[q]}.${cgUid()}`
        cgOut(`  ${slot} = alloca ${pltys[q]}`)
        if pftys[q] == 'text' {
            cgOut(`  ${slot}.ap = alloca ptr`)
            cgOut(`  ${slot}.aplen = alloca i64`)
        }
        L_SLOT[pnames[q]] = slot
        L_FTY[pnames[q]] = pftys[q]
        if psnames[q] != '' { L_SNAME[pnames[q]] = psnames[q] }
        if petys[q] != '' { L_ETY[pnames[q]] = petys[q] }
        text arg = `%arg.${pnames[q]}`
        if pftys[q] == 'text' {
            cgOut(`  store ptr null, ptr ${slot}.ap`)
            cgOut(`  store i64 0, ptr ${slot}.aplen`)
            if escSet[pnames[q]] != null {
                // claude.md #83/#84: a parameter the body reassigns --
                // or lets escape any other way -- must own its own
                // buffer, because the CALLER still owns what it passed
                // and will free it. One the body only reads borrows it
                // for the call's duration and copies nothing.
                text owned = cgTmp()
                cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${arg})`)
                arg = owned
                CG_PARAM_LIVE.push(`text|${slot}|`)
                // Before the binding store, not after: a parameter's
                // store IS the last thing its binding does, and the
                // original registers it here. claude.md #236.
                cgCleanupPush('text', slot, '')
            }
        } else if cgIsRefcounted(pftys[q]) {
            if escSet[pnames[q]] != null {
                // The refcounted counterpart of the text copy above.
                // A text parameter the body lets escape takes its OWN
                // buffer, because text is copy-on-alias; a refcounted
                // one takes its own reference instead. Either way the
                // caller still owns what it passed, so the binding must
                // not end up sharing the caller's single claim on it.
                cgOut(`  call void @festina_retain(ptr ${arg})`)
                CG_PARAM_LIVE.push(`${pftys[q]}|${slot}|${petys[q]}${psnames[q]}`)
                cgCleanupPush(pftys[q], slot, `${petys[q]}${psnames[q]}`)
            }
        }
        cgOut(`  store ${pltys[q]} ${arg}, ptr ${slot}`)
        q++
    }
    // Through cgBlockInto, not a loop of its own: the helper resets
    // CG_TERM on entry, and without that a function whose body does not
    // return inherits the flag from whichever function was emitted
    // before it and silently loses its `ret void`.
    cgBlockInto(childOf(d, 'body'))
    if retF == 'void' {
        if CG_TERM == false {
            cgFreeParams()
            cgOut('  ret void')
        }
    }
    cgOut('}')
    cgOut('')
    int b = 0
    while b < body.length {
        CG_FUNCS.push(body[b])
        b++
    }
    CG_IN_FUNC = false
    CG_FUNC_NAME = savedFn
    CG_FUNC_RET = savedRet
    CG_ESC = savedEsc

    // claude.md #74 stage 2: registered AFTER the body, so a LATER
    // function's own analysis can exempt a call argument this one
    // proves safe -- and an earlier one cannot. The order functions are
    // emitted in is part of the answer.
    escRegisterParams(name, params, escSet)
}

// ---------------------------------------------------------------------
// The module.

// claude.md #236: does this subtree contain a `try` anywhere -- inside
// any function, handler, thread body or nested block? A generic walk
// over every node's own fields, rather than a per-statement case list,
// so a shape this port has not thought about cannot be missed.
//
// Only `try` counts, not `throw`: with no try anywhere a throw is
// fail(), there is nothing to unwind to, and the cleanup stack would
// be pure cost.
bool func cgContainsTry(n:Node) {
    if n == null { return false }
    if n.kind == 'TryStmt' { return true }
    int i = 0
    while i < n.fields.length {
        Field f = n.fields[i]
        if f.tag == 'node' {
            if cgContainsTry(f.node) { return true }
        } else if f.tag == 'list' {
            int j = 0
            while j < f.list.length {
                if cgContainsTry(f.list[j]) { return true }
                j++
            }
        }
        i++
    }
    return false
}

void func cgProgram(body:arr[Node], srcPath:text) {
    // Decided once, up front, for the whole program: every function
    // body emitted below needs to know it, and the first one emitted
    // may well come before the try itself.
    int tq = 0
    while tq < body.length {
        if cgContainsTry(body[tq]) { CG_HAS_TRY = true }
        tq++
    }
    cgEmit('; ModuleID = "festina"')
    cgEmit(`; generated from ${srcPath} -- claude.md #47`)
    int i = 0
    while i < CG_PRE.length {
        cgEmit(CG_PRE[i])
        i++
    }

    // One type definition per declared struct, in source order,
    // directly after the runtime's own -- the struct-definition section
    // festina/codegen.py builds from `analyzed.structs`, whose key
    // order is registration order.
    int sd = 0
    while sd < body.length {
        Node d = body[sd]
        if d.kind == 'StructDecl' {
            text sn = rawText(d, 'name')
            arr[Node] fs = listOf(d, 'fields')
            text row = ''
            int fi = 0
            while fi < fs.length {
                Ty ft = resolveTypeField(fs[fi], 'type_expr')
                text flty = cgFieldType(ft)
                if fi > 0 { row = row + ', ' }
                row = row + flty
                text fname = rawText(fs[fi], 'name')
                text key = `${sn}.${fname}`
                SF_IDX[key] = fi
                SF_LTY[key] = flty
                text ffty = cgDeclFty(fs[fi])
                if ffty == '' { ffty = cgManagedFty(ft) }
                SF_FTY[key] = ffty
                if ffty == 'struct' { SF_SNAME[key] = ft.name }
                if ffty == 'arr' || ffty == 'map' {
                    if ft.elem != null {
                        SF_ETY[key] = cgEtyOfTy(ft)
                    }
                }
                fi++
            }
            bool plain = true
            int pf = 0
            while pf < fs.length {
                text pk = `${sn}.${rawText(fs[pf], 'name')}`
                text pfty = SF_FTY[pk]
                if pfty != 'int' && pfty != 'float' && pfty != 'bool' { plain = false }
                pf++
            }
            if plain { SF_PLAIN[sn] = 1 }
            // The field names in declaration order, joined -- a
            // release cascade walks them and the per-field tables are
            // keyed by name, so the order has to be recoverable from
            // the struct alone.
            text fnames = ''
            int fn2 = 0
            while fn2 < fs.length {
                if fn2 > 0 { fnames = fnames + '|' }
                fnames = fnames + rawText(fs[fn2], 'name')
                fn2++
            }
            SF_NAMES[sn] = fnames
            cgEmit(`%struct.${sn} = type { ${row} }`)
        }
        sd++
    }

    // claude.md #70: the DatabaseURL directive, lifted out before any
    // statement is emitted. festina/imports.py strips it from the
    // ENTRY file's body and enforces that it is that file's first
    // statement; by the time it reaches here the bodies are already
    // merged, so position has been settled and all that is left is to
    // find it and take it out of the run.
    int du = 0
    while du < body.length {
        Node ds = body[du]
        if ds.kind == 'ExprStmt' {
            Node dex = childOf(ds, 'expr')
            if dex != null {
                if dex.kind == 'Assign' {
                    Node dtg = childOf(dex, 'target')
                    if dtg != null {
                        if dtg.kind == 'Identifier' {
                            if rawText(dtg, 'name') == 'DatabaseURL' {
                                CG_DB_URL = childOf(dex, 'value')
                            }
                        }
                    }
                }
            }
        }
        du++
    }

    // Every declared `table`'s columns, before anything is emitted.
    // A table's own declaration produces no code at all -- what it
    // produces is a line in main's prologue, which is built long after
    // this point, so all that happens here is recording.
    int td = 0
    while td < body.length {
        if body[td].kind == 'TableDecl' {
            Node t = body[td]
            text tn = rawText(t, 'name')
            arr[Node] tcols = listOf(t, 'fields')
            text cnames = ''
            text ctypes = ''
            int ci = 0
            while ci < tcols.length {
                if ci > 0 {
                    cnames = cnames + '|'
                    ctypes = ctypes + '|'
                }
                text colName = rawText(tcols[ci], 'name')
                cnames = cnames + colName
                // The SQL type is the column's type expression SPELLED
                // OUT, not a resolved type: festina_sync_table matches
                // on the source's own word, and `analyzed.tables` keeps
                // the raw type-expr string for exactly that reason. A
                // column whose type is not a bare name has no such
                // word (and no SQL type either), so it stops the port
                // rather than being guessed at.
                if fieldOf(tcols[ci], 'type_expr').tag != 'raw' {
                    cgUnported('table column of a non-scalar type')
                    return
                }
                text colType = rawText(tcols[ci], 'type_expr')
                ctypes = ctypes + colType
                // A row's slots are flat 8-byte cells in DECLARATION
                // order, so a column's index is its offset divided by
                // eight -- unlike a struct field, whose offset only
                // LLVM knows.
                TB_IDX[`${tn}.${colName}`] = ci
                TB_FTY[`${tn}.${colName}`] = colType
                ci++
            }
            TBL_ORDER.push(tn)
            TBL_COLS[tn] = cnames
            TBL_TYPES[tn] = ctypes
            TBL_NCOLS[tn] = tcols.length
        }
        td++
    }

    // argv is registered with no VarDecl of its own (claude.md #150),
    // so its storage is emitted unconditionally, for every module.
    cgEmit('')
    cgEmit('@argv.header = global {i64, %struct._FestinaArray} {i64 -1, %struct._FestinaArray zeroinitializer}')
    cgEmit('@argv = global ptr getelementptr({i64, %struct._FestinaArray}, ptr @argv.header, i32 0, i32 1)')
    // And registered like any other global, because that is exactly
    // what it is from here on. claude.md #150: the only thing special
    // about argv is its INITIAL value, which main() stores from the
    // real argc/argv rather than from a user-written initializer --
    // every read, copy, index and free of it is an ordinary
    // `arr[text]` global's.
    G_SLOT['argv'] = '@argv'
    G_FTY['argv'] = 'arr'
    G_ETY['argv'] = 'text'

    // Every top-level declaration's storage, in source order.
    int g = 0
    while g < body.length {
        Node d = body[g]
        if d.kind == 'VarDecl' {
            text gn = rawText(d, 'name')
            text gf = cgDeclFty(d)
            if gf != '' {
                text gl = cgLtyOf(gf)
                // claude.md #91: a colour's zero is its 'none'
                // sentinel, not 0 -- and cgZeroFor cannot tell the two
                // apart, because a colour and an int are the same i64
                // by then. The FTY is what still knows.
                text gz = cgZeroFor(gl)
                if gf == 'color' { gz = cgNullValue('color') }
                cgEmit(`@${gn} = global ${gl} ${gz}`)
                // claude.md #243: a text binding carries an append
                // shadow -- the buffer it is growing in place and how
                // much of it is used -- alongside the pointer itself.
                if gf == 'text' {
                    cgEmit(`@${gn}.ap = global ptr null`)
                    cgEmit(`@${gn}.aplen = global i64 0`)
                }
                G_SLOT[gn] = `@${gn}`
                G_FTY[gn] = gf
                if gf == 'func' { G_ETY[gn] = cgFuncSig(resolveTypeField(d, 'type_expr')) }
            } else {
                // A managed global's storage is its payload wrapped in
                // a {refcount, payload} header, with the visible
                // pointer GEP'd past the count -- exactly argv's own
                // shape above. The count is -1, the immortal sentinel:
                // a global is reachable until the process exits, so
                // nothing ever releases it.
                Ty gt = resolveTypeField(d, 'type_expr')
                // A blob is a HANDLE, not a value with storage of its
                // own: there is no payload to wrap in a header, only a
                // pointer that starts null. So it takes the plain form
                // a scalar global takes, not the {refcount, payload}
                // one every other refcounted global has.
                text handleG = cgManagedFty(gt)
                if handleG == 'blob' || handleG == 'regex' || handleG == 'ascii'
                        || handleG == 'img' || handleG == 'aud' {
                    cgEmit(`@${gn} = global ptr null`)
                    G_SLOT[gn] = `@${gn}`
                    G_FTY[gn] = handleG
                }
                text payload = cgPayloadFor(gt)
                if payload != '' {
                    cgEmit(`@${gn}.header = global {i64, ${payload}} {i64 -1, ${payload} zeroinitializer}`)
                    cgEmit(`@${gn} = global ptr getelementptr({i64, ${payload}}, ptr @${gn}.header, i32 0, i32 1)`)
                    G_SLOT[gn] = `@${gn}`
                    G_FTY[gn] = cgManagedFty(gt)
                    if gt.kind == 'struct' { G_SNAME[gn] = gt.name }
                    if gt.kind == 'arr' || gt.kind == 'map' {
                        if gt.elem != null {
                            G_ETY[gn] = cgEtyOfTy(gt)
                        }
                    }
                }
            }
        }
        g++
    }

    // Function signatures are registered before any body is emitted,
    // so a call can precede its declaration -- function hoisting
    // (claude.md #58) is a language rule, not an ordering accident.
    int fs2 = 0
    while fs2 < body.length {
        if body[fs2].kind == 'FuncDecl' {
            Ty rt = resolveTypeField(body[fs2], 'return_type')
            text rf = 'void'
            text rkey = ''
            if rt != null {
                if rt.kind == 'prim' { rf = rt.name }
                else { rf = '' }
                // A struct/container return is one `ptr` whatever it
                // holds, exactly like a parameter -- but the release a
                // call result eventually gets depends on WHICH, so the
                // second half of the key travels with it.
                if rf == '' || cgLtyOf(rf) == '' {
                    text managedR = cgManagedFty(rt)
                    if managedR != '' {
                        rf = managedR
                        if rt.kind == 'struct' { rkey = rt.name }
                        else if rt.elem != null { rkey = rt.elem.name }
                    }
                }
            }
            if rf == 'void' || cgLtyOf(rf) != '' || cgIsRefcounted(rf) {
                FN_RETKEY[rawText(body[fs2], 'name')] = rkey
            }
            if rf == 'void' || cgLtyOf(rf) != '' || cgIsRefcounted(rf) {
                text fname = rawText(body[fs2], 'name')
                FN_RET[fname] = rf
                // The parameter types too, joined -- a `null` ARGUMENT
                // has no type of its own and takes the parameter's, so
                // a call site needs the signature and not just the
                // return. An entry is '' for a parameter whose type
                // this port does not spell, which falls back to the
                // untyped null exactly as the original does.
                arr[Node] ps = listOf(body[fs2], 'params')
                text joined = ''
                int pi = 0
                while pi < ps.length {
                    if pi > 0 { joined = joined + '|' }
                    joined = joined + cgDeclFty(ps[pi])
                    pi++
                }
                FN_PARAMS[fname] = joined
                // claude.md #141: and the whole signature, encoded, so
                // a bare reference to this name can become a
                // first-class VALUE with a callable type. Separate
                // from FN_PARAMS because that one deliberately spells
                // a non-scalar parameter as '' and a call through a
                // value cannot.
                FN_SIG[fname] = cgSigOfDeclNode(body[fs2])
            }
        }
        fs2++
    }

    // Bodies next, into their own buffer, so the shared counters reach
    // them before main.
    int fb = 0
    while fb < body.length {
        if body[fb].kind == 'FuncDecl' {
            CG_STUCK = false
            cgFunc(body[fb])
        }
        fb++
    }

    // Then main's own statements.
    CUR = CG_MAIN
    // main gets a fresh local scope too, and for the same reason every
    // function does. Without this it inherits whichever function was
    // emitted LAST -- so a top-level `int i = 0` that shadows nothing
    // at all would resolve to some unrelated body's `%i.685`, storing
    // into a slot that is not in scope and leaving the global it just
    // declared untouched. Invisible until a driver's top level shared
    // a name with a function local, which is to say until now.
    map[text] mainSlot = {}
    map[text] mainFty = {}
    L_SLOT = mainSlot
    L_FTY = mainFty
    // claude.md #74 over main's own body, for the same reason every
    // function gets it. What this reaches is a local declared inside a
    // NESTED block at the top level -- `text row = a + b` in a
    // top-level `while` -- which is an ordinary alloca and, without an
    // escaping-name set, gets the wrong storage answer. Top-level
    // declarations themselves are globals and are unaffected; the
    // analysis input is the same whole-body statement list a function
    // gets.
    CG_ESC = findEscapingNames(body)
    if ESC_UNKNOWN {
        cgUnported(`escape analysis: expression ${ESC_UNKNOWN_KIND}`)
    }
    cgOut('define void @__festina_main() {')
    cgBlockLabel('entry')
    CG_TERM = false
    arr[text] mainLive = []
    arr[int] mainFrames = []
    CG_LIVE = mainLive
    CG_FRAME = mainFrames
    cgPushFrame()
    int s = 0
    while s < body.length {
        CG_STUCK = false
        cgStmt(body[s])
        s++
    }
    cgPopFrame()
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    cgOut('define i32 @main(i32 %argc, ptr %argv_raw) {')
    cgBlockLabel('entry')
    cgOut('  call void @festina_runtime_init()')
    cgOut('  %argv_arr = call ptr @festina_argv_array(i32 %argc, ptr %argv_raw)')
    cgOut('  store ptr %argv_arr, ptr @argv')
    // A program with scheduled callbacks needs a blocking loop for
    // them to fire in -- the pure-POSIX one, which lives in the core
    // translation unit, so a timers-only program never links the
    // graphics object just to wait. The shutdown handler goes with it:
    // the loop polls festina_shutdown_requested() once per iteration,
    // which is what makes installing one meaningful here.
    if CG_USES_TIMERS { cgOut('  call void @festina_install_shutdown_handler()') }
    // claude.md #101/#199: the image decoder is registered here, before
    // anything could decode an img column -- and before any thread is
    // spawned, so no thread's own on_load can race this store. Only for
    // a program that already links the feature, so the symbol exists.
    // The audio decoder goes FIRST, which is the original's order
    // rather than an alphabetical accident.
    if CG_USES_AUDIO {
        cgOut('  call void @festina_set_audio_decoder(ptr @festina_audio_from_bytes)')
    }
    if CG_USES_GRAPHICS_CODE {
        cgOut('  call void @festina_set_image_decoder(ptr @festina_image_from_bytes)')
    }
    // claude.md #29-31: the database is opened and every declared table
    // synced HERE, in main's own prologue, before __festina_main runs a
    // single statement -- so a top-level query in the program's very
    // first line already has a schema to query. Every table is synced
    // whether or not anything queries it, which is both simpler than
    // tracking use and free: festina_sync_table does nothing to a table
    // already shaped right.
    cgSyncTables()
    cgOut('  call void @__festina_main()')
    if CG_USES_TIMERS { cgOut('  call void @festina_run_timer_loop()') }
    // The last thing main does, and only when there is a database to
    // close: for a program that never opens one this call would be the
    // single live reference into the runtime's SQLite code, which on
    // wasm is the difference between linking the whole vendored engine
    // and dropping it.
    if TBL_ORDER.length > 0 || CG_USES_SQLITE {
        cgOut('  %final_db = load ptr, ptr @__festina_db')
        cgOut('  call void @festina_db_close(ptr %final_db)')
    }
    cgOut('  ret i32 0')
    cgOut('}')

    // The section layout festina/codegen.py's own `generate` builds:
    // an empty extra-globals section and its separator, then the
    // function definitions (each already followed by its own blank
    // line), then a separator, then the entry points, then a separator
    // and the string constants.
    cgEmit('')
    int xg = 0
    while xg < CG_EXTRA.length {
        cgEmit(CG_EXTRA[xg])
        xg++
    }
    cgEmit('')
    int ff = 0
    while ff < CG_FUNCS.length {
        cgEmit(CG_FUNCS[ff])
        ff++
    }
    cgEmit('')
    int mm = 0
    while mm < CG_MAIN.length {
        cgEmit(CG_MAIN[mm])
        mm++
    }
    cgEmit('')
    int c = 0
    while c < CG_STRS.length {
        cgEmit(CG_STRS[c])
        c++
    }

    CG_IR = cgHoistAllocas(CG_IR)
}
