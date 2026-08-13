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
   dumps a screenshot and then rewinds to the top of the page before retrying:
   the original only ever scrolls DOWN, on the assumption that fields are filled
   top-to-bottom, so once the page slipped past ProjectName no amount of further
   scrolling could bring it back.
4. ``_jenkins_ocr_read`` warns when a capture yields zero tokens, to distinguish
   "text genuinely absent" from "window captured black".
5. ``_resize_jenkins_edge_window`` always applies ``SetWindowPos`` to the work
   area (the bytecode skipped resize when ``GetWindowRect`` already looked
   large enough, and logged the target size rather than the actual result).
   The override restores minimized/maximized windows first, verifies with
   ``GetWindowRect``, retries once, and warns when height stays far below target.
6. ``_run_jenkins_ocr_deploy`` maximizes the remote Edge window from inside the
   Citrix session (Win+Up) before the OCR flow starts. The Jenkins window is
   created with Ctrl+N on the user's existing remote Edge, so Chromium clones
   that window's geometry; when the source window is short the Jenkins window
   is short too, the login fields land below the fold, and the zoom-out-to-fit
   fallback fires. That is the root cause of the zoom-out, not a zoom bug.

7. ``_get_client_area_screen_point`` repairs a destroyed Edge window handle.
   Opening a native ``<select>`` in the seamless session destroys the local
   proxy window; Citrix creates a replacement with a different handle, but the
   old one stayed threaded through every later call, so ``GetWindowRect``
   returned 0x0 and every capture came back empty for the rest of the run.
   This is the one function capture, click and scroll all funnel through, so
   recovering the handle here fixes all three at once.
8. ``_jenkins_ocr_select_dropdown`` anchors its click to the label's LEFT edge
   rather than ``label_centre + 3%``, and dumps a screenshot when a field
   cannot be selected. Jenkins renders each ``<select>`` left-aligned under
   its label; a narrow one (ProjectName showing "OGW") is only ~3% wide, so
   the centre-plus-offset click landed past its right edge on blank page and
   the list never opened. Release_name is wide enough that the same offset
   landed inside it, which is why only the narrow selects failed.
   The first version of this override never actually took effect: it read
   ``label["left"]``, but ``_jenkins_reveal_label`` hands back the
   ``_jenkins_find_phrase`` centroid, which carries only ``cx``/``cy``. The
   resulting KeyError was swallowed and the label returned unchanged, so every
   run kept clicking centre+3% — confirmed by its log line being absent from
   every session log. ``_label_left_ratio`` now recovers the left edge from the
   raw OCR word dicts (which do keep pixel geometry) for the words on the
   label's own row, falls back to ``left`` and then to a ``cx`` estimate, and
   warns instead of failing silently when no geometry is usable.
   Two further corrections came out of the 12 Aug 10:56 run, where the log
   finally showed the anchor firing but still "via cx estimate": the word scan
   looked for ``top``/``height``/``left``, none of which this build's word
   dicts contain — they carry ``("text", "norm", "cx", "cy", "x1", "conf",
   "line")`` with no separate left/width keys. In this build ``x1`` is the
   word's *right* edge as a ratio (bytecode: ``x1 = (left + width) / img_w``),
   not the left edge the first override comment assumed; the true left edge is
   ``2*cx - x1`` (Aug 12 capture: ProjectName x1=0.265 matches pixel-right
   0.264, ``2*cx-x1``=0.216 matches pixel-left 0.217). A loose ``norm in w``
   row match also let sidebar tokens such as ``"a"`` match ``environmentname``
   and win ``min(lefts)``, producing the logged ``cx 0.259 -> 0.106`` without
   any x1 misread. Matching now requires length >= 3 for substring tests, and
   the left edge is recovered from ``2*cx - x1`` before the compiled ``+ 3%``
   click offset. The click also missed *vertically*: cf42a0a added
   ``ENVPILOT_DROPDOWN_CY_LIFT`` (default 0.01),
   subtracting it from the label ``cy`` before the compiled ``+ 4%`` click
   offset runs. That moved the aim *upward* toward the label text, but every
   ``<select>`` sits *below* its label — measured on four saved captures
   (1920x1116) the select centre is label_cy +0.030 to +0.036, while the lift
   pushed the pre-offset anchor to label_cy −0.01 (log line ``cy 0.344 ->
   0.334``). The compiled ``+ 4%`` only partly compensates; Release_name needs
   the largest gap (+0.036) and ProjectName's value is often too small for OCR,
   so a single constant misses it. The override now *drops* the anchor toward
   the select: it prefers a label-to-select offset derived from OCR words in the
   column below the label (e.g. ``4000_WAVE11_PCK1`` at cy 0.459 under
   Release_name), and falls back to ``ENVPILOT_DROPDOWN_CY_DROP`` (default
   0.031, the measured mean). ``ENVPILOT_DROPDOWN_CY_LIFT`` is still honoured
   when ``DROP`` is unset (``drop = 0.04 − lift``) so the previous aim can be
   restored. The shifted ``cy`` is clamped to 0.01..0.99 before ``+ 4%``.

9. ``_wait_for_mfa_verification`` polls for the Microsoft MFA page to appear
   before concluding it is absent. The original checked visibility once, about
   a second after Sign in was clicked — before Microsoft had rendered the
   approval page — so it logged "No MFA approval page detected", returned
   immediately, and never spent any of its 300-second approval budget. The
   sign-in then sat on an unapproved prompt until the Citrix workspace wait
   timed out, and the run failed before the NetScaler AD password was ever
   reached. Appearance budget: ``ENVPILOT_MFA_APPEAR_WAIT`` (default 25s).

10. ``_page_advanced_after_continue`` demands positive evidence that the Access
    Rules step really was accepted. Its last resort is
    ``_is_citrix_auth_progress_url(url)``, and ``CITRIX_AUTH_URL_MARKERS``
    contains ``deshpda.caas.vodafone.com``, ``logonpoint`` and ``/logon/`` —
    the Access Rules page's own address. Sitting ON the unaccepted page
    therefore reads as having advanced past it, unless
    ``_disclaimer_page_visible`` or the ``access rules`` page-source probe
    catches it first. On a cold Edge profile neither did: the page had not
    rendered that text yet, ``_access_rules_step_needed`` was told the step was
    already done, Continue was never clicked, the browser never reached the
    Microsoft SAML endpoint, and the 60s redirect wait plus the 45s
    email/password wait both expired against a URL that could never match
    ``login.microsoftonline.com`` — surfacing as an empty Selenium
    ``TimeoutException`` after ~128s. The override keeps every original signal
    and only vetoes the fallback while the browser is still on the logon point
    with no Microsoft form, no AD Password field and no loaded Workspace. Set
    ``ENVPILOT_STRICT_ACCESS_RULES=0`` to restore the original behaviour.

11. ``_enable_per_monitor_dpi_awareness`` (import time, not an override) makes
    the process report window geometry in physical pixels. Without it Windows
    scales ``GetWindowRect`` and ``SPI_GETWORKAREA`` down by the display
    scaling factor while screen capture stays physical, so at 175% a
    full-screen remote Edge measured 1097x638 against a 1920x1200 screen.
    The OCR bbox then captured only the top-left 57% of the window, the
    Jenkins login card sat clipped in the bottom-right corner of every
    capture, and the Username click — computed as a ratio of that crop —
    landed on the label instead of the input, retried 18 times and gave up
    with "box click kept missing". The same mismatch made resizing look
    inert, since the work-area target was measured in the same shrunken
    units the window already matched. Set ``ENVPILOT_DPI_AWARE=0`` to restore
    the original behaviour.

12. ``_jenkins_focus`` sends Escape after the original runs, to close the Edge
    menu its own Alt tap opens. The bytecode taps Alt
    (``keybd_event(18, 0, 0, 0)`` then key-up) before ``BringWindowToTop`` and
    ``SetForegroundWindow`` to defeat the Windows foreground lock, but
    Chromium treats a bare Alt press-and-release as the menu accelerator, so
    every focus call opened the "Settings and more" menu inside the Citrix
    session — visible as the three-dots button lighting up before each field.
    An open menu owns the keyboard, so the dropdown filter text and the
    arrow-scan's Down/Up keys went to the menu rather than the page: selects
    never opened and the page appeared to scroll by itself, which is the
    "ProjectName not verified" / "could not select" loop. The Escape already
    in ``_jenkins_reveal_label`` fires before the reveal, and the Alt tap
    happens inside it, so it can never close this menu.

