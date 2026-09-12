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
bool CG_UNPORTED = false
text CG_WHY = ''
arr[text] CG_WHYS = []

void func cgEmit(line:text) {
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
    text name = `@.str.${CG_STR_N}`
    CG_STR_N++
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
// Statements.
//
// v1 covers exactly one statement shape: `log(<string literal>)`, which
// is what `benchmarks/hello.f` is made of. Everything else reports
// unported. That is deliberately a narrow start -- the point of this
// slice is the HARNESS, and a harness is only believable once one real
// file matches end to end.

void func cgStmt(s:Node) {
    // A top-level declaration's storage was already emitted by
    // cgGlobals; what remains in main is the store its initializer
    // performs, in source order alongside every other statement.
    if s.kind == 'VarDecl' {
        if fieldOf(s, 'is_const').raw == 'true' {
            cgUnported('const declaration')
            return
        }
        if fieldOf(s, 'manually_managed').raw == 'true' {
            cgUnported('manually-managed declaration')
            return
        }
        text ty = cgLlvmType(resolveTypeField(s, 'type_expr'))
        if ty == '' {
            cgUnported('declaration of a non-scalar type')
            return
        }
        Node init = childOf(s, 'init')
        if init == null { return }
        text value = cgConstFor(ty, init)
        if CG_UNPORTED { return }
        cgEmit(`  store ${ty} ${value}, ptr @${rawText(s, 'name')}`)
        return
    }
    // A struct declaration is pure type information: its definition
    // was already emitted above, and it contributes nothing to main.
    if s.kind == 'StructDecl' { return }
    if s.kind != 'ExprStmt' {
        cgUnported(`statement ${s.kind}`)
        return
    }
    Node call = childOf(s, 'expr')
    if call == null || call.kind != 'Call' {
        cgUnported('expression statement that is not a call')
        return
    }
    Node callee = childOf(call, 'callee')
    if callee == null || callee.kind != 'Identifier' {
        cgUnported('call through a non-identifier callee')
        return
    }
    if rawText(callee, 'name') != 'log' {
        cgUnported(`call to ${rawText(callee, 'name')}`)
        return
    }
    arr[Node] args = listOf(call, 'args')
    if args.length != 1 {
        cgUnported('log() with other than one argument')
        return
    }
    if args[0].kind != 'StringLit' {
        cgUnported(`log(${args[0].kind})`)
        return
    }
    text name = cgStringConst(rawText(args[0], 'value'))
    cgEmit(`  call void @festina_log_text(ptr ${name})`)
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
            arr[Node] fs = listOf(d, 'fields')
            text row = ''
            int fi = 0
            while fi < fs.length {
                if fi > 0 { row = row + ', ' }
                row = row + cgFieldType(resolveTypeField(fs[fi], 'type_expr'))
                fi++
            }
            cgEmit(`%struct.${rawText(d, 'name')} = type { ${row} }`)
        }
        sd++
    }

    // argv is registered with no VarDecl of its own (claude.md #150),
    // so its storage is emitted unconditionally, for every module.
    cgEmit('')
    cgEmit('@argv.header = global {i64, %struct._FestinaArray} {i64 -1, %struct._FestinaArray zeroinitializer}')
    cgEmit('@argv = global ptr getelementptr({i64, %struct._FestinaArray}, ptr @argv.header, i32 0, i32 1)')

    // Every top-level declaration's storage, in source order, right
    // after argv's -- the globals section festina/codegen.py builds in
    // _toplevel. The initializers themselves are statements, emitted
    // inside __festina_main below.
    int g = 0
    while g < body.length {
        Node d = body[g]
        if d.kind == 'VarDecl' {
            text gty = cgLlvmType(resolveTypeField(d, 'type_expr'))
            if gty != '' {
                cgEmit(`@${rawText(d, 'name')} = global ${gty} ${cgZeroFor(gty)}`)
            }
        }
        g++
    }

    // Three blank lines: the globals, struct-definition and function
    // sections are each joined with a trailing blank, and all three are
    // empty for a program this simple. Emitted from that structure
    // rather than as a magic constant, so the shape stays honest when
    // the sections start carrying content.
    cgEmit('')
    cgEmit('')
    cgEmit('')

    cgEmit('define void @__festina_main() {')
    cgEmit('entry:')
    int s = 0
    while s < body.length {
        cgStmt(body[s])
        s++
    }
    cgEmit('  ret void')
    cgEmit('}')
    cgEmit('')
    cgEmit('define i32 @main(i32 %argc, ptr %argv_raw) {')
    cgEmit('entry:')
    cgEmit('  call void @festina_runtime_init()')
    cgEmit('  %argv_arr = call ptr @festina_argv_array(i32 %argc, ptr %argv_raw)')
    cgEmit('  store ptr %argv_arr, ptr @argv')
    cgEmit('  call void @__festina_main()')
    cgEmit('  ret i32 0')
    cgEmit('}')
    cgEmit('')
    int c = 0
    while c < CG_STRS.length {
        cgEmit(CG_STRS[c])
        c++
    }
}
