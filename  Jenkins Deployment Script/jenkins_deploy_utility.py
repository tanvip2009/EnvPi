import argparse
from datetime import datetime
import getpass
import glob
import io
import os
import re
import smtplib
import subprocess
import sys
import time
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


LOGIN_URL = (
    "https://illinipas01:8443/login?from=%2Fjob%2FVFDE_NEXUS_JOBS%2Fjob%2F"
    "upload_zip_to_vfde_nexus%2Fbuild%3Fdelay%3D0sec"
)
TARGET_JOB_URL = "https://illinipas01:8443/job/VFDE_NEXUS_JOBS/job/upload_zip_to_vfde_nexus/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Login to Jenkins and trigger 'Build with Parameters' for "
            "upload_zip_to_vfde_nexus."
        )
    )
    parser.add_argument("--release-name", help="Value for Release_name")
    parser.add_argument("--build-number", help="Value for BuildNumber")
    parser.add_argument("--folder-name", help="Value for Folder_Name")
    parser.add_argument(
        "--project-name",
        help="Value for ProjectName",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("JENKINS_USER"),
        help="Jenkins username (default: env JENKINS_USER)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("JENKINS_PASS"),
        help="Jenkins password (default: env JENKINS_PASS)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--pause-on-exit",
        action="store_true",
        help="Pause before exiting so you can read output/errors",
    )
    parser.add_argument(
        "--debug-dir",
        default="jenkins_debug",
        help="Folder to save screenshots/page source for debugging",
    )
    parser.add_argument(
        "--log-file",
        default="jenkins_debug/run.log",
        help="Path to log file (console output is mirrored here)",
    )
    parser.add_argument(
        "--console-timeout-seconds",
        type=int,
        default=1800,
        help="Max wait time for Console Output to reach Finished status",
    )
    parser.add_argument(
        "--email-to",
        default="gaurav.chauhan@amdocs.com",
        help="Recipient email for status notification",
    )
    parser.add_argument(
        "--smtp-server",
        default=os.getenv("SMTP_SERVER"),
        help="SMTP server for email notification",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=int(os.getenv("SMTP_PORT", "25")),
        help="SMTP port (default: 25)",
    )
    parser.add_argument(
        "--smtp-user",
        default=os.getenv("SMTP_USER"),
        help="SMTP username if auth is required",
    )
    parser.add_argument(
        "--smtp-password",
        default=os.getenv("SMTP_PASSWORD"),
        help="SMTP password if auth is required",
    )
    parser.add_argument(
        "--smtp-from",
        default=os.getenv("SMTP_FROM"),
        help="Sender email address (defaults to SMTP_USER or username@amdocs.com)",
    )
    parser.add_argument(
        "--smtp-starttls",
        action="store_true",
        help="Enable STARTTLS for SMTP connection",
    )
    return parser.parse_args()


def wait_for_and_click(wait: WebDriverWait, by: By, locator: str, label: str) -> None:
    element = wait.until(EC.element_to_be_clickable((by, locator)))
    element.click()
    print(f"[OK] Clicked: {label}")


def set_input(wait: WebDriverWait, name: str, value: str) -> None:
    field = wait.until(EC.presence_of_element_located((By.NAME, name)))
    field.clear()
    field.send_keys(value)
    print(f"[OK] Set parameter {name}='{value}'")


def set_choice_dropdown(wait: WebDriverWait, param_label: str, value: str) -> None:
    # Jenkins Active Choice parameters render as:
    # label div + hidden input(name='name', value='<param>') + select(name='value')
    container = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                f"//div[contains(@class,'jenkins-form-item')]"
                f"[.//div[contains(@class,'jenkins-form-label') and normalize-space()='{param_label}']]",
            )
        )
    )
    select_el = container.find_element(By.XPATH, ".//select")
    select_obj = Select(select_el)
    select_obj.select_by_visible_text(value)
    print(f"[OK] Selected dropdown {param_label}='{value}'")