13. ``_capture_viewer_client_image`` renders the target window itself with
    ``PrintWindow(PW_RENDERFULLCONTENT)`` instead of photographing its screen
    rectangle. The bytecode calls ``ImageGrab.grab(bbox=...)`` on the client
    rectangle, which returns whatever pixels happen to be on that part of the
    desktop, so any window covering the session is OCR'd in its place — an
    editor window was captured and read as if it were the Jenkins page, and a
    minimized window (rectangle collapsed to 160x28 at -32000) returns a black
    strip. Measured on four live seamless windows: the screen grab captured
    the covering window every time, while ``PrintWindow`` returned the
    target's own content, including for the minimized one. The result is
    cropped back to the client rectangle so its size and origin are identical
    to the grab and every OCR ratio stays valid. A minimized window is first
    restored with ``SW_SHOWNOACTIVATE``, which un-minimizes it without taking
    focus, because a minimized window has no full-size pixels to render. If
    ``PrintWindow`` yields nothing or a blank frame (HDX may refuse to render
    the seamless proxy) the original grab is used, and the log says whether
    another window was on top at the time. Disable with
    ``ENVPILOT_WINDOW_CAPTURE=0``, keep windows untouched with
    ``ENVPILOT_RESTORE_MINIMIZED=0``.

14. ``_jenkins_click_build_submit`` runs a pre-submit OCR guard immediately
    before the Build click. The 12 Aug 13:22 deploy logged
    ``could not select ENVIRONMENT_NAME = SIT5``, captured
    ``jenkins_before_build_133110.png`` with ``sitiv`` (garbled ``SIT1``) still
    on screen, then logged ``Build submitted.`` anyway — a real deploy went to
    the wrong environment while the GUI reported success. Dropdown failures were
    only WARNED and their return values discarded in
    ``_fill_jenkins_ocr_parameters``; nothing re-checked the page before
    ``_jenkins_click_build_submit``. The guard hooks that gap: after the
    existing ``before_build`` dump (same function, next call is submit) it
    re-reads every non-empty GUI parameter and aborts with ``RuntimeError`` on
    any definite mismatch. Policy: (a) if OCR reads a value that clearly does
    not match the request (e.g. requested ``SIT5``, observed ``sitiv`` /
    ``SIT1``), abort; (b) if the select box is unreadable (common for narrow
    ``ProjectName``), crop the region below the label (left edge + ~0.031 cy
    offset, matching override 8) and re-OCR before deciding; (c) if still
    inconclusive, allow submit — OCR silence is not proof of a wrong value, and
    the 12 Aug run showed ``ProjectName`` verification failed while ``OGW`` was
    genuinely on screen. Fields that logged ``could not select`` are still
    checked, but only abort when a conflicting value is read, not when the
    token is absent. ``SIT*`` environment tokens apply a small garble normalizer
    (``sitiv`` → ``sit1``) so a matching environment is not rejected. Set
    ``ENVPILOT_ABORT_ON_PARAM_MISMATCH=0`` to restore submit-anyway behaviour.

15. ``_find_jenkins_edge_hwnd`` refuses to hand back a different application's
    Citrix Edge window, and the capture refuses to OCR a covering window. The
    compiled finder prefers a ``JENKINS``/``DEOSSKVR``/``SIGN IN`` title but
    falls back to *any* seamless Edge window when none matches. A native
    ``<select>`` popup destroys the Jenkins proxy window for a moment, so on
    13 Aug 08:54:43 that fallback fired mid-dropdown and returned the user's
    unrelated session::

        no Jenkins-titled window; using seamless Edge 'OSF - Profile 1 - ...'
        Edge handle 333492 was destroyed ... — recovered handle 6032946

    The substituted handle stays alive forever, so it was cached and every
    later click, retry and verification landed on an unrelated customer-search
    page: the ``ENVIRONMENT_NAME`` "dropdown failure" screenshot is
    ``osf-telesales-sit2.vodafone.de`` showing ``Kundensuche``, not Jenkins.
    That is why dropdowns failed while text fields worked — only ``<select>``
    popups recreate the window. The override now waits up to
    ``ENVPILOT_JENKINS_WINDOW_WAIT`` seconds (default 20) for the real window
    to return, discards an already-cached non-Jenkins handle, and gives up
    rather than driving the wrong application. Strictness only engages once a
    Jenkins-titled window has been seen, so the initial resolution — when the
    remote Edge window is still on its start page — is unchanged. The capture
    fallback in override 13 has the same failure: with ``PrintWindow``
    returning nothing it screen-grabbed the rectangle and OCR'd whatever
    covered it, which on 13 Aug 08:55:28 was the editor window
    (``'EnvPi'``, ``'repository'``, ``'launch'``, ``'app'`` among 323 tokens).
    It now returns ``None`` when another window is on top, which
    ``_jenkins_ocr_read`` already turns into ``(None, [])`` for the callers to
    retry. Set ``ENVPILOT_STRICT_JENKINS_WINDOW=0`` to restore the old
    substitute-anything behaviour.

Host-side ``SetWindowPos`` cannot fix the height: it moves the seamless proxy
window only, so the resize logs ``actual 1920x1032`` while the capture the OCR
pipeline receives stays 1920x569. Only the remote window manager can resize the
window Edge actually paints into, hence Win+Up.

