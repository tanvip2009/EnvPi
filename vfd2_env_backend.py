"""EnvPilot VFD2 backend — recovery shim with Jenkins login behaviour overrides.

The original ``vfd2_env_backend.py`` source (last edited 2026-08-03) was lost when
the working tree was reverted with no git history, leaving this file empty. The
last fully-working build (2026-07-30, where Citrix + Jenkins login succeeded end
to end through the BuildNumber field) survives only as compiled bytecode in
``vfd2_env_backend_compiled.pyc``. No editable copy of that source exists
anywhere on this machine, and no reliable Python 3.13 decompiler is available.

This shim loads that bytecode, overrides two Jenkins helpers *before* running the
backend's entry point, then calls it. Python resolves globals at call time, so
replacing a name in the module namespace changes every internal caller too.

Overrides (see ``_apply_overrides``):

1. ``_jenkins_zoom`` caps cumulative zoom-OUT at ``ENVPILOT_MAX_ZOOM_OUT`` (6)
   steps per reset. Zoom-out is a legitimate fit mechanism — the login card can
   be taller than a short Edge viewport, and without it the form is unreachable.
   The bug was that it compounded: a failed login zoomed out 8 steps, Edge
   persisted that zoom per-site, and the next run started on a speck-sized page
   and zoomed out further still. The cap plus override 2 breaks the spiral while
   keeping the fit behaviour.
2. ``_jenkins_zoom_reset`` prepares Citrix/Edge input focus, tries Ctrl+0,
   verifies the page is no longer zoomed-out-to-fit (Welcome + Please sign in
   visible but Username absent), and falls back to Ctrl+mouse-wheel zoom-IN
   when keyboard reset does not reach the remote page through Citrix HDX.
3. ``_jenkins_reveal_label`` presses Escape first. A native <select> popup left
   open by a previous parameter attempt covers the form below it and can black
   out the window capture, which is why FOLDER_NAME and BuildNumber were
   reported as "label never found" while sitting below the fold. On failure it
   also dumps a screenshot, since the run that exposed this saved none past the
   params page and left the cause unprovable.
4. ``_jenkins_ocr_read`` warns when a capture yields zero tokens, to distinguish
   "text genuinely absent" from "window captured black".
5. ``_resize_jenkins_edge_window`` always applies ``SetWindowPos`` to the work
   area (the bytecode skipped resize when ``GetWindowRect`` already looked
   large enough, and logged the target size rather than the actual result).
   The override restores minimized/maximized windows first, verifies with
   ``GetWindowRect``, retries once, and warns when height stays far below target.

The window is deliberately NOT maximized. The bytecode documents that maximizing
the seamless Citrix Edge window makes Windows screen-capture return an all-black
frame (Citrix HDX capture protection), yielding 0 OCR tokens. It is instead sized
to fill the work area as a normal window, which keeps it capturable.
"""

import ctypes
import marshal
import os
import sys
import time
from ctypes import wintypes

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPILED = os.path.join(_HERE, "vfd2_env_backend_compiled.pyc")


