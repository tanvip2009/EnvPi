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

15. ``_open_new_citrix_edge_with_jenkins`` keeps retrying for focus instead of
    letting Jenkins land in the user's existing window. The compiled version
    focuses the window ``Ctrl+N`` just created, sleeps 0.8s, tests the
    foreground once, and on a single miss logs
    ``New Edge window not foreground (fg=...) — skipping paste`` and returns
    False; ``_try_edge_app_deploy`` then loads Jenkins into the existing remote
    Edge instead. On 13 Aug 10:25:46 a Teams chat window held the foreground
    for that one moment, so Jenkins became one tab among six
    (``Sign in [Jenkins] and 5 more pages``). That matters more than it looks:
    an Edge window's title *and* captured content follow the active tab, so any
    later tab switch silently points OCR and clicks at a different page. It is
    the likeliest explanation for the 13 Aug 08:55 capture that showed
    ``osf-telesales-sit2.vodafone.de`` under a ``dropdown_fail_ENVIRONMENT_NAME``
    name, and for titles flipping between ``OSF``, ``Email to ID Request`` and
    ``Sign in [Jenkins]`` within one run. Re-running the original is not an
    option — each call spends another ``Ctrl+N`` and would leave orphan windows
    — so the override finishes the job on the window already created, retrying
    ``_focus_window_handle``/``_bring_window_to_front``
    ``ENVPILOT_NEW_WINDOW_FOCUS_ATTEMPTS`` times (default 8, ~0.8s apart) and
    only then pasting the URL. The foreground gate itself is kept exactly as
    the compiled code had it, because it is what stops keystrokes going to the
    Citrix desktop or another app; only the give-up-after-one-try is changed.
    If nothing was created, the original is retried once, which cannot orphan a
    window. Set ``ENVPILOT_JENKINS_OWN_WINDOW=0`` to restore the old behaviour.

16. ``_jenkins_reveal_label`` waits for the scroll to stop before reporting a
    label's position. The compiled version scrolls a label into view and OCRs
    immediately, but Edge animates keyboard scrolling, so the geometry it
    returns is often a mid-flight frame — and every click derived from it lands
    where the row *was*. The 13 Aug 13:30 run shows this twice, in the same
    numbers: ``ENVIRONMENT_NAME`` was measured at cy 0.753 while the row was
    travelling from 0.86 to its resting 0.65, so the click at 0.784 missed the
    select by ~10% of the page and the arrow keys that followed scrolled the
    document instead (the "dropdown does not open, page scrolls down" report);
    ``BuildNumber`` was measured near 0.90, clicked at 0.94, and by then the row
    had settled at 0.84 with the *next* parameter's label ``BuildNummer`` at
    0.94 — so the triple-click selected that label as page text (visible
    highlighted in ``jenkins_before_build_133802.png``) and ``Ctrl+V`` went into
    a non-editable area, leaving BuildNumber empty. The override captures until
    two consecutive frames differ by ≤2% of the client area, re-OCRs, and
    returns the re-found label only when the row actually moved (≥0.008), so a
    still page keeps its original reading. Set ``ENVPILOT_SETTLE_SCROLL=0`` to
    restore the old behaviour.

17. ``_jenkins_ocr_select_dropdown`` reads the select back from the control
    itself before accepting a success. The compiled arrow-scan confirms its own
    progress with OCR of the whole 1920x1032 page, where a ~56x26px select is
    unreadable: ``ENVIRONMENT_NAME`` showing ``SIT1`` came back as ``sitiv``
    then ``sity``, close enough to garbage that the scan matched noise and
    logged ``selected ENVIRONMENT_NAME = SIT5 (arrow-scan)`` against a control
    it had never changed. The wrong value then survived to the submit guard,
    which is the only reason the build did not run with SIT1. The override crops
    the control's own region (label left edge, ``_PARAM_BAND_TOP``..
    ``_PARAM_BAND_BOTTOM`` below the label) and OCRs it upscaled 4x, which
    resolves the value cleanly; on a confident mismatch it turns the result into
    a failure so the field is recorded in ``param_select_failures`` and override
    14 can abort. Silence still means inconclusive, never failure. Set
    ``ENVPILOT_VERIFY_DROPDOWN_READBACK=0`` to restore the old behaviour.

18. ``_jenkins_ocr_select_dropdown`` skips a select that already holds the
    requested value. Reading a ~60x30px value needs more than upscaling: Citrix
    and Edge render with ClearType subpixel anti-aliasing, so every glyph edge
    carries orange and blue fringes. Straight-stroked values survive luminance
    conversion (``SIT1`` reads exactly) but round and diagonal strokes do not —
    ``OGW`` dissolves into hollow outlines and Tesseract returned *nothing* for
    it under every page-segmentation mode, which is why ProjectName has never
    verified in any run. Collapsing to the darkest RGB channel and blurring the
    fringes back together recovers it, so ``_control_crop_variants`` offers
    luminance, darkest-channel and darkest-plus-blur renderings at ``--psm 7``
    and the caller keeps whichever reading matches. Digits are then compared
    exactly (``_digits_agree``): the fuzzy matcher scores
    ``4000_WAVE11_PCK1`` and ``4000_WAVE11_PCK01`` above threshold, so a select
    holding PCK1 was being accepted as PCK01. This is scoped to crop readings —
    the full-page submit guard keeps its looser rules, where a stray ``0`` read
    out of ``OMI`` would otherwise invent a mismatch. On 13 Aug 18:01 this would
    have turned ProjectName from 3.5 minutes of clicking, typing and
    option-cycling — on a field that already read ``OGW`` in the first capture —
    into a single no-op. Set ``ENVPILOT_SKIP_CORRECT_DROPDOWN=0`` to disable.

19. ``_jenkins_ocr_select_dropdown`` sets a select by type-ahead before falling
    back to the compiled strategies. Strategy A types a filter then clicks the
    option row that matches, but a native ``<select>`` popup is an OS window of
    its own and never appears in the window-owned capture, so the only matching
    text OCR can see is the value the closed select already displays. That is
    why both clicks in the 18:01 log land on the identical point (23%, 38%): the
    second reopens the box instead of choosing a row. Strategy B then steps
    through options one at a time for ~2.5 minutes per attempt with the list
    held open, which is also what gives the popup time to destroy the Citrix
    seamless window (``handle 2888292 was destroyed`` → the run adopted an
    unrelated ``AskVodafone`` Edge window). Escape closes the popup and leaves
    the ``<select>`` focused, and Chromium type-ahead on a focused closed select
    jumps straight to the matching option: one step, nothing left open, popup
    lifetime ~0.4s instead of minutes. Enter is deliberately never sent — on a
    parameter form that would submit the build. Unverified attempts return None
    so the compiled routine still runs, making this strictly additive. Set
    ``ENVPILOT_DIRECT_DROPDOWN=0`` to disable.

20. ``_automate_citrix_to_kias_desktop``, ``_complete_citrix_desktop_launch``
    and ``_open_putty_on_citrix_desktop`` route the SIT environment switch
    through the published ``Putty`` app tile instead of the KIAS desktop.
    PuTTY used to be typed into the desktop's Start menu, so a switch first had
    to launch that desktop — but the switch reuses the deploy sign-in flow, and
    a deploy never launches a desktop: it launches the published ``Edge
    KiaSDev`` app, because Jenkins only needs a browser. Every switch therefore
    opened a seamless remote Edge and then failed looking for a desktop it had
    never asked for (``KIAS DEV & TEST DESKTOP did not open for SIT switch``,
    14 Sep 13:32 log, after ``Edge KiaSDev app opened``). The store publishes
    PuTTY itself — ``Putty KiaSDev 2019 PT`` and ``Putty 0_80 KiaSDev 2019 PT``
    among its 25 app tiles — so the switch now launches that tile and drives
    the seamless window, with no desktop involved. Sign-in is untouched: only
    the launch step at the end of ``_run_full_deploy_flow`` is swapped, and
    only while a switch is running, so Deploy keeps its Edge-app behaviour.
    ``_configure_putty_session`` and ``_submit_putty_session_password`` already
    locate their windows globally by title, so they work on a seamless window
    unchanged. Set ``ENVPILOT_PUTTY_APP=0`` to restore the desktop route.

21. ``_find_submit_button_centre`` rejects text, and ``_click_located_submit``
    scrolls the button into view before clicking. Two 15 Sep runs (11:57:52 and
    12:04:36) filled and verified every parameter, then logged
    ``found 'Build' submit button by fill at 24%,91% - clicking it`` followed by
    ``parameter form is still on screen after the submit click - the build was
    NOT submitted``. Re-scanning ``jenkins_before_build_120213.png`` shows why:
    on the ``/rebuild/parameterized`` page the form ends with ``BuildNummm`` at
    the bottom edge of the 1920x1032 window, so the button is below the fold and
    every one of the ten shapes the scan matched was navy *text*. The
    cell-density test cannot tell them apart -- a word fills its own bounding
    box as densely as a button -- but the pixels inside the box can:
    ``_interior_fill_fraction`` measures 0.89 for the real button against 0.14
    for the ``BuildNummm`` label and 0.05-0.15 for every heading, so a 0.50
    threshold drops all of them. That alone would only turn a wrong click into
    no click, so the finder now scrolls down up to
    ``ENVPILOT_SUBMIT_SCROLL_ATTEMPTS`` times (default 4) and rescans, widening
    the search band to the top 20% of the viewport because a scrolled-in button
    can land anywhere in it. ``_find_submit_label_centre`` adds a token match on
    ``Rebuild``/``Build`` for the rare capture where Tesseract reads the
    white-on-navy label, restricted to below cy 0.40 so the ``Rebuild``
    breadcrumb cannot be clicked -- that would reload the form and discard every
    filled parameter. Scrolling happens after the override 14 parameter guard
    has read the page, so verification still sees the filled fields.

22. ``_jenkins_click_exact_job`` clicks the centre of the job link rather than
    the point ``_jenkins_find_token_run`` reports. That helper averages the
    centres of the tokens it picked, so a name OCR splits into pieces yields a
    point near the start of the link -- 28-29% for
    ``deploy_from_nexus_maven_2``, a couple of pixels inside the text. On 15 Sep
    at 14:22 it landed in the health-icon column instead: Jenkins opened the
    "Build stability" tooltip, the page never left the dashboard, and the
    ``'Rebuild Last' link not found`` that followed ended the run. The same 29%
    navigated fine at 13:14, so the point is simply too close to the edge to
    survive a small layout shift, such as the ``All``/``Self-service`` tab row.
    The token run's full extent is reconstructed from the ``cx``/``x1`` of every
    token on the hit's row, and its midpoint clicked. The finder's retries and
    its fuzzy fallback are all worth keeping, so the read and click helpers are
    wrapped for the duration of the original call rather than reimplemented.

23. ``JENKINS_UPLOAD_DIR`` and ``JENKINS_UPLOAD_SCRIPT`` are repointed at the
    upload folder that actually exists. The compiled backend builds them from
    ``SCRIPT_DIR / ' Jenkins Deployment Script'`` -- with a leading space --
    and the repository also carried a copy under the ordinary name. Deleting
    the leading-space duplicate therefore broke Upload in both halves of the
    app: the GUI's own ``Test-Path`` guard refused to open the form, and
    ``handle_upload`` would have raised ``Jenkins upload script not found``.
    Both names are accepted, the ordinary one first, so the folder can be named
    sanely without stranding a checkout that still has the old one.

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
    from PIL import Image, ImageChops, ImageFilter
except ImportError:
    # Only the window-owned capture below needs PIL directly; without it that
    # override stands down and the compiled ImageGrab path is used unchanged.
    Image = None
    ImageChops = None
    ImageFilter = None

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

