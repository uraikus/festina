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

import semantic.f

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

text func cgCEscape(s:text) {
    text out = ''
    int i = 0
    while i < s.length {
        int c = s.charCodeAt(i)
        if c == 92 { out = out + '\\5C' }
        if c == 34 { out = out + '\\22' }
        if c == 10 { out = out + '\\0A' }
        if c == 9  { out = out + '\\09' }
        if c == 13 { out = out + '\\0D' }
        if c != 92 && c != 34 && c != 10 && c != 9 && c != 13 {
            out = out + c.toChar()
        }
        i++
    }
    return out
}

text func cgStringConst(v:text) {
    int i = 0
    while i < v.length {
        if v.charCodeAt(i) > 127 {
            cgUnported('non-ASCII string literal')
            return '@.str.0'
        }
        i++
    }
    if CG_STR_MAP[v] != null { return CG_STR_MAP[v] }
    text name = `@.str.${CG_STR_N}`
    CG_STR_N++
    CG_STR_MAP[v] = name
    int bytes = v.length + 1
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

float func cgParseFloat(s:text) {
    CG_NUM_OK = true
    arr[text] parts = s.split('.')
    if parts.length == 1 {
        if parts[0].length > 18 { CG_NUM_OK = false  return 0.0 }
        return parts[0].toInt().toFloat()
    }
    if parts.length != 2 { CG_NUM_OK = false  return 0.0 }
    int k = parts[1].length
    if k > 22 { CG_NUM_OK = false  return 0.0 }
    text digits = parts[0] + parts[1]
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
    if fty == 'float' { return 'double' }
    if fty == 'bool' { return 'i8' }
    if fty == 'text' { return 'ptr' }
    // arr[T], map[T] and struct values are all a pointer to their own
    // storage -- never the aggregate inline (claude.md #79), which is
    // what gives two bindings a shared identity on assignment.
    if fty == 'arr' { return 'ptr' }
    if fty == 'map' { return 'ptr' }
    if fty == 'struct' { return 'ptr' }
    return ''
}

// The LLVM payload type sitting behind a managed value's pointer, or
// '' when the type is not one of them. This is the shape the
// {refcount, payload} global header wraps -- the same layout
// festina_retain/festina_release expect, with the count at payload-8.
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

// Struct field layout, keyed '<Struct>.<field>'. codegen.py reads this
// off `analyzed.structs`; here it is collected while the type
// definitions are emitted, which is the same information in the same
// order.
map[int] SF_IDX = {}
map[text] SF_LTY = {}
map[text] SF_FTY = {}
map[text] SF_SNAME = {}

bool func cgIsLocal(name:text) {
    return L_SLOT[name] != null
}

text func cgSlotOf(name:text) {
    if L_SLOT[name] != null { return L_SLOT[name] }
    if G_SLOT[name] != null { return G_SLOT[name] }
    return ''
}

map[text] G_SNAME = {}
map[text] L_SNAME = {}

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

// Values needing a free when their scope ends, and where each scope
// began. festina/codegen.py keeps the same thing as a frame stack and
// frees "down to" a given frame; this is that, with the frames as
// indices into one flat list.
//
// Frees are emitted in DECLARATION order, not reverse -- read off the
// original's output for two locals in one block, not assumed.
arr[text] CG_LIVE = []
arr[int] CG_FRAME = []

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

    if e.kind == 'Identifier' {
        text name = rawText(e, 'name')
        text slot = cgSlotOf(name)
        text fty = cgFtyOf(name)
        if slot == '' || cgLtyOf(fty) == '' {
            cgUnported(`read of ${name}`)
            return none
        }
        text lty = cgLtyOf(fty)
        text t = cgTmp()
        cgOut(`  ${t} = load ${lty}, ptr ${slot}`)
        if fty == 'struct' { return cgStructVal(t, cgSnameOf(name)) }
        return cgVal(t, lty, fty)
    }

    if e.kind == 'BinOp' { return cgBinOp(e) }
    if e.kind == 'LogicalOp' { return cgLogical(e) }
    if e.kind == 'UnaryOp' { return cgUnary(e) }
    if e.kind == 'Ternary' { return cgTernary(e) }
    if e.kind == 'Member' { return cgMemberRead(e) }
    if e.kind == 'Call' { return cgCall(e, true) }
    if e.kind == 'TemplateLit' { return cgTemplate(e) }

    cgUnported(`expression ${e.kind}`)
    return none
}