def _apply_overrides(ns):
    """Replace Jenkins zoom helpers in the loaded backend namespace."""
    orig_zoom = ns.get("_jenkins_zoom")
    orig_reset = ns.get("_jenkins_zoom_reset")
    focus = ns.get("_jenkins_focus")
    release_mods = ns.get("_release_keyboard_modifiers")
    ensure_citrix = ns.get("_ensure_citrix_viewer_input_focus")
    find_desktop = ns.get("_find_kias_desktop_window_handle")
    ocr_read = ns.get("_jenkins_ocr_read")
    find_any = ns.get("_jenkins_find_any")
    try:
        max_zoom_out = int(os.environ.get("ENVPILOT_MAX_ZOOM_OUT", "6"))
    except ValueError:
        max_zoom_out = 6
    try:
        max_wheel_in = int(os.environ.get("ENVPILOT_MAX_ZOOM_IN", "8"))
    except ValueError:
        max_wheel_in = 8

    # Cumulative zoom-out steps since the last reset to 100%.
    budget = {"used": 0}

    def _arg(args, kwargs, index, name, default=None):
        if name in kwargs:
            return kwargs[name]
        if len(args) > index:
            return args[index]
        return default

    def _is_zoomed_out_to_fit(words):
        """True when the login card was shrunk to fit a short viewport."""
        if not words or not callable(find_any):
            return False
        found_user, _ = find_any(words, ["Username"])
        if found_user:
            return False
        found_welcome, _ = find_any(words, ["Welcome"])
        found_please, _ = find_any(words, ["Please sign in"])
        if not found_welcome or not found_please:
            return False
        return (
            found_welcome.get("cy", 1) < 0.88
            and found_please.get("cy", 1) < 0.88
        )

    def _read_login_words(edge_hwnd, logger):
        if not callable(ocr_read):
            return []
        try:
            _image, words = ocr_read(edge_hwnd, logger)
        except (TypeError, ValueError):
            return []
        return words or []

    def _prepare_remote_input(edge_hwnd, logger):
        if callable(release_mods):
            try:
                release_mods()
            except Exception:
                pass
        if callable(find_desktop) and callable(ensure_citrix):
            try:
                desktop_hwnd = find_desktop()
                if desktop_hwnd:
                    ensure_citrix(desktop_hwnd, logger)
            except Exception:
                pass
        if callable(focus) and edge_hwnd is not None:
            try:
                focus(edge_hwnd, logger)
            except Exception:
                pass

    if callable(orig_zoom):

        def _jenkins_zoom(*args, **kwargs):
            steps = _arg(args, kwargs, 2, "steps", 0)
            if steps is None or steps >= 0:
                return orig_zoom(*args, **kwargs)
            logger = _arg(args, kwargs, 1, "logger")
            remaining = max_zoom_out - budget["used"]
            if remaining <= 0:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: zoom-out of %d step(s) suppressed "
                        "(already %d/%d steps out)",
                        steps,
                        budget["used"],
                        max_zoom_out,
                    )
                return None
            allowed = -min(-steps, remaining)
            budget["used"] += -allowed
            if allowed != steps and logger is not None:
                logger.info(
                    "Jenkins OCR: zoom-out clamped from %d to %d step(s)",
                    steps,
                    allowed,
                )
            if "steps" in kwargs:
                kwargs["steps"] = allowed
                return orig_zoom(*args, **kwargs)
            new_args = list(args)
            new_args[2] = allowed
            return orig_zoom(*new_args, **kwargs)

        ns["_jenkins_zoom"] = _jenkins_zoom

    if callable(orig_reset):

        def _jenkins_zoom_reset(*args, **kwargs):
            edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
            logger = _arg(args, kwargs, 1, "logger")
            if edge_hwnd is None:
                return None

            _prepare_remote_input(edge_hwnd, logger)

            ctrl_ok = False
            for _ in range(3):
                try:
                    orig_reset(*args, **kwargs)
                except Exception:
                    pass
                time.sleep(0.5)
                if not _is_zoomed_out_to_fit(_read_login_words(edge_hwnd, logger)):
                    ctrl_ok = True
                    if logger is not None:
                        logger.info(
                            "Jenkins OCR: Ctrl+0 reset appears effective "
                            "(login card no longer zoomed-out-to-fit)"
                        )
                    break

            wheel_used = 0
            if not ctrl_ok and callable(orig_zoom):
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: Ctrl+0 did not restore zoom — "
                        "using Ctrl+wheel zoom-in fallback"
                    )
                wheel_budget = max(budget["used"], max_wheel_in)
                best_words = _read_login_words(edge_hwnd, logger)
                best_tokens = len(best_words)
                for _ in range(wheel_budget):
                    try:
                        orig_zoom(edge_hwnd, logger, 1)
                    except Exception:
                        break
                    wheel_used += 1
                    time.sleep(0.45)
                    words = _read_login_words(edge_hwnd, logger)
                    if not _is_zoomed_out_to_fit(words):
                        ctrl_ok = True
                        if logger is not None:
                            logger.info(
                                "Jenkins OCR: wheel zoom-in restored readable "
                                "zoom after %d step(s) (%d OCR tokens)",
                                wheel_used,
                                len(words),
                            )
                        break
                    if len(words) > best_tokens:
                        best_tokens = len(words)

            if ctrl_ok:
                budget["used"] = 0
            elif logger is not None:
                logger.warning(
                    "Jenkins OCR: zoom reset incomplete "
                    "(Ctrl+0 ineffective, wheel steps=%d) — "
                    "page may still be zoomed out from a prior run",
                    wheel_used,
                )
            return None

        ns["_jenkins_zoom_reset"] = _jenkins_zoom_reset

    orig_reveal = ns.get("_jenkins_reveal_label")
    send_escape = ns.get("_send_escape_key")
    ocr_dump = ns.get("_jenkins_ocr_dump")

    if callable(orig_reveal):

        def _jenkins_reveal_label(edge_hwnd, labels, logger):
            if callable(send_escape):
                try:
                    send_escape()
                except Exception:
                    pass
                time.sleep(0.3)
            result = orig_reveal(edge_hwnd, labels, logger)
            try:
                label, words = result
            except (TypeError, ValueError):
                return result
            if label is None:
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: label %s not revealed (%d OCR tokens) "
                        "— dumping screenshot and retrying",
                        labels,
                        len(words or []),
                    )
                if callable(ocr_dump):
                    try:
                        ocr_dump(edge_hwnd, "reveal_fail", logger)
                    except Exception:
                        pass
                result = orig_reveal(edge_hwnd, labels, logger)
            return result

        ns["_jenkins_reveal_label"] = _jenkins_reveal_label

    orig_read = ns.get("_jenkins_ocr_read")

    if callable(orig_read):

        def _jenkins_ocr_read(*args, **kwargs):
            result = orig_read(*args, **kwargs)
            try:
                _image, words = result
            except (TypeError, ValueError):
                return result
            if not words:
                logger = _arg(args, kwargs, 1, "logger")
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: capture returned 0 tokens "
                        "(window blacked out or occluded)"
                    )
            return result

        ns["_jenkins_ocr_read"] = _jenkins_ocr_read

    def _resize_jenkins_edge_window(edge_hwnd, logger):
        if sys.platform != "win32" or not edge_hwnd:
            return None
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        SPI_GETWORKAREA = 48
        try:
            rect = wintypes.RECT()
            if not user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
            ):
                rect.left = rect.top = 0
                rect.right = user32.GetSystemMetrics(0)
                rect.bottom = user32.GetSystemMetrics(1)
            target_w = rect.right - rect.left
            target_h = rect.bottom - rect.top

            def _actual_size():
                cur = wintypes.RECT()
                if not user32.GetWindowRect(edge_hwnd, ctypes.byref(cur)):
                    return None, None
                return cur.right - cur.left, cur.bottom - cur.top

            def _apply_resize():
                if user32.IsIconic(edge_hwnd) or user32.IsZoomed(edge_hwnd):
                    user32.ShowWindow(edge_hwnd, SW_RESTORE)
                    time.sleep(0.3)
                user32.SetWindowPos(
                    edge_hwnd,
                    0,
                    rect.left,
                    rect.top,
                    target_w,
                    target_h,
                    SWP_NOZORDER | SWP_SHOWWINDOW,
                )
                time.sleep(0.6)

            _apply_resize()
            aw, ah = _actual_size()
            if logger is not None:
                logger.info(
                    "Jenkins OCR: sized Edge window to %dx%d "
                    "(actual %dx%d, normal window, not maximized)",
                    target_w,
                    target_h,
                    aw or 0,
                    ah or 0,
                )
            if ah is not None and ah < target_h * 0.85:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: Edge window height short after resize "
                        "(%dx%d vs target %dx%d) — retrying once",
                        aw or 0,
                        ah,
                        target_w,
                        target_h,
                    )
                _apply_resize()
                aw, ah = _actual_size()
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: Edge window after retry: actual %dx%d "
                        "(target %dx%d)",
                        aw or 0,
                        ah or 0,
                        target_w,
                        target_h,
                    )
                if ah is not None and ah < target_h * 0.85:
                    if logger is not None:
                        logger.warning(
                            "Jenkins OCR: Edge window height %d is far below "
                            "target %d — Citrix session/viewer may be too "
                            "short; enlarge the remote desktop manually",
                            ah,
                            target_h,
                        )
        except Exception as exc:
            if logger is not None:
                logger.info("Jenkins OCR: resize attempt failed: %s", exc)
        return None

    ns["_resize_jenkins_edge_window"] = _resize_jenkins_edge_window


if not os.path.isfile(_COMPILED):
    sys.stderr.write(
        "vfd2_env_backend: compiled backend not found: {}\n".format(_COMPILED)
    )
    sys.exit(2)

with open(_COMPILED, "rb") as _fh:
    _fh.read(16)  # skip 16-byte CPython 3.7+ .pyc header before the code object
    _code = marshal.load(_fh)

# Execute under a non-"__main__" name so the bytecode's own
# ``if __name__ == "__main__": sys.exit(main())`` tail does not fire before the
# overrides are in place.
_globals = {
    "__name__": "vfd2_env_backend_compiled",
    "__file__": os.path.join(_HERE, "vfd2_env_backend.py"),
    "__builtins__": __builtins__,
}
exec(_code, _globals)

_apply_overrides(_globals)

_main = _globals.get("main")
if not callable(_main):
    sys.stderr.write("vfd2_env_backend: compiled backend exposes no main()\n")
    sys.exit(2)

sys.exit(_main())
