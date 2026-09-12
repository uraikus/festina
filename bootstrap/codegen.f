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
}

Val func cgVal(v:text, lty:text, fty:text) {
    Val r
    r.v = v
    r.lty = lty
    r.fty = fty
    return r
}

text func cgLtyOf(fty:text) {
    if fty == 'int' { return 'i64' }
    if fty == 'float' { return 'double' }
    if fty == 'bool' { return 'i8' }
    if fty == 'text' { return 'ptr' }
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

bool func cgIsLocal(name:text) {
    return L_SLOT[name] != null
}

text func cgSlotOf(name:text) {
    if L_SLOT[name] != null { return L_SLOT[name] }
    if G_SLOT[name] != null { return G_SLOT[name] }
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
    if CG_UNPORTED { return none }
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
        return cgVal(t, lty, fty)
    }

    if e.kind == 'BinOp' { return cgBinOp(e) }
    if e.kind == 'Call' { return cgCall(e, true) }

    cgUnported(`expression ${e.kind}`)
    return none
}

Val func cgBinOp(e:Node) {
    Val none
    Val l = cgExpr(childOf(e, 'left'))
    if CG_UNPORTED { return none }
    Val r = cgExpr(childOf(e, 'right'))
    if CG_UNPORTED { return none }
    text op = rawText(e, 'op')

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

    if op == '/' || op == '%' {
        cgUnported(`operator ${op}`)
        return none
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
        if CG_UNPORTED { return none }
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
    if CG_UNPORTED { return }
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
            cgUnported('declaration of a non-scalar type')
            return
        }
        text name = rawText(s, 'name')
        text lty = cgLtyOf(fty)
        // A local declaration allocates its own slot; a global's
        // storage was emitted with the module's globals.
        if cgSlotOf(name) == '' || cgIsLocal(name) == false {
            if CUR.length > 0 && G_SLOT[name] == null {
                text slot = `%${name}.${cgUid()}`
                cgOut(`  ${slot} = alloca ${lty}`)
                L_SLOT[name] = slot
                L_FTY[name] = fty
            }
        }
        Node init = childOf(s, 'init')
        if init == null { return }
        Val v = cgExpr(init)
        if CG_UNPORTED { return }
        if v.fty != fty && cgNumericPair(v.fty, fty) == false {
            cgUnported(`initializer of type ${v.fty} for ${fty}`)
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

// Runs a block's statements and leaves CG_TERM saying whether it ended
// in a terminator. Reset on entry so the answer is about THIS block and
// not one a sibling already closed.
void func cgBlockInto(b:Node) {
    CG_TERM = false
    arr[Node] body = cgBlockStmts(b)
    int i = 0
    while i < body.length {
        cgStmt(body[i])
        i++
    }
}

// An `if` always emits all three blocks, even with no `else` -- the
// else block then holds nothing but the branch to the end. Matching
// that matters: skipping it would renumber every label after it.
void func cgIf(s:Node) {
    Val c = cgExpr(childOf(s, 'test'))
    if CG_UNPORTED { return }
    text thenL = cgLabel('if.then')
    text elseL = cgLabel('if.else')
    text endL = cgLabel('if.end')
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${thenL}, label %${elseL}`)
    cgOut(`${thenL}:`)
    cgBlockInto(childOf(s, 'then'))
    if CG_TERM == false { cgOut(`  br label %${endL}`) }
    cgOut(`${elseL}:`)
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
    cgOut(`${endL}:`)
    // The end block itself falls through, so whatever follows the `if`
    // is reachable regardless of what the arms did.
    CG_TERM = false
}

// Labels in allocation order cond, body, update, end -- so the branch
// out of the condition names an `end` whose number is higher than the
// update block's.
void func cgFor(s:Node) {
    cgStmt(childOf(s, 'init'))
    if CG_UNPORTED { return }
    text condL = cgLabel('for.cond')
    text bodyL = cgLabel('for.body')
    text updateL = cgLabel('for.update')
    text endL = cgLabel('for.end')
    cgOut(`  br label %${condL}`)
    cgOut(`${condL}:`)
    Val c = cgExpr(childOf(s, 'test'))
    if CG_UNPORTED { return }
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${bodyL}, label %${endL}`)
    cgOut(`${bodyL}:`)
    cgBlockInto(childOf(s, 'body'))
    if CG_TERM == false { cgOut(`  br label %${updateL}`) }
    cgOut(`${updateL}:`)
    cgEvalForEffect(childOf(s, 'update'))
    cgOut(`  br label %${condL}`)
    cgOut(`${endL}:`)
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
    return ''
}

void func cgAssign(e:Node) {
    Node target = childOf(e, 'target')
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
    Val v = cgExpr(childOf(e, 'value'))
    if CG_UNPORTED { return }
    cgOut(`  store ${cgLtyOf(fty)} ${v.v}, ptr ${slot}`)
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
    cgOut(`${condL}:`)
    Val c = cgExpr(childOf(s, 'test'))
    if CG_UNPORTED { return }
    text t = cgTmp()
    cgOut(`  ${t} = icmp ne i8 ${c.v}, 0`)
    cgOut(`  br i1 ${t}, label %${bodyL}, label %${endL}`)
    cgOut(`${bodyL}:`)
    cgBlockInto(childOf(s, 'body'))
    if CG_TERM == false { cgOut(`  br label %${condL}`) }
    cgOut(`${endL}:`)
    CG_TERM = false
}

void func cgReturn(s:Node) {
    Node v = childOf(s, 'value')
    if v == null {
        cgOut('  ret void')
        CG_TERM = true
        return
    }
    Val r = cgExpr(v)
    if CG_UNPORTED { return }
    cgOut(`  ret ${r.lty} ${r.v}`)
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
    cgOut(`define ${retL} @${name}(${joined}) {`)
    cgOut(`${cgLabel('entry')}:`)
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

    // Every top-level declaration's storage, in source order.
    int g = 0
    while g < body.length {
        Node d = body[g]
        if d.kind == 'VarDecl' {
            text gf = cgDeclFty(d)
            if gf != '' {
                text gl = cgLtyOf(gf)
                cgEmit(`@${rawText(d, 'name')} = global ${gl} ${cgZeroFor(gl)}`)
                G_SLOT[rawText(d, 'name')] = `@${rawText(d, 'name')}`
                G_FTY[rawText(d, 'name')] = gf
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
            cgFunc(body[fb])
        }
        fb++
    }

    // Then main's own statements.
    CUR = CG_MAIN
    cgOut('define void @__festina_main() {')
    cgOut('entry:')
    CG_TERM = false
    int s = 0
    while s < body.length {
        cgStmt(body[s])
        s++
    }
    cgOut('  ret void')
    cgOut('}')
    cgOut('')
    cgOut('define i32 @main(i32 %argc, ptr %argv_raw) {')
    cgOut('entry:')
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