``ShowWindow(SW_MAXIMIZE)`` remains off-limits — the bytecode documents that
this host-side call pushes the seamless Edge window into a compositor state
where Windows screen-capture returns an all-black frame (Citrix HDX capture
protection), yielding 0 OCR tokens. Win+Up goes through the remote window
manager instead, and override 6 verifies the capture afterwards and undoes it
with Win+Down if the frame comes back black. Once an in-session maximize
succeeds, override 5 stands down so host-side resizing cannot undo it.
"""

import ctypes
import difflib
import marshal
import os
import re
import sys
import time
from ctypes import wintypes

try:
    from PIL import Image
except ImportError:
    # Only the window-owned capture below needs PIL directly; without it that
    # override stands down and the compiled ImageGrab path is used unchanged.
    Image = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPILED = os.path.join(_HERE, "vfd2_env_backend_compiled.pyc")


def _enable_per_monitor_dpi_awareness():
    """Make this process report window geometry in physical pixels.

    Windows lies to DPI-unaware processes: GetWindowRect and SPI_GETWORKAREA
    come back in logical (scale-divided) pixels, while a desktop screen grab
    always returns physical ones. At 175% scaling that is a 1.75x discrepancy,
    so a full-screen remote Edge window measured 1097x638 while the screen it
    occupied was 1920x1200. Every capture bbox then cropped the top-left 57%
    of the window, every OCR ratio was computed against that crop, and the
    Jenkins Username click landed far up and left of the real box — the
    "box click kept missing" failure. It also made the resize look inert
    ("capture stuck at 1097x638"), because the target work area was measured
    in the same shrunken units the window already matched.

    Declaring per-monitor awareness puts both sides in physical pixels, which
    is the coordinate space screen capture and SetCursorPos already use.
    Must run before any window or DC is touched, hence at import time. Set
    ``ENVPILOT_DPI_AWARE=0`` to restore the previous behaviour.
    """
    if sys.platform != "win32":
        return None
    if os.environ.get("ENVPILOT_DPI_AWARE", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return "disabled"

    # PER_MONITOR_AWARE_V2, Windows 10 1703+.
    try:
        set_ctx = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_ctx.argtypes = [ctypes.c_void_p]
        set_ctx.restype = ctypes.c_bool
        if set_ctx(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass

    # PROCESS_PER_MONITOR_DPI_AWARE, Windows 8.1+. S_OK == 0.
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass

    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass

    return None


_DPI_MODE = _enable_per_monitor_dpi_awareness()

# Jenkins Build-with-Parameters field layout (mirrors compiled constants).
_JENKINS_PARAM_DROPDOWNS = (
    ("ProjectName", ("ProjectName",)),
    ("Release_name", ("Release_name", "Release")),
    ("ENVIRONMENT_NAME", ("ENVIRONMENT_NAME", "ENVIRONMENT")),
)
_JENKINS_PARAM_TEXTFIELDS = (
    ("FOLDER_NAME", ("FOLDER_NAME", "FOLDER")),
    ("BuildNumber", ("BuildNumber",)),
)
_PARAM_VERIFY_LABEL_CONF = 15
_PARAM_BAND_TOP = 0.012
_PARAM_BAND_BOTTOM = 0.065
_PARAM_SELECT_OFFSET = 0.031
_PARAM_MATCH_RATIO = 0.92
_PARAM_MISMATCH_RATIO = 0.85
_SIT_ENV_RE = re.compile(r"^sit(\d+)$", re.IGNORECASE)


def _param_verify_enabled():
    return os.environ.get("ENVPILOT_ABORT_ON_PARAM_MISMATCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _ocr_words_from_image(image, ocr_tesseract_data, normalize_ocr, logger):
    """Build the same word dict list ``_jenkins_ocr_read`` uses, from a PIL image."""
    data = ocr_tesseract_data(image, logger)
    if not data:
        return []
    width, height = image.size
    words = []
    for index, raw in enumerate(data.get("text") or []):
        text = (raw or "").strip()
        if not text:
            continue
        box_w = int(data["width"][index])
        box_h = int(data["height"][index])
        if box_w <= 0 or box_h <= 0:
            continue
        left = int(data["left"][index])
        top = int(data["top"][index])
        conf_raw = data.get("conf", [0])
        conf = int(float(conf_raw[index] if index < len(conf_raw) else 0) or 0)
        words.append(
            {
                "text": text,
                "norm": normalize_ocr(text),
                "cx": (left + box_w / 2) / width,
                "cy": (top + box_h / 2) / height,
                "x1": (left + box_w) / width,
                "conf": conf,
                "line": int(data.get("line_num", [0])[index] or 0),
            }
        )
    return words


def _param_label_left_ratio(label, words, labels, normalize_ocr):
    label_cy = label.get("cy")
    if label_cy is None:
        return None
    wanted = {normalize_ocr(text) for text in labels if text}
    wanted.discard("")
    lefts = []
    for word in words:
        try:
            row_cy = float(word["cy"])
            x1 = float(word["x1"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(row_cy - float(label_cy)) > 0.015:
            continue
        norm = word.get("norm") or normalize_ocr(word.get("text") or "")
        if norm and any(
            norm == w
            or (len(norm) >= 3 and norm in w)
            or (len(w) >= 3 and w in norm)
            for w in wanted
        ):
            cx = float(word["cx"])
            left_edge = max(0.0, 2.0 * cx - (x1 if x1 <= 1.0 else x1))
            lefts.append(left_edge)
    if lefts:
        return min(lefts)
    if label.get("cx") is not None:
        try:
            return max(0.0, float(label["cx"]) - 0.03)
        except (TypeError, ValueError):
            pass
    return None


def _param_band_words(words, label, labels, normalize_ocr):
    label_cy = label.get("cy")
    if label_cy is None:
        return []
    left_ratio = _param_label_left_ratio(label, words, labels, normalize_ocr)
    lo_cy = float(label_cy) + _PARAM_BAND_TOP
    hi_cy = float(label_cy) + _PARAM_BAND_BOTTOM
    band = []
    for word in words:
        try:
            cy = float(word["cy"])
            cx = float(word["cx"])
            conf = int(word.get("conf") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if conf < _PARAM_VERIFY_LABEL_CONF:
            continue
        if cy < lo_cy or cy > hi_cy:
            continue
        if left_ratio is not None and not (left_ratio - 0.02 <= cx <= left_ratio + 0.28):
            continue
        band.append(word)
    return sorted(band, key=lambda w: (w["cy"], w["cx"]))


def _param_join_band(band):
    return "".join(w.get("text") or "" for w in band).strip()


def _param_normalize_sit_garble(norm):
    """Map common OCR garble on Jenkins SIT environment selects (sitiv -> sit1)."""
    lowered = (norm or "").lower()
    if not lowered.startswith("sit"):
        return lowered
    tail = lowered[3:]
    digit_run = re.sub(r"[^0-9]", "", tail)
    if digit_run:
        return "sit" + digit_run
    if tail.endswith(("v", "l")) or tail in ("iv", "1v", "i"):
        return "sit1"
    return lowered


def _param_values_match(observed_norm, requested_norm, normalize_ocr):
    observed = (observed_norm or "").strip()
    requested = normalize_ocr(requested_norm or "")
    if not requested:
        return True
    if not observed:
        return False
    observed = normalize_ocr(observed)
    if observed == requested:
        return True
    if difflib.SequenceMatcher(None, observed, requested).ratio() >= _PARAM_MATCH_RATIO:
        return True
    if len(requested) >= 4 and (requested in observed or observed in requested):
        return True
    req_sit = _SIT_ENV_RE.match(requested)
    if req_sit:
        obs_sit = _SIT_ENV_RE.match(_param_normalize_sit_garble(observed))
        if obs_sit and obs_sit.group(1) == req_sit.group(1):
            return True
    return False


def _param_confident_mismatch(observed_norm, requested_norm, normalize_ocr):
    observed = normalize_ocr(observed_norm or "")
    requested = normalize_ocr(requested_norm or "")
    if not observed or not requested:
        return False, observed
    if _param_values_match(observed, requested, normalize_ocr):
        return False, observed
    req_sit = _SIT_ENV_RE.match(requested)
    if req_sit:
        obs_sit = _SIT_ENV_RE.match(_param_normalize_sit_garble(observed))
        if obs_sit and obs_sit.group(1) != req_sit.group(1):
            return True, observed
        if obs_sit and obs_sit.group(1) == req_sit.group(1):
            return False, observed
    if len(observed) >= 3 and difflib.SequenceMatcher(
        None, observed, requested
    ).ratio() < _PARAM_MISMATCH_RATIO:
        return True, observed
    if len(observed) >= 2 and observed != requested:
        return True, observed
    return False, observed


def _param_crop_reread(image, label, labels, normalize_ocr, ocr_tesseract_data, logger):
    left_ratio = _param_label_left_ratio(
        label, _ocr_words_from_image(image, ocr_tesseract_data, normalize_ocr, logger),
        labels,
        normalize_ocr,
    )
    try:
        label_cy = float(label["cy"])
    except (KeyError, TypeError, ValueError):
        return []
    width, height = image.size
    anchor_left = left_ratio
    if anchor_left is None:
        try:
            anchor_left = max(0.0, float(label.get("cx", 0.2)) - 0.03)
        except (TypeError, ValueError):
            anchor_left = 0.2
    x0 = int(max(0, (anchor_left - 0.01) * width))
    y0 = int(max(0, (label_cy + _PARAM_BAND_TOP) * height))
    x1 = int(min(width, x0 + 0.35 * width))
    y1 = int(min(height, y0 + 0.055 * height))
    if x1 <= x0 or y1 <= y0:
        return []
    crop = image.crop((x0, y0, x1, y1))
    return _ocr_words_from_image(crop, ocr_tesseract_data, normalize_ocr, logger)


def _param_verify_field(
    words,
    image,
    field_key,
    labels,
    requested,
    had_select_failure,
    find_any,
    normalize_ocr,
    ocr_tesseract_data,
    logger,
):
    """Return (status, observed_display) where status is ok|mismatch|inconclusive."""
    if not requested:
        return "ok", requested
    label = None
    if callable(find_any):
        try:
            label, _which = find_any(
                words, labels, min_conf=_PARAM_VERIFY_LABEL_CONF
            )
        except TypeError:
            label, _which = find_any(words, labels)
    if not label:
        return "inconclusive", None

    band = _param_band_words(words, label, labels, normalize_ocr)
    observed_raw = _param_join_band(band)
    observed_norm = normalize_ocr(observed_raw) if observed_raw else ""

    if _param_values_match(observed_norm, requested, normalize_ocr):
        return "ok", observed_raw or requested

    mismatch, mismatch_obs = _param_confident_mismatch(
        observed_norm, requested, normalize_ocr
    )
    if mismatch:
        return "mismatch", mismatch_obs or observed_raw

    crop_words = _param_crop_reread(
        image, label, labels, normalize_ocr, ocr_tesseract_data, logger
    )
    crop_observed = _param_join_band(
        sorted(crop_words, key=lambda w: (w["cy"], w["cx"]))
    )
    crop_norm = normalize_ocr(crop_observed) if crop_observed else ""

    if _param_values_match(crop_norm, requested, normalize_ocr):
        return "ok", crop_observed or requested

    mismatch2, mismatch_obs2 = _param_confident_mismatch(
        crop_norm, requested, normalize_ocr
    )
    if mismatch2:
        return "mismatch", mismatch_obs2 or crop_observed

    if had_select_failure and (observed_norm or crop_norm):
        return "mismatch", observed_raw or crop_observed or observed_norm

    return "inconclusive", observed_raw or crop_observed or None


def _collect_param_requests(deploy_form):
    requests = []
    form = deploy_form or {}
    for field_key, labels in _JENKINS_PARAM_DROPDOWNS + _JENKINS_PARAM_TEXTFIELDS:
        value = str(form.get(field_key) or "").strip()
        if value:
            requests.append((field_key, labels, value))
    return requests


def _verify_jenkins_params_before_submit(
    image,
    words,
    deploy_form,
    select_failures,
    find_any,
    normalize_ocr,
    ocr_tesseract_data,
    logger,
):
    """Raise RuntimeError when any requested parameter clearly mismatches the screen."""
    mismatches = []
    for field_key, labels, requested in _collect_param_requests(deploy_form):
        status, observed = _param_verify_field(
            words,
            image,
            field_key,
            labels,
            requested,
            field_key in (select_failures or set()),
            find_any,
            normalize_ocr,
            ocr_tesseract_data,
            logger,
        )
        if status == "mismatch":
            mismatches.append((field_key, requested, observed))
        elif logger is not None and status == "inconclusive":
            logger.info(
                "Jenkins OCR: pre-submit check inconclusive for %s "
                "(requested %s%s) — OCR could not read the control; "
                "allowing submit",
                field_key,
                requested,
                " after could-not-select" if field_key in (select_failures or set()) else "",
            )

    if not mismatches:
        return

    parts = []
    for field_key, requested, observed in mismatches:
        if observed:
            parts.append(
                "{}: requested {}, observed {}".format(field_key, requested, observed)
            )
        else:
            parts.append(
                "{}: requested {}, could not read on-screen value".format(
                    field_key, requested
                )
            )
    message = (
        "Jenkins build aborted: parameter mismatch before submit — "
        + "; ".join(parts)
        + ". Fix the field(s) on the Jenkins page or set "
        "ENVPILOT_ABORT_ON_PARAM_MISMATCH=0 to restore submit-anyway "
        "behaviour (not recommended)."
    )
    if logger is not None:
        logger.error(message)
    raise RuntimeError(message)


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
    # Set once the remote window has been maximized from inside the session,
    # after which host-side SetWindowPos must stand down (see below).
    remote_state = {"maximized": False, "tried": False}

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
    scroll_to_top = ns.get("_jenkins_scroll_to_top")

    orig_focus = ns.get("_jenkins_focus")

    if callable(orig_focus) and callable(send_escape):

        def _jenkins_focus(*args, **kwargs):
            """Close the Edge menu that the foreground-lock Alt tap opens.

            ``_jenkins_focus`` taps Alt (``keybd_event(18, ...)`` down then up)
            before ``BringWindowToTop``/``SetForegroundWindow``, the usual way
            to defeat the Windows foreground lock. Chromium reads a bare Alt
            press-and-release as the menu accelerator, so every focus call also
            opens Edge's "Settings and more" menu inside the Citrix session.
            While that menu is open it owns the keyboard: the typed filter text
            and the Down/Up arrows of the dropdown arrow-scan go to the menu
            instead of the page, so parameter selects never open and the page
            appears to scroll on its own. The Alt tap is still needed for
            focus, so the menu is dismissed immediately afterwards instead.

            The Escape in ``_jenkins_reveal_label`` cannot cover this: it fires
            *before* the reveal, and the Alt tap happens inside it.
            """
            result = orig_focus(*args, **kwargs)
            try:
                send_escape()
            except Exception:
                pass
            time.sleep(0.15)
            return result

        ns["_jenkins_focus"] = _jenkins_focus
        # _prepare_remote_input captured the original before this point.
        focus = _jenkins_focus

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
                # The original only ever scrolls DOWN, so a label that has moved
                # above the viewport is unreachable. Rewind to the top so the
                # downward scan can find it again.
                if callable(scroll_to_top):
                    try:
                        scroll_to_top(edge_hwnd, logger)
                        time.sleep(0.5)
                        if logger is not None:
                            logger.info(
                                "Jenkins OCR: scrolled back to top before "
                                "retrying label %s",
                                labels,
                            )
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
            try:
                live["img_w"] = int(_image.size[0])
                live["img_h"] = int(_image.size[1])
            except Exception:
                pass
            if not words:
                logger = _arg(args, kwargs, 1, "logger")
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: capture returned 0 tokens "
                        "(window blacked out or occluded)"
                    )
            return result

        ns["_jenkins_ocr_read"] = _jenkins_ocr_read

    capture_image = ns.get("_capture_viewer_client_image")
    orig_ocr_deploy = ns.get("_run_jenkins_ocr_deploy")
    orig_client_point = ns.get("_get_client_area_screen_point")
    find_edge_hwnd = ns.get("_find_jenkins_edge_hwnd")

    # Last handle known to be alive, and the last logger seen, so the handle
    # recovery below can report itself even from call sites that take no logger.
    live = {
        "hwnd": None,
        "logger": None,
        "recoveries": 0,
        "deploy_form": None,
        "param_select_failures": set(),
        "jenkins_title": None,
        "strict_giveup_until": 0.0,
    }

    # Observed Jenkins window titles: 'Sign in [Jenkins] - ... - \\Remote' and
    # '<job> [Jenkins] - ... - \\Remote'. DEOSSKVR is the Jenkins host, which
    # is what the title shows while a tab is still loading.
    _JENKINS_TITLE_MARKERS = ("JENKINS", "DEOSSKVR")

    strict_jenkins_window = os.environ.get(
        "ENVPILOT_STRICT_JENKINS_WINDOW", "1"
    ).strip().lower() not in ("0", "false", "no")
    try:
        jenkins_window_wait = float(
            os.environ.get("ENVPILOT_JENKINS_WINDOW_WAIT", "20")
        )
    except ValueError:
        jenkins_window_wait = 20.0

    def _window_title_upper(hwnd):
        # Resolved from ns per call, so this always agrees with the title the
        # compiled finder itself saw.
        get_title = ns.get("_get_window_title")
        if not hwnd or not callable(get_title):
            return ""
        try:
            title = get_title(hwnd) or ""
        except Exception:
            return ""
        normalize = ns.get("_normalize_window_title")
        if callable(normalize):
            try:
                title = normalize(title) or title
            except Exception:
                pass
        return title.upper()

    def _is_jenkins_window(hwnd):
        upper = _window_title_upper(hwnd)
        if not upper:
            return False
        return any(marker in upper for marker in _JENKINS_TITLE_MARKERS)

    def _seen_jenkins_window():
        return live.get("jenkins_title") is not None

    def _find_jenkins_window_strict(logger=None):
        """Resolve the Jenkins window, never a different app's Edge window.

        The compiled finder prefers a JENKINS/DEOSSKVR/SIGN IN title but, when
        none is present, returns *any* Citrix seamless Edge window. A native
        <select> popup destroys the Jenkins proxy window for a moment, and in
        that window of time the fallback handed back a colleague application
        (``OSF - Profile 1 - Microsoft Edge - \\\\Remote``), which was then
        cached and driven for the rest of the run. Wait for the real window to
        come back instead; it is only gone while the popup is up.
        """
        if logger is not None:
            live["logger"] = logger
        if not callable(orig_find_edge_hwnd):
            return None
        # _get_client_area_screen_point runs this on every click, scroll and
        # capture, so a real outage must not cost the full wait each time.
        if time.time() < live.get("strict_giveup_until", 0.0):
            return None
        deadline = time.time() + jenkins_window_wait
        warned = False
        while True:
            try:
                candidate = orig_find_edge_hwnd(logger)
            except Exception:
                candidate = None
            if candidate and _is_jenkins_window(candidate):
                live["jenkins_title"] = _window_title_upper(candidate)
                live["strict_giveup_until"] = 0.0
                return candidate
            # Before Jenkins has ever been on screen the remote Edge window is
            # still on its start page, so the permissive choice is all there is.
            if not strict_jenkins_window or not _seen_jenkins_window():
                return candidate
            if logger is not None and not warned:
                warned = True
                logger.warning(
                    "Jenkins OCR: refusing to drive %r - it is not the Jenkins "
                    "window; waiting up to %.0fs for the Jenkins window to "
                    "come back (a native dropdown popup recreates it)",
                    _window_title_upper(candidate) or candidate,
                    jenkins_window_wait,
                )
            if time.time() >= deadline:
                live["strict_giveup_until"] = time.time() + jenkins_window_wait
                if logger is not None:
                    logger.error(
                        "Jenkins OCR: Jenkins window did not reappear within "
                        "%.0fs; refusing to fall back to another application's "
                        "Citrix Edge window",
                        jenkins_window_wait,
                    )
                return None
            time.sleep(0.5)

    orig_find_edge_hwnd = find_edge_hwnd
    if callable(orig_find_edge_hwnd):
        ns["_find_jenkins_edge_hwnd"] = _find_jenkins_window_strict
        find_edge_hwnd = _find_jenkins_window_strict

    def _live_hwnd(hwnd):
        """Return a valid Edge handle, re-resolving if the given one is dead.

        Interacting with a native <select> in the seamless Citrix session
        destroys the local proxy window and Citrix builds a new one with a
        different handle. Every later call still carried the old handle, so
        GetWindowRect returned 0x0, every capture came back empty, and the
        flow spent minutes scrolling for labels it could no longer see. The
        handle is threaded by value through the whole call chain, so it is
        repaired here: _get_client_area_screen_point is the one function that
        capture, click and scroll all go through.
        """
        if sys.platform != "win32":
            return hwnd
        user32 = ctypes.windll.user32
        if hwnd and user32.IsWindow(hwnd):
            live["hwnd"] = hwnd
            return hwnd
        if live["hwnd"] and user32.IsWindow(live["hwnd"]):
            # A handle recovered before this guard existed could be another
            # application's window, and it stays alive forever, so the run
            # never finds its way back to Jenkins. Drop it and re-resolve.
            if (
                strict_jenkins_window
                and _seen_jenkins_window()
                and not _is_jenkins_window(live["hwnd"])
            ):
                logger = live["logger"]
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: discarding cached handle %r (%r) - not "
                        "the Jenkins window",
                        live["hwnd"],
                        _window_title_upper(live["hwnd"]),
                    )
                live["hwnd"] = None
            else:
                return live["hwnd"]
        if not callable(find_edge_hwnd):
            return hwnd
        try:
            fresh = find_edge_hwnd(live["logger"])
        except Exception:
            return hwnd
        if fresh and user32.IsWindow(fresh):
            live["hwnd"] = fresh
            live["recoveries"] += 1
            logger = live["logger"]
            if logger is not None and live["recoveries"] <= 5:
                logger.warning(
                    "Jenkins OCR: Edge handle %r was destroyed (likely a "
                    "native dropdown popup recreating the Citrix seamless "
                    "window) — recovered handle %r",
                    hwnd,
                    fresh,
                )
            return fresh
        return hwnd

    if callable(orig_client_point):

        def _get_client_area_screen_point(hwnd, rel_x_ratio, rel_y_ratio):
            return orig_client_point(_live_hwnd(hwnd), rel_x_ratio, rel_y_ratio)

        ns["_get_client_area_screen_point"] = _get_client_area_screen_point

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    def _is_uniform(image):
        """True when every channel is a single value, i.e. a blank/black frame."""
        try:
            return all(lo == hi for lo, hi in image.getextrema())
        except (TypeError, ValueError):
            return False

    def _print_window_client_image(hwnd):
        """Capture the window's OWN pixels, cropped to its client area.

        ``PrintWindow`` asks the window to render itself into a bitmap, so the
        result is the target's content even when another window covers it. The
        crop back to the client rectangle keeps the returned image the same
        size and origin as the ImageGrab path, which every OCR ratio and click
        coordinate is computed against.
        """
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        win = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return None
        win_w = win.right - win.left
        win_h = win.bottom - win.top
        if win_w <= 0 or win_h <= 0:
            return None

        client = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            return None
        client_w = client.right - client.left
        client_h = client.bottom - client.top
        if client_w <= 0 or client_h <= 0:
            return None

        origin = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None

        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        mem_dc = bitmap = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(hdc)
            bitmap = gdi32.CreateCompatibleBitmap(hdc, win_w, win_h)
            if not mem_dc or not bitmap:
                return None
            gdi32.SelectObject(mem_dc, bitmap)
            # PW_RENDERFULLCONTENT (2) is what makes this work for Chromium.
            if not user32.PrintWindow(hwnd, mem_dc, 2):
                return None

            header = _BitmapInfoHeader()
            header.biSize = ctypes.sizeof(_BitmapInfoHeader)
            header.biWidth = win_w
            header.biHeight = -win_h  # top-down rows
            header.biPlanes = 1
            header.biBitCount = 32
            buffer = ctypes.create_string_buffer(win_w * win_h * 4)
            if not gdi32.GetDIBits(
                mem_dc, bitmap, 0, win_h, buffer, ctypes.byref(header), 0
            ):
                return None

            image = Image.frombuffer(
                "RGBA", (win_w, win_h), buffer, "raw", "BGRA", 0, 1
            ).convert("RGB")
        finally:
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(hwnd, hdc)

        off_x = origin.x - win.left
        off_y = origin.y - win.top
        if off_x < 0 or off_y < 0:
            return None
        if off_x + client_w > win_w or off_y + client_h > win_h:
            return None
        return image.crop((off_x, off_y, off_x + client_w, off_y + client_h))

    def _topmost_root_at_centre(hwnd):
        """Root window actually drawn at the centre of hwnd's rectangle."""
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        point = wintypes.POINT(
            (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        )
        top = user32.WindowFromPoint(point)
        if not top:
            return None
        return user32.GetAncestor(top, 2)  # GA_ROOT

    if callable(capture_image) and Image is not None:
        window_capture_enabled = os.environ.get(
            "ENVPILOT_WINDOW_CAPTURE", "1"
        ).strip().lower() not in ("0", "false", "no")
        restore_minimized = os.environ.get(
            "ENVPILOT_RESTORE_MINIMIZED", "1"
        ).strip().lower() not in ("0", "false", "no")
        orig_capture = capture_image
        capture_state = {"printed": 0, "grabbed": 0, "occluded": 0, "restored": 0}

        def _capture_viewer_client_image(desktop_hwnd):
            """Read the target window's own pixels, not whatever is on top.

            The compiled version screenshots the client rectangle off the
            desktop with ``ImageGrab.grab(bbox=...)``, so any window covering
            that region is what gets OCR'd — a Teams or Cursor window has been
            captured and read as if it were the Jenkins page. A minimized
            window is worse: its rectangle collapses to 160x28 at -32000, so
            the grab returns a black strip.
            """
            logger = live.get("logger")
            if not window_capture_enabled or sys.platform != "win32":
                return orig_capture(desktop_hwnd)
            if not desktop_hwnd:
                return orig_capture(desktop_hwnd)

            user32 = ctypes.windll.user32
            if restore_minimized and user32.IsIconic(desktop_hwnd):
                # A minimized window has no full-size pixels to render, so it
                # must come back first. SW_SHOWNOACTIVATE keeps the user's
                # current window in front.
                user32.ShowWindow(desktop_hwnd, 4)  # SW_SHOWNOACTIVATE
                time.sleep(0.4)
                capture_state["restored"] += 1
                if logger is not None and capture_state["restored"] <= 3:
                    logger.info(
                        "Jenkins OCR: target window was minimized - restored "
                        "without taking focus so it can be captured"
                    )

            try:
                image = _print_window_client_image(desktop_hwnd)
            except Exception as exc:
                image = None
                if logger is not None and capture_state["grabbed"] < 1:
                    logger.info(
                        "Jenkins OCR: PrintWindow capture unavailable (%s) - "
                        "falling back to screen grab",
                        exc,
                    )

            if image is not None and not _is_uniform(image):
                capture_state["printed"] += 1
                if capture_state["printed"] == 1 and logger is not None:
                    logger.info(
                        "Jenkins OCR: capturing the window's own pixels via "
                        "PrintWindow (%dx%d) - occluding windows can no longer "
                        "be read by mistake",
                        image.size[0],
                        image.size[1],
                    )
                return image

            # PrintWindow gave nothing usable (HDX can refuse to render the
            # seamless proxy). The screen grab is only trustworthy when the
            # target is genuinely the top window at that rectangle; otherwise
            # it returns the covering window, and OCR happily reads it as the
            # Jenkins page. _jenkins_ocr_read turns None into (None, []), which
            # the callers already treat as "nothing readable, retry".
            capture_state["grabbed"] += 1
            reason = "nothing" if image is None else "a blank frame"
            covering = _topmost_root_at_centre(desktop_hwnd)
            if covering is not None and covering != desktop_hwnd:
                capture_state["occluded"] += 1
                if logger is not None and capture_state["occluded"] <= 5:
                    logger.warning(
                        "Jenkins OCR: PrintWindow returned %s and %r is on top "
                        "- refusing to OCR the covering window; retrying",
                        reason,
                        _window_title_upper(covering) or covering,
                    )
                return None
            if logger is not None and capture_state["grabbed"] <= 3:
                logger.info(
                    "Jenkins OCR: PrintWindow returned %s - using the screen "
                    "grab (target appears to be on top)",
                    reason,
                )
            return orig_capture(desktop_hwnd)

        ns["_capture_viewer_client_image"] = _capture_viewer_client_image
        # So the in-session maximize check below measures the same pixels.
        capture_image = _capture_viewer_client_image

    def _work_area_height():
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
            return user32.GetSystemMetrics(1)
        return rect.bottom - rect.top

    def _capture_size(hwnd):
        """Real on-screen size of the remote window, as the OCR pipeline sees it.

        GetWindowRect reports the seamless proxy rectangle, which stays at the
        size host-side SetWindowPos asked for even when the remote window never
        changed. Only the capture tells the truth.
        """
        if not callable(capture_image) or not hwnd:
            return 0, 0
        try:
            image = capture_image(hwnd)
            if image is None:
                return 0, 0
            return int(image.size[0]), int(image.size[1])
        except Exception:
            return 0, 0

    def _send_win_arrow(up):
        """Win+Up / Win+Down as a chord, for the remote window manager."""
        user32 = ctypes.windll.user32
        VK_LWIN, VK_UP, VK_DOWN = 0x5B, 0x26, 0x28
        EXT, KEYUP = 0x0001, 0x0002
        vk = VK_UP if up else VK_DOWN
        user32.keybd_event(VK_LWIN, 0, 0, 0)
        time.sleep(0.06)
        user32.keybd_event(vk, 0, EXT, 0)
        time.sleep(0.06)
        user32.keybd_event(vk, 0, EXT | KEYUP, 0)
        time.sleep(0.06)
        user32.keybd_event(VK_LWIN, 0, KEYUP, 0)

    def _maximize_remote_edge(edge_hwnd, logger):
        """Maximize the Jenkins Edge window from *inside* the Citrix session.

        The window is created with Ctrl+N on the user's existing remote Edge,
        so Chromium clones that window's geometry — which is why Jenkins came
        up ~569px tall and the login fields sat below the fold, triggering the
        zoom-out-to-fit fallback. Host-side SetWindowPos cannot fix it: it
        moves the seamless proxy only, reporting 1920x1032 while the capture
        stays 569. Win+Up is handled by the *remote* window manager, so it
        resizes the window Edge is actually painting into.

        This is deliberately not ShowWindow(SW_MAXIMIZE): that host-side call
        is what puts the seamless window into the compositor state where HDX
        returns an all-black capture. If Win+Up produces a black or empty
        capture anyway, it is undone with Win+Down.
        """
        if sys.platform != "win32" or not edge_hwnd:
            return False
        if remote_state["tried"]:
            return remote_state["maximized"]
        remote_state["tried"] = True
        if os.environ.get("ENVPILOT_REMOTE_MAXIMIZE", "1") == "0":
            if logger is not None:
                logger.info(
                    "Jenkins OCR: in-session maximize disabled "
                    "(ENVPILOT_REMOTE_MAXIMIZE=0)"
                )
            return False

        target_h = _work_area_height()
        before_w, before_h = _capture_size(edge_hwnd)
        if before_h and before_h >= target_h * 0.85:
            if logger is not None:
                logger.info(
                    "Jenkins OCR: remote Edge already full height "
                    "(capture %dx%d) — no maximize needed",
                    before_w,
                    before_h,
                )
            return False

        if logger is not None:
            logger.info(
                "Jenkins OCR: remote Edge capture is %dx%d (target height %d) "
                "— maximizing inside the Citrix session with Win+Up",
                before_w,
                before_h,
                target_h,
            )

        if callable(release_mods):
            try:
                release_mods()
            except Exception:
                pass
        _prepare_remote_input(edge_hwnd, logger)

        try:
            _send_win_arrow(True)
        except Exception as exc:
            if logger is not None:
                logger.warning("Jenkins OCR: Win+Up failed: %s", exc)
            return False
        time.sleep(1.2)

        after_w, after_h = _capture_size(edge_hwnd)
        if after_h == 0:
            if logger is not None:
                logger.warning(
                    "Jenkins OCR: capture went black/empty after Win+Up "
                    "(HDX capture protection) — undoing with Win+Down"
                )
            try:
                _send_win_arrow(False)
                time.sleep(1.0)
            except Exception:
                pass
            return False

        if after_h > before_h * 1.1 or after_h >= target_h * 0.85:
            remote_state["maximized"] = True
            if logger is not None:
                logger.info(
                    "Jenkins OCR: in-session maximize worked — capture now "
                    "%dx%d (was %dx%d); host-side resize disabled from here",
                    after_w,
                    after_h,
                    before_w,
                    before_h,
                )
            return True

        if logger is not None:
            logger.warning(
                "Jenkins OCR: Win+Up did not enlarge the remote window "
                "(capture %dx%d, was %dx%d) — the Citrix Desktop Viewer "
                "itself is likely too short",
                after_w,
                after_h,
                before_w,
                before_h,
            )
        return False

    if callable(orig_ocr_deploy):

        def _run_jenkins_ocr_deploy(*args, **kwargs):
            edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
            logger = _arg(args, kwargs, 4, "logger")
            live["logger"] = logger
            if logger is not None and not live.get("dpi_logged"):
                live["dpi_logged"] = True
                logger.info(
                    "Jenkins OCR: process DPI awareness = %s; window geometry "
                    "and screen captures now share one coordinate space",
                    _DPI_MODE or "unchanged (all APIs unavailable)",
                )
            try:
                _maximize_remote_edge(edge_hwnd, logger)
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: in-session maximize skipped (%s)", exc
                    )
            return orig_ocr_deploy(*args, **kwargs)

        ns["_run_jenkins_ocr_deploy"] = _run_jenkins_ocr_deploy

    orig_select = ns.get("_jenkins_ocr_select_dropdown")
    normalize_ocr = ns.get("_normalize_ocr_text")

    # Compiled _jenkins_ocr_select_dropdown adds this to the revealed label cy.
    _COMPILED_DROPDOWN_CY_OFFSET = 0.04
    # Offline-measured label-cy -> select-centre gap (1920x1116 captures, Aug 12).
    _DEFAULT_LABEL_TO_SELECT_OFFSET = 0.031

    if "ENVPILOT_DROPDOWN_CY_DROP" in os.environ:
        try:
            dropdown_label_to_select = float(
                os.environ["ENVPILOT_DROPDOWN_CY_DROP"]
            )
        except ValueError:
            dropdown_label_to_select = _DEFAULT_LABEL_TO_SELECT_OFFSET
    elif "ENVPILOT_DROPDOWN_CY_LIFT" in os.environ:
        try:
            dropdown_cy_lift = float(os.environ["ENVPILOT_DROPDOWN_CY_LIFT"])
        except ValueError:
            dropdown_cy_lift = 0.01
        # Legacy: final click was (label_cy - lift + 0.04); express as drop offset.
        dropdown_label_to_select = _COMPILED_DROPDOWN_CY_OFFSET - dropdown_cy_lift
    else:
        dropdown_label_to_select = _DEFAULT_LABEL_TO_SELECT_OFFSET

    def _norm_text(value):
        if callable(normalize_ocr):
            try:
                return normalize_ocr(value)
            except Exception:
                pass
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    def _label_left_ratio(label, words, labels):
        """Left edge of the label, as a 0..1 ratio, and how it was determined.

        ``_jenkins_reveal_label`` returns whatever ``_jenkins_find_phrase``
        produced, which is a centroid carrying only ``cx``/``cy`` — no ``left``.
        Reading ``label["left"]`` therefore raises KeyError, and the previous
        version of this override swallowed that and returned the label
        untouched, so the re-anchoring silently never happened on any run. The
        raw OCR word dicts from ``_jenkins_ocr_read`` DO keep pixel geometry,
        so the left edge is recovered from the words on the label's own row.
        """
        img_w = live.get("img_w")
        img_h = live.get("img_h")
        label_cy = label.get("cy")

        if img_w and words and label_cy is not None:
            wanted = {_norm_text(text) for text in (labels or ()) if text}
            wanted.discard("")
            lefts = []
            for word in words:
                # This build's word dicts carry ("text", "norm", "cx", "cy",
                # "x1", "conf", "line") — no top/height/left — so the row is
                # matched on "cy". x1 is the RIGHT edge (left+width)/img_w;
                # recover the left edge as 2*cx-x1 for anchoring.
                try:
                    row_cy = float(word["cy"])
                    x1 = float(word["x1"])
                except (KeyError, TypeError, ValueError):
                    continue
                if abs(row_cy - float(label_cy)) > 0.015:
                    continue
                norm = _norm_text(word.get("norm") or word.get("text") or "")
                if norm and any(
                    norm == w or (len(norm) >= 3 and norm in w) or (len(w) >= 3 and w in norm)
                    for w in wanted
                ):
                    # x1 is the word's RIGHT edge in this build; recover left edge.
                    try:
                        cx = float(word["cx"])
                        right = x1 if x1 <= 1.0 else x1 / float(img_w)
                        left_edge = max(0.0, 2.0 * cx - right)
                    except (TypeError, ValueError):
                        left_edge = x1 if x1 <= 1.0 else x1 / float(img_w)
                    lefts.append(left_edge)
            if lefts:
                left_ratio = min(lefts)
                label_cx = label.get("cx")
                # A left edge right of the label centre means the row match
                # picked up the wrong word; the estimate below is safer.
                sane = 0.0 <= left_ratio <= 1.0
                if sane and label_cx is not None:
                    try:
                        sane = left_ratio <= float(label_cx) + 0.005
                    except (TypeError, ValueError):
                        pass
                if sane:
                    return left_ratio, "OCR word geometry"

        if img_w:
            try:
                return float(label["left"]) / float(img_w), "label left key"
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass

        if label_cy is not None and label.get("cx") is not None:
            # Last resort: approximate the left edge from the centre, so the
            # compiled centre+3% offset still lands inside a narrow control.
            try:
                return max(0.0, float(label["cx"]) - 0.03), "cx estimate"
            except (TypeError, ValueError):
                pass

        return None, "no usable label geometry"

    def _estimate_label_to_select_offset(label, words, labels):
        """Distance from label cy to select-centre cy, from OCR words below the label."""
        label_cy = label.get("cy")
        if label_cy is None or not words:
            return None, None
        left_ratio, _source = _label_left_ratio(label, words, labels)
        if left_ratio is None and label.get("cx") is not None:
            try:
                left_ratio = max(0.0, float(label["cx"]) - 0.03)
            except (TypeError, ValueError):
                left_ratio = None
        select_centres = []
        for word in words:
            try:
                word_cy = float(word["cy"])
                word_cx = float(word["cx"])
                conf = int(word.get("conf") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if conf < 20:
                continue
            if word_cy <= float(label_cy) + 0.012:
                continue
            if word_cy > float(label_cy) + 0.065:
                continue
            if left_ratio is not None and abs(word_cx - (left_ratio + 0.03)) > 0.10:
                continue
            norm = _norm_text(word.get("norm") or word.get("text") or "")
            wanted = {_norm_text(text) for text in (labels or ()) if text}
            wanted.discard("")
            if norm and any(
                norm == w or (len(norm) >= 3 and norm in w) or (len(w) >= 3 and w in norm)
                for w in wanted
            ):
                continue
            select_centres.append(word_cy)
        if not select_centres:
            return None, None
        centre = sum(select_centres) / len(select_centres)
        return centre - float(label_cy), "OCR select value"

    def _dropdown_shifted_cy(label, words, labels):
        """Pre-compiled cy anchor: lands at label_cy + offset after +4%."""
        label_cy_val = label.get("cy")
        if label_cy_val is None:
            return None, None, None
        derived, derived_source = _estimate_label_to_select_offset(
            label, words, labels
        )
        if derived is not None:
            offset = derived
            source = derived_source
        else:
            offset = dropdown_label_to_select
            source = "configured drop default"
        try:
            shifted = float(label_cy_val) + offset - _COMPILED_DROPDOWN_CY_OFFSET
        except (TypeError, ValueError):
            return None, source, offset
        return max(0.01, min(0.99, shifted)), source, offset

    if callable(orig_select):

        def _jenkins_ocr_select_dropdown(*args, **kwargs):
            logger = _arg(args, kwargs, 4, "logger")
            if logger is not None:
                live["logger"] = logger

            # The dropdown code clicks at label_centre_x + 3%. Jenkins renders
            # each <select> left-aligned under its label, and a narrow one
            # (ProjectName showing "OGW", ENVIRONMENT_NAME showing "SIT1") is
            # only ~3% wide — so +3% from the label CENTRE lands past its right
            # edge, on blank page, and the list never opens. Release_name is
            # wide enough that the same offset happens to land inside it, which
            # is why only the narrow ones failed. Reporting the label's left
            # edge instead puts the click inside the control at any width.
            # Scoped to this call so text-field and login paths are untouched.
            current_reveal = ns.get("_jenkins_reveal_label")

            def _left_aligned_reveal(edge_hwnd, labels, logger_):
                result_ = current_reveal(edge_hwnd, labels, logger_)
                try:
                    label, words = result_
                except (TypeError, ValueError):
                    return result_
                if not label:
                    return label, words
                left_ratio, source = _label_left_ratio(label, words, labels)
                if left_ratio is None:
                    if logger_ is not None:
                        logger_.warning(
                            "Jenkins OCR: dropdown click NOT re-anchored (%s) — "
                            "the centre+3%% click will miss narrow selects",
                            source,
                        )
                    return label, words
                shifted = dict(label)
                shifted["cx"] = max(0.01, left_ratio - 0.015)
                # Each <select> sits below its label; the compiled path adds
                # +4% to whatever cy _jenkins_reveal_label returns. Drop the
                # anchor down to the measured select centre (derived from OCR
                # words in the control when visible, else ENVPILOT_DROPDOWN_CY_DROP).
                label_cy_val = label.get("cy")
                cy_source = None
                cy_offset = None
                if label_cy_val is not None:
                    shifted_cy, cy_source, cy_offset = _dropdown_shifted_cy(
                        label, words, labels
                    )
                    if shifted_cy is not None:
                        shifted["cy"] = shifted_cy
                if logger_ is not None:
                    logger_.info(
                        "Jenkins OCR: dropdown click anchored to label left "
                        "edge via %s (cx %.3f -> %.3f, cy %.3f -> %.3f, "
                        "vertical %s offset %+.3f) so narrow selects are hit",
                        source,
                        label.get("cx", -1.0),
                        shifted["cx"],
                        label.get("cy", -1.0),
                        shifted.get("cy", label.get("cy", -1.0)),
                        cy_source or "none",
                        cy_offset if cy_offset is not None else 0.0,
                    )
                return shifted, words

            if callable(current_reveal):
                ns["_jenkins_reveal_label"] = _left_aligned_reveal
            try:
                result = orig_select(*args, **kwargs)
            finally:
                if callable(current_reveal):
                    ns["_jenkins_reveal_label"] = current_reveal

            if not result:
                edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
                field_key = _arg(args, kwargs, 3, "field_key", "dropdown")
                if field_key:
                    live["param_select_failures"].add(field_key)
                if callable(ocr_dump):
                    try:
                        ocr_dump(
                            _live_hwnd(edge_hwnd),
                            "dropdown_fail_{}".format(field_key),
                            logger,
                        )
                    except Exception:
                        pass
            return result

        ns["_jenkins_ocr_select_dropdown"] = _jenkins_ocr_select_dropdown

    orig_fill_params = ns.get("_fill_jenkins_ocr_parameters")
    orig_click_submit = ns.get("_jenkins_click_build_submit")
    ocr_tesseract_data = ns.get("_ocr_tesseract_data")

    if callable(orig_fill_params):

        def _fill_jenkins_ocr_parameters(*args, **kwargs):
            deploy_form = _arg(args, kwargs, 1, "deploy_form")
            logger = _arg(args, kwargs, 2, "logger")
            if logger is not None:
                live["logger"] = logger
            live["deploy_form"] = dict(deploy_form or {})
            live["param_select_failures"] = set()
            return orig_fill_params(*args, **kwargs)

        ns["_fill_jenkins_ocr_parameters"] = _fill_jenkins_ocr_parameters

    if (
        callable(orig_click_submit)
        and callable(orig_read)
        and callable(ocr_tesseract_data)
        and callable(normalize_ocr)
        and callable(find_any)
    ):

        def _jenkins_click_build_submit(*args, **kwargs):
            edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
            logger = _arg(args, kwargs, 1, "logger")
            if logger is not None:
                live["logger"] = logger
            if _param_verify_enabled() and live.get("deploy_form") is not None:
                try:
                    image, words = orig_read(_live_hwnd(edge_hwnd), logger)
                except Exception as exc:
                    if logger is not None:
                        logger.warning(
                            "Jenkins OCR: pre-submit parameter check skipped "
                            "(capture/OCR failed: %s)",
                            exc,
                        )
                else:
                    if image is not None and words is not None:
                        if logger is not None:
                            logger.info(
                                "Jenkins OCR: pre-submit parameter verification "
                                "(%d OCR tokens, %d prior could-not-select)",
                                len(words),
                                len(live.get("param_select_failures") or ()),
                            )
                        _verify_jenkins_params_before_submit(
                            image,
                            words,
                            live["deploy_form"],
                            live.get("param_select_failures"),
                            find_any,
                            normalize_ocr,
                            ocr_tesseract_data,
                            logger,
                        )
            return orig_click_submit(*args, **kwargs)

        ns["_jenkins_click_build_submit"] = _jenkins_click_build_submit

    orig_mfa_wait = ns.get("_wait_for_mfa_verification")
    mfa_page_visible = ns.get("_mfa_approval_page_visible")
    stay_page_visible = ns.get("_stay_signed_in_page_visible")

    if callable(orig_mfa_wait) and callable(mfa_page_visible):

        def _wait_for_mfa_verification(driver, logger, timeout=300):
            """Wait for the MFA page to APPEAR before deciding it is absent.

            The original checks visibility once, roughly a second after Sign in
            is clicked. Microsoft has usually not rendered the approval page by
            then, so it logged "No MFA approval page detected", returned, and
            never spent any of its 300-second approval budget — leaving the
            sign-in sitting on an unapproved MFA prompt until the Citrix
            workspace wait timed out.
            """
            try:
                appear_wait = float(
                    os.environ.get("ENVPILOT_MFA_APPEAR_WAIT", "25")
                )
            except ValueError:
                appear_wait = 25.0

            deadline = time.time() + appear_wait
            started = time.time()
            while time.time() < deadline:
                try:
                    if mfa_page_visible(driver):
                        if logger is not None:
                            logger.info(
                                "MFA approval page appeared %.1fs after Sign in "
                                "— APPROVE THE REQUEST ON YOUR PHONE",
                                time.time() - started,
                            )
                        break
                except Exception:
                    pass
                # Already past MFA (silent SSO or remembered device).
                if callable(stay_page_visible):
                    try:
                        if stay_page_visible(driver):
                            break
                    except Exception:
                        pass
                time.sleep(1.0)
            else:
                if logger is not None:
                    logger.info(
                        "No MFA approval page within %.0fs of Sign in — "
                        "continuing without an MFA wait",
                        appear_wait,
                    )
            return orig_mfa_wait(driver, logger, timeout)

        ns["_wait_for_mfa_verification"] = _wait_for_mfa_verification

    orig_advanced = ns.get("_page_advanced_after_continue")
    orig_any_advanced = ns.get("_any_tab_page_advanced_after_continue")
    netscaler_visible = ns.get("_netscaler_logon_page_visible")
    workspace_loaded = ns.get("_citrix_workspace_loaded")
    strict_access_rules = os.environ.get(
        "ENVPILOT_STRICT_ACCESS_RULES", "1"
    ).strip().lower() not in ("0", "false", "no")

    if callable(orig_advanced) and strict_access_rules:
        # _page_advanced_after_continue takes no logger of its own; the caller
        # that has one is _any_tab_page_advanced_after_continue, so keep the
        # most recent logger here to report a veto in the session log.
        advanced_log = {"logger": None}

        def _page_advanced_after_continue(driver):
            """Treat the logon point as advanced only on positive evidence."""
            if not orig_advanced(driver):
                return False
            try:
                url = (driver.current_url or "").lower()
            except Exception:
                return True
            if "login.microsoftonline.com" in url or "login.live.com" in url:
                return True
            if callable(netscaler_visible):
                try:
                    if netscaler_visible(driver, None):
                        return True
                except Exception:
                    pass
            if callable(workspace_loaded):
                try:
                    if workspace_loaded(driver):
                        return True
                except Exception:
                    pass
            if "logonpoint" not in url and "/logon/" not in url:
                return True
            logger = advanced_log["logger"]
            if logger is not None:
                logger.info(
                    "Still on the NetScaler logon point with no Microsoft form, "
                    "AD Password field or Workspace - treating Access Rules as "
                    "NOT yet accepted: %s",
                    url,
                )
            return False

        ns["_page_advanced_after_continue"] = _page_advanced_after_continue

        if callable(orig_any_advanced):

            def _any_tab_page_advanced_after_continue(*args, **kwargs):
                advanced_log["logger"] = _arg(args, kwargs, 1, "logger")
                return orig_any_advanced(*args, **kwargs)

            ns["_any_tab_page_advanced_after_continue"] = (
                _any_tab_page_advanced_after_continue
            )

    def _resize_jenkins_edge_window(edge_hwnd, logger):
        if sys.platform != "win32" or not edge_hwnd:
            return None
        if logger is not None:
            live["logger"] = logger
        edge_hwnd = _live_hwnd(edge_hwnd)
        # A window maximized by the remote window manager must not be poked
        # with host-side SetWindowPos: that drags the seamless proxy back to
        # "normal" geometry and undoes the height we just gained.
        if remote_state["maximized"]:
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
