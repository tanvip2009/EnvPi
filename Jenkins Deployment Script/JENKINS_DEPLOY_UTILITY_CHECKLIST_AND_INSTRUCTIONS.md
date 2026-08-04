# Jenkins deployment utility — checklist and instructions

## Files to share

| File | Purpose |
|------|---------|
| `jenkins_deploy_utility.py` | Main automation script |
| `run_jenkins_deploy.bat` | Windows launcher (optional; recommended) |
| This document | Checklist and how to run |

Do **not** need to share `jenkins_debug\` — it is recreated each run.

---

## Prerequisites checklist (before first run)

- [ ] **Windows** PC (batch file is for Windows; Python script can run on other OS with manual commands).
- [ ] **Python 3** installed; `py -3` or `python` works in Command Prompt or PowerShell.
- [ ] **Google Chrome** and/or **Microsoft Edge** installed. The script tries **Chrome first**, then **Edge** if Chrome cannot start.
- [ ] **Network access** to Jenkins: `https://illinipas01:8443` (VPN/corporate network if required).
- [ ] Valid **Jenkins** credentials for the job `VFDE_NEXUS_JOBS` → `upload_zip_to_vfde_nexus`.
- [ ] **Microsoft Outlook (desktop)** installed and configured for your mailbox — email is sent via Outlook COM by default.
- [ ] **Selenium**: the `.bat` can install it automatically (`pip install selenium`). Or run manually: `py -3 -m pip install --user selenium`.
- [ ] **pywin32** (optional): `.bat` may install it; not strictly required if Outlook send works via PowerShell path in the script.

---

## What the utility does (short)

1. Opens Jenkins login, signs in (retries username/password once if login fails).
2. Navigates to `upload_zip_to_vfde_nexus` → **Build with Parameters**.
3. Sets **ProjectName**, **Release_name**, **BuildNumber**, **Folder_Name** (Jenkins field is still `FOLDER_NAME` internally).
4. Submits the build, opens the **latest** build, opens **Console Output**, waits until **Finished: Success** or **Finished: Failure**.
5. Sends a status email (subject format includes Folder_Name, release text with leading `4000` stripped for subject only, project, build number, Success/Failure).

---

## How to run (recommended)

1. Put `jenkins_deploy_utility.py` and `run_jenkins_deploy.bat` in the **same folder**.
2. Double-click **`run_jenkins_deploy.bat`**.
3. Enter when prompted:
   - **Jenkins username** (NTNET / short id, e.g. `gauracha`)
   - **ProjectName**: `OGW`, `O2A`, or `SKY`
   - **Release_name**: e.g. `4000_WAVE11`, `4000_WAVE10_PCK21_1` (must exist in the dropdown for that project)
   - **BuildNumber**: must **exactly** match an option in the dropdown for that Project + Release (or you get a popup and the run stops)
   - **Folder_Name**: e.g. `26.06.OMA`, `26.02.OMA`, `26.06.OMI`
4. When asked, enter **Jenkins password** (hidden).
5. Wait for the browser to finish; read the window or **`jenkins_debug\run.log`** if something fails.

---

## Command-line run (alternative)

From the folder that contains the script:

```text
py -3 jenkins_deploy_utility.py --username YOUR_USER --project-name OGW --release-name 4000_WAVE11 --build-number 8018 --folder-name "26.06.OMA" --pause-on-exit
```

Optional environment variables:

- `JENKINS_USER`, `JENKINS_PASS` — avoid password prompt if set.

---

## Troubleshooting checklist

| Symptom | What to check |
|--------|----------------|
| Window flashes and closes | Run via `run_jenkins_deploy.bat` (it pauses) or use `--pause-on-exit`. Read `jenkins_debug\run.log`. |
| Python not found | Install Python 3; ensure **App execution aliases** for “python” do not block real Python (Windows Settings → Apps). |
| `ModuleNotFoundError: selenium` | `py -3 -m pip install --user selenium` |
| Browser does not start | Update Chrome/Edge; ensure Selenium matches browser major version (Selenium 4 manages drivers in many setups). |
| Login fails twice | Correct Jenkins URL access; correct username/password; SSO may require using the same login method as in browser. |
| Popup: BuildNumber not available | Pick a **BuildNumber** that appears in Jenkins for that **ProjectName** + **Release_name** combination. |
| No email | Outlook desktop must be installed; check `run.log` for Outlook or SMTP errors. Default recipient is configurable in script (`--email-to`). |
| Jenkins SSL / certificate | Script uses insecure-cert flags for internal HTTPS; if still blocked, IT may need to trust the Jenkins cert. |

---

## Log and debug output

- Each run **overwrites** `jenkins_debug\run.log` and clears previous `jenkins_debug\*.png` / `*.html` snapshots.
- If you need to report an issue, attach the latest **`jenkins_debug\run.log`** and any **`jenkins_debug\*_failure*.html`** from that run.

---

## Security note

- Do not commit real passwords or share logs that contain secrets.
- Prefer `JENKINS_PASS` only on a private machine or use interactive password entry.

---

## Support / customization

- Jenkins URL and job path are defined at the top of `jenkins_deploy_utility.py` (`LOGIN_URL`, `TARGET_JOB_URL`).
- Email recipient default: see `--email-to` in the script.