def get_dropdown_options(wait: WebDriverWait, param_label: str):
    container = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                f"//div[contains(@class,'jenkins-form-item')]"
                f"[.//div[contains(@class,'jenkins-form-label') and normalize-space()='{param_label}']]",
            )
        )
    )
    select_el = container.find_element(By.XPATH, ".//select")
    return [o.text.strip() for o in select_el.find_elements(By.TAG_NAME, "option") if o.text.strip()]


def wait_for_dropdown_options(wait: WebDriverWait, param_label: str, min_count: int = 1):
    def _ready(_driver):
        try:
            options = get_dropdown_options(wait, param_label)
            return len(options) >= min_count
        except Exception:
            return False

    wait.until(_ready)


def select_dropdown_smart(wait: WebDriverWait, param_label: str, requested: str) -> None:
    options = get_dropdown_options(wait, param_label)
    if not options:
        raise TimeoutException(f"No options available yet for {param_label}.")

    # 1) exact match
    if requested in options:
        set_choice_dropdown(wait, param_label, requested)
        return

    # 2) case-insensitive exact
    lowered = {opt.lower(): opt for opt in options}
    if requested.lower() in lowered:
        set_choice_dropdown(wait, param_label, lowered[requested.lower()])
        return

    # 3) contains match
    req = requested.lower()
    contains = [opt for opt in options if req in opt.lower()]
    if len(contains) == 1:
        set_choice_dropdown(wait, param_label, contains[0])
        print(f"[WARN] Used closest match for {param_label}: '{contains[0]}'")
        return

    preview = ", ".join(options[:20])
    print(f"[ERROR] Could not match {param_label}='{requested}'.")
    print(f"[INFO] Available {param_label} options (first 20): {preview}")
    raise TimeoutException(f"No matching option for {param_label}: {requested}")


def show_popup(driver: webdriver.Chrome, message: str) -> None:
    try:
        safe = message.replace("\\", "\\\\").replace("'", "\\'")
        driver.execute_script(f"alert('{safe}')")
    except Exception:
        pass


def create_webdriver(headless: bool):
    # Priority: Chrome first, then Edge fallback.
    last_error = None

    chrome_options = webdriver.ChromeOptions()
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--window-size=1600,1200")
    chrome_options.set_capability("acceptInsecureCerts", True)

    try:
        print("[INFO] Trying Chrome browser...")
        driver = webdriver.Chrome(options=chrome_options)
        print("[OK] Using Chrome browser.")
        return driver
    except Exception as exc:
        last_error = exc
        print(f"[WARN] Chrome start failed: {exc}")

    edge_options = webdriver.EdgeOptions()
    if headless:
        edge_options.add_argument("--headless=new")
    edge_options.add_argument("--ignore-certificate-errors")
    edge_options.add_argument("--window-size=1600,1200")
    edge_options.set_capability("acceptInsecureCerts", True)

    try:
        print("[INFO] Trying Edge browser fallback...")
        driver = webdriver.Edge(options=edge_options)
        print("[OK] Using Edge browser.")
        return driver
    except Exception as exc:
        raise RuntimeError(
            "Could not start Chrome or Edge WebDriver. "
            f"Chrome error: {last_error}; Edge error: {exc}"
        )


def set_text_by_label(wait: WebDriverWait, param_label: str, value: str) -> None:
    container = wait.until(
        EC.presence_of_element_located(
            (
                By.XPATH,
                f"//div[contains(@class,'jenkins-form-item')]"
                f"[.//div[contains(@class,'jenkins-form-label') and normalize-space()='{param_label}']]",
            )
        )
    )
    input_el = container.find_element(By.XPATH, ".//input[@type='text' and @name='value']")
    input_el.clear()
    input_el.send_keys(value)
    print(f"[OK] Set text parameter {param_label}='{value}'")


def extract_build_numbers(page_source: str):
    # Support both absolute and relative Jenkins URLs in page HTML.
    patterns = [
        r"/job/VFDE_NEXUS_JOBS/job/upload_zip_to_vfde_nexus/(\d+)/",
        r"https?://[^\"']+/job/VFDE_NEXUS_JOBS/job/upload_zip_to_vfde_nexus/(\d+)/",
    ]
    nums = set()
    for pattern in patterns:
        nums.update(int(n) for n in re.findall(pattern, page_source))
    return sorted(nums)


