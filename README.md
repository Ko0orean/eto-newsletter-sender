# ETO Newsletter Sender

A Windows desktop application for sending the monthly e-newsletter to the
office contact list through MailerLite.

## What it does

- Loads a subscriber list from CSV (`name, email, company, joined, suspicious`).
- Shows every contact in a searchable, sortable table with a live total count.
- Flags suspicious addresses (bot sign-ups, test addresses) with a warning icon.
- Compares the uploaded CSV with the saved MailerLite group (last send's
  recipients; falls back to the whole account before the first send): new
  sign-ups, departures, and MailerLite unsubscribes. From the comparison you
  can tick contacts to add new sign-ups to the saved group, pull departed
  contacts back into the list (and save an updated CSV), or delete them.
- A **Preview** button renders the exact email (PDF button and footer banner
  included) in your browser before anything is sent.
- The email ends with a navy banner holding your social links (set them once
  in Settings; only filled ones appear) and an Unsubscribe button.
- Sends a test email to one or more addresses you type (comma-separated), so
  several people can check it first. The addresses are remembered.
- The send button stays locked until a **pre-send checklist** is complete:
  run the comparison, send a test email, then tick the confirmation box.
- Sends the newsletter with a deliberate **two-step** action:
  1. The first click on **Send to all subscribers** fills the button with
     colour and ticks every recipient. You can untick any address to exclude it.
  2. The second click asks for confirmation, then starts a **5-second
     countdown** — the button turns into a red cancel button — and only then
     sends to exactly the ticked recipients.
- Built-in safety checks: the PDF link is verified before any send, changing
  the newsletter content invalidates the last test, a recent campaign with the
  same subject triggers a duplicate-send warning, and after a successful send
  the button re-locks. CSV files saved by Korean Excel (CP949) load fine.
- The comparison dialog can optionally delete departed contacts from the
  MailerLite account (unsubscribed ones are always kept, preserving opt-outs);
  old [TEST] campaigns are cleaned up automatically.
- The newsletter body is written in Markdown; the official PDF is linked (hosted
  on your own website) as a download button inside the email.
- MailerLite adds the mandatory unsubscribe link automatically.

## Setting up on a new computer (e.g. the manager's laptop)

1. Install [Python 3.11+](https://www.python.org/downloads/) — tick
   **"Add Python to PATH"** — and [Git](https://git-scm.com/download/win)
   (default options).
2. Clone the app (no GitHub account needed):
   ```
   git clone https://github.com/Ko0orean/eto-newsletter-sender.git
   ```
3. Get `config.json` (contains the API key) and the subscriber CSV from the
   developer **by USB or another safe channel — never email or chat**, and put
   `config.json` in the app folder. (Or start the app and enter the key in
   Settings.)
4. From then on, double-click **Update ETO Newsletter.bat** — it pulls the
   latest version and starts the app. `config.json` and CSV files are never
   touched by updates.

## Running the app (double-click)

Two double-click options are provided:

- **Run ETO Newsletter.bat** — the most reliable. On the first run it installs
  the required packages automatically, then starts the app. If anything goes
  wrong the window stays open so you can read the message. Use this the first
  time.
- **Run ETO Newsletter.pyw** — starts the app directly with no console window.
  Use this on later runs once the packages are installed.

Both require Python 3.11+ to be installed on the machine, with "Add Python to
PATH" ticked during installation. (Python is the only prerequisite; VS Code is
not needed.)

If you prefer the command line:
```
pip install -r requirements.txt
python -m eto_newsletter
```

## First-time setup

1. Start the app (see above).
2. Click **Settings** and enter:
   - your MailerLite API key (MailerLite -> Integrations -> API),
   - the sender name and sender email (e.g. `hknewsletter_korea@hketotyo.gov.hk`).
   - tick **Skip SSL verification** only if the office proxy blocks the
     connection (see note below).
   Click **Test connection**, then **Save**.

### Where settings are stored

All settings - API key, sender name, sender email, subject, and the
skip-SSL-verification flag - are saved in a single file, **`config.json`**, in
the application folder. Everything travels with the folder.

> Security note: the API key is stored in plain text inside `config.json`.
> Keep the application folder private and do not copy `config.json` to shared
> drives or email. If you move the app to another machine, the key moves with
> it.

### Sending from your own domain

Before the first real send, IT must add the SPF/DKIM/DMARC DNS records that
MailerLite provides for `hketotyo.gov.hk`. The SPF record in particular must be
*merged* into the domain's existing SPF entry, not added separately.

### If the connection is blocked (SSL)

On a corporate network that inspects SSL traffic, the connection to MailerLite
may fail with a certificate error. As a temporary measure, tick **Skip SSL
verification** in Settings. The proper fix is to obtain the proxy root CA
certificate from IT; ask for it alongside the DNS request.

## Monthly use

1. Start the app and click **Upload list…** to load the latest CSV.
2. Click **Compare with MailerLite** to see new sign-ups, departures, and
   unsubscribes against last month's send.
3. Choose the newsletter Markdown file and paste the PDF link.
4. Check/adjust the subject (it is remembered from last time).
5. Type one or more test addresses (comma-separated) and click **Send test**;
   check it in Outlook/Gmail/mobile.
6. Tick **"I reviewed the comparison and the test email"** — this unlocks the
   send button.
7. Click **Send to all subscribers**, review/untick recipients, then click
   **Confirm — send now**. A 5-second countdown follows; click the red button
   to cancel, or wait and the newsletter goes out.

After a successful send the app rebuilds the saved MailerLite group
(`Korea contacts`) to match exactly who received this issue, so next month's
comparison is always against the previous send.

## Building a standalone .exe

To produce a single executable that runs without a Python install:
```
pip install pyinstaller
python build_exe.py
```
The packaged app appears in `dist/ETO Newsletter Sender/`. Its `config.json`
is created next to the executable.

## Project layout

```
eto_newsletter/
  __init__.py
  __main__.py            entry point (python -m eto_newsletter)
  app.py                 the GUI (PySide6) and two-step send flow
  service.py             orchestration: sync, test send, live send
  mailerlite_client.py   the only file that talks to MailerLite
  content.py             CSV loading and Markdown -> email HTML
  settings.py            config.json read/write (project folder)
Run ETO Newsletter.bat   double-click launcher (installs deps first run)
Run ETO Newsletter.pyw   double-click launcher (no console)
requirements.txt
build_exe.py
```

The MailerLite-specific code is isolated in `mailerlite_client.py`. Switching to
another provider later means rewriting only that file.