# Scroll-settle (override 16) and small-control OCR (override 17) tuning.
_SETTLE_TRIES = 6
_SETTLE_PAUSE = 0.2
_SETTLE_CHANGED_RATIO = 0.02
_SETTLE_MIN_SHIFT = 0.008
_SMALL_CONTROL_UPSCALE = 6
_SMALL_CONTROL_BLUR = 0.6
# --psm 7 treats the crop as one text line; the whitelist keeps the <select>
# chevron from being read as a stray letter.
_SMALL_CONTROL_CONFIG = (
    "--psm 7 -c tessedit_char_whitelist="
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _scroll_settle_enabled():
    return os.environ.get("ENVPILOT_SETTLE_SCROLL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _dropdown_readback_enabled():
    return os.environ.get("ENVPILOT_VERIFY_DROPDOWN_READBACK", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _dropdown_skip_enabled():
    return os.environ.get("ENVPILOT_SKIP_CORRECT_DROPDOWN", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _own_window_preferred():
    """Whether Jenkins should get its own Edge window rather than a tab.

    Same switch the new-window opener already honours, so
    ``ENVPILOT_JENKINS_OWN_WINDOW=0`` turns off both.
    """
    return os.environ.get(
        "ENVPILOT_JENKINS_OWN_WINDOW", "1"
    ).strip().lower() not in ("0", "false", "no")


def _rebuild_text_params_enabled():
    """Treat Rebuild-page parameters as the text inputs they are.

    Set ``ENVPILOT_REBUILD_TEXT_PARAMS=0`` to restore the dropdown mechanics
    on that page.
    """
    return os.environ.get(
        "ENVPILOT_REBUILD_TEXT_PARAMS", "1"
    ).strip().lower() not in ("0", "false", "no")


def _direct_dropdown_enabled():
    return os.environ.get("ENVPILOT_DIRECT_DROPDOWN", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _images_settled(first, second):
    """True when two captures differ only in a small region (e.g. a text caret).

    Used to tell "the scroll animation has finished" from "the page is still
    moving": a scroll repaints nearly the whole client area, while a blinking
    caret or a spinner changes a sliver of it.
    """
    if ImageChops is None or first is None or second is None:
        return False
    if first.size != second.size:
        return False
    try:
        bbox = ImageChops.difference(
            first.convert("L"), second.convert("L")
        ).getbbox()
    except Exception:
        return False
    if bbox is None:
        return True
    total = float(first.size[0] * first.size[1])
    if total <= 0:
        return False
    changed = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    return (changed / total) <= _SETTLE_CHANGED_RATIO


def _wait_until_settled(capture, hwnd, logger):
    """Capture until two consecutive frames match; return the settled frame."""
    previous = None
    image = None
    for _ in range(_SETTLE_TRIES):
        try:
            image = capture(hwnd)
        except Exception:
            return None
        if image is None:
            return None
        if previous is not None and _images_settled(previous, image):
            return image
        previous = image
        time.sleep(_SETTLE_PAUSE)
    if logger is not None:
        logger.info(
            "Jenkins OCR: page still repainting after %.1fs — measuring the "
            "last frame anyway",
            _SETTLE_TRIES * _SETTLE_PAUSE,
        )
    return image


def _crop_control_region(image, box):
    """Crop a ratio box out of a capture, clamped to the image."""
    if Image is None or image is None or not box:
        return None
    width, height = image.size
    x0 = int(max(0, min(width - 1, box[0] * width)))
    y0 = int(max(0, min(height - 1, box[1] * height)))
    x1 = int(max(x0 + 1, min(width, box[2] * width)))
    y1 = int(max(y0 + 1, min(height, box[3] * height)))
    return image.crop((x0, y0, x1, y1)).convert("RGB")


def _control_crop_variants(region):
    """Yield de-fringed renderings of a small control crop.

    Citrix/Edge render text with ClearType subpixel anti-aliasing, so every
    glyph edge carries orange and blue fringes. Straight-stroked values survive
    a plain luminance conversion — ``SIT1`` reads exactly — but round and
    diagonal strokes do not: ``OGW`` dissolves into hollow, colour-noised
    outlines and Tesseract returned *nothing at all* for it under every
    page-segmentation mode, which is why ProjectName has never once verified.
    Collapsing to the darkest channel and blurring the fringes back together
    recovers ``OGW``, while plain luminance stays best for ``SIT1``, and the
    darkest channel alone is best for ``Release_name``. No single recipe wins,
    so all three are offered and the caller keeps whichever reading matches.
    """
    yield region.convert("L")
    if ImageChops is None:
        return
    red, green, blue = region.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    yield darkest
    if ImageFilter is not None:
        yield darkest.filter(ImageFilter.GaussianBlur(_SMALL_CONTROL_BLUR))


def _prepare_for_ocr(prepped):
    """Upscale and pad one rendering; Tesseract needs margin around the text."""
    upscaled = prepped.resize(
        (
            prepped.width * _SMALL_CONTROL_UPSCALE,
            prepped.height * _SMALL_CONTROL_UPSCALE,
        ),
        Image.LANCZOS,
    )
    padded = Image.new("L", (upscaled.width + 40, upscaled.height + 40), 255)
    padded.paste(upscaled.convert("L"), (20, 20))
    return padded


def _read_control_readings(image, box, configure_tesseract, logger):
    """Return the distinct OCR readings of one small control region."""
    if Image is None:
        return []
    region = _crop_control_region(image, box)
    if region is None:
        return []
    if callable(configure_tesseract) and not configure_tesseract(logger):
        return []
    try:
        import pytesseract
    except ImportError:
        return []

    readings = []
    for prepped in _control_crop_variants(region):
        try:
            text = pytesseract.image_to_string(
                _prepare_for_ocr(prepped), config=_SMALL_CONTROL_CONFIG
            )
        except Exception:
            continue
        text = (text or "").strip()
        if text and text not in readings:
            readings.append(text)
    return readings


# Glyph pairs Tesseract cannot separate on Jenkins' small controls. A select
# holding SIT5 reads back as "SITS" and FOLDER_NAME's 26.08.OMI reads as
# "26080MI", so comparing the raw text rejects values that are actually correct.
# Folding each pair onto one representative fixes that without loosening
# length: PCK1 still differs from PCK01, because that is a missing character
# rather than a misread one.
_OCR_CONFUSABLE_FOLD = str.maketrans(
    {"o": "0", "i": "1", "l": "1", "|": "1", "s": "5", "z": "2", "b": "8", "g": "6"}
)


def _fold_ocr_confusables(text):
    """Canonicalise glyphs OCR cannot tell apart at this resolution."""
    return (text or "").lower().translate(_OCR_CONFUSABLE_FOLD)


def _digits_exact(observed, requested):
    """True when two values carry exactly the same digits in the same order."""
    observed_digits = re.sub(r"\D", "", _fold_ocr_confusables(observed))
    requested_digits = re.sub(r"\D", "", _fold_ocr_confusables(requested))
    return observed_digits == requested_digits


def _digit_groups(text):
    """Digit runs with leading zeros stripped, so PCK01 and PCK1 line up."""
    return [
        group.lstrip("0") or "0"
        for group in re.findall(r"\d+", _fold_ocr_confusables(text))
    ]


def _digits_agree(observed, requested):
    """True when two values carry the same digits, ignoring zero padding.

    The fuzzy matcher exists to absorb OCR noise, but that tolerance is fatal
    for option names that differ by a digit: SIT1 and SIT5 score above the match
    threshold, so a select holding SIT1 was accepted as SIT5. Letters can stay
    fuzzy — digits cannot.

    Zero padding is the one exception, because it is an OCR artefact rather than
    a real difference: the enumerated Release_name list reads its first option as
    ``4000_WAVE11_PCK1`` where Jenkins actually offers ``4000_WAVE11_PCK01``, the
    zero being too narrow to survive the crop. Comparing digit runs with leading
    zeros stripped makes those agree while keeping SIT1 (groups 51,1) apart from
    SIT5 (groups 51,5).

    Two options in the same list can differ only by padding (this list holds both
    ``PCK2`` and ``PCK02``), so the caller that picks an option by index must
    still require the padding-tolerant match to be unique — see
    ``_enumerate_dropdown_options``.
    """
    return _digit_groups(observed) == _digit_groups(requested)


def _match_control_reading(readings, requested, normalize_ocr, strict_digits=True):
    """Classify a control's readings against the requested value.

    Returns ``(status, reading)`` with status ``ok``, ``mismatch`` or
    ``unreadable``. Any single reading that matches is accepted as proof: these
    are renderings of the same pixels, so a recipe spelling out the requested
    value when the control holds something else is not a realistic failure. A
    mismatch needs the most informative reading to conflict confidently, and
    an empty result is always ``unreadable`` — never a failure.

    ``strict_digits`` is for dropdowns, where the option list makes a one-digit
    difference a different build. Free-text values pass it False: a folder like
    ``26.08.OMI`` reads its 'O' as '0' often enough that digit-exactness would
    invent a mismatch on a field the user typed correctly.
    """
    for reading in readings:
        if _param_values_match(reading, requested, normalize_ocr) and (
            not strict_digits or _digits_agree(reading, requested)
        ):
            return "ok", reading
    best = max(readings, key=len) if readings else ""
    if not best:
        return "unreadable", ""
    if strict_digits and not _digits_agree(best, requested):
        return "mismatch", best
    mismatch, observed = _param_confident_mismatch(best, requested, normalize_ocr)
    return ("mismatch" if mismatch else "unreadable"), observed or best


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
    folded_observed = _fold_ocr_confusables(observed)
    folded_requested = _fold_ocr_confusables(requested)
    if folded_observed == folded_requested:
        return True
    # A closed select is captured with its dropdown arrow, which OCR appends as
    # a stray letter, so the requested value is a prefix rather than the whole
    # reading.
    if len(folded_requested) >= 4 and (
        folded_requested in folded_observed or folded_observed in folded_requested
    ):
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


def _param_value_box(label, words, labels, normalize_ocr):
    """Ratio box covering the control that sits directly under a param label."""
    try:
        label_cy = float(label["cy"])
    except (KeyError, TypeError, ValueError):
        return None
    left = _param_label_left_ratio(label, words, labels, normalize_ocr)
    if left is None:
        try:
            left = max(0.0, float(label.get("cx", 0.2)) - 0.03)
        except (TypeError, ValueError):
            return None
    return (
        max(0.0, left - 0.005),
        max(0.0, label_cy + _PARAM_BAND_TOP),
        min(1.0, left + 0.22),
        min(1.0, label_cy + _PARAM_BAND_BOTTOM),
    )


def _read_param_control(
    image,
    labels,
    requested,
    find_any,
    normalize_ocr,
    ocr_tesseract_data,
    configure_tesseract,
    logger,
    label=None,
    words=None,
    strict_digits=True,
):
    """Locate a parameter control and classify what it currently displays.

    Returns ``(status, reading)`` from ``_match_control_reading``, or
    ``("unreadable", "")`` when the label itself cannot be found.
    """
    if not words:
        words = _ocr_words_from_image(image, ocr_tesseract_data, normalize_ocr, logger)
    if not words:
        return "unreadable", ""
    if label is None:
        try:
            label, _which = find_any(
                words, list(labels), min_conf=_PARAM_VERIFY_LABEL_CONF
            )
        except TypeError:
            label, _which = find_any(words, list(labels))
    if not label:
        return "unreadable", ""
    box = _param_value_box(label, words, labels, normalize_ocr)
    if box is None:
        return "unreadable", ""
    readings = _read_control_readings(image, box, configure_tesseract, logger)
    return _match_control_reading(
        readings, requested, normalize_ocr, strict_digits=strict_digits
    )


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
    configure_tesseract=None,
    strict_digits=True,
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
        # A parameter scrolled out of view cannot be checked, which is fine for a
        # field nobody reported trouble with. It is not fine for a select the
        # fill step gave up on: in the 13:04 capture Release_name had scrolled
        # off the top, so "could not select" turned into "inconclusive, allowing
        # submit" purely because the guard could not see it.
        if had_select_failure:
            return "mismatch", None
        return "inconclusive", None

    band = _param_band_words(words, label, labels, normalize_ocr)
    observed_raw = _param_join_band(band)
    observed_norm = normalize_ocr(observed_raw) if observed_raw else ""

    if _param_values_match(observed_norm, requested, normalize_ocr):
        return "ok", observed_raw or requested

    # Ask the control's own pixels before trusting a full-page verdict. Page OCR
    # renders these selects a few pixels tall and drops the character that
    # matters: the 13:03 run read ENVIRONMENT_NAME as "sity", with the digit
    # missing entirely, and aborted on it. The de-fringed upscaled crop reads the
    # same control as "SITSY", which is SIT5 with a confusable 5. Leaving this
    # after the full-page check meant it was never consulted.
    control_status, control_reading = "unreadable", ""
    if callable(configure_tesseract):
        try:
            control_status, control_reading = _read_param_control(
                image,
                labels,
                requested,
                find_any,
                normalize_ocr,
                ocr_tesseract_data,
                configure_tesseract,
                logger,
                label=label,
                words=words,
                strict_digits=strict_digits,
            )
        except Exception as exc:
            if logger is not None:
                logger.info(
                    "Jenkins OCR: pre-submit control re-read failed for %s (%s)",
                    field_key,
                    exc,
                )
    if control_status == "ok":
        return "ok", control_reading or requested
    if control_status == "mismatch":
        return "mismatch", control_reading

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

    # The fill step already retried this select twice and gave up. Reaching here
    # means no reader could then show it holding the requested value, so there is
    # no reading of the evidence in which submitting is safe.
    if had_select_failure:
        return "mismatch", observed_raw or crop_observed or control_reading or None

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
    configure_tesseract=None,
):
    """Raise RuntimeError when any requested parameter clearly mismatches the screen."""
    dropdown_keys = {field_key for field_key, _labels in _JENKINS_PARAM_DROPDOWNS}
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
            configure_tesseract=configure_tesseract,
            strict_digits=field_key in dropdown_keys,
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


_CITRIX_EMPTY_APPS_ERROR = "No Application visible on Icron cloud."

_JENKINS_AUTOMATION_ERROR_PREFIX = (
    "Jenkins in-page automation did not complete"
)

_jenkins_automation_abort = {"requested": False, "step": "", "message": ""}


def _reset_jenkins_automation_abort():
    _jenkins_automation_abort["requested"] = False
    _jenkins_automation_abort["step"] = ""
    _jenkins_automation_abort["message"] = ""


def _record_jenkins_automation_failure(step=""):
    _jenkins_automation_abort["requested"] = True
    if step and not _jenkins_automation_abort["step"]:
        _jenkins_automation_abort["step"] = step


def _dropdown_missing_message(field, requested, options=None):
    """Name the field and the value that the dropdown does not offer."""
    message = (
        "%s value '%s' was not found in the Jenkins dropdown — "
        "build not submitted" % (field or "dropdown", requested)
    )
    available = [str(o) for o in (options or []) if str(o).strip()]
    if available:
        message += " (dropdown offers: %s)" % ", ".join(available)
    return message


def _record_dropdown_value_missing(field, requested, options=None):
    """Record a missing dropdown value, keeping the specific wording.

    A generic "did not complete" message forces the user back into the log to
    find out which field failed, so the specific text wins over the generic
    one built from ``step``.
    """
    _jenkins_automation_abort["requested"] = True
    if field and not _jenkins_automation_abort["step"]:
        _jenkins_automation_abort["step"] = field
    if not _jenkins_automation_abort["message"]:
        _jenkins_automation_abort["message"] = _dropdown_missing_message(
            field, requested, options
        )
    return _jenkins_automation_abort["message"]


def _jenkins_automation_error_message():
    specific = (_jenkins_automation_abort.get("message") or "").strip()
    if specific:
        return specific
    step = (_jenkins_automation_abort.get("step") or "").strip()
    if step:
        return (
            "%s — failed to fill '%s' field — build not submitted"
            % (_JENKINS_AUTOMATION_ERROR_PREFIX, step)
        )
    return "%s — build not submitted" % _JENKINS_AUTOMATION_ERROR_PREFIX


def _raise_if_jenkins_automation_failed(logger=None):
    if not _jenkins_automation_abort["requested"]:
        return
    message = _jenkins_automation_error_message()
    if logger is not None:
        logger.error(message)
    raise RuntimeError(message)


_EMPTY_APPS_MESSAGE_RE = re.compile(
    r"there\s+are\s+no\s+apps\s+or\s+desktops\s+available",
    re.IGNORECASE,
)
_ALL_ZERO_FILTER_RE = re.compile(
    r"\bAll\s*\(\s*0\s*\)",
    re.IGNORECASE,
)
# StoreFront renders sprite/template markup such as
# class="storeapp-action-link-sprite" even when zero apps are published, so a
# loose "storeapp" substring reports phantom tiles. Only the bare "storeapp"
# class token marks a real app tile.
_STOREAPP_TILE_RE = re.compile(
    r'class\s*=\s*"[^"]*(?<![-\w])storeapp(?![-\w])',
    re.IGNORECASE,
)

_citrix_empty_apps_abort = {"requested": False}
_citrix_empty_apps_state = {"detected": None}


def _empty_apps_wait_seconds():
    try:
        return float(os.environ.get("ENVPILOT_EMPTY_APPS_WAIT", "15"))
    except ValueError:
        return 15.0


def _reset_citrix_empty_apps_abort():
    _citrix_empty_apps_abort["requested"] = False
    _citrix_empty_apps_state["detected"] = None


def _driver_on_citrix_workspace(driver):
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        return False
    if "logonpoint" in url or "/logon/" in url:
        return False
    return "deshpdaweb" in url or "storefront" in url or (
        "/citrix/" in url and "logon" not in url
    )


def _get_driver_page_source(driver):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        return driver.page_source or ""
    except Exception:
        return ""


def _page_source_shows_empty_apps_message(page_source):
    if not page_source:
        return False
    return bool(_EMPTY_APPS_MESSAGE_RE.search(page_source))


def _page_source_shows_zero_apps_filter(page_source):
    if not page_source:
        return False
    return bool(_ALL_ZERO_FILTER_RE.search(page_source))


def _page_source_has_app_tiles(page_source):
    if not page_source:
        return False
    return bool(_STOREAPP_TILE_RE.search(page_source))


def _count_storeapp_tiles_via_js(driver):
    """Count real app tiles by exact class token, ignoring sprite markup."""
    try:
        driver.switch_to.default_content()
        count = driver.execute_script(
            "return Array.prototype.filter.call("
            "document.querySelectorAll('a, div'),"
            "function (el) { return el.classList.contains('storeapp'); }"
            ").length;"
        )
        return int(count or 0)
    except Exception:
        return 0


def _empty_apps_banner_visible(driver):
    """True when StoreFront's own empty-list element is rendered on screen."""
    try:
        driver.switch_to.default_content()
        return bool(
            driver.execute_script(
                "var el = document.querySelector("
                "'.no-apps-or-desktops-message');"
                "if (!el) { return false; }"
                "if (el.offsetParent === null "
                "&& !el.getClientRects().length) { return false; }"
                "return /no apps or desktops available/i.test("
                "el.textContent || '');"
            )
        )
    except Exception:
        return False


def _zero_count_filter_visible(driver):
    """True when the "All (n)" filter button reports zero items."""
    try:
        driver.switch_to.default_content()
        return bool(
            driver.execute_script(
                "var els = document.querySelectorAll("
                "'#allAppsFilterBtn, .filter-button.allApps');"
                "for (var i = 0; i < els.length; i++) {"
                " if (/\\(\\s*0\\s*\\)/.test(els[i].textContent || '')) {"
                "  return true; } }"
                "return false;"
            )
        )
    except Exception:
        return False


def _citrix_empty_apps_page_detected(driver, logger=None):
    """Return True when Citrix Workspace shows a stable empty Apps list."""
    if _citrix_empty_apps_state["detected"] is not None:
        return _citrix_empty_apps_state["detected"]
    if driver is None or not _driver_on_citrix_workspace(driver):
        _citrix_empty_apps_state["detected"] = False
        return False

    wait_s = _empty_apps_wait_seconds()
    short_grace = min(3.0, wait_s)
    poll_interval = 0.5
    start = time.time()
    deadline = start + wait_s
    consecutive_empty = 0
    saw_empty_signal = False

    while time.time() < deadline:
        src = _get_driver_page_source(driver)
        tile_count = _count_storeapp_tiles_via_js(driver)
        # Live DOM queries are authoritative; the page_source regexes only
        # cover the case where scripting is unavailable.
        has_tiles = tile_count > 0 or _page_source_has_app_tiles(src)
        has_msg = _empty_apps_banner_visible(driver) or (
            _page_source_shows_empty_apps_message(src)
        )
        has_zero = _zero_count_filter_visible(driver) or (
            _page_source_shows_zero_apps_filter(src)
        )

        if has_tiles:
            if logger is not None:
                logger.info(
                    "Citrix Workspace app list is not empty "
                    "(%d tile(s)) - continuing normally",
                    tile_count,
                )
            _citrix_empty_apps_state["detected"] = False
            return False
        if has_msg:
            saw_empty_signal = True
            consecutive_empty += 1
            if consecutive_empty >= 2:
                _citrix_empty_apps_state["detected"] = True
                return True
        elif has_zero:
            saw_empty_signal = True
            consecutive_empty += 1
            if consecutive_empty >= 4:
                _citrix_empty_apps_state["detected"] = True
                return True
        else:
            consecutive_empty = 0
            if not saw_empty_signal and (time.time() - start) >= short_grace:
                if logger is not None:
                    logger.info(
                        "No empty-app-list signal on the Citrix Workspace "
                        "page within %.1fs - continuing normally",
                        short_grace,
                    )
                _citrix_empty_apps_state["detected"] = False
                return False

        time.sleep(poll_interval)

    src = _get_driver_page_source(driver)
    tile_count = _count_storeapp_tiles_via_js(driver)
    has_tiles = tile_count > 0 or _page_source_has_app_tiles(src)
    if has_tiles:
        _citrix_empty_apps_state["detected"] = False
        return False
    if saw_empty_signal and (
        _empty_apps_banner_visible(driver)
        or _zero_count_filter_visible(driver)
        or _page_source_shows_empty_apps_message(src)
        or _page_source_shows_zero_apps_filter(src)
    ):
        _citrix_empty_apps_state["detected"] = True
        return True

    if logger is not None:
        logger.info(
            "Citrix Workspace app list check inconclusive after %.1fs "
            "(tiles=%d) - continuing normally",
            wait_s,
            tile_count,
        )
    _citrix_empty_apps_state["detected"] = False
    return False


def _raise_if_citrix_empty_apps_page(driver, logger):
    if not _citrix_empty_apps_page_detected(driver, logger):
        return
    _citrix_empty_apps_abort["requested"] = True
    if logger is not None:
        logger.error(
            "Citrix Workspace Apps list is empty — %s",
            _CITRIX_EMPTY_APPS_ERROR,
        )
    raise RuntimeError(_CITRIX_EMPTY_APPS_ERROR)


# Titles that belong to a shell, editor or file view rather than a logon box.
# A published PuTTY session reached this list as
# "/opt/SP/users/ogwvfde2/TRANSFER/tparasha/catalina.out - tparas - \\Remote".
_SHELL_WINDOW_MARKERS = (
    "CATALINA",
    "BASH",
    "SSH",
    "SFTP",
    "TELNET",
    "XTERM",
    "CONSOLE",
    "COMMAND PROMPT",
    "TAIL -",
    "/VAR/",
    "/OPT/",
    "/USR/",
    "/HOME/",
    "/ETC/",
    "/TMP/",
)

_SHELL_WINDOW_SUFFIXES = (".OUT", ".LOG", ".TXT", ".SH", ".CONF", ".XML")

# A window may only receive the password if its title says it is a logon box.
_LOGON_TITLE_MARKERS = (
    "PASSWORD",
    "SIGN IN",
    "SIGN-IN",
    "LOG ON",
    "LOGON",
    "AUTHENTICATION",
    "CREDENTIAL",
    "WINDOWS SECURITY",
    "DESKTOP VIEWER",
)


def _looks_like_remote_shell_window(title):
    """True when a title reads as a shell/file session rather than a logon box.

    Seamless published apps are hosted by the Citrix client process, so the
    process-name checks that catch a host-side PuTTY cannot see them. The
    title is the only thing that distinguishes them.
    """
    text = str(title or "").strip().upper()
    if not text:
        return False
    if any(marker in text for marker in _SHELL_WINDOW_MARKERS):
        return True
    # "name.out - user - \\Remote": test the first segment's extension.
    head = text.split(" - ")[0].strip().rstrip("\u2014").strip()
    return head.endswith(_SHELL_WINDOW_SUFFIXES)


def _citrix_logon_window_is_safe(title):
    """False when the password must not be typed into this window.

    Fails closed: a shell-looking title is only accepted if it also carries a
    logon word, so a genuine prompt that happens to mention a path still works
    while a published terminal never receives the password.
    """
    if not _looks_like_remote_shell_window(title):
        return True
    text = str(title or "").upper()
    return any(marker in text for marker in _LOGON_TITLE_MARKERS)


# Sampled from the Rebuild button fill, which is a flat (6, 63, 97).
_SUBMIT_FILL_MIN_BLUE = 90
_SUBMIT_FILL_MAX_RED = 120
_SUBMIT_FILL_BLUE_OVER_RED = 40
_SUBMIT_FILL_BLUE_OVER_GREEN = 25

_SUBMIT_SEARCH_TOP_RATIO = 0.55
_SUBMIT_CELL_PX = 8
_SUBMIT_SCAN_STEP_PX = 2
_SUBMIT_MIN_WIDTH_RATIO = 0.015
_SUBMIT_MAX_WIDTH_RATIO = 0.30
_SUBMIT_MIN_HEIGHT_RATIO = 0.010
_SUBMIT_MAX_HEIGHT_RATIO = 0.10
_SUBMIT_MIN_FILL_DENSITY = 0.60
# A solid button reads ~0.89 of its own box; the navy ``BuildNummm`` label
# reads 0.14 and the page headings 0.05-0.15 (measured 15 Sep).
_SUBMIT_MIN_INTERIOR_FILL = 0.50
# Retry passes search higher up the page: once the button must be scrolled
# into view there is no telling where in the viewport it lands.
_SUBMIT_RESCAN_TOP_RATIO = 0.20
_SUBMIT_SCROLL_NOTCHES = 4
# The ``Rebuild`` breadcrumb sits at the top of the page, so label matches
# are only trusted below this point.
_SUBMIT_LABEL_MIN_CY = 0.40
_SUBMIT_LABEL_WORDS = ("rebuild", "build")

# Tokens within this much of the hit's centre line count as the same row.
_JOB_ROW_TOLERANCE = 0.01
_JOB_CENTRE_MIN_SHIFT = 0.005

# Preference order; the leading-space spelling is the one the compiled backend
# was built against.
_JENKINS_UPLOAD_DIR_NAMES = (
    "Jenkins Deployment Script",
    " Jenkins Deployment Script",
)
_JENKINS_UPLOAD_SCRIPT_NAME = "jenkins_deploy_utility.py"

_PARAM_FORM_MARKERS = ("buildnummm", "parameterized")
_SUBMIT_CONFIRM_WAIT = 3.0


def _is_submit_button_fill(pixel):
    """True for the flat blue of a Jenkins primary button."""
    red, green, blue = pixel[0], pixel[1], pixel[2]
    return (
        blue > _SUBMIT_FILL_MIN_BLUE
        and red < _SUBMIT_FILL_MAX_RED
        and blue - red > _SUBMIT_FILL_BLUE_OVER_RED
        and blue - green > _SUBMIT_FILL_BLUE_OVER_GREEN
    )


def _submit_scroll_attempts():
    """How many scroll-and-rescan passes to spend looking for the button."""
    try:
        return max(0, int(os.environ.get("ENVPILOT_SUBMIT_SCROLL_ATTEMPTS", "4")))
    except ValueError:
        return 4


def _interior_fill_fraction(image, box):
    """Share of the box's own pixels carrying the button fill.

    The cell-density test alone cannot tell a button from a word: both fill
    their bounding box with touching cells. Measuring the real pixels inside
    the box separates them cleanly -- a solid button keeps ~0.9 (only its white
    glyphs interrupt the fill) while navy text keeps ~0.15, the rest being page
    background between the strokes.
    """
    crop = image.convert("RGB").crop(box)
    width, height = crop.size
    if not width or not height:
        return 0.0
    pixels = crop.load()
    hits = 0
    total = 0
    for y in range(0, height, _SUBMIT_SCAN_STEP_PX):
        for x in range(0, width, _SUBMIT_SCAN_STEP_PX):
            total += 1
            if _is_submit_button_fill(pixels[x, y]):
                hits += 1
    return hits / float(total or 1)


def _pixel_clusters(cells):
    """Group touching cells, so each blue shape on the page is separate."""
    clusters = []
    seen = set()
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        cluster = []
        while stack:
            cell_x, cell_y = stack.pop()
            cluster.append((cell_x, cell_y))
            for step_x in (-1, 0, 1):
                for step_y in (-1, 0, 1):
                    neighbour = (cell_x + step_x, cell_y + step_y)
                    if neighbour in cells and neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
        clusters.append(cluster)
    return clusters


def _find_submit_button_centre(image, top_ratio=_SUBMIT_SEARCH_TOP_RATIO):
    """Locate the blue submit button and return its centre as (x, y) ratios.

    The styled button carries white text on a solid fill, so Tesseract returns
    no token for it at all -- the compiled helper therefore clicks a fixed
    0.09 of the window height below the last parameter label. The real gap is
    about 0.143, so that click lands on the ``BuildNummm`` label and the page
    never submits while still reporting success. The fill colour is the one
    part of the button that is unambiguous, so match on that instead.

    Returns ``None`` when nothing button-shaped is found, leaving the caller
    to fall back rather than click a guessed position.
    """
    width, height = image.size
    if not width or not height:
        return None

    top = int(height * top_ratio)
    crop = image.convert("RGB").crop((0, top, width, height))
    crop_width, crop_height = crop.size
    pixels = crop.load()

    cells = set()
    for y in range(0, crop_height, _SUBMIT_SCAN_STEP_PX):
        for x in range(0, crop_width, _SUBMIT_SCAN_STEP_PX):
            if _is_submit_button_fill(pixels[x, y]):
                cells.add((x // _SUBMIT_CELL_PX, y // _SUBMIT_CELL_PX))
    if not cells:
        return None

    candidates = []
    for cluster in _pixel_clusters(cells):
        xs = [cell[0] for cell in cluster]
        ys = [cell[1] for cell in cluster]
        left = min(xs) * _SUBMIT_CELL_PX
        right = (max(xs) + 1) * _SUBMIT_CELL_PX
        box_top = min(ys) * _SUBMIT_CELL_PX + top
        box_bottom = (max(ys) + 1) * _SUBMIT_CELL_PX + top

        box_width_ratio = (right - left) / float(width)
        box_height_ratio = (box_bottom - box_top) / float(height)
        if not (
            _SUBMIT_MIN_WIDTH_RATIO <= box_width_ratio <= _SUBMIT_MAX_WIDTH_RATIO
            and _SUBMIT_MIN_HEIGHT_RATIO <= box_height_ratio <= _SUBMIT_MAX_HEIGHT_RATIO
        ):
            continue

        # A button is a solid fill; a focused input's blue border is hollow and
        # would otherwise pass the size test on its own.
        cells_wide = max(xs) - min(xs) + 1
        cells_high = max(ys) - min(ys) + 1
        if len(cluster) / float(cells_wide * cells_high) < _SUBMIT_MIN_FILL_DENSITY:
            continue

        # Cell density passes for any dense navy shape, text included: the
        # 15 Sep captures matched the ``BuildNummm`` label at 24%,91% and
        # clicked it twice while the real button sat below the fold.
        if (
            _interior_fill_fraction(image, (left, box_top, right, box_bottom))
            < _SUBMIT_MIN_INTERIOR_FILL
        ):
            continue

        candidates.append(
            (
                box_bottom,
                (left + right) / 2.0 / float(width),
                (box_top + box_bottom) / 2.0 / float(height),
            )
        )

    if not candidates:
        return None

    # The submit button sits below the parameter rows, so prefer the lowest.
    _bottom, x_ratio, y_ratio = max(candidates)
    return x_ratio, y_ratio


def _find_submit_label_centre(words):
    """Centre of a ``Rebuild``/``Build`` token in the lower page, or None.

    The fill scan is the primary finder because the button's white-on-navy
    label usually returns no token at all. When Tesseract does read it this
    still beats the compiled fixed offset. Matches above
    ``_SUBMIT_LABEL_MIN_CY`` are ignored: ``Rebuild`` also appears in the
    breadcrumb, and clicking that reloads the form with every parameter lost.
    """
    best = None
    for word in words or ():
        norm = str(word.get("norm") or "").strip()
        if norm not in _SUBMIT_LABEL_WORDS:
            continue
        cx, cy = word.get("cx"), word.get("cy")
        if cx is None or cy is None or cy < _SUBMIT_LABEL_MIN_CY:
            continue
        if best is None or cy > best[1]:
            best = (float(cx), float(cy))
    return best


def _still_on_param_form(words):
    """True while the parameter form is on screen, i.e. nothing was submitted.

    Both the ``BuildNummm`` label and the ``/rebuild/parameterized`` address
    disappear once Jenkins accepts the build, so either one still being
    readable means the click missed.
    """
    if not words:
        return False
    joined = " ".join(str(word.get("norm") or "") for word in words)
    return any(marker in joined for marker in _PARAM_FORM_MARKERS)


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
    send_arrow = ns.get("_send_arrow_key")
    ocr_dump = ns.get("_jenkins_ocr_dump")
    scroll_to_top = ns.get("_jenkins_scroll_to_top")
    scroll_down = ns.get("_jenkins_scroll_down")

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

    def _remeasure_after_scroll(edge_hwnd, labels, label, logger):
        """Re-measure a revealed label once the scroll animation has stopped.

        ``_jenkins_reveal_label`` scrolls the label into view and OCRs straight
        away, but Edge animates keyboard scrolling, so the geometry it hands
        back can be a mid-flight frame. Every click derived from it then lands
        where the row *was*. BuildNumber was measured near cy 0.90 and clicked
        at 0.94; by then the row had settled at 0.84 and the next parameter's
        label occupied 0.94, so the triple-click selected that label as page
        text and the paste went into a non-editable area.

        Returns a replacement ``(label, words)`` only when the row actually
        moved, so a page that was already still keeps the original reading.
        """
        if not _scroll_settle_enabled():
            return None
        if not (
            callable(capture_image)
            and callable(ocr_tesseract_data)
            and callable(normalize_ocr)
            and callable(find_any)
        ):
            return None
        try:
            before_cy = float(label["cy"])
        except (KeyError, TypeError, ValueError):
            return None

        image = _wait_until_settled(capture_image, _live_hwnd(edge_hwnd), logger)
        if image is None:
            return None
        words = _ocr_words_from_image(
            image, ocr_tesseract_data, normalize_ocr, logger
        )
        if not words:
            return None
        try:
            fresh, _which = find_any(words, labels, min_conf=_PARAM_VERIFY_LABEL_CONF)
        except TypeError:
            fresh, _which = find_any(words, labels)
        if not fresh:
            return None
        try:
            after_cy = float(fresh["cy"])
        except (KeyError, TypeError, ValueError):
            return None
        if abs(after_cy - before_cy) < _SETTLE_MIN_SHIFT:
            return None
        if logger is not None:
            logger.info(
                "Jenkins OCR: %s moved while the page was still scrolling "
                "(cy %.3f -> %.3f) — re-measured on the settled page so the "
                "click lands on the control, not the row below it",
                labels[0] if labels else "label",
                before_cy,
                after_cy,
            )
        return fresh, words

    # The fill routines click the label position plus 0.04 and clamp the result
    # to 0.94, so a label revealed below this has no room for its control.
    _LABEL_MAX_CY = 0.84

    def _lift_label_into_view(edge_hwnd, labels, label, logger):
        """Scroll on when a label was revealed against the bottom edge.

        ``_jenkins_reveal_label`` stops the moment OCR can see the label, which
        for the last parameters on the page means at the very bottom: the 10:29
        capture has BuildNumber's label at cy 0.94 with its input box below the
        fold. Every control sits *under* its label, so cy + 0.04 exceeded the
        0.94 ceiling the fill routine clamps to and the click came back onto the
        label — the capture shows "BuildNumber" selected as page text and both
        it and FOLDER_NAME left empty after their pastes.

        One more scroll step puts the label mid-viewport with its control
        visible underneath. Returns a replacement ``(label, words)``, or None to
        keep the original reading.
        """
        if not (_scroll_settle_enabled() and callable(scroll_down)):
            return None
        try:
            before_cy = float(label["cy"])
        except (KeyError, TypeError, ValueError):
            return None
        if before_cy <= _LABEL_MAX_CY:
            return None
        try:
            scroll_down(edge_hwnd, logger, 3)
        except TypeError:
            scroll_down(edge_hwnd, logger)
        time.sleep(0.4)

        image = _wait_until_settled(capture_image, _live_hwnd(edge_hwnd), logger)
        if image is None:
            return None
        words = _ocr_words_from_image(image, ocr_tesseract_data, normalize_ocr, logger)
        if not words:
            return None
        try:
            fresh, _which = find_any(words, labels, min_conf=_PARAM_VERIFY_LABEL_CONF)
        except TypeError:
            fresh, _which = find_any(words, labels)
        if not fresh:
            return None
        try:
            after_cy = float(fresh["cy"])
        except (KeyError, TypeError, ValueError):
            return None
        # Scrolling past it is worse than the bottom edge: keep the original.
        if after_cy > _LABEL_MAX_CY or after_cy < 0.1:
            return None
        if logger is not None:
            logger.info(
                "Jenkins OCR: %s was revealed at the bottom edge (cy %.3f) with "
                "its control below the fold — scrolled on to cy %.3f so the "
                "click lands in the control instead of the label",
                labels[0] if labels else "label",
                before_cy,
                after_cy,
            )
        return fresh, words

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
                try:
                    label, words = result
                except (TypeError, ValueError):
                    return result
                if label is None:
                    return result

            # Advisory refinement only: a capture or OCR hiccup here must never
            # take down a run that the original reading could have completed.
            try:
                settled = _remeasure_after_scroll(edge_hwnd, labels, label, logger)
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: scroll-settle re-measure failed (%s) — "
                        "using the original reading",
                        exc,
                    )
                settled = None
            if settled is not None:
                label, words = settled
                result = settled

            try:
                lifted = _lift_label_into_view(edge_hwnd, labels, label, logger)
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: bottom-edge lift failed (%s) — using the "
                        "revealed position",
                        exc,
                    )
                lifted = None
            return lifted if lifted is not None else result

        ns["_jenkins_reveal_label"] = _jenkins_reveal_label

    def _field_already_shows_value(edge_hwnd, placeholder, value, logger):
        """True when the box already displays ``value``.

        The verified fill locates its target by OCR-ing the placeholder, so a
        successful paste hides the very anchor the check needs: the 16:13
        capture shows "sudhansh" sitting in the box at confidence 84 while
        "Username" is absent from the page entirely. The check then reported
        "box click kept missing", which sent the login into re-click, scroll
        and finally zoom-to-fit — the escalation that destroyed the Edge
        handle. Reading the value instead settles it from the field's own
        pixels.
        """
        if not value or not callable(orig_read) or not callable(find_any):
            return False
        # A masked field never shows its value, so a read proves nothing.
        if "password" in str(placeholder or "").lower():
            return False
        try:
            _image, words = orig_read(_live_hwnd(edge_hwnd), logger)
        except Exception:
            return False
        if not words:
            return False
        try:
            match, _which = find_any(words, [str(value)])
        except Exception:
            return False
        if not match:
            return False
        if logger is not None:
            logger.info(
                "Jenkins login: %s already contains the requested value — "
                "its placeholder disappears once the box is filled, so the "
                "placeholder-based check misreported it as missing; "
                "accepting the fill instead of escalating",
                placeholder,
            )
        return True

    orig_fill_verified = ns.get("_jenkins_fill_field_verified")

    if callable(orig_fill_verified) and callable(ocr_dump):

        def _jenkins_fill_field_verified(*args, **kwargs):
            """Capture the page when a verified fill gives up.

            'Build on' is prefilled by Jenkins with its own default, so a fill
            that fails verification leaves the field in one of several states —
            still the default, the pasted value OCR misread, or the two run
            together — and the run aborts before any screenshot is taken. The
            13:52 failure exhausted all four attempts with no capture, leaving
            nothing to diagnose. Observation only: the result is passed straight
            through.
            """
            result = orig_fill_verified(*args, **kwargs)
            if not result:
                placeholder = _arg(args, kwargs, 1, "placeholder")
                logger = _arg(args, kwargs, 3, "logger")
                if _field_already_shows_value(
                    _arg(args, kwargs, 0, "edge_hwnd"),
                    placeholder,
                    _arg(args, kwargs, 2, "value"),
                    logger,
                ):
                    return True
                try:
                    tag = "fill_fail_{}".format(
                        re.sub(r"\W+", "_", str(placeholder or "field")).strip("_")
                    )
                    ocr_dump(_arg(args, kwargs, 0, "edge_hwnd"), tag, logger)
                except Exception:
                    pass
                step = str(placeholder or "").strip()
                if step:
                    _record_jenkins_automation_failure(step)
            return result

        ns["_jenkins_fill_field_verified"] = _jenkins_fill_field_verified

    orig_fill_build_on = ns.get("_jenkins_fill_build_on")

    if callable(orig_fill_build_on) and callable(ocr_dump):

        def _jenkins_fill_build_on(*args, **kwargs):
            """Capture the page when the 'Build on' fill gives up.

            Jenkins prefills this field with its own date, so a failed fill
            leaves it in one of several states and the run stops with no
            capture of what the box actually held — the 16:25 failure burned
            four attempts and left nothing to diagnose. Observation only: the
            result is passed straight through.
            """
            result = orig_fill_build_on(*args, **kwargs)
            if not result:
                edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
                logger = _arg(args, kwargs, 2, "logger")
                try:
                    ocr_dump(edge_hwnd, "fill_fail_Build_on", logger)
                except Exception:
                    pass
                _record_jenkins_automation_failure("Build on")
            return result

        ns["_jenkins_fill_build_on"] = _jenkins_fill_build_on

    orig_jenkins_after_launch = ns.get("_run_jenkins_automation_after_launch")
    if callable(orig_jenkins_after_launch):

        def _run_jenkins_automation_after_launch(*args, **kwargs):
            ok = orig_jenkins_after_launch(*args, **kwargs)
            if not ok:
                _record_jenkins_automation_failure()
            return ok

        ns["_run_jenkins_automation_after_launch"] = (
            _run_jenkins_automation_after_launch
        )

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
    }

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

    def _window_is_topmost_at_centre(hwnd):
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
        root = user32.GetAncestor(top, 2)  # GA_ROOT
        return bool(root == hwnd)

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
            # seamless proxy); fall back, but say so, and note whether the
            # frame we are about to grab even belongs to the target window.
            capture_state["grabbed"] += 1
            if logger is not None and capture_state["grabbed"] <= 3:
                on_top = _window_is_topmost_at_centre(desktop_hwnd)
                if on_top is False:
                    capture_state["occluded"] += 1
                    logger.warning(
                        "Jenkins OCR: PrintWindow returned %s and another "
                        "window is on top - the screen grab may read the wrong "
                        "window",
                        "nothing" if image is None else "a blank frame",
                    )
                else:
                    logger.info(
                        "Jenkins OCR: PrintWindow returned %s - using the "
                        "screen grab (target appears to be on top)",
                        "nothing" if image is None else "a blank frame",
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

    orig_open_new_edge = ns.get("_open_new_citrix_edge_with_jenkins")

    if callable(orig_open_new_edge):
        own_window_enabled = os.environ.get(
            "ENVPILOT_JENKINS_OWN_WINDOW", "1"
        ).strip().lower() not in ("0", "false", "no")
        try:
            focus_attempts = int(
                os.environ.get("ENVPILOT_NEW_WINDOW_FOCUS_ATTEMPTS", "8")
            )
        except ValueError:
            focus_attempts = 8

        def _session_edge_hwnds(logger):
            lister = ns.get("_list_citrix_session_edge_hwnds")
            if not callable(lister):
                return set()
            for call in (lambda: lister(), lambda: lister(logger)):
                try:
                    return set(call() or ())
                except TypeError:
                    continue
                except Exception:
                    return set()
            return set()

        def _window_title(hwnd):
            get_title = ns.get("_get_window_title")
            if not hwnd or not callable(get_title):
                return ""
            try:
                return get_title(hwnd) or ""
            except Exception:
                return ""

        def _new_window_is_foreground(new_hwnd):
            """Same test the compiled code uses, so behaviour is unchanged."""
            info = ns.get("_get_foreground_window_info")
            seamless = ns.get("_is_citrix_seamless_edge_title")
            if not callable(info):
                return False, ""
            try:
                fg_hwnd, fg_title = info()
            except Exception:
                return False, ""
            if fg_hwnd == new_hwnd:
                return True, fg_title
            if callable(seamless):
                try:
                    if seamless(fg_title):
                        return True, fg_title
                except Exception:
                    pass
            return False, fg_title

        def _paste_jenkins_url(new_hwnd, logger):
            """The tail of the compiled function, once the window is in front."""
            url = ns.get("CITRIX_JENKINS_LOGIN_URL")
            copy_clip = ns.get("_copy_to_clipboard_windows")
            release = ns.get("_release_keyboard_modifiers")
            ctrl_l = ns.get("_send_ctrl_l")
            ctrl_v = ns.get("_send_ctrl_v")
            enter = ns.get("_send_enter_key")
            front = ns.get("_bring_window_to_front")
            if not (url and callable(copy_clip) and callable(ctrl_l)
                    and callable(ctrl_v) and callable(enter)):
                return False
            if not copy_clip(url, logger, label="jenkins login url"):
                if logger is not None:
                    logger.warning("Could not copy Jenkins URL to clipboard")
                return False
            if callable(release):
                release()
            ctrl_l()
            time.sleep(0.5)
            ctrl_v()
            time.sleep(0.35)
            enter()
            if logger is not None:
                logger.info(
                    "Jenkins: recovered the new Edge window and loaded the "
                    "Jenkins URL: %s",
                    url,
                )
            time.sleep(5.0)
            if callable(front):
                front(new_hwnd, logger)
            return True

        def _open_new_citrix_edge_with_jenkins(*args, **kwargs):
            """Keep Jenkins in its own window even if focus is stolen.

            The compiled version focuses the window Ctrl+N just made, waits
            0.8s, checks the foreground once, and on a single miss logs
            ``New Edge window not foreground ... skipping paste`` and returns
            False. The caller then falls back to loading Jenkins in the user's
            existing window, where it becomes one tab among many — and an Edge
            window's title and content follow the *active tab*, so any later
            tab switch silently points OCR at the wrong page. That is what
            happened on 13 Aug 10:25:46, when a Teams chat window held the
            foreground for a moment.

            Rather than re-running the original (every call spends another
            Ctrl+N and would leave orphan windows), finish the job on the
            window it already created, retrying the focus.
            """
            logger = _arg(args, kwargs, 2, "logger")
            if not own_window_enabled:
                return orig_open_new_edge(*args, **kwargs)
            before = _session_edge_hwnds(logger)
            result = orig_open_new_edge(*args, **kwargs)
            if result:
                return result

            created = _session_edge_hwnds(logger) - before
            if not created:
                # Nothing was opened, so a plain retry cannot leave orphans.
                if logger is not None:
                    logger.info(
                        "Jenkins: no new Edge window was created - retrying "
                        "the open once"
                    )
                return orig_open_new_edge(*args, **kwargs)

            user32 = ctypes.windll.user32
            new_hwnd = max(created)
            focus_window = ns.get("_focus_window_handle")
            front = ns.get("_bring_window_to_front")
            if logger is not None:
                logger.warning(
                    "Jenkins: new Edge window %r exists but the paste was "
                    "skipped; retrying focus up to %d times so Jenkins keeps "
                    "its own window instead of becoming a tab",
                    _window_title(new_hwnd) or new_hwnd,
                    focus_attempts,
                )
            for attempt in range(1, max(1, focus_attempts) + 1):
                if not user32.IsWindow(new_hwnd):
                    if logger is not None:
                        logger.error(
                            "Jenkins: the new Edge window disappeared while "
                            "waiting for focus"
                        )
                    return False
                if callable(focus_window):
                    focus_window(new_hwnd, logger)
                if callable(front):
                    front(new_hwnd, logger)
                time.sleep(0.8)
                ok, fg_title = _new_window_is_foreground(new_hwnd)
                if ok:
                    if logger is not None:
                        logger.info(
                            "Jenkins: new Edge window came to the front on "
                            "attempt %d - pasting the Jenkins URL",
                            attempt,
                        )
                    return _paste_jenkins_url(new_hwnd, logger)
                if logger is not None:
                    logger.info(
                        "Jenkins: new Edge window still not foreground "
                        "(attempt %d/%d, fg=%r)",
                        attempt,
                        focus_attempts,
                        fg_title,
                    )
            if logger is not None:
                logger.error(
                    "Jenkins: could not bring the new Edge window to the front "
                    "after %d attempts - not typing, so no keystrokes reach "
                    "another window",
                    focus_attempts,
                )
            return False

        ns["_open_new_citrix_edge_with_jenkins"] = (
            _open_new_citrix_edge_with_jenkins
        )

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

    # Compiled _jenkins_ocr_select_dropdown adds these to the revealed label
    # position before clicking.
    _COMPILED_DROPDOWN_CY_OFFSET = 0.04
    _COMPILED_DROPDOWN_CX_OFFSET = 0.03
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

    def _dropdown_reader_ready():
        return (
            callable(capture_image)
            and callable(ocr_tesseract_data)
            and callable(normalize_ocr)
            and callable(find_any)
        )

    def _anchored_dropdown_point(edge_hwnd, labels, logger):
        """Return (x_ratio, y_ratio) of the select itself, or None."""
        reveal = ns.get("_jenkins_reveal_label")
        if not callable(reveal):
            return None
        try:
            label, words = reveal(edge_hwnd, list(labels), logger)
        except (TypeError, ValueError):
            return None
        if not label:
            return None
        left_ratio, _source = _label_left_ratio(label, words, labels)
        if left_ratio is None:
            left_ratio = label.get("cx")
        if left_ratio is None:
            return None
        shifted_cy, _cy_source, _cy_offset = _dropdown_shifted_cy(label, words, labels)
        if shifted_cy is None:
            shifted_cy = label.get("cy")
        if shifted_cy is None:
            return None
        # Both anchors are pre-compensated for the offsets the compiled path
        # adds on top of a reported label position (+0.03 x, +0.04 y). This
        # clicks directly, so add both back to land on the control itself.
        return (
            max(0.01, float(left_ratio) - 0.015 + _COMPILED_DROPDOWN_CX_OFFSET),
            min(0.99, float(shifted_cy) + _COMPILED_DROPDOWN_CY_OFFSET),
        )

    def _set_dropdown_by_typeahead(edge_hwnd, labels, requested, logger):
        """Set a native <select> without leaving its option list on screen.

        The compiled Strategy A types a filter and then clicks the option row
        that matches — but a native <select> popup is an OS window of its own
        and never appears in the window-owned capture, so the only matching text
        OCR can see is the value the closed select already displays. That is why
        both clicks in the log land on the identical point: the second one
        reopens the box instead of choosing a row. Strategy B then steps through
        options one at a time for ~2.5 minutes with the list held open, which is
        also what gives the native popup time to destroy the Citrix window.

        Escape closes the popup and leaves the <select> focused, and Chromium
        type-ahead on a focused closed select jumps straight to the matching
        option — one step, nothing left open. Returns True on a verified change,
        or None to let the compiled routine try as before.

        Enter is deliberately never sent: on a Jenkins parameter form that would
        submit the build.
        """
        if not _direct_dropdown_enabled():
            return None
        if not (
            _dropdown_reader_ready()
            and callable(click_client_area)
            and callable(send_escape)
            and callable(type_text_unicode)
        ):
            return None

        point = _anchored_dropdown_point(edge_hwnd, labels, logger)
        if point is None:
            return None
        x_ratio, y_ratio = point
        hwnd = _live_hwnd(edge_hwnd)

        click_client_area(hwnd, logger, x_ratio=x_ratio, y_ratio=y_ratio)
        time.sleep(0.4)
        send_escape()
        time.sleep(0.3)
        if callable(release_mods):
            release_mods()
        type_text_unicode(requested, logger)
        time.sleep(0.5)

        status, reading = _read_dropdown(edge_hwnd, labels, requested, logger)
        if status == "ok":
            if logger is not None:
                logger.info(
                    "Jenkins OCR: set %s = %s by type-ahead on the closed "
                    "select (no option list left open, no arrow-scan)",
                    labels[0] if labels else "dropdown",
                    reading,
                )
            return True
        if logger is not None:
            logger.info(
                "Jenkins OCR: type-ahead on %s left %s (requested %s) — "
                "handing over to the compiled filter+click/arrow-scan",
                labels[0] if labels else "dropdown",
                reading or "an unreadable value",
                requested,
            )
        return None

    # A list longer than this is not a Jenkins parameter dropdown.
    _ENUM_MAX_OPTIONS = 60
    _ENUM_STEP_PAUSE = 0.22

    def _enumerate_enabled():
        return os.environ.get(
            "ENVPILOT_ENUMERATE_DROPDOWN", "1"
        ).strip().lower() not in ("0", "false", "no")

    def _capture_client_image(edge_hwnd, logger):
        """Capture the window's pixels whichever capture binding is in force.

        ``capture_image`` starts out as the compiled two-argument function but
        is rebound partway through ``_apply_overrides`` to the shim's own
        single-argument override, which reads its logger from ``live``. A
        two-argument call then raises TypeError at run time — that is what
        silenced the Release_name option-list diagnostic at 17:00:11 with
        "takes 1 positional argument but 2 were given", leaving the question
        of whether the requested option exists unanswered for a second run.
        """
        try:
            return capture_image(edge_hwnd, logger)
        except TypeError:
            return capture_image(edge_hwnd)

    def _control_box_now(edge_hwnd, labels, logger):
        """Locate the select's crop box once, for repeated reads at one position.

        Uses the settled capture the rest of the dropdown code reads from, so a
        page still easing to a stop cannot lose the label.
        """
        image = _wait_until_settled(capture_image, _live_hwnd(edge_hwnd), logger)
        if image is None:
            image = _capture_client_image(_live_hwnd(edge_hwnd), logger)
        if image is None:
            return None, None, "no capture"
        words = _ocr_words_from_image(
            image, ocr_tesseract_data, normalize_ocr, logger
        )
        if not words:
            return None, None, "capture produced 0 OCR tokens"
        label = None
        try:
            label, _which = find_any(
                words, list(labels), min_conf=_PARAM_VERIFY_LABEL_CONF
            )
        except TypeError:
            label, _which = find_any(words, list(labels))
        if not label:
            return None, None, "label not found among %d tokens" % len(words)
        box = _param_value_box(label, words, labels, normalize_ocr)
        if box is None:
            return None, None, "could not derive a box from the label"
        return box, image, None

    def _read_box(edge_hwnd, box, logger):
        """Best reading of one fixed crop box, without re-locating the label."""
        image = _capture_client_image(_live_hwnd(edge_hwnd), logger)
        if image is None:
            return ""
        readings = _read_control_readings(image, box, configure_tesseract, logger)
        return max(readings, key=len) if readings else ""

    def _step_option(down):
        send_arrow(down)
        time.sleep(_ENUM_STEP_PAUSE)

    def _enumerate_dropdown_options(edge_hwnd, labels, requested, logger):
        """Read out a select's option list, then land on the requested value.

        A native ``<select>`` popup is an OS window of its own, so it never
        appears in the window-owned capture: OCR can only ever see the closed
        control's current value, and nothing in the log has been able to say
        whether a requested option exists at all. Release_name has failed
        identically in every run — type-ahead, the compiled filter+click and two
        full arrow-scans all left it on 4000_WAVE11_PCK1 — which is what a value
        that is *not in the list* looks like.

        Stepping the closed select with arrow keys and reading each value from
        its own pixels enumerates the list, which both answers that question and
        gives a selection path that does not depend on type-ahead: once the list
        is known, the requested option is a known number of steps from the top.

        Returns True when it lands on the requested value, False when the value
        is provably absent, or None when the list could not be read.
        """
        name = labels[0] if labels else "dropdown"

        def _give_up(reason):
            if logger is not None:
                logger.info(
                    "Jenkins OCR: could not read %s's option list (%s) — "
                    "falling back to the compiled routine",
                    name,
                    reason,
                )
            return None

        if not _enumerate_enabled():
            return None
        if not callable(send_arrow):
            return _give_up("no arrow-key primitive")
        if not (
            _dropdown_reader_ready()
            and callable(click_client_area)
            and callable(send_escape)
            and callable(capture_image)
        ):
            return _give_up("reader or input primitives unavailable")

        point = _anchored_dropdown_point(edge_hwnd, labels, logger)
        if point is None:
            return _give_up("could not anchor a click on the select")
        if logger is not None:
            logger.info(
                "Jenkins OCR: reading %s's option list from the control to find "
                "out whether %s is even offered",
                name,
                requested,
            )
        hwnd = _live_hwnd(edge_hwnd)
        click_client_area(hwnd, logger, x_ratio=point[0], y_ratio=point[1])
        time.sleep(0.4)
        send_escape()
        time.sleep(0.3)
        if callable(release_mods):
            release_mods()

        box, _image, why = _control_box_now(edge_hwnd, labels, logger)
        if box is None:
            return _give_up(why or "could not locate the control")
        started_on = _read_box(edge_hwnd, box, logger)

        # No Home key primitive exists, so walk to the top instead.
        for _ in range(_ENUM_MAX_OPTIONS):
            send_arrow(False)
        time.sleep(0.4)

        options = []
        previous = None
        unchanged = 0
        for _ in range(_ENUM_MAX_OPTIONS):
            reading = _read_box(edge_hwnd, box, logger)
            if reading and reading == previous:
                unchanged += 1
                if unchanged >= 2:
                    break
            else:
                unchanged = 0
                if reading and reading not in options:
                    options.append(reading)
            previous = reading
            _step_option(True)

        if not options:
            return _give_up(
                "stepped the select but every read of its box was blank"
            )

        # An exact digit match is trusted straight away. Only when nothing
        # matches exactly is zero padding treated as OCR noise, and then only if
        # exactly one option qualifies: this list holds both PCK2 and PCK02, so
        # an ambiguous padding match must not be resolved by guessing.
        wanted = None
        for index, reading in enumerate(options):
            if _param_values_match(
                reading, requested, normalize_ocr
            ) and _digits_exact(reading, requested):
                wanted = index
                break

        if wanted is None:
            padded = [
                index
                for index, reading in enumerate(options)
                if _param_values_match(reading, requested, normalize_ocr)
                and _digits_agree(reading, requested)
            ]
            if len(padded) == 1:
                wanted = padded[0]
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: %s option %d reads %s, which is %s with a "
                        "zero too narrow to survive the crop — it is the only "
                        "option that fits, so selecting it",
                        name,
                        wanted + 1,
                        options[wanted],
                        requested,
                    )
            elif len(padded) > 1:
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: %s has %d options that could be %s once "
                        "zero padding is ignored (%s) — refusing to guess",
                        name,
                        len(padded),
                        requested,
                        " | ".join(options[i] for i in padded),
                    )

        if logger is not None:
            logger.info(
                "Jenkins OCR: %s option list as read from the control (%d "
                "values): %s",
                labels[0] if labels else "dropdown",
                len(options),
                " | ".join(options),
            )
        # Kept so the abort message can name what the list actually offers.
        live["dropdown_options"] = (name, list(options))

        target = wanted if wanted is not None else None
        if target is None:
            # Put the select back where the page had it, so the pre-submit
            # capture still shows what Jenkins would actually build.
            restore = None
            for index, reading in enumerate(options):
                if started_on and reading == started_on:
                    restore = index
                    break
            target = restore

        if target is not None:
            for _ in range(_ENUM_MAX_OPTIONS):
                send_arrow(False)
            time.sleep(0.3)
            for _ in range(target):
                _step_option(True)

        if wanted is None:
            if logger is not None:
                logger.warning(
                    "Jenkins OCR: %s = %s is NOT in the dropdown — the list "
                    "holds %s. No selection method can set a value the list "
                    "does not contain; correct the stored value. Left the "
                    "select on %s.",
                    labels[0] if labels else "dropdown",
                    requested,
                    " | ".join(options),
                    started_on or "its original value",
                )
            return False

        status, reading = _read_dropdown(edge_hwnd, labels, requested, logger)
        if status == "ok":
            if logger is not None:
                logger.info(
                    "Jenkins OCR: set %s = %s by stepping to option %d of %d "
                    "(no type-ahead, no filter)",
                    labels[0] if labels else "dropdown",
                    reading,
                    wanted + 1,
                    len(options),
                )
            return True
        if logger is not None:
            logger.info(
                "Jenkins OCR: stepped %s to option %d of %d but it reads %s "
                "— handing over to the compiled routine",
                labels[0] if labels else "dropdown",
                wanted + 1,
                len(options),
                reading or "an unreadable value",
            )
        return None

    def _read_dropdown(edge_hwnd, labels, requested, logger, label=None):
        """Settle the page, then classify what the select currently shows."""
        image = _wait_until_settled(capture_image, _live_hwnd(edge_hwnd), logger)
        if image is None:
            return "unreadable", ""
        return _read_param_control(
            image,
            labels,
            requested,
            find_any,
            normalize_ocr,
            ocr_tesseract_data,
            configure_tesseract,
            logger,
            label=label,
        )

    def _dropdown_already_correct(edge_hwnd, labels, requested, logger):
        """True when the select already holds the requested value.

        Nothing on this page needed changing for ProjectName: it read ``OGW``
        in the very first capture of every run. The compiled code could not know
        that, because reading the value needs the de-fringed crop above, so it
        opened the list, typed, clicked again and then spent ~2.5 minutes per
        attempt stepping through options — twice — on a field that was already
        right, and the native popup destroyed the Citrix window handle on the
        way out. Checking first turns all of that into a no-op.
        """
        if not (_dropdown_skip_enabled() and requested and _dropdown_reader_ready()):
            return False
        status, reading = _read_dropdown(edge_hwnd, labels, requested, logger)
        if status != "ok":
            return False
        if logger is not None:
            logger.info(
                "Jenkins OCR: %s already shows %s — skipping the dropdown "
                "entirely and moving to the next parameter",
                labels[0] if labels else "dropdown",
                reading or requested,
            )
        return True

    def _dropdown_readback_mismatch(edge_hwnd, label, labels, requested, logger):
        """Re-read a select after a claimed success; True on a clear mismatch.

        The compiled arrow-scan confirms its own progress with OCR taken from
        the whole page, where a ~56x26px select is unreadable: ENVIRONMENT_NAME
        showing SIT1 read as 'sitiv' then 'sity'. That was close enough to
        garbage to match, so the scan logged "selected ENVIRONMENT_NAME = SIT5"
        against a control it had never changed, and the wrong value survived all
        the way to the submit guard. Reading the control alone tells a real
        selection from a claimed one.
        """
        if not (_dropdown_readback_enabled() and requested):
            return False
        if not (_dropdown_reader_ready() and label):
            return False
        status, reading = _read_dropdown(
            edge_hwnd, labels, requested, logger, label=label
        )
        if status == "ok":
            if logger is not None:
                logger.info(
                    "Jenkins OCR: read back %s = %s from the control itself "
                    "(requested %s) — selection confirmed",
                    labels[0] if labels else "dropdown",
                    reading,
                    requested,
                )
            return False
        if status == "mismatch" and logger is not None:
            logger.warning(
                "Jenkins OCR: %s still shows %s after the scan reported "
                "success (requested %s) — treating the selection as failed so "
                "the build cannot be submitted with the wrong value",
                labels[0] if labels else "dropdown",
                reading,
                requested,
            )
        return status == "mismatch"

    def _edge_window_title(edge_hwnd):
        get_title = ns.get("_get_window_title")
        if not callable(get_title):
            return ""
        try:
            return get_title(_live_hwnd(edge_hwnd)) or ""
        except Exception:
            return ""

    def _on_jenkins_rebuild_page(edge_hwnd):
        """True when the open page is the Rebuild plugin's parameter form.

        "Rebuild Last" opens .../rebuild/parameterized, where every parameter
        — including the ones the flow calls dropdowns — is a plain text input
        pre-filled with the previous build's value. Build with Parameters
        renders the same parameters as real <select> controls, so the page
        decides which mechanics are correct.
        """
        title = _edge_window_title(edge_hwnd).upper()
        return "REBUILD" in title and "JENKINS" in title

    def _fill_param_as_text_field(edge_hwnd, labels, requested, field_key, logger):
        """Set a Rebuild-page parameter the way its text fields are already set.

        ``_jenkins_fill_param_textfield`` is the routine FOLDER_NAME and
        BuildNumber go through: it triple-clicks the box — which selects the
        pre-filled value so the paste replaces it — then verifies and retries.
        Its signature matches the dropdown selector's, so the arguments pass
        straight through.

        An earlier attempt used ``_jenkins_ocr_fill``, which aims at the label
        rather than the control: on 10 Sep 12:59 it clicked y=34.1% for
        Release_name when the input sits at 38.3%, so the paste hit label text,
        took no focus and was lost, leaving the old value in place.
        """
        filler = ns.get("_jenkins_fill_param_textfield")
        if not callable(filler):
            if logger is not None:
                logger.warning(
                    "Jenkins OCR: no text-field filler available for %s",
                    field_key or (labels[0] if labels else "parameter"),
                )
            return False
        try:
            return bool(
                filler(
                    _live_hwnd(edge_hwnd),
                    list(labels),
                    requested,
                    field_key,
                    logger,
                )
            )
        except Exception as exc:
            if logger is not None:
                logger.warning(
                    "Jenkins OCR: text fill of %s failed (%s)",
                    field_key or (labels[0] if labels else "parameter"),
                    exc,
                )
            return False

    orig_value_on_page = ns.get("_jenkins_value_on_page")

    if callable(orig_value_on_page) and callable(normalize_ocr):

        def _jenkins_value_on_page(*args, **kwargs):
            """Confirm a fill with the tolerance the submit guard already uses.

            The plain check compares OCR tokens at a 0.92 fuzzy ratio with no
            glyph folding, so a *correct* paste that reads back as ``sits``
            (SIT5), ``26.10.0MI`` (26.10.OMI) or ``4000_WAVE11_PCKO2``
            (…PCK02) fails it. ``_jenkins_fill_param_textfield`` then pastes a
            second time and settles for "OCR verify inconclusive": on 10 Sep
            14:14 three of the four fields were pasted twice for this reason.
            The values were right — the triple-click reselects, so paste two
            replaced paste one — but that only holds while every reselect
            lands, and a miss would append instead of replace.

            ``_param_values_match`` folds the confusable glyphs and is the
            comparison the pre-submit guard is already trusted with. It still
            separates SIT1 from SIT5 and PCK1 from PCK02, so this admits OCR
            noise without admitting a wrong value. Only reached when the
            original check has already said no, and the dropdown paths do not
            use this helper.
            """
            if orig_value_on_page(*args, **kwargs):
                return True
            words = _arg(args, kwargs, 0, "words")
            value = _arg(args, kwargs, 1, "value")
            if not (words and value):
                return False
            for word in words:
                if isinstance(word, dict):
                    text = word.get("norm") or word.get("text")
                else:
                    text = word
                if not text:
                    continue
                try:
                    if _param_values_match(text, value, normalize_ocr):
                        return True
                except Exception:
                    continue
            return False

        ns["_jenkins_value_on_page"] = _jenkins_value_on_page

    if callable(orig_select):

        def _abort_dropdown_missing(field, requested, logger):
            """Stop the run on the failing dropdown, naming the value.

            Continuing past a dropdown whose value could not be set only
            produces a wrong build (or a late, vague pre-submit abort), so the
            failure is raised here, at the field that caused it.
            """
            def _key(text):
                return "".join(
                    ch for ch in str(text or "").lower() if ch.isalnum()
                )

            options = None
            recorded = live.get("dropdown_options")
            if recorded and (
                not field or not recorded[0] or _key(recorded[0]) == _key(field)
            ):
                options = recorded[1]
            message = _record_dropdown_value_missing(field, requested, options)
            if logger is not None:
                logger.error("Jenkins OCR: %s", message)
            raise RuntimeError(message)

        def _jenkins_ocr_select_dropdown(*args, **kwargs):
            logger = _arg(args, kwargs, 4, "logger")
            if logger is not None:
                live["logger"] = logger
            anchor = {}

            requested_value = _arg(args, kwargs, 2, "value")
            requested_labels = _arg(args, kwargs, 1, "labels") or ()
            try:
                if _dropdown_already_correct(
                    _arg(args, kwargs, 0, "edge_hwnd"),
                    requested_labels,
                    requested_value,
                    logger,
                ):
                    return True
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: pre-check of %s failed (%s) — opening the "
                        "dropdown as before",
                        requested_labels[0] if requested_labels else "dropdown",
                        exc,
                    )

            # On the Rebuild page this parameter is a text input, so every
            # dropdown mechanic below is wrong for it: type-ahead inserts at
            # the caret, and the option-list read sees only the box's own text.
            # Fill it as text and never fall through, because the fallbacks
            # would corrupt the value they are meant to repair.
            if _rebuild_text_params_enabled() and _on_jenkins_rebuild_page(
                _arg(args, kwargs, 0, "edge_hwnd")
            ):
                field_key = _arg(args, kwargs, 3, "field_key")
                field_name = field_key or (
                    requested_labels[0] if requested_labels else "parameter"
                )
                if _fill_param_as_text_field(
                    _arg(args, kwargs, 0, "edge_hwnd"),
                    requested_labels,
                    requested_value,
                    field_key,
                    logger,
                ):
                    return True
                message = (
                    "%s could not be set to '%s' on the Jenkins Rebuild page — "
                    "build not submitted. The Rebuild form holds text inputs "
                    "pre-filled from the last build, so a wrong value here "
                    "would deploy the previous build's parameters."
                    % (field_name, requested_value)
                )
                if logger is not None:
                    logger.error("Jenkins OCR: %s", message)
                raise RuntimeError(message)

            try:
                if _set_dropdown_by_typeahead(
                    _arg(args, kwargs, 0, "edge_hwnd"),
                    requested_labels,
                    requested_value,
                    logger,
                ):
                    return True
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: type-ahead attempt on %s failed (%s) — "
                        "falling back to the compiled routine",
                        requested_labels[0] if requested_labels else "dropdown",
                        exc,
                    )

            # Type-ahead could not set it, so find out what the list actually
            # offers rather than spending ~2.5 minutes per attempt guessing.
            try:
                enumerated = _enumerate_dropdown_options(
                    _arg(args, kwargs, 0, "edge_hwnd"),
                    requested_labels,
                    requested_value,
                    logger,
                )
                if enumerated is True:
                    return True
                if enumerated is False:
                    # The value is absent from the list. Scanning cannot help,
                    # and neither can any later step, so stop here.
                    _abort_dropdown_missing(
                        _arg(args, kwargs, 3, "field_key")
                        or (requested_labels[0] if requested_labels else ""),
                        requested_value,
                        logger,
                    )
            except RuntimeError:
                raise
            except Exception as exc:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: option-list read of %s failed (%s) — "
                        "falling back to the compiled routine",
                        requested_labels[0] if requested_labels else "dropdown",
                        exc,
                    )

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
                # Keep the label's own geometry: the shifted copy below aims at
                # the control, but reading the value back needs the label row.
                anchor["label"] = dict(label)
                anchor["labels"] = labels
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

            edge_hwnd = _arg(args, kwargs, 0, "edge_hwnd")
            field_key = _arg(args, kwargs, 3, "field_key", "dropdown")

            if result:
                # A failed read-back must not invent a failure: only a value
                # that was read and clearly conflicts turns success into
                # failure, and any error here leaves the result alone.
                try:
                    if _dropdown_readback_mismatch(
                        edge_hwnd,
                        anchor.get("label"),
                        anchor.get("labels") or requested_labels,
                        requested_value,
                        logger,
                    ):
                        result = False
                except Exception as exc:
                    if logger is not None:
                        logger.info(
                            "Jenkins OCR: dropdown read-back failed (%s) — "
                            "keeping the reported result",
                            exc,
                        )

            if not result:
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
                _abort_dropdown_missing(
                    field_key
                    or (requested_labels[0] if requested_labels else ""),
                    requested_value,
                    logger,
                )
            return result

        ns["_jenkins_ocr_select_dropdown"] = _jenkins_ocr_select_dropdown

    orig_fill_params = ns.get("_fill_jenkins_ocr_parameters")
    orig_click_submit = ns.get("_jenkins_click_build_submit")
    ocr_tesseract_data = ns.get("_ocr_tesseract_data")
    configure_tesseract = ns.get("_configure_tesseract")
    click_client_area = ns.get("_click_client_area")
    type_text_unicode = ns.get("_type_text_unicode")

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
                            configure_tesseract=configure_tesseract,
                        )
            return _click_submit_and_confirm(edge_hwnd, logger, args, kwargs)

        def _submit_left_the_form(edge_hwnd, logger):
            """Re-read the page and report whether the build was really taken."""
            time.sleep(_SUBMIT_CONFIRM_WAIT)
            try:
                _image, words = orig_read(_live_hwnd(edge_hwnd), logger)
            except Exception as exc:
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: could not confirm the submit (%s)", exc
                    )
                return None
            return not _still_on_param_form(words)

        def _locate_submit(edge_hwnd, logger, top_ratio):
            """(x, y) ratios of the submit button in the current viewport."""
            try:
                image, words = orig_read(_live_hwnd(edge_hwnd), logger)
            except Exception as exc:
                if logger is not None:
                    logger.warning(
                        "Jenkins OCR: submit button search failed (%s)", exc
                    )
                return None
            if image is None:
                return None
            centre = _find_submit_button_centre(image, top_ratio)
            if centre is not None:
                return centre
            centre = _find_submit_label_centre(words)
            if centre is not None and logger is not None:
                logger.info(
                    "Jenkins OCR: no solid button in view, but read a submit "
                    "label at %.0f%%,%.0f%%",
                    centre[0] * 100,
                    centre[1] * 100,
                )
            return centre

        def _click_located_submit(edge_hwnd, logger):
            """Click the blue button found by its fill. False when not found.

            The Rebuild page ends with ``BuildNummm`` at the bottom edge of a
            1920x1032 window, so its button is below the fold and no scan of
            the viewport can see it. Scroll down and look again before giving
            up, otherwise the fallback clicks a guessed position on a form
            whose button was never on screen.
            """
            if not callable(click_client_area):
                return False

            centre = _locate_submit(edge_hwnd, logger, _SUBMIT_SEARCH_TOP_RATIO)
            passes = _submit_scroll_attempts() if callable(scroll_down) else 0
            for attempt in range(1, passes + 1):
                if centre is not None:
                    break
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: submit button not in view - scrolling "
                        "down (pass %d of %d)",
                        attempt,
                        passes,
                    )
                try:
                    scroll_down(
                        _live_hwnd(edge_hwnd), logger, _SUBMIT_SCROLL_NOTCHES
                    )
                except Exception as exc:
                    if logger is not None:
                        logger.warning(
                            "Jenkins OCR: scroll towards the submit button "
                            "failed (%s)",
                            exc,
                        )
                    break
                time.sleep(1.0)
                centre = _locate_submit(
                    edge_hwnd, logger, _SUBMIT_RESCAN_TOP_RATIO
                )

            if centre is None:
                if logger is not None:
                    logger.info(
                        "Jenkins OCR: no blue submit button found by fill "
                        "- falling back to the positional click"
                    )
                return False
            x_ratio, y_ratio = centre
            if logger is not None:
                logger.info(
                    "Jenkins OCR: found 'Build' submit button by fill at "
                    "%.0f%%,%.0f%% - clicking it",
                    x_ratio * 100,
                    y_ratio * 100,
                )
            click_client_area(
                _live_hwnd(edge_hwnd), logger, x_ratio=x_ratio, y_ratio=y_ratio
            )
            return True

        def _click_submit_and_confirm(edge_hwnd, logger, args, kwargs):
            """Submit the build, then prove the form is gone before saying so.

            The compiled helper returns True for any click it managed to
            perform, so a click that misses the button is still reported as a
            submitted build. Confirming the form has gone is what makes the
            caller's "Build submitted." line mean something.
            """
            clicked = _click_located_submit(edge_hwnd, logger)
            if clicked:
                left_form = _submit_left_the_form(edge_hwnd, logger)
                if left_form:
                    return True
                if logger is not None and left_form is False:
                    logger.warning(
                        "Jenkins OCR: parameter form still on screen after "
                        "clicking the located button - retrying by position"
                    )

            positional = orig_click_submit(*args, **kwargs)
            if not positional:
                return False
            left_form = _submit_left_the_form(edge_hwnd, logger)
            if left_form is None:
                # Confirmation itself failed; trust the click as before.
                return True
            if not left_form and logger is not None:
                logger.error(
                    "Jenkins OCR: parameter form is still on screen after the "
                    "submit click - the build was NOT submitted"
                )
            return left_form

        ns["_jenkins_click_build_submit"] = _jenkins_click_build_submit

    orig_job_click = ns.get("_jenkins_click_exact_job")

    if (
        callable(orig_job_click)
        and callable(orig_read)
        and callable(click_client_area)
        and callable(normalize_ocr)
    ):

        def _job_run_centre_x(words, target_norm, cy, fallback):
            """Horizontal centre of the whole job-name run on ``cy``'s row."""
            lefts = []
            rights = []
            for word in words or ():
                word_cy = word.get("cy")
                norm = word.get("norm")
                cx = word.get("cx")
                x1 = word.get("x1")
                if word_cy is None or cx is None or x1 is None or not norm:
                    continue
                if abs(word_cy - cy) > _JOB_ROW_TOLERANCE:
                    continue
                if norm not in target_norm:
                    continue
                if x1 <= cx:
                    # x1 is not the right edge after all; do not guess.
                    return fallback
                lefts.append(2.0 * cx - x1)
                rights.append(x1)
            if not lefts:
                return fallback
            return (min(lefts) + max(rights)) / 2.0

        def _jenkins_click_exact_job(*args, **kwargs):
            """Click the middle of the job link, not the mean of its tokens.

            See override 22: the averaged token centres land on the link's
            leading edge, which on 15 Sep 14:22 hit the health-icon column and
            opened a tooltip instead of following the link.
            """
            target = _arg(args, kwargs, 1, "target")
            target_norm = normalize_ocr(target) or ""
            saved_read = ns.get("_jenkins_ocr_read")
            saved_click = ns.get("_click_client_area")
            if not (callable(saved_read) and callable(saved_click)):
                return orig_job_click(*args, **kwargs)
            seen = {"words": None}

            def _read_and_remember(*read_args, **read_kwargs):
                image, words = saved_read(*read_args, **read_kwargs)
                seen["words"] = words
                return image, words

            def _click_run_centre(hwnd, click_logger, x_ratio=None,
                                  y_ratio=None, **rest):
                if x_ratio is not None and y_ratio is not None and target_norm:
                    widened = _job_run_centre_x(
                        seen.get("words"), target_norm, y_ratio, x_ratio
                    )
                    if abs(widened - x_ratio) > _JOB_CENTRE_MIN_SHIFT:
                        if click_logger is not None:
                            click_logger.info(
                                "Jenkins OCR: clicking the centre of the '%s' "
                                "link at %.0f%% rather than its leading edge "
                                "at %.0f%%",
                                target,
                                widened * 100,
                                x_ratio * 100,
                            )
                        x_ratio = widened
                return saved_click(
                    hwnd, click_logger, x_ratio=x_ratio, y_ratio=y_ratio, **rest
                )

            ns["_jenkins_ocr_read"] = _read_and_remember
            ns["_click_client_area"] = _click_run_centre
            try:
                return orig_job_click(*args, **kwargs)
            finally:
                ns["_jenkins_ocr_read"] = saved_read
                ns["_click_client_area"] = saved_click

        ns["_jenkins_click_exact_job"] = _jenkins_click_exact_job

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

    orig_click_apps_tab = ns.get("_click_citrix_apps_tab")
    if callable(orig_click_apps_tab):

        def _click_citrix_apps_tab(*args, **kwargs):
            driver = _arg(args, kwargs, 0, "driver")
            logger = _arg(args, kwargs, 1, "logger")
            if driver is not None and _driver_on_citrix_workspace(driver):
                _raise_if_citrix_empty_apps_page(driver, logger)
            return orig_click_apps_tab(*args, **kwargs)

        ns["_click_citrix_apps_tab"] = _click_citrix_apps_tab

    # The same empty StoreFront page also breaks the DESKTOPS tab, which
    # otherwise fails with a misleading "Could not switch to DESKTOPS tab".
    orig_click_desktops_tab = ns.get("_click_citrix_desktops_tab")
    if callable(orig_click_desktops_tab):

        def _click_citrix_desktops_tab(*args, **kwargs):
            driver = _arg(args, kwargs, 0, "driver")
            logger = _arg(args, kwargs, 1, "logger")
            if driver is not None and _driver_on_citrix_workspace(driver):
                _raise_if_citrix_empty_apps_page(driver, logger)
            return orig_click_desktops_tab(*args, **kwargs)

        ns["_click_citrix_desktops_tab"] = _click_citrix_desktops_tab

    orig_launch_kias = ns.get("_launch_kias_desktop_from_storefront")
    if callable(orig_launch_kias):

        def _launch_kias_desktop_from_storefront(*args, **kwargs):
            driver = _arg(args, kwargs, 0, "driver")
            logger = _arg(args, kwargs, 1, "logger")
            if driver is not None and _driver_on_citrix_workspace(driver):
                _raise_if_citrix_empty_apps_page(driver, logger)
            return orig_launch_kias(*args, **kwargs)

        ns["_launch_kias_desktop_from_storefront"] = (
            _launch_kias_desktop_from_storefront
        )

    # A published PuTTY session once scored highest as the "Citrix desktop
    # password prompt" and was sent the AD password followed by Enter, because
    # seamless apps run under the Citrix client process that the scorer trusts.
    # The window title is the only signal that separates them, so it is checked
    # both when the prompt is picked and again immediately before typing.
    get_window_title = ns.get("_get_window_title")

    def _window_title_for(hwnd):
        if hwnd is None or not callable(get_window_title):
            return ""
        try:
            return get_window_title(hwnd) or ""
        except Exception:
            return ""

    orig_find_logon_window = ns.get("_find_citrix_desktop_logon_window")
    if callable(orig_find_logon_window):
        # The finder is polled in a wait loop, so report each window once.
        rejected_titles = set()

        def _find_citrix_desktop_logon_window(*args, **kwargs):
            hwnd = orig_find_logon_window(*args, **kwargs)
            if hwnd is None:
                return hwnd
            title = _window_title_for(hwnd)
            if _citrix_logon_window_is_safe(title):
                return hwnd
            logger = _arg(args, kwargs, 0, "logger")
            if logger is not None and title not in rejected_titles:
                rejected_titles.add(title)
                logger.warning(
                    "Citrix logon: ignoring %r as the password prompt — it "
                    "reads as a remote shell or file session, not a logon box",
                    title,
                )
            return None

        ns["_find_citrix_desktop_logon_window"] = _find_citrix_desktop_logon_window

    orig_submit_logon = ns.get("_submit_citrix_desktop_logon_password")
    if callable(orig_submit_logon):

        def _submit_citrix_desktop_logon_password(*args, **kwargs):
            hwnd = _arg(args, kwargs, 0, "hwnd")
            logger = _arg(args, kwargs, 2, "logger")
            title = _window_title_for(hwnd)
            if not _citrix_logon_window_is_safe(title):
                if logger is not None:
                    logger.error(
                        "Citrix logon: refusing to type the password into %r — "
                        "that window is a remote shell or file session, not a "
                        "logon prompt. Nothing was typed.",
                        title,
                    )
                return False
            return orig_submit_logon(*args, **kwargs)

        ns["_submit_citrix_desktop_logon_password"] = (
            _submit_citrix_desktop_logon_password
        )

    orig_try_edge_app = ns.get("_try_edge_app_deploy")
    if callable(orig_try_edge_app):

        def _try_edge_app_deploy(*args, **kwargs):
            driver = _arg(args, kwargs, 0, "driver")
            logger = _arg(args, kwargs, 1, "logger")
            result = orig_try_edge_app(*args, **kwargs)
            if result is None and driver is not None:
                if _driver_on_citrix_workspace(driver):
                    _raise_if_citrix_empty_apps_page(driver, logger)
            return result

        ns["_try_edge_app_deploy"] = _try_edge_app_deploy

    # Launching the published 'Edge KiaSDev' app does not always give a fresh
    # browser: Citrix reconnects the user's existing remote Edge session, tabs
    # and all. _try_edge_app_deploy only opens a new window on the branch that
    # already saw an Edge window open; the app-launch branch calls
    # _navigate_citrix_edge_to_jenkins, which Ctrl+L's whatever tab is active.
    # On 10 Sep 12:32 that turned Jenkins into tab 5 of 7 ('AskVodafone and 6
    # more pages'), and since an Edge window's title and content follow the
    # active tab, any later tab switch silently points OCR at the wrong page.
    orig_open_own_window = ns.get("_open_new_citrix_edge_with_jenkins")
    own_window_state = {"attempted": False}

    if callable(orig_open_own_window):

        def _open_new_citrix_edge_with_jenkins_tracked(*args, **kwargs):
            own_window_state["attempted"] = True
            return orig_open_own_window(*args, **kwargs)

        ns["_open_new_citrix_edge_with_jenkins"] = (
            _open_new_citrix_edge_with_jenkins_tracked
        )

    orig_navigate_edge = ns.get("_navigate_citrix_edge_to_jenkins")

    if callable(orig_navigate_edge) and callable(orig_open_own_window):

        def _seamless_edge_window(logger):
            """(hwnd, title) of the remote Edge that navigation would hit."""
            lister = ns.get("_list_citrix_session_edge_hwnds")
            get_title = ns.get("_get_window_title")
            if not (callable(lister) and callable(get_title)):
                return None, ""
            hwnds = ()
            for call in (lambda: lister(), lambda: lister(logger)):
                try:
                    hwnds = call() or ()
                    break
                except TypeError:
                    continue
                except Exception:
                    return None, ""
            best = None, ""
            for hwnd in hwnds:
                try:
                    title = get_title(hwnd) or ""
                except Exception:
                    continue
                if title:
                    best = hwnd, title
                    if " MORE PAGE" in title.upper():
                        return hwnd, title
            return best

        def _navigate_citrix_edge_to_jenkins(*args, **kwargs):
            logger = _arg(args, kwargs, 0, "logger")
            if own_window_state["attempted"] or not _own_window_preferred():
                return orig_navigate_edge(*args, **kwargs)

            edge_hwnd, title = _seamless_edge_window(logger)
            if not edge_hwnd or " MORE PAGE" not in title.upper():
                # A single-tab window is the app's own, so navigating it in
                # place costs nothing and keeps the existing behaviour.
                return orig_navigate_edge(*args, **kwargs)

            if logger is not None:
                logger.info(
                    "Jenkins: the remote Edge holds other pages (%r) — opening "
                    "Jenkins in its own window instead of taking over a tab",
                    title,
                )
            try:
                if ns["_open_new_citrix_edge_with_jenkins"](None, edge_hwnd, logger):
                    return True
            except Exception as exc:
                if logger is not None:
                    logger.warning(
                        "Jenkins: opening its own window failed (%s)", exc
                    )
            if logger is not None:
                logger.warning(
                    "Jenkins: could not give Jenkins its own window — loading "
                    "it in the existing remote Edge instead. OCR follows the "
                    "active tab, so do not switch tabs during this run."
                )
            return orig_navigate_edge(*args, **kwargs)

        ns["_navigate_citrix_edge_to_jenkins"] = _navigate_citrix_edge_to_jenkins

    orig_complete_launch = ns.get("_complete_citrix_desktop_launch")
    if callable(orig_complete_launch):

        def _complete_citrix_desktop_launch(*args, **kwargs):
            driver = _arg(args, kwargs, 0, "driver")
            logger = _arg(args, kwargs, 2, "logger")
            if driver is not None and _driver_on_citrix_workspace(driver):
                _raise_if_citrix_empty_apps_page(driver, logger)
            return orig_complete_launch(*args, **kwargs)

        ns["_complete_citrix_desktop_launch"] = _complete_citrix_desktop_launch

    orig_launch_ica = ns.get("_try_launch_existing_ica_session")
    if callable(orig_launch_ica):

        def _try_launch_existing_ica_session(*args, **kwargs):
            if _citrix_empty_apps_abort["requested"]:
                raise RuntimeError(_CITRIX_EMPTY_APPS_ERROR)
            if _jenkins_automation_abort["requested"]:
                _raise_if_jenkins_automation_failed(
                    _arg(args, kwargs, 1, "logger")
                )
            return orig_launch_ica(*args, **kwargs)

        ns["_try_launch_existing_ica_session"] = _try_launch_existing_ica_session

    orig_automate_deploy = ns.get("_automate_deploy_page")
    if callable(orig_automate_deploy):

        def _automate_deploy_page(*args, **kwargs):
            _reset_citrix_empty_apps_abort()
            _reset_jenkins_automation_abort()
            logger = _arg(args, kwargs, 0, "logger")
            try:
                result = orig_automate_deploy(*args, **kwargs)
            except RuntimeError as exc:
                if str(exc) == _CITRIX_EMPTY_APPS_ERROR:
                    raise
                raise
            _raise_if_jenkins_automation_failed(logger)
            return result

        ns["_automate_deploy_page"] = _automate_deploy_page

    # A SIT switch needs a PuTTY window, not a desktop. The published PuTTY app
    # gives one directly, so the desktop launch that never fired is skipped.
    _putty_app_enabled = os.environ.get("ENVPILOT_PUTTY_APP", "1") != "0"
    _putty_app_mode = {"on": False}
    _putty_app_ctx = {"driver": None, "download_dir": None}
    _PUTTY_CONFIG_TITLE = "PuTTY Configuration"
    # The store publishes more than one PuTTY build; take them in this order.
    _PUTTY_TILE_NAMES = ("putty kiasdev", "putty 0_80", "putty")

    def _putty_config_hwnd():
        finder = ns.get("_find_window_by_title_contains")
        if not callable(finder):
            return None
        try:
            return finder(_PUTTY_CONFIG_TITLE)
        except Exception:
            return None

    def _ica_files(download_dir):
        try:
            names = os.listdir(download_dir)
        except Exception:
            return {}
        found = {}
        for name in names:
            if not name.lower().endswith(".ica"):
                continue
            try:
                found[name] = os.path.getmtime(os.path.join(download_dir, name))
            except OSError:
                continue
        return found

    def _wait_for_new_ica(download_dir, before, logger, timeout=40.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, mtime in _ica_files(download_dir).items():
                if name in before and mtime <= before[name] + 0.5:
                    continue
                path = os.path.join(download_dir, name)
                ready = ns.get("_wait_for_file_ready")
                if callable(ready):
                    try:
                        ready(path, logger, 10)
                    except Exception:
                        pass
                return path
            time.sleep(0.5)
        return None

    _PUTTY_TILE_SCRIPT = """
    const wanted = arguments[0];
    const tiles = Array.from(document.querySelectorAll('a.storeapp, .storeapp'));
    for (const want of wanted) {
      for (const tile of tiles) {
        const text = (tile.getAttribute('aria-label') || tile.textContent || '')
          .toLowerCase().replace(/\\s+/g, ' ');
        if (text.indexOf(want) !== -1) { return tile; }
      }
    }
    return null;
    """

    def _find_putty_tile(driver, logger):
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        try:
            return driver.execute_script(
                _PUTTY_TILE_SCRIPT, list(_PUTTY_TILE_NAMES)
            )
        except Exception as exc:
            if logger is not None:
                logger.warning("Could not search for a PuTTY app tile: %s", exc)
            return None

    def _launch_putty_app_tile(driver, download_dir, logger):
        """Launch the published PuTTY app; return its Configuration hwnd."""
        hwnd = _putty_config_hwnd()
        if hwnd:
            if logger is not None:
                logger.info("PuTTY Configuration window already open - reusing it")
            return hwnd

        click_apps = ns.get("_click_citrix_apps_tab")
        if callable(click_apps) and not click_apps(driver, logger):
            raise RuntimeError("Could not open the APPS tab to launch PuTTY.")

        tile = _find_putty_tile(driver, logger)
        if tile is None:
            raise RuntimeError(
                "No published PuTTY app tile found in the Citrix APPS view."
            )
        if logger is not None:
            logger.info("Found published PuTTY app tile - launching it")

        before = _ica_files(download_dir)
        double_click = ns.get("_double_click_kias_desktop")
        if not callable(double_click):
            raise RuntimeError("No tile click helper available for PuTTY.")
        # The click helper reports success inconsistently across tile types, so
        # the downloaded .ica is what actually decides whether this worked.
        try:
            double_click(driver, tile, logger)
        except Exception as exc:
            if logger is not None:
                logger.warning("PuTTY tile click raised %s - checking for .ica", exc)

        path = _wait_for_new_ica(download_dir, before, logger)
        if not path:
            raise RuntimeError(
                "Citrix did not download a .ica for the PuTTY app after the "
                "tile was clicked."
            )
        if logger is not None:
            logger.info("PuTTY app .ica downloaded: %s", os.path.basename(path))

        launch = ns.get("_launch_via_citrix_client")
        if not callable(launch) or not launch(path, logger):
            raise RuntimeError(
                "Could not open the PuTTY .ica with the Citrix client."
            )

        waiter = ns.get("_wait_for_window_title")
        timeout = ns.get("PUTTY_OPEN_TIMEOUT") or 60
        hwnd = None
        if callable(waiter):
            hwnd = waiter(_PUTTY_CONFIG_TITLE, logger, timeout)
        if not hwnd:
            hwnd = _putty_config_hwnd()
        if not hwnd:
            raise RuntimeError(
                "The PuTTY app launched but its Configuration window did not "
                "appear."
            )
        if logger is not None:
            logger.info("Published PuTTY app open (seamless, no KIAS desktop)")
        return hwnd

    orig_complete_launch_putty = ns.get("_complete_citrix_desktop_launch")
    if _putty_app_enabled and callable(orig_complete_launch_putty):

        def _complete_citrix_desktop_launch_putty(*args, **kwargs):
            if not _putty_app_mode["on"]:
                return orig_complete_launch_putty(*args, **kwargs)
            driver = _arg(args, kwargs, 0, "driver")
            download_dir = _arg(args, kwargs, 1, "download_dir")
            logger = _arg(args, kwargs, 2, "logger")
            _putty_app_ctx["driver"] = driver
            _putty_app_ctx["download_dir"] = download_dir
            if logger is not None:
                logger.info(
                    "SIT switch: launching the published PuTTY app instead of "
                    "the KIAS desktop or Edge app"
                )
            _launch_putty_app_tile(driver, download_dir, logger)
            return "PuTTY app launched for SIT switch."

        ns["_complete_citrix_desktop_launch"] = _complete_citrix_desktop_launch_putty

    orig_sit_to_desktop = ns.get("_automate_citrix_to_kias_desktop")
    if _putty_app_enabled and callable(orig_sit_to_desktop):

        def _automate_citrix_to_kias_desktop(logger, citrix_id, password):
            hwnd = _putty_config_hwnd()
            if hwnd:
                if logger is not None:
                    logger.info("SIT switch: reusing the PuTTY window already open")
                return hwnd
            if not citrix_id or not password:
                raise ValueError(
                    "Citrix Id and Password are required for SIT environment switch."
                )
            deploy_url = ns.get("DEPLOY_URL")
            driver, download_dir, browser_mode = ns["_create_edge_driver"](logger)
            _putty_app_ctx["driver"] = driver
            _putty_app_ctx["download_dir"] = download_dir
            url_loaded = False
            if browser_mode == "new":
                if logger is not None:
                    logger.info("Opening Citrix URL for SIT switch: %s", deploy_url)
                driver.get(deploy_url)
                time.sleep(1.5)
                url_loaded = True
            else:
                url_loaded = ns["_prepare_deploy_browser"](
                    driver, browser_mode, logger
                )
            ns["_set_cdp_download_path"](driver, download_dir, logger)
            _putty_app_mode["on"] = True
            try:
                ns["_run_full_deploy_flow"](
                    driver,
                    download_dir,
                    browser_mode,
                    citrix_id,
                    password,
                    logger,
                    url_loaded=url_loaded,
                    open_deploy=False,
                )
            finally:
                _putty_app_mode["on"] = False
            hwnd = _putty_config_hwnd()
            if not hwnd:
                raise RuntimeError("PuTTY did not open for the SIT switch.")
            return hwnd

        ns["_automate_citrix_to_kias_desktop"] = _automate_citrix_to_kias_desktop

    orig_open_putty = ns.get("_open_putty_on_citrix_desktop")
    if _putty_app_enabled and callable(orig_open_putty):

        def _open_putty_on_citrix_desktop(*args, **kwargs):
            logger = _arg(args, kwargs, 1, "logger")
            hwnd = _putty_config_hwnd()
            if hwnd:
                if logger is not None:
                    logger.info(
                        "PuTTY Configuration window is already open - not "
                        "searching a KIAS desktop Start menu"
                    )
                return hwnd
            # Each host in an all-SIT run needs its own PuTTY, and the first
            # one has since turned into a session window.
            driver = _putty_app_ctx["driver"]
            download_dir = _putty_app_ctx["download_dir"]
            if driver is not None and download_dir:
                return _launch_putty_app_tile(driver, download_dir, logger)
            return orig_open_putty(*args, **kwargs)

        ns["_open_putty_on_citrix_desktop"] = _open_putty_on_citrix_desktop

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

    script_dir = ns.get("SCRIPT_DIR")
    upload_dir = ns.get("JENKINS_UPLOAD_DIR")

    if script_dir is not None and upload_dir is not None:
        for folder in _JENKINS_UPLOAD_DIR_NAMES:
            candidate = script_dir / folder
            if not candidate.is_dir():
                continue
            if candidate != upload_dir:
                ns["JENKINS_UPLOAD_DIR"] = candidate
                ns["JENKINS_UPLOAD_SCRIPT"] = candidate / _JENKINS_UPLOAD_SCRIPT_NAME
            break


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