def wait_for_latest_build_url(
    driver: webdriver.Chrome, timeout_seconds: int, baseline_max: int
) -> str:
    end = time.time() + timeout_seconds
    while time.time() < end:
        # Primary: Jenkins "lastBuild" endpoint is the most reliable way.
        try:
            driver.get(TARGET_JOB_URL + "lastBuild/")
            m = re.search(
                r"/job/VFDE_NEXUS_JOBS/job/upload_zip_to_vfde_nexus/(\d+)/",
                driver.current_url,
            )
            if m:
                latest = int(m.group(1))
                print(f"[INFO] Detected lastBuild as #{latest}")
                if latest > baseline_max:
                    return driver.current_url if driver.current_url.endswith("/") else driver.current_url + "/"
        except Exception:
            pass

        # Fallback: parse job page build list.
        driver.get(TARGET_JOB_URL)
        nums = extract_build_numbers(driver.page_source)
        if nums:
            latest = max(nums)
            print(f"[INFO] Parsed latest build from page as #{latest}")
            if latest > baseline_max:
                return f"{TARGET_JOB_URL}{latest}/"
        time.sleep(5)
    raise TimeoutException("Timed out waiting for a newly created build.")


def wait_for_console_finished(
    driver: webdriver.Chrome, timeout_seconds: int, poll_seconds: int = 10
) -> str:
    end = time.time() + timeout_seconds
    while time.time() < end:
        text = driver.page_source
        if "Finished: SUCCESS" in text:
            return "SUCCESS"
        if "Finished: FAILURE" in text:
            return "FAILURE"
        time.sleep(poll_seconds)
        driver.refresh()
    raise TimeoutException("Timed out waiting for console output to finish.")


