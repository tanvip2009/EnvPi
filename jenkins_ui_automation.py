"""Native Python UI-Automation of the Jenkins Edge window.

This is a faithful port of the reference ``deploy.ps1`` (Start-EdgeAndLogin for
"Deploy Now" and Start-ScheduledBuild for "Deploy Later") using the
``uiautomation`` package instead of PowerShell's System.Windows.Automation.

It drives the already-open Jenkins Edge window (including the Citrix published
seamless Edge, matched by ``\\Remote`` / ``Sign in`` / ``Jenkins`` in the title):

  Deploy Now   : login -> deploy_from_nexus_maven_2 -> Rebuild Last -> fill
                 rebuild form -> Rebuild.
  Deploy Later : login -> deploy_from_nexus_maven_2 -> Schedule Build ->
                 "Build on" date -> Schedule -> Build with Parameters
                 (dropdowns + text fields, 5-strategy selection) -> Build.

The public entry point is :func:`run_jenkins_deploy`.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Iterable, Optional

try:  # uiautomation is optional at import time; only required at run time.
    import uiautomation as auto  # type: ignore
    _UIA_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - environment dependent
    auto = None  # type: ignore
    _UIA_IMPORT_ERROR = exc


# ── Jenkins constants (aligned with deploy.ps1) ──────────────────────────────
JENKINS_BASE = "http://deosskvr.dc-ratingen.de:8080/jenkins"
JENKINS_JOB = "deploy_from_nexus_maven_2"

# Rebuild page field order (top-to-bottom on the page) — matches deploy.ps1.
REBUILD_FIELD_ORDER = (
    "ProjectName",
    "Release_name",
    "BBs_TO_DEPLOY",
    "ENVIRONMENT_NAME",
    "FOLDER_NAME",
    "BuildNumber",
    "BuildNummm",
)

# Windows whose titles must never be treated as the Jenkins Edge window.
_SKIP_WINDOW_RE = re.compile(
    r"Citrix|NetScaler|DESHPDA|Desktop Viewer|EnvPilot|"
    r"being controlled by automated test",
    re.IGNORECASE,
)


class JenkinsAutomationError(RuntimeError):
    """Raised when the Jenkins UI automation cannot complete."""


# ── low-level key / clipboard helpers ────────────────────────────────────────
def _send(keys: str, wait: float = 0.05) -> None:
    auto.SendKeys(keys, waitTime=wait)


def _set_clipboard(text: str) -> None:
    try:
        auto.SetClipboardText(text)
    except Exception:
        time.sleep(0.2)
        auto.SetClipboardText(text)


def _ctrl_a() -> None:
    _send("{Ctrl}a", wait=0.1)


def _ctrl_v() -> None:
    _send("{Ctrl}v", wait=0.1)


# ── window discovery ─────────────────────────────────────────────────────────
def _iter_top_windows() -> Iterable["auto.Control"]:
    root = auto.GetRootControl()
    for child in root.GetChildren():
        yield child


def find_jenkins_edge_window(
    logger: logging.Logger, max_attempts: int = 20
) -> Optional["auto.Control"]:
    """Return the Edge window showing Jenkins (or a usable Edge fallback)."""
    for attempt in range(max_attempts):
        fallback = None
        for win in _iter_top_windows():
            try:
                name = win.Name or ""
            except Exception:
                continue
            if not name.strip():
                continue
            if _SKIP_WINDOW_RE.search(name):
                continue
            if re.search(r"Jenkins|deosskvr", name, re.IGNORECASE):
                logger.info("Found Jenkins Edge window: '%s'", name)
                return win
            if fallback is None and re.search(r"Sign in", name, re.IGNORECASE):
                fallback = win
            elif fallback is None and "\\Remote" in name:
                fallback = win
            elif fallback is None and re.search(
                r"Microsoft.?\bEdge\b|AskVodafone|New tab", name, re.IGNORECASE
            ):
                fallback = win
        if fallback is not None:
            logger.info("Using Edge window fallback: '%s'", fallback.Name)
            return fallback
        if max_attempts > 1:
            logger.info(
                "Waiting for Jenkins Edge window... attempt %d/%d",
                attempt + 1,
                max_attempts,
            )
            time.sleep(1.0)
    return None


def _activate(win: "auto.Control") -> None:
    """Bring the Edge window to the foreground before sending keys."""
    for method in ("SetActive", "SetFocus"):
        try:
            getattr(win, method)()
        except Exception:
            pass
    time.sleep(0.2)


def _walk(root: "auto.Control", type_name: Optional[str] = None):
    try:
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
            if type_name is None or ctrl.ControlTypeName == type_name:
                yield ctrl
    except Exception:
        return


def _rect(ctrl: "auto.Control"):
    return ctrl.BoundingRectangle


def _click(ctrl: "auto.Control") -> None:
    try:
        ctrl.Click(simulateMove=False, waitTime=0.1)
    except Exception:
        rect = _rect(ctrl)
        auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.1)


def _maximize(win: "auto.Control", logger: logging.Logger) -> None:
    logger.info("Preparing browser window (maximize)...")
    try:
        wp = win.GetWindowPattern()
        wp.SetWindowVisualState(auto.WindowVisualState.Maximized)
        logger.info("Browser maximized via WindowPattern.")
    except Exception as exc:
        logger.info("WindowPattern maximize failed (continuing): %s", exc)


# ── page element helpers ─────────────────────────────────────────────────────
def _find_by_name(
    win: "auto.Control", name: str, type_name: Optional[str] = None
) -> Optional["auto.Control"]:
    """Exact name match first, then case-insensitive partial (visible only)."""
    for ctrl in _walk(win, type_name):
        try:
            if ctrl.Name == name:
                return ctrl
        except Exception:
            continue
    needle = name.lower()
    for ctrl in _walk(win, type_name):
        try:
            cname = (ctrl.Name or "").lower()
            if needle in cname:
                rect = _rect(ctrl)
                if rect.width() > 0 and rect.height() > 0:
                    return ctrl
        except Exception:
            continue
    return None


def _fill_field_via_clipboard(
    ctrl: "auto.Control", value: str, logger: logging.Logger
) -> None:
    _click(ctrl)
    time.sleep(0.3)
    _set_clipboard(value)
    time.sleep(0.12)
    _ctrl_a()
    time.sleep(0.12)
    _ctrl_v()
    time.sleep(0.4)


def click_link_on_page(
    win: "auto.Control", link_text: str, logger: logging.Logger
) -> None:
    """Click a link/element by visible text, with Ctrl+F + scroll fallbacks."""
    _activate(win)
    _send("{Ctrl}{Home}", wait=0.1)
    time.sleep(0.5)

    el = _find_by_name(win, link_text)
    if el is not None:
        logger.info("Clicking '%s' (direct match).", link_text)
        _click(el)
        return

    # Ctrl+F browser find to scroll the text into view, then re-search.
    logger.info("'%s' not directly found — using Ctrl+F search.", link_text)
    _send("{Ctrl}f", wait=0.1)
    time.sleep(0.5)
    _set_clipboard(link_text)
    _activate(win)
    time.sleep(0.2)
    _ctrl_v()
    time.sleep(0.8)
    _send("{Esc}", wait=0.1)
    time.sleep(0.5)
    el = _find_by_name(win, link_text)
    if el is not None:
        _click(el)
        return

    for i in range(10):
        _send("{PageDown}", wait=0.1)
        time.sleep(0.6)
        el = _find_by_name(win, link_text)
        if el is not None:
            logger.info("Found '%s' after scroll (%d).", link_text, i + 1)
            _click(el)
            return

    raise JenkinsAutomationError(f"Could not find '{link_text}' on the page.")


# ── login ────────────────────────────────────────────────────────────────────
def _login(
    win: "auto.Control", user: str, password: str, logger: logging.Logger
) -> "auto.Control":
    logger.info("Searching for Username and Password fields...")
    username_field = None
    password_field = None
    for attempt in range(10):
        win = find_jenkins_edge_window(logger, max_attempts=1) or win
        for edit in _walk(win, "EditControl"):
            try:
                nm = edit.Name
            except Exception:
                continue
            if nm == "Username":
                username_field = edit
            elif nm == "Password":
                password_field = edit
        if username_field and password_field:
            break
        logger.info("Waiting for login fields... attempt %d/10", attempt + 1)
        time.sleep(0.5)

    if not username_field or not password_field:
        raise JenkinsAutomationError(
            "Could not find Username or Password field on the login page."
        )
    logger.info("Found Username and Password fields.")

    logger.info("Filling Username field...")
    _fill_field_via_clipboard(username_field, user, logger)
    logger.info("Filling Password field...")
    _fill_field_via_clipboard(password_field, password, logger)
    try:
        auto.SetClipboardText("")
    except Exception:
        pass

    logger.info("Clicking Sign in button...")
    time.sleep(0.5)
    win = find_jenkins_edge_window(logger, max_attempts=1) or win
    sign_in = _find_by_name(win, "Sign in", "ButtonControl") or _find_by_name(
        win, "Sign in"
    )
    if sign_in is not None:
        _click(sign_in)
        logger.info("Sign in button clicked.")
    else:
        logger.info("Sign in button not found — pressing Enter as fallback.")
        _activate(win)
        _send("{Enter}", wait=0.1)

    logger.info("Waiting 4s for login to complete...")
    time.sleep(4)
    return find_jenkins_edge_window(logger) or win


# ── field / dropdown value helpers ───────────────────────────────────────────
def _scroll_into_view(ctrl: "auto.Control", win: "auto.Control") -> None:
    try:
        sip = ctrl.GetScrollItemPattern()
        sip.ScrollIntoView()
        time.sleep(0.3)
        return
    except Exception:
        pass
    try:
        rect = _rect(ctrl)
        screen_h = auto.GetRootControl().BoundingRectangle.height()
        if rect.bottom > screen_h - 50 or rect.top < 0:
            _activate(win)
            _send("{PageDown}", wait=0.1)
            time.sleep(0.5)
    except Exception:
        pass


def _read_value(ctrl: "auto.Control") -> Optional[str]:
    try:
        return ctrl.GetValuePattern().Value
    except Exception:
        return None


def set_field_value(
    ctrl: "auto.Control", value: str, win: "auto.Control", logger: logging.Logger
) -> None:
    """Set a text field with clipboard paste, verify, retry char-by-char."""
    _scroll_into_view(ctrl, win)
    # triple-click to select existing text
    rect = _rect(ctrl)
    try:
        for _ in range(3):
            auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.04)
    except Exception:
        _click(ctrl)
    time.sleep(0.2)
    _set_clipboard(value)
    time.sleep(0.15)
    _activate(win)
    _click(ctrl)
    time.sleep(0.2)
    _ctrl_a()
    time.sleep(0.15)
    _ctrl_v()
    time.sleep(0.35)

    actual = _read_value(ctrl)
    if actual == value:
        logger.info("  -> value verified OK ('%s')", value)
        return

    logger.info("  -> value mismatch (got '%s'), retrying char-by-char", actual)
    _click(ctrl)
    time.sleep(0.2)
    _activate(win)
    _ctrl_a()
    time.sleep(0.15)
    _send("{Delete}", wait=0.1)
    time.sleep(0.15)
    for ch in value:
        _activate(win)
        _set_clipboard(ch)
        _ctrl_v()
        time.sleep(0.06)
    time.sleep(0.2)
    actual2 = _read_value(ctrl)
    if actual2 == value:
        logger.info("  -> retry verified OK")
    else:
        logger.warning("  -> value still incorrect after retry (got '%s')", actual2)


def _combo_value(combo: "auto.Control") -> Optional[str]:
    try:
        return combo.GetValuePattern().Value
    except Exception:
        return None


def _combo_list_items(combo: "auto.Control") -> list["auto.Control"]:
    return list(_walk(combo, "ListItemControl"))


def select_dropdown_value(
    combo: "auto.Control",
    value: str,
    field_name: str,
    win: "auto.Control",
    logger: logging.Logger,
) -> None:
    """5-strategy dropdown selection with verification (ported from deploy.ps1)."""
    logger.info("  Setting %s dropdown to: %s", field_name, value)
    _scroll_into_view(combo, win)

    def verify() -> bool:
        time.sleep(0.6)
        actual = _combo_value(combo)
        logger.info(
            "    %s VERIFY: actual='%s' expected='%s'", field_name, actual, value
        )
        if actual == value:
            logger.info("    %s selection VERIFIED OK.", field_name)
            return True
        return False

    # Strategy 1: ValuePattern.SetValue
    logger.info("    Strategy 1: ValuePattern.SetValue()...")
    try:
        combo.GetValuePattern().SetValue(value)
        time.sleep(0.5)
        if verify():
            return
    except Exception as exc:
        logger.info("    ValuePattern.SetValue not supported: %s", exc)

    # Strategy 2: filter + keyboard DOWN with focus detection
    logger.info("    Strategy 2: filter + keyboard DOWN...")
    _click(combo)
    time.sleep(0.6)
    _ctrl_a()
    time.sleep(0.15)
    _send("{Delete}", wait=0.1)
    time.sleep(0.3)
    _set_clipboard(value)
    _ctrl_v()
    time.sleep(1.5)
    items = _combo_list_items(combo)
    logger.info("    Dropdown shows %d items after filtering.", len(items))
    if not items:
        try:
            auto.SetClipboardText("")
        except Exception:
            pass
        raise JenkinsAutomationError(
            f"No items in {field_name} dropdown after filtering for '{value}'."
        )
    found = False
    max_scrolls = min(len(items) + 5, 60)
    for i in range(max_scrolls):
        _send("{Down}", wait=0.1)
        time.sleep(0.3)
        for item in _combo_list_items(combo):
            try:
                if item.HasKeyboardFocus and item.Name == value:
                    logger.info("    Found '%s' at pos %d. Enter.", value, i)
                    _send("{Enter}", wait=0.1)
                    time.sleep(0.5)
                    found = True
                    break
            except Exception:
                continue
        if found:
            break
    if found and verify():
        return
    if not found:
        _send("{Esc}", wait=0.1)
        time.sleep(0.3)

    # Strategy 3: SelectionItemPattern.Select on exact match
    logger.info("    Strategy 3: SelectionItemPattern.Select()...")
    _click(combo)
    time.sleep(0.6)
    _ctrl_a()
    time.sleep(0.15)
    _send("{Delete}", wait=0.1)
    time.sleep(0.3)
    _set_clipboard(value)
    _ctrl_v()
    time.sleep(1.5)
    found = False
    for item in _combo_list_items(combo):
        try:
            if item.Name == value:
                item.GetSelectionItemPattern().Select()
                time.sleep(0.4)
                _send("{Enter}", wait=0.1)
                time.sleep(0.5)
                found = True
                break
        except Exception:
            continue
    if found and verify():
        return
    if not found:
        _send("{Esc}", wait=0.1)
        time.sleep(0.3)

    # Strategy 4: direct click on the matching ListItem coordinates
    logger.info("    Strategy 4: direct click on matching ListItem...")
    _click(combo)
    time.sleep(0.6)
    _ctrl_a()
    time.sleep(0.15)
    _send("{Delete}", wait=0.1)
    time.sleep(0.3)
    _set_clipboard(value)
    _ctrl_v()
    time.sleep(1.5)
    found = False
    for item in _combo_list_items(combo):
        try:
            if item.Name == value:
                rect = _rect(item)
                auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.1)
                time.sleep(0.5)
                found = True
                break
        except Exception:
            continue
    if found and verify():
        return

    # Strategy 5: no filter, scroll through all items with DOWN
    logger.info("    Strategy 5: no filter, scroll all items...")
    _send("{Esc}", wait=0.1)
    time.sleep(0.3)
    _click(combo)
    time.sleep(0.6)
    _ctrl_a()
    time.sleep(0.15)
    _send("{Delete}", wait=0.1)
    time.sleep(0.5)
    all_items = _combo_list_items(combo)
    found = False
    max_all = min(len(all_items) + 5, 120)
    for i in range(max_all):
        _send("{Down}", wait=0.1)
        time.sleep(0.2)
        for item in _combo_list_items(combo):
            try:
                if item.HasKeyboardFocus and item.Name == value:
                    logger.info("    Found '%s' at pos %d (unfiltered).", value, i)
                    _send("{Enter}", wait=0.1)
                    time.sleep(0.5)
                    found = True
                    break
            except Exception:
                continue
        if found:
            break
    if found and verify():
        return

    try:
        auto.SetClipboardText("")
    except Exception:
        pass
    if _combo_value(combo) == value:
        logger.info("    %s selection VERIFIED OK (final).", field_name)
        return
    raise JenkinsAutomationError(
        f"Could not select '{value}' for {field_name} after all strategies."
    )


# ── Deploy Now: Rebuild form ─────────────────────────────────────────────────
def _content_edits(win: "auto.Control", min_y: int = 100) -> list["auto.Control"]:
    edits = []
    for edit in _walk(win, "EditControl"):
        try:
            nm = (edit.Name or "").lower()
            rect = _rect(edit)
        except Exception:
            continue
        if re.search(r"search|address|url|filter", nm):
            continue
        if rect.width() > 100 and rect.height() > 0 and rect.top > min_y:
            edits.append(edit)
    edits.sort(key=lambda c: _rect(c).top)
    return edits


def _fill_rebuild_form(
    win: "auto.Control", fields: dict, logger: logging.Logger
) -> None:
    _activate(win)
    _send("{Ctrl}{Home}", wait=0.1)
    time.sleep(0.8)

    form_edits: list["auto.Control"] = []
    for _ in range(10):
        form_edits = _content_edits(win)
        if len(form_edits) >= len(REBUILD_FIELD_ORDER):
            break
        time.sleep(0.5)

    logger.info(
        "Found %d edit fields on Rebuild page (need %d).",
        len(form_edits),
        len(REBUILD_FIELD_ORDER),
    )
    # trim leading nav/search fields if extras present
    while len(form_edits) > len(REBUILD_FIELD_ORDER):
        removed = form_edits.pop(0)
        try:
            logger.info(
                "Trimming non-form edit at Y=%d", int(_rect(removed).top)
            )
        except Exception:
            pass

    count = min(len(form_edits), len(REBUILD_FIELD_ORDER))
    for i in range(count):
        key = REBUILD_FIELD_ORDER[i]
        value = str(fields.get(key, "") or "")
        if value:
            logger.info("Filling field [%s] (index %d) = %s", key, i, value)
            set_field_value(form_edits[i], value, win, logger)
        else:
            logger.info("Skipping empty field [%s] (index %d)", key, i)
        time.sleep(0.3)

    try:
        auto.SetClipboardText("")
    except Exception:
        pass
    logger.info("All fields filled. Clicking Rebuild button...")
    _activate(win)
    if form_edits:
        _click(form_edits[-1])
        time.sleep(0.3)
    _activate(win)
    _send("{Tab}", wait=0.1)
    time.sleep(0.3)
    _activate(win)
    _send("{Enter}", wait=0.1)
    logger.info("Rebuild button clicked.")


def run_deploy_now(
    user: str, password: str, fields: dict, logger: logging.Logger
) -> None:
    logger.info("===== Jenkins Deploy Now (rebuild) — native UIA =====")
    win = find_jenkins_edge_window(logger)
    if win is None:
        raise JenkinsAutomationError("Could not find the Jenkins Edge window.")
    _activate(win)
    _maximize(win, logger)
    time.sleep(3)
    win = find_jenkins_edge_window(logger) or win

    win = _login(win, user, password, logger)

    logger.info("Clicking %s link on dashboard...", JENKINS_JOB)
    click_link_on_page(win, JENKINS_JOB, logger)
    time.sleep(4)
    win = find_jenkins_edge_window(logger) or win

    logger.info("Clicking 'Rebuild Last' link...")
    click_link_on_page(win, "Rebuild Last", logger)
    time.sleep(4)
    win = find_jenkins_edge_window(logger) or win

    logger.info("Filling Rebuild form...")
    _fill_rebuild_form(win, fields, logger)
    logger.info("===== Deploy Now completed — Rebuild form submitted =====")


# ── Deploy Later: Schedule Build ─────────────────────────────────────────────
def _format_schedule(dt: datetime) -> str:
    """Format as 'M/dd/yy h:mm:ss tt' (e.g. 6/16/26 4:16:00 PM)."""
    hour12 = dt.hour % 12
    if hour12 == 0:
        hour12 = 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return (
        f"{dt.month}/{dt.day:02d}/{dt.strftime('%y')} "
        f"{hour12}:{dt.minute:02d}:{dt.second:02d} {ampm}"
    )


def _find_build_on_field(win: "auto.Control") -> Optional["auto.Control"]:
    edits = []
    for edit in _walk(win, "EditControl"):
        try:
            rect = _rect(edit)
            if rect.width() > 50 and rect.height() > 0:
                edits.append(edit)
        except Exception:
            continue
    edits.sort(key=lambda c: _rect(c).top)
    for edit in edits:
        try:
            nm = (edit.Name or "").lower()
            rect = _rect(edit)
        except Exception:
            continue
        if re.search(r"search|address|url|filter", nm):
            continue
        if rect.top < 150 or rect.top > 700:
            continue
        if rect.width() > 300 and rect.left > 300:
            return edit
    return None


def _fill_schedule_field(
    win: "auto.Control", sched_value: str, logger: logging.Logger
) -> None:
    _activate(win)
    _send("{Ctrl}{Home}", wait=0.1)
    time.sleep(0.5)
    _send("{Ctrl}{Home}", wait=0.1)
    time.sleep(2)

    sched_field = None
    for attempt in range(10):
        win = find_jenkins_edge_window(logger) or win
        sched_field = _find_build_on_field(win)
        if sched_field is not None:
            break
        logger.info("  Waiting for 'Build on' field... attempt %d/10", attempt + 1)
        _activate(win)
        _send("{Ctrl}{Home}", wait=0.1)
        time.sleep(2)

    if sched_field is None:
        raise JenkinsAutomationError(
            "Could not find the schedule 'Build on' date/time field."
        )

    logger.info("  Filling 'Build on' field with: %s", sched_value)
    _click(sched_field)
    time.sleep(0.3)
    _activate(win)
    _ctrl_a()
    time.sleep(0.2)
    _send("{Delete}", wait=0.1)
    time.sleep(0.3)
    _set_clipboard(sched_value)
    time.sleep(0.15)
    _activate(win)
    _click(sched_field)
    time.sleep(0.2)
    _ctrl_a()
    time.sleep(0.15)
    _ctrl_v()
    time.sleep(0.4)

    actual = _read_value(sched_field)
    logger.info("  After paste, field value = '%s'", actual)
    if actual is not None and actual != sched_value:
        logger.info("  Mismatch — typing char-by-char...")
        _click(sched_field)
        time.sleep(0.2)
        _ctrl_a()
        time.sleep(0.15)
        _send("{Delete}", wait=0.1)
        time.sleep(0.2)
        for ch in sched_value:
            _activate(win)
            _set_clipboard(ch)
            _ctrl_v()
            time.sleep(0.06)
        time.sleep(0.3)
        logger.info("  After char-by-char = '%s'", _read_value(sched_field))
    else:
        logger.info("  VALUE VERIFIED OK")
    try:
        auto.SetClipboardText("")
    except Exception:
        pass


def _fill_build_parameters(
    win: "auto.Control", fields: dict, last_build_number: str, logger: logging.Logger
) -> None:
    _activate(win)
    _send("{Ctrl}{Home}", wait=0.1)
    time.sleep(1)

    combos = []
    for combo in _walk(win, "ComboBoxControl"):
        try:
            rect = _rect(combo)
            if rect.width() > 30 and rect.top > 150:
                combos.append(combo)
        except Exception:
            continue
    combos.sort(key=lambda c: _rect(c).top)
    logger.info("  Found %d dropdowns on page.", len(combos))

    edits = []
    for edit in _walk(win, "EditControl"):
        try:
            nm = (edit.Name or "").lower()
            rect = _rect(edit)
        except Exception:
            continue
        if re.search(r"search|address|url|filter", nm):
            continue
        if rect.width() > 100 and rect.top > 200 and rect.left > 280:
            edits.append(edit)
    edits.sort(key=lambda c: _rect(c).top)
    logger.info("  Found %d text fields on page.", len(edits))

    # ComboBox[0]=ProjectName, [1]=Release_name, [2]=ENVIRONMENT_NAME
    project = str(fields.get("ProjectName", "") or "")
    if project and len(combos) >= 1:
        select_dropdown_value(combos[0], project, "ProjectName", win, logger)
    release = str(fields.get("Release_name", "") or "")
    if release and len(combos) >= 2:
        select_dropdown_value(combos[1], release, "Release_name", win, logger)
    env = str(fields.get("ENVIRONMENT_NAME", "") or "")
    if env and len(combos) >= 3:
        select_dropdown_value(combos[2], env, "ENVIRONMENT_NAME", win, logger)

    # Edit[0]=FOLDER_NAME, Edit[1]=BuildNumber
    folder = str(fields.get("FOLDER_NAME", "") or "")
    if folder and len(edits) >= 1:
        logger.info("  Filling FOLDER_NAME = %s", folder)
        set_field_value(edits[0], folder, win, logger)
        time.sleep(0.3)

    build_number = str(fields.get("BuildNumber", "") or "")
    if not build_number and last_build_number:
        build_number = last_build_number
        logger.info("  BuildNumber empty in GUI, using last value: %s", build_number)
    if build_number and len(edits) >= 2:
        logger.info("  Filling BuildNumber = %s", build_number)
        set_field_value(edits[1], build_number, win, logger)
        time.sleep(0.3)

    logger.info("  Clicking Build button...")
    time.sleep(0.5)
    _activate(win)
    if edits:
        _click(edits[-1])
        time.sleep(0.3)
    _activate(win)
    _send("{Tab}", wait=0.1)
    time.sleep(0.3)
    _activate(win)
    _send("{Enter}", wait=0.1)
    logger.info("  Build button clicked.")


def run_deploy_later(
    user: str,
    password: str,
    fields: dict,
    schedule_time: datetime,
    last_build_number: str,
    logger: logging.Logger,
) -> None:
    logger.info(
        "===== Jenkins Deploy Later (schedule) — native UIA — time %s =====",
        schedule_time.strftime("%Y-%m-%d %H:%M"),
    )
    win = find_jenkins_edge_window(logger)
    if win is None:
        raise JenkinsAutomationError("Could not find the Jenkins Edge window.")
    _activate(win)
    _maximize(win, logger)
    time.sleep(3)
    win = find_jenkins_edge_window(logger) or win

    win = _login(win, user, password, logger)

    logger.info("Step: clicking %s link on dashboard...", JENKINS_JOB)
    click_link_on_page(win, JENKINS_JOB, logger)
    time.sleep(4)
    win = find_jenkins_edge_window(logger) or win

    logger.info("Step: clicking 'Schedule Build' link in sidebar...")
    time.sleep(2)
    try:
        click_link_on_page(win, "Schedule Build", logger)
    except JenkinsAutomationError:
        logger.info("Schedule Build link not found — navigating via URL.")
        sched_url = f"{JENKINS_BASE}/job/{JENKINS_JOB}/schedule"
        _activate(win)
        _send("{F6}", wait=0.1)
        time.sleep(0.5)
        _set_clipboard(sched_url)
        _ctrl_a()
        time.sleep(0.1)
        _ctrl_v()
        time.sleep(0.3)
        _send("{Enter}", wait=0.1)
        time.sleep(4)
    win = find_jenkins_edge_window(logger) or win
    logger.info("Schedule Build page loaded.")
    time.sleep(2)

    sched_value = _format_schedule(schedule_time)
    logger.info("Step: filling schedule field with: %s", sched_value)
    _fill_schedule_field(win, sched_value, logger)

    logger.info("Step: clicking Schedule button...")
    win = find_jenkins_edge_window(logger) or win
    sched_btn = _find_by_name(win, "Schedule", "ButtonControl") or _find_by_name(
        win, "Schedule"
    )
    if sched_btn is not None:
        _click(sched_btn)
        logger.info("Schedule button clicked.")
    else:
        logger.info("Schedule button not found — Tab + Enter fallback.")
        _activate(win)
        _send("{Tab}", wait=0.1)
        time.sleep(0.3)
        _send("{Enter}", wait=0.1)

    logger.info("Step: waiting for Build with Parameters page...")
    time.sleep(4)
    win = find_jenkins_edge_window(logger) or win

    logger.info("Step: filling build parameter fields...")
    _fill_build_parameters(win, fields, last_build_number, logger)
    logger.info("===== Scheduled build completed — time: %s =====", sched_value)


# ── public entry point ───────────────────────────────────────────────────────
def _parse_schedule(schedule: str) -> Optional[datetime]:
    if not schedule:
        return None
    try:
        return datetime.fromisoformat(schedule.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M"):
            try:
                return datetime.strptime(schedule, fmt)
            except ValueError:
                continue
    return None


def run_jenkins_deploy(
    deploy_form: dict,
    timing: str,
    schedule: str,
    logger: logging.Logger,
    last_build_number: str = "",
) -> bool:
    """Run the Jenkins deploy automation against the open Edge window.

    Returns True on success. Raises JenkinsAutomationError on failure so the
    caller can log/handle it.
    """
    if auto is None:
        raise JenkinsAutomationError(
            f"uiautomation package not available: {_UIA_IMPORT_ERROR}"
        )

    user = str(deploy_form.get("Username", "") or "")
    password = str(deploy_form.get("Password", "") or "")
    if not user or not password:
        raise JenkinsAutomationError(
            "Username/Password missing from deploy_form — cannot log in."
        )

    timing_norm = (timing or "now").strip().lower()
    if timing_norm == "later":
        sched_dt = _parse_schedule(schedule)
        if sched_dt is None:
            raise JenkinsAutomationError(
                f"Deploy Later requires a valid schedule (got '{schedule}')."
            )
        run_deploy_later(
            user, password, deploy_form, sched_dt, last_build_number, logger
        )
    else:
        run_deploy_now(user, password, deploy_form, logger)
    return True