Val func cgBinOp(e:Node) {
    Val none
    Val l = cgExpr(childOf(e, 'left'))
    if CG_STUCK { return none }
    Val r = cgExpr(childOf(e, 'right'))
    if CG_STUCK { return none }
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
    // sizeof via getelementptr-on-null: LLVM's own layout rules rather
    // than a reimplementation of them.
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
    cgOut(`  store ptr ${made}, ptr ${fp.v}`)
    text makePred = CG_BLOCK
    cgOut(`  br label %${doneL}`)

    cgBlockLabel(doneL)
    text out = cgTmp()
    cgOut(`  ${out} = phi ptr [ ${loaded}, %${loadPred} ], [ ${made}, %${makePred} ]`)
    if fp.fty == 'struct' { return cgStructVal(out, fp.sname) }
    return cgVal(out, 'ptr', fp.fty)
}

Val func cgMemberRead(e:Node) {
    Val none
    Val fp = cgFieldPtr(e)
    if CG_STUCK { return none }
    if cgLtyOf(fp.fty) == '' {
        cgUnported(`read of a ${fp.fty} field`)
        return none
    }
    return cgLoadFieldValue(fp)
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
    cgBlockLabel(thenL)
    Val a = cgExpr(childOf(e, 'cons'))
    if CG_STUCK { return none }
    text thenPred = CG_BLOCK
    cgOut(`  br label %${endL}`)
    cgBlockLabel(elseL)
    Val b = cgExpr(childOf(e, 'alt'))
    if CG_STUCK { return none }
    text elsePred = CG_BLOCK
    cgOut(`  br label %${endL}`)
    cgBlockLabel(endL)
    if a.fty != 'int' && a.fty != 'float' && a.fty != 'bool' {
        cgUnported(`ternary of type ${a.fty}`)
        return none
    }
    if a.fty != b.fty {
        cgUnported(`ternary mixing ${a.fty} and ${b.fty}`)
        return none
    }
    text out = cgTmp()
    cgOut(`  ${out} = phi ${a.lty} [ ${a.v}, %${thenPred} ], [ ${b.v}, %${elsePred} ]`)
    return cgVal(out, a.lty, a.fty)
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
        if a.fty == 'text' { pieceOwned = cgIsOwningTextSource(exprs[i]) }
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
Val func cgCall(e:Node, wantValue:bool) {
    Val none
    Node callee = childOf(e, 'callee')
    if callee == null || callee.kind != 'Identifier' {
        cgUnported('call through a non-identifier callee')
        return none
    }
    text name = rawText(callee, 'name')
    if FN_RET[name] == null {
        cgUnported(`call to ${name}`)
        return none
    }
    text retF = FN_RET[name]
    arr[Node] args = listOf(e, 'args')
    arr[text] parts = []
    int i = 0
    while i < args.length {
        Val a = cgExpr(args[i])
        if CG_STUCK { return none }
        parts.push(`${a.lty} ${a.v}`)
        i++
    }
    text joined = ''
    int j = 0
    while j < parts.length {
        if j > 0 { joined = joined + ', ' }
        joined = joined + parts[j]
        j++
    }
    if retF == 'void' {
        cgOut(`  call void @${name}(${joined})`)
        return cgVal('', 'void', 'void')
    }
    text lty = cgLtyOf(retF)
    text t = cgTmp()
    cgOut(`  ${t} = call ${lty} @${name}(${joined})`)
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
    cgUnported(`log(${a.fty})`)
}

void func cgStmt(s:Node) {
    // Pure type information: the definition was emitted with the
    // module's type section and nothing reaches main.
    if s.kind == 'StructDecl' { return }
    if s.kind == 'FuncDecl' { return }

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
            // is nothing for main to do. Anything else -- a local, or
            // an initializer of any kind -- needs the header allocated
            // and released, which is not ported.
            text gname = rawText(s, 'name')
            if G_SLOT[gname] == null {
                cgUnported(`${managed} local`)
                return
            }
            if childOf(s, 'init') != null {
                cgUnported(`${managed} initializer`)
                return
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
        if isLocalDecl {
            text slot = `%${name}.${cgUid()}`
            cgOut(`  ${slot} = alloca ${lty}`)
            if fty == 'text' {
                // claude.md #243's append shadow gets storage of its
                // own and is initialized empty. The allocas are hoisted
                // to the entry block; these stores are not, so they run
                // once per execution of the declaration -- which is
                // what makes a declaration inside a loop correct.
                cgOut(`  ${slot}.ap = alloca ptr`)
                cgOut(`  ${slot}.aplen = alloca i64`)
                cgOut(`  store ptr null, ptr ${slot}.ap`)
                cgOut(`  store i64 0, ptr ${slot}.aplen`)
                CG_LIVE.push(slot)
            }
            L_SLOT[name] = slot
            L_FTY[name] = fty
            freshLocal = true
        }
        Node init = childOf(s, 'init')
        if init == null { return }
        Val v = cgExpr(init)
        if CG_STUCK { return }
        if v.fty != fty && cgNumericPair(v.fty, fty) == false {
            cgUnported(`initializer of type ${v.fty} for ${fty}`)
            return
        }
        if fty == 'text' {
            // A fresh local's slot holds nothing yet, so there is no
            // old buffer to free and no stale append shadow to null --
            // only the owning copy and the store. A global's slot may
            // already hold a value from an earlier execution, which is
            // why it goes the long way round.
            if freshLocal {
                text owned = v.v
                if cgIsOwningTextSource(init) == false {
                    text o = cgTmp()
                    cgOut(`  ${o} = call ptr @festina_text_own(ptr ${owned})`)
                    owned = o
                }
                cgOut(`  store ptr ${owned}, ptr ${cgSlotOf(name)}`)
                return
            }
            cgStoreText(cgSlotOf(name), `${cgSlotOf(name)}.ap`, v,
                        cgIsOwningTextSource(init))
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
        cgCall(ex, false)
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

void func cgPushFrame() {
    CG_FRAME.push(CG_LIVE.length)
}

// Frees every live value from `downTo` onward. A `return` passes 0, so
// it unwinds every frame at once; a block's natural end passes its own
// base and unwinds just itself.
void func cgFreeFrom(downTo:int) {
    int i = downTo
    while i < CG_LIVE.length {
        text t = cgTmp()
        cgOut(`  ${t} = load ptr, ptr ${CG_LIVE[i]}`)
        cgOut(`  call void @free(ptr ${t})`)
        i++
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
    cgBlockInto(childOf(s, 'body'))
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
    if t.kind != 'prim' { return '' }
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
            if a.fty == 'text' { owned = cgIsOwningTextSource(pieces[i]) }
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

void func cgAssign(e:Node) {
    Node target = childOf(e, 'target')
    if target != null && target.kind == 'Member' {
        // The object and its GEP come first, then the value -- the
        // order festina/codegen.py's own _emit_assign uses, because it
        // resolves the target's type before emitting the value so an
        // array-literal right-hand side can pick its element type from
        // context.
        Val fp = cgFieldPtr(target)
        if CG_STUCK { return }
        if fp.fty != 'int' && fp.fty != 'float' && fp.fty != 'bool' {
            cgUnported(`assignment to a ${fp.fty} field`)
            return
        }
        Val fv = cgExpr(childOf(e, 'value'))
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

    Val v = cgExpr(value)
    if CG_STUCK { return }
    if fty == 'text' {
        cgStoreText(slot, `${slot}.ap`, v, cgIsOwningTextSource(value))
        return
    }
    cgOut(`  store ${cgLtyOf(fty)} ${v.v}, ptr ${slot}`)
}

// Whether an expression already hands back a buffer nothing else
// holds, so storing it needs no copy. A string literal is NOT one: it
// is a pointer into .rodata that every use of that literal shares.
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
void func cgFreeTextTemp(e:Node, v:Val) {
    if v.fty != 'text' { return }
    if cgIsOwningTextSource(e) == false { return }
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
    cgBlockInto(childOf(s, 'body'))
    if CG_TERM == false { cgOut(`  br label %${condL}`) }
    cgBlockLabel(endL)
    CG_TERM = false
}

void func cgReturn(s:Node) {
    Node v = childOf(s, 'value')
    if v == null {
        cgFreeFrom(0)
        cgOut('  ret void')
        CG_TERM = true
        return
    }
    Val r = cgExpr(v)
    if CG_STUCK { return }
    // Returning text hands the caller ownership, so the value is
    // copied BEFORE the locals are freed -- returning a local's own
    // buffer and then freeing it would hand back a dangling pointer.
    // The order here (own, free, ret) is the original's.
    text val = r.v
    if r.fty == 'text' {
        if cgIsOwningTextSource(v) == false {
            text o = cgTmp()
            cgOut(`  ${o} = call ptr @festina_text_own(ptr ${val})`)
            val = o
        }
    }
    cgFreeFrom(0)
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

    arr[Node] params = listOf(d, 'params')
    arr[text] sig = []
    arr[text] pnames = []
    arr[text] pftys = []
    int i = 0
    while i < params.length {
        text pf = cgDeclFty(params[i])
        if pf == '' {
            cgUnported('parameter of a non-scalar type')
            return
        }
        // A `text` parameter needs more than one alloca, and the port
        // was silently getting it wrong: festina/codegen.py gives every
        // text parameter claude.md #243's {pointer, length} append
        // shadow beside its slot, and gives an ESCAPING one a
        // festina_text_own copy plus a scope-exit free as well. The
        // port emitted a bare `alloca ptr` and nothing else -- verified
        // against a two-line probe, which differs at the first shadow
        // line. No corpus file was affected (a file with a text
        // parameter was already unported for other reasons, so the
        // difference never surfaced), which is exactly why it stayed
        // hidden.
        //
        // Refused rather than half-implemented: whether the copy is
        // needed is what festina/escape_analysis.py answers, and
        // hand-rolling a partial version of that rule here would be
        // the same mistake in a new place. It lifts with that module.
        if pf == 'text' {
            cgUnported('text parameter')
            return
        }
        text pn = rawText(params[i], 'name')
        sig.push(`${cgLtyOf(pf)} %arg.${pn}`)
        pnames.push(pn)
        pftys.push(pf)
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

    CUR = CG_FUNCS
    CG_IN_FUNC = true
    cgOut(`define ${retL} @${name}(${joined}) {`)
    cgBlockLabel(cgLabel('entry'))
    int p = 0
    while p < pnames.length {
        text slot = `%${pnames[p]}.${cgUid()}`
        cgOut(`  ${slot} = alloca ${cgLtyOf(pftys[p])}`)
        L_SLOT[pnames[p]] = slot
        L_FTY[pnames[p]] = pftys[p]
        p++
    }
    int q = 0
    while q < pnames.length {
        cgOut(`  store ${cgLtyOf(pftys[q])} %arg.${pnames[q]}, ptr ${L_SLOT[pnames[q]]}`)
        q++
    }
    // A fresh live-value list per function: a frame left over from a
    // previous function would be freed inside this one.
    arr[text] freshLive = []
    arr[int] freshFrames = []
    CG_LIVE = freshLive
    CG_FRAME = freshFrames

    // Through cgBlockInto, not a loop of its own: the helper resets
    // CG_TERM on entry, and without that a function whose body does not
    // return inherits the flag from whichever function was emitted
    // before it and silently loses its `ret void`.
    cgBlockInto(childOf(d, 'body'))
    if retF == 'void' {
        if CG_TERM == false { cgOut('  ret void') }
    }
    cgOut('}')
    cgOut('')
    CG_IN_FUNC = false
}

// ---------------------------------------------------------------------
// The module.

void func cgProgram(body:arr[Node], srcPath:text) {
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
                fi++
            }
            cgEmit(`%struct.${sn} = type { ${row} }`)
        }
        sd++
    }

    // argv is registered with no VarDecl of its own (claude.md #150),
    // so its storage is emitted unconditionally, for every module.
    cgEmit('')
    cgEmit('@argv.header = global {i64, %struct._FestinaArray} {i64 -1, %struct._FestinaArray zeroinitializer}')
    cgEmit('@argv = global ptr getelementptr({i64, %struct._FestinaArray}, ptr @argv.header, i32 0, i32 1)')

    // Every top-level declaration's storage, in source order.
    int g = 0
    while g < body.length {
        Node d = body[g]
        if d.kind == 'VarDecl' {
            text gn = rawText(d, 'name')
            text gf = cgDeclFty(d)
            if gf != '' {
                text gl = cgLtyOf(gf)
                cgEmit(`@${gn} = global ${gl} ${cgZeroFor(gl)}`)
                // claude.md #243: a text binding carries an append
                // shadow -- the buffer it is growing in place and how
                // much of it is used -- alongside the pointer itself.
                if gf == 'text' {
                    cgEmit(`@${gn}.ap = global ptr null`)
                    cgEmit(`@${gn}.aplen = global i64 0`)
                }
                G_SLOT[gn] = `@${gn}`
                G_FTY[gn] = gf
            } else {
                // A managed global's storage is its payload wrapped in
                // a {refcount, payload} header, with the visible
                // pointer GEP'd past the count -- exactly argv's own
                // shape above. The count is -1, the immortal sentinel:
                // a global is reachable until the process exits, so
                // nothing ever releases it.
                Ty gt = resolveTypeField(d, 'type_expr')
                text payload = cgPayloadFor(gt)
                if payload != '' {
                    cgEmit(`@${gn}.header = global {i64, ${payload}} {i64 -1, ${payload} zeroinitializer}`)
                    cgEmit(`@${gn} = global ptr getelementptr({i64, ${payload}}, ptr @${gn}.header, i32 0, i32 1)`)
                    G_SLOT[gn] = `@${gn}`
                    G_FTY[gn] = cgManagedFty(gt)
                    if gt.kind == 'struct' { G_SNAME[gn] = gt.name }
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
            if rt != null {
                if rt.kind == 'prim' { rf = rt.name }
                else { rf = '' }
            }
            if rf == 'void' || cgLtyOf(rf) != '' {
                FN_RET[rawText(body[fs2], 'name')] = rf
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
    cgOut('  call void @__festina_main()')
    cgOut('  ret i32 0')
    cgOut('}')

    // The section layout festina/codegen.py's own `generate` builds:
    // an empty extra-globals section and its separator, then the
    // function definitions (each already followed by its own blank
    // line), then a separator, then the entry points, then a separator
    // and the string constants.
    cgEmit('')
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