def send_status_email(args: argparse.Namespace, build_url: str, status: str) -> None:
    from_addr = args.smtp_from or args.smtp_user or f"{args.username}@amdocs.com"
    status_pretty = status.capitalize()
    release_trimmed = re.sub(r"^4000_?", "", args.release_name, flags=re.IGNORECASE)
    subject = (
        f"{args.folder_name} || {release_trimmed} || {args.project_name} || "
        f"{args.build_number} || {status_pretty}"
    )
    body = (
        f"Deployment finished with status: {status_pretty}\n\n"
        f"ProjectName: {args.project_name}\n"
        f"Release_name: {args.release_name}\n"
        f"BuildNumber: {args.build_number}\n"
        f"Folder_Name: {args.folder_name}\n"
        f"Build URL: {build_url}\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = args.email_to

    # Fast path for this environment: Outlook desktop send is quickest/reliable.
    try:
        send_via_outlook_desktop(
            to_addr=args.email_to,
            subject=subject,
            body=body,
        )
        print(f"[OK] Status email ({status}) sent to {args.email_to} via Outlook desktop.")
        return
    except Exception as outlook_exc:
        print(f"[WARN] Outlook desktop send failed, trying SMTP fallback: {outlook_exc}")

    # Quick SMTP fallback: only secure 587 endpoints, short timeout.
    candidates = []
    primary_host = args.smtp_server or "smtp.office365.com"
    candidates.append((primary_host, 587, True))
    for host in ["outlook.office365.com", "smtp-mail.outlook.com"]:
        if host != primary_host:
            candidates.append((host, 587, True))

    last_error = None
    for host, port, use_tls in candidates:
        try:
            print(f"[INFO] Trying SMTP endpoint {host}:{port} (STARTTLS={use_tls})")
            with smtplib.SMTP(host, port, timeout=8) as server:
                if use_tls:
                    server.starttls()
                if args.smtp_user and args.smtp_password:
                    print(f"[INFO] Attempting SMTP login as {args.smtp_user}")
                    server.login(args.smtp_user, args.smtp_password)
                server.send_message(msg)
            print(f"[OK] Status email ({status}) sent to {args.email_to} via {host}:{port}")
            return
        except Exception as exc:
            last_error = exc
            print(f"[WARN] SMTP attempt failed for {host}:{port}: {exc}")

    raise RuntimeError(f"Email send failed via Outlook and SMTP. Last SMTP error: {last_error}")


def send_via_outlook_desktop(to_addr: str, subject: str, body: str) -> None:
    # Fallback for enterprise environments where SMTP AUTH is blocked.
    # Use PowerShell COM directly to avoid pywin32/pywintypes dependency issues.
    to_escaped = to_addr.replace("'", "''")
    subject_escaped = subject.replace("'", "''")
    body_escaped = body.replace("'", "''")
    ps_script = (
        "$outlook = New-Object -ComObject Outlook.Application; "
        "$mail = $outlook.CreateItem(0); "
        f"$mail.To = '{to_escaped}'; "
        f"$mail.Subject = '{subject_escaped}'; "
        f"$mail.Body = '{body_escaped}'; "
        "$mail.Send();"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Outlook COM send failed. "
            f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
        )


def configure_smtp_defaults(args: argparse.Namespace) -> None:
    # Auto-configure Outlook/Office365 SMTP when user doesn't provide SMTP values.
    if not args.smtp_server:
        args.smtp_server = "smtp.office365.com"
    if not args.smtp_port:
        args.smtp_port = 587
    if not args.smtp_starttls:
        args.smtp_starttls = True

    # Infer sender/user from recipient domain or jenkins username.
    if not args.smtp_user:
        if "@" in args.username:
            args.smtp_user = args.username
        elif args.email_to and "@" in args.email_to:
            domain = args.email_to.split("@", 1)[1]
            args.smtp_user = f"{args.username}@{domain}"
        else:
            args.smtp_user = f"{args.username}@amdocs.com"

    if not args.smtp_from:
        args.smtp_from = args.smtp_user
    if not args.smtp_password:
        # Best-effort default to Jenkins password to avoid extra prompts.
        args.smtp_password = args.password


def debug_snapshot(driver: webdriver.Chrome, debug_dir: str, step: str) -> None:
    os.makedirs(debug_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_step = step.replace(" ", "_")
    screenshot_path = os.path.join(debug_dir, f"{ts}_{safe_step}.png")
    html_path = os.path.join(debug_dir, f"{ts}_{safe_step}.html")
    driver.save_screenshot(screenshot_path)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"[DEBUG] Snapshot saved: {screenshot_path}")
    print(f"[DEBUG] HTML saved: {html_path}")


def log_page(driver: webdriver.Chrome, label: str) -> None:
    print(f"[INFO] {label} URL: {driver.current_url}")
    print(f"[INFO] {label} Title: {driver.title}")


def locate_login_fields(wait: WebDriverWait):
    candidates = [
        ((By.NAME, "j_username"), (By.NAME, "j_password")),
        ((By.ID, "j_username"), (By.ID, "j_password")),
        ((By.NAME, "username"), (By.NAME, "password")),
        ((By.ID, "username"), (By.ID, "password")),
    ]
    for u_sel, p_sel in candidates:
        try:
            u = wait.until(EC.presence_of_element_located(u_sel))
            p = wait.until(EC.presence_of_element_located(p_sel))
            return u, p
        except TimeoutException:
            continue
    raise TimeoutException("Could not locate login username/password fields.")


class TeeStream(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for stream in self.streams:
            stream.write(s)
            stream.flush()
        return len(s)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main() -> int:
    args = parse_args()

    os.makedirs(args.debug_dir, exist_ok=True)
    # Keep each run fresh: clear old debug artifacts and overwrite run log.
    for old in glob.glob(os.path.join(args.debug_dir, "*.png")):
        try:
            os.remove(old)
        except OSError:
            pass
    for old in glob.glob(os.path.join(args.debug_dir, "*.html")):
        try:
            os.remove(old)
        except OSError:
            pass

    with open(args.log_file, "w", encoding="utf-8") as lf:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lf.write(f"\n--- Run started: {ts} ---\n")
        lf.flush()

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        sys.stdout = TeeStream(sys.stdout, lf)
        sys.stderr = TeeStream(sys.stderr, lf)
        try:
            return run_flow(args)
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def run_flow(args: argparse.Namespace) -> int:
    if not args.username:
        args.username = input("Enter Jenkins username (NTNET user, e.g., gauracha): ").strip()
    if not args.project_name:
        args.project_name = input("Enter ProjectName (OGW/O2A/SKY): ").strip()
    if not args.release_name:
        args.release_name = input(
            "Enter Release_name (e.g., 4000_WAVE11 or 4000_WAVE10_PCK02): "
        ).strip()
    if not args.build_number:
        args.build_number = input(
            "Enter BuildNumber (must exist for selected ProjectName+Release_name): "
        ).strip()
    if not args.folder_name:
        args.folder_name = input(
            "Enter Folder_Name (e.g., 26.06.OMA / 26.02.OMA / 26.06.OMI): "
        ).strip()

    if (
        not args.username
        or not args.project_name
        or not args.release_name
        or not args.build_number
        or not args.folder_name
    ):
        print(
            "[ERROR] username, ProjectName, Release_name, BuildNumber, "
            "and Folder_Name are required."
        )
        return 1

    configure_smtp_defaults(args)

    password = args.password or getpass.getpass("Enter Jenkins password: ")
    if not password:
        print("[ERROR] Password is required.")
        return 1

    try:
        driver = create_webdriver(args.headless)
    except Exception as exc:
        print(f"[ERROR] Could not start browser WebDriver: {exc}")
        print(
            "[HINT] Install/update Chrome or Edge and Selenium, then retry. "
            "Command: pip install -U selenium"
        )
        return 1

    wait = WebDriverWait(driver, 30)

    try:
        login_ok = False
        for attempt in [1, 2]:
            if attempt == 2:
                print("[WARN] First login attempt failed. Please re-enter credentials.")
                args.username = input("Re-enter Jenkins username: ").strip()
                password = getpass.getpass("Re-enter Jenkins password: ")
            print(f"[INFO] Login attempt #{attempt}")
            print("[INFO] Opening login URL...")
            driver.get(LOGIN_URL)
            log_page(driver, "After opening login URL")
            debug_snapshot(driver, args.debug_dir, f"open_login_page_attempt_{attempt}")

            username_field, password_field = locate_login_fields(wait)
            username_field.clear()
            username_field.send_keys(args.username)
            password_field.clear()
            password_field.send_keys(password)
            password_field.send_keys(Keys.ENTER)
            print("[OK] Login submitted.")
            debug_snapshot(driver, args.debug_dir, f"after_login_submit_attempt_{attempt}")

            try:
                WebDriverWait(driver, 10).until(
                    lambda d: ("/login" not in d.current_url.lower()) and ("sign in" not in d.title.lower())
                )
                login_ok = True
                break
            except TimeoutException:
                login_ok = False

        if not login_ok:
            show_popup(driver, "Login failed. Please verify Jenkins username/password.")
            print("[ERROR] Login failed after 2 attempts.")
            return 5

        # Navigate exactly through requested clicks.
        driver.get("https://illinipas01:8443/")
        log_page(driver, "Home page")
        debug_snapshot(driver, args.debug_dir, "home_page")
        wait_for_and_click(wait, By.LINK_TEXT, "VFDE_NEXUS_JOBS", "VFDE_NEXUS_JOBS")
        try:
            wait_for_and_click(
                wait, By.LINK_TEXT, "upload_zip_to_vfde_nexus", "upload_zip_to_vfde_nexus"
            )
        except TimeoutException:
            print("[WARN] Could not click upload_zip_to_vfde_nexus, opening job URL directly.")
            driver.get(TARGET_JOB_URL)
        log_page(driver, "Job page")
        debug_snapshot(driver, args.debug_dir, "job_page")

        # Open Build with Parameters from the job page.
        try:
            wait_for_and_click(wait, By.LINK_TEXT, "Build with Parameters", "Build with Parameters")
        except TimeoutException:
            # Fallback direct URL if sidebar label varies.
            print("[WARN] 'Build with Parameters' link not found, opening parameter page directly.")
            driver.get(TARGET_JOB_URL + "build")
        log_page(driver, "Build with Parameters page")
        debug_snapshot(driver, args.debug_dir, "build_parameters_page")

        baseline_nums = extract_build_numbers(driver.page_source)
        baseline_max = max(baseline_nums) if baseline_nums else 0
        print(f"[INFO] Latest build before trigger: #{baseline_max}")

        # Parameters are Active Choice cascading dropdowns in this Jenkins job.
        # Select ProjectName first, then wait for Release_name options to load.
        select_dropdown_smart(wait, "ProjectName", args.project_name)
        wait_for_dropdown_options(wait, "Release_name", min_count=1)
        select_dropdown_smart(wait, "Release_name", args.release_name)

        # BuildNumber is cascade-dependent; it must match exactly what user provides.
        wait_for_dropdown_options(wait, "BuildNumber", min_count=1)
        try:
            select_dropdown_smart(wait, "BuildNumber", args.build_number)
        except TimeoutException:
            options = get_dropdown_options(wait, "BuildNumber")
            preview = ", ".join(options[:20]) if options else "(no options loaded)"
            msg = f"BuildNumber '{args.build_number}' is not available for selected parameters."
            print(f"[ERROR] {msg}")
            print(f"[INFO] Available BuildNumber options (first 20): {preview}")
            show_popup(driver, msg)
            return 4
        set_text_by_label(wait, "FOLDER_NAME", args.folder_name)

        # Submit build form. Jenkins usually uses a button named 'Submit' or label 'Build'.
        submitted = False
        for by, locator in [
            (By.XPATH, "//button[normalize-space()='Build']"),
            (By.XPATH, "//button[normalize-space()='Build with Parameters']"),
            (By.XPATH, "//button[contains(normalize-space(),'Build')]"),
            (By.XPATH, "//div[@id='bottom-sticker']//button[contains(@class,'jenkins-button--primary')]"),
            (By.XPATH, "//input[@type='submit']"),
        ]:
            try:
                wait_for_and_click(wait, by, locator, "Build submit button")
                submitted = True
                break
            except TimeoutException:
                continue

        if not submitted:
            print("[ERROR] Could not find a submit button on Build with Parameters page.")
            return 2

        print("[SUCCESS] Build triggered successfully.")
        log_page(driver, "After build submit")
        debug_snapshot(driver, args.debug_dir, "build_submitted")

        print("[INFO] Waiting for newly triggered build in build history...")
        latest_build_url = wait_for_latest_build_url(driver, timeout_seconds=300, baseline_max=baseline_max)
        print(f"[OK] Latest build URL: {latest_build_url}")
        driver.get(latest_build_url)
        log_page(driver, "Latest build page")
        debug_snapshot(driver, args.debug_dir, "latest_build_page")

        try:
            wait_for_and_click(wait, By.LINK_TEXT, "Console Output", "Console Output")
        except TimeoutException:
            print("[WARN] Console Output link not clickable; opening direct console URL.")
            driver.get(latest_build_url + "console")

        log_page(driver, "Console Output page")
        print("[INFO] Waiting for console completion status...")
        result = wait_for_console_finished(driver, timeout_seconds=args.console_timeout_seconds)
        print(f"[SUCCESS] Build finished with status: {result}")
        debug_snapshot(driver, args.debug_dir, f"console_finished_{result.lower()}")
        try:
            send_status_email(args, latest_build_url, result)
        except Exception as exc:
            print(f"[WARN] Email notification failed: {exc}")
        return 0
    except Exception as exc:
        print(f"[ERROR] Deployment utility failed: {exc}")
        try:
            log_page(driver, "Failure page")
            debug_snapshot(driver, args.debug_dir, "failure")
        except Exception:
            pass
        return 3
    finally:
        driver.quit()


if __name__ == "__main__":
    exit_code = main()
    # Useful when the script is run by double-click on Windows.
    should_pause = ("--pause-on-exit" in sys.argv) or (exit_code != 0)
    if should_pause:
        input("Press Enter to close...")
    sys.exit(exit_code)
