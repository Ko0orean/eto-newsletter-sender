"""ETO Newsletter Sender - desktop GUI (PySide6).

Implements:
  * Upload a CSV (name, email, company, joined, suspicious).
  * Subscriber table with live search and click-to-sort columns.
  * Total-subscriber count in the top bar.
  * Suspicious addresses flagged with a warning icon and amber row tint.
  * Test send to a typed address.
  * Two-press "Send to all subscribers":
      1st press -> button fills with colour, every row becomes selected
                   (checkboxes), and the user may untick addresses.
      2nd press -> a confirmation dialog, then the campaign is sent to exactly
                   the ticked recipients.

All network calls run on a background QThread so the window stays responsive.
"""
from __future__ import annotations

import os
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal, QSortFilterProxyModel
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QPalette,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import settings
from .content import (
    load_subscribers,
    markdown_to_email_html,
    parse_email_list,
    save_subscribers,
)
from .mailerlite_client import MailerLiteClient, MailerLiteError, Subscriber
from .service import CampaignDraft, GroupComparison, NewsletterService

# Column layout for the model.
COL_SELECT = 0
COL_NAME = 1
COL_EMAIL = 2
COL_COMPANY = 3
COL_JOINED = 4
COL_FLAG = 5
HEADERS = ["", "Name", "Email", "Company", "Joined", "Flag"]

NAVY = "#1f3864"
AMBER_TINT = QColor("#faeeda")


class ContactFilterProxy(QSortFilterProxyModel):
    """Filters rows by a case-insensitive substring match across the name,
    email and company columns. A dedicated proxy is used rather than the
    built-in single-column filter because the table's first column holds a
    checkbox (no text), which makes the default all-column matching unreliable.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""

    def set_needle(self, text: str) -> None:
        self._needle = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        if not self._needle:
            return True
        model = self.sourceModel()
        for col in (COL_NAME, COL_EMAIL, COL_COMPANY):
            idx = model.index(source_row, col, source_parent)
            value = (model.data(idx) or "").lower()
            if self._needle in value:
                return True
        return False


# --------------------------------------------------------------------------
# Background worker: runs a callable off the UI thread and reports result.
# --------------------------------------------------------------------------
class Worker(QObject):
    progress = Signal(str)
    finished = Signal(object)   # result on success
    failed = Signal(str)        # error message

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, progress=self.progress.emit, **self._kwargs)
        except MailerLiteError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # defensive: never let a thread die silently
            self.failed.emit(f"Unexpected error: {exc}")
        else:
            self.finished.emit(result)


# --------------------------------------------------------------------------
# Group-comparison dialog
# --------------------------------------------------------------------------
class ComparisonDialog(QDialog):
    """Shows new sign-ups / departures / unsubscribes after comparing the
    uploaded CSV with MailerLite, and lets the user act on them:

    * tick new sign-ups and add them to the saved group,
    * tick departed contacts and pull them back into the current list (CSV),
    * or delete departed contacts from the account.

    The dialog only records what was requested; the main window performs the
    actual work (network calls run on its worker thread).
    """

    def __init__(
        self,
        comparison: GroupComparison,
        csv_total: int,
        group_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("List comparison")
        self.setMinimumSize(560, 560)

        # Filled in when the user picks an action:
        self.add_to_group_emails: list[str] | None = None
        self.import_departed: list[dict] | None = None
        self.delete_departed_requested = False

        layout = QVBoxLayout(self)

        if comparison.basis == "none":
            summary = QLabel(
                "The MailerLite account has no subscribers yet, so everyone "
                f"in this CSV ({csv_total} contacts) counts as new."
            )
            baseline_name = "MailerLite"
        elif comparison.basis == "account":
            summary = QLabel(
                f"The group “{group_name}” was not found, so the CSV was "
                "compared against the whole MailerLite account. (Check the "
                "group name in Settings if this is unexpected.)\n"
                f"Uploaded CSV: {csv_total} contacts  ·  "
                f"Account subscribers: {comparison.baseline_total}\n"
                f"New sign-ups: {len(comparison.new_emails)}  ·  "
                f"Departed: {len(comparison.departed)}  ·  "
                f"Unsubscribed via MailerLite: {len(comparison.unsubscribed)}"
            )
            baseline_name = "the MailerLite account"
        else:
            summary = QLabel(
                f"Uploaded CSV: {csv_total} contacts  ·  "
                f"Group “{group_name}”: {comparison.baseline_total} contacts\n"
                f"New sign-ups: {len(comparison.new_emails)}  ·  "
                f"Departed: {len(comparison.departed)}  ·  "
                f"Unsubscribed via MailerLite: {len(comparison.unsubscribed)}"
            )
            baseline_name = f"“{group_name}”"
        summary.setWordWrap(True)
        summary.setStyleSheet("font-weight:600;")
        layout.addWidget(summary)

        def section_label(text: str) -> None:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{NAVY}; font-weight:600; margin-top:6px;")
            layout.addWidget(label)

        # ---- New sign-ups: checkable, can be added to the saved group ------
        section_label(
            f"New sign-ups ({len(comparison.new_emails)}) — in the CSV, "
            f"not in {baseline_name}"
        )
        if comparison.new_emails:
            self.new_list = self._make_check_list(
                [(email, email) for email in comparison.new_emails]
            )
            layout.addWidget(self.new_list)
            row = QHBoxLayout()
            row.addWidget(self._select_all_box(self.new_list))
            row.addStretch()
            add_btn = QPushButton(f"Add checked to “{group_name}”")
            add_btn.clicked.connect(self._request_add_to_group)
            row.addWidget(add_btn)
            layout.addLayout(row)
        else:
            self.new_list = None
            layout.addWidget(self._none_label())

        # ---- Departed: checkable, can be pulled back into the list, or
        # deleted from the account --------------------------------------------
        section_label(
            f"Departed ({len(comparison.departed)}) — in {baseline_name}, "
            "missing from the CSV"
        )
        if comparison.departed:
            self.dep_list = self._make_check_list(
                [
                    (
                        f"{d['email']}"
                        + (f"  —  {d['name']}" if d.get("name") else "")
                        + f"  (status: {d['status']})",
                        d,
                    )
                    for d in comparison.departed
                ]
            )
            layout.addWidget(self.dep_list)
            row = QHBoxLayout()
            row.addWidget(self._select_all_box(self.dep_list))
            row.addStretch()
            import_btn = QPushButton("Add checked to the current list (CSV)")
            import_btn.clicked.connect(self._request_import)
            row.addWidget(import_btn)
            self._deletable = sum(
                1 for d in comparison.departed
                if d.get("status") == "active" and d.get("id")
            )
            if self._deletable:
                del_btn = QPushButton("Delete checked from MailerLite…")
                del_btn.setStyleSheet("color:#a32d2d;")
                del_btn.clicked.connect(self._confirm_delete)
                row.addWidget(del_btn)
            layout.addLayout(row)
            note = QLabel(
                "Deleting only removes contacts whose status is active; "
                "unsubscribed/bounced ones are kept so their opt-out record "
                "is preserved."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color:#888; font-size:11px;")
            layout.addWidget(note)
        else:
            self.dep_list = None
            layout.addWidget(self._none_label())

        # ---- Unsubscribed: information only ---------------------------------
        section_label(
            f"Unsubscribed via MailerLite ({len(comparison.unsubscribed)}) — "
            "still in the CSV but opted out; MailerLite will not email them"
        )
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(
            "\n".join(comparison.unsubscribed) if comparison.unsubscribed else "None."
        )
        box.setMaximumHeight(70)
        layout.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # ---- widgets --------------------------------------------------------------

    @staticmethod
    def _none_label() -> QLabel:
        label = QLabel("None.")
        label.setStyleSheet("color:#888;")
        return label

    @staticmethod
    def _make_check_list(items: list[tuple[str, object]]) -> QListWidget:
        lw = QListWidget()
        for text, payload in items:
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, payload)
            lw.addItem(item)
        lw.setMaximumHeight(130)
        return lw

    @staticmethod
    def _select_all_box(lw: QListWidget) -> QCheckBox:
        box = QCheckBox("Select all")

        def toggle(checked: bool) -> None:
            state = Qt.Checked if checked else Qt.Unchecked
            for i in range(lw.count()):
                lw.item(i).setCheckState(state)

        box.toggled.connect(toggle)
        return box

    @staticmethod
    def _checked_payloads(lw: QListWidget) -> list:
        return [
            lw.item(i).data(Qt.UserRole)
            for i in range(lw.count())
            if lw.item(i).checkState() == Qt.Checked
        ]

    # ---- actions --------------------------------------------------------------

    def _request_add_to_group(self) -> None:
        emails = self._checked_payloads(self.new_list)
        if not emails:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one new sign-up first.")
            return
        self.add_to_group_emails = emails
        self.accept()

    def _request_import(self) -> None:
        entries = self._checked_payloads(self.dep_list)
        if not entries:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one departed contact first.")
            return
        self.import_departed = entries
        self.accept()

    def _confirm_delete(self) -> None:
        entries = [
            d for d in self._checked_payloads(self.dep_list)
            if d.get("status") == "active" and d.get("id")
        ]
        if not entries:
            QMessageBox.information(
                self, "Nothing to delete",
                "Tick at least one departed contact whose status is active. "
                "Unsubscribed/bounced contacts are never deleted.",
            )
            return
        confirm = QMessageBox.warning(
            self, "Delete from MailerLite",
            f"Permanently delete {len(entries)} contact(s) from the "
            "MailerLite account?\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.delete_departed_requested = True
            self._delete_entries = entries
            self.accept()


# --------------------------------------------------------------------------
# Settings dialog
# --------------------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        self.key_edit = QLineEdit(settings.load_api_key())
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("MailerLite API key")

        sender = settings.load_sender()
        self.name_edit = QLineEdit(sender.get("from_name", ""))
        self.email_edit = QLineEdit(sender.get("from_email", ""))
        self.email_edit.setPlaceholderText("e.g. enquiry@hketotyo.gov.hk")

        self.group_edit = QLineEdit(settings.load_group_name())
        self.group_edit.setPlaceholderText("ETO Korea Newsletter Subscribers")
        self.group_edit.setToolTip(
            "The MailerLite group used as the comparison baseline and kept in "
            "sync after each send. Must match the group name in the MailerLite "
            "dashboard (case and spacing are forgiven)."
        )

        self.skip_ssl_check = QCheckBox(
            "Skip SSL verification (use only behind a corporate proxy)"
        )
        self.skip_ssl_check.setChecked(bool(sender.get("skip_ssl_verify", False)))
        self.skip_ssl_check.setToolTip(
            "Bypasses TLS certificate checks. This weakens transport security "
            "and should be a temporary measure. Ask IT for the proxy root "
            "certificate for a proper fix."
        )

        self.status = QLabel("")
        self.status.setStyleSheet("color:#888;")

        form = QFormLayout()
        form.addRow("API key", self.key_edit)
        form.addRow("Sender name", self.name_edit)
        form.addRow("Sender email", self.email_edit)
        form.addRow("Subscriber group", self.group_edit)
        form.addRow("", self.skip_ssl_check)

        banner_head = QLabel("Footer banner links (shown in the email when filled)")
        banner_head.setStyleSheet("font-weight:600; margin-top:8px;")
        form.addRow(banner_head)
        links = settings.load_social_links()
        self.social_edits: dict[str, QLineEdit] = {}
        for label in settings.SOCIAL_LABELS:
            edit = QLineEdit(links.get(label, ""))
            edit.setPlaceholderText(f"https://… ({label}, optional)")
            self.social_edits[label] = edit
            form.addRow(label, edit)

        test_btn = QPushButton("Test connection")
        test_btn.clicked.connect(self._test_connection)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(test_btn)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

    def _test_connection(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.status.setText("Enter an API key first.")
            return
        self.status.setText("Checking…")
        QApplication.processEvents()
        try:
            MailerLiteClient(
                api_key=key, verify_ssl=not self.skip_ssl_check.isChecked()
            ).verify_key()
        except MailerLiteError as exc:
            self.status.setStyleSheet("color:#a32d2d;")
            self.status.setText(str(exc))
        else:
            self.status.setStyleSheet("color:#3b6d11;")
            self.status.setText("Connection OK.")

    def _save(self) -> None:
        settings.save_api_key(self.key_edit.text())
        settings.save_sender(
            self.name_edit.text().strip(),
            self.email_edit.text().strip(),
            self.skip_ssl_check.isChecked(),
        )
        settings.save_group_name(self.group_edit.text())
        settings.save_social_links(
            {label: edit.text() for label, edit in self.social_edits.items()}
        )
        self.accept()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ETO Newsletter Sender")
        self.resize(1040, 680)

        self.subscribers: list[Subscriber] = []
        self.arming = False           # True after first press of the send button
        self.compare_done = False     # list compared with MailerLite group
        self.test_sent = False        # a test email went out for this list
        self._tested_fingerprint = None  # draft state at the time of the test
        self._busy = False            # a background task is running
        self._countdown_timer: QTimer | None = None
        self._countdown = 0
        self._pending_send = None     # (draft, selected, client) during countdown
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._on_done = None
        self._pending_ok = None
        self._pending_result = None

        self._build_ui()
        self._refresh_count()
        self._update_checklist()

    def closeEvent(self, event) -> None:
        # A pending countdown must never survive the window.
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
            self._pending_send = None
        # If a send/test is still running, stop its thread cleanly before the
        # window (and its C++ objects) are destroyed, to avoid
        # "QThread: Destroyed while thread is still running".
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(5000)  # main thread waiting on the worker thread: safe
        super().closeEvent(event)

    # ---- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top bar: title + settings
        topbar = QWidget()
        topbar.setStyleSheet(f"background:{NAVY};")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(14, 8, 14, 8)
        title = QLabel("ETO Newsletter Sender")
        title.setStyleSheet("color:white; font-size:15px; font-weight:600;")
        tb.addWidget(title)
        tb.addStretch()
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self._open_settings)
        tb.addWidget(settings_btn)
        outer.addWidget(topbar)

        # Toolbar row: upload, search, count
        toolrow = QWidget()
        tr = QHBoxLayout(toolrow)
        tr.setContentsMargins(14, 10, 14, 10)
        upload_btn = QPushButton("Upload list…")
        upload_btn.clicked.connect(self._upload_csv)
        tr.addWidget(upload_btn)

        self.compare_btn = QPushButton("Compare with MailerLite")
        self.compare_btn.setToolTip(
            "Compares the uploaded CSV with the saved MailerLite group "
            "(last send's recipients): new sign-ups, departures, unsubscribes."
        )
        self.compare_btn.clicked.connect(self._run_compare)
        self.compare_btn.setEnabled(False)
        tr.addWidget(self.compare_btn)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search name, company, email…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        tr.addWidget(self.search_edit, stretch=1)

        self.count_label = QLabel("Total subscribers: 0")
        self.count_label.setStyleSheet("font-weight:600;")
        tr.addWidget(self.count_label)
        outer.addWidget(toolrow)

        # Main split: table (left) + campaign panel (right)
        split = QHBoxLayout()
        split.setContentsMargins(14, 0, 14, 8)
        split.setSpacing(14)

        # Table + model + proxy for search/sort
        self.model = QStandardItemModel(0, len(HEADERS))
        self.model.setHorizontalHeaderLabels(HEADERS)
        self.model.itemChanged.connect(self._on_item_changed)

        self.proxy = ContactFilterProxy()
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionMode(QTableView.NoSelection)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_EMAIL, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_COMPANY, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_JOINED, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_SELECT, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_FLAG, QHeaderView.ResizeToContents)
        split.addWidget(self.table, stretch=2)

        # Campaign panel
        split.addWidget(self._build_campaign_panel(), stretch=1)
        outer.addLayout(split, stretch=1)

        # Status bar
        self.setStatusBar(QStatusBar())
        self._set_status("Ready. Open Settings to enter your API key, then upload a list.")

    def _build_campaign_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        heading = QLabel("Campaign")
        heading.setStyleSheet("font-weight:600; font-size:14px;")
        v.addWidget(heading)

        v.addWidget(QLabel("Newsletter (Markdown)"))
        md_row = QHBoxLayout()
        self.md_path_edit = QLineEdit()
        self.md_path_edit.setPlaceholderText("Choose a .md file…")
        self.md_path_edit.setReadOnly(True)
        md_btn = QPushButton("Browse…")
        md_btn.clicked.connect(self._choose_markdown)
        preview_btn = QPushButton("Preview")
        preview_btn.setToolTip(
            "Opens the rendered email (exactly as it will be sent, including "
            "the PDF button and the footer banner) in your browser."
        )
        preview_btn.clicked.connect(self._preview_newsletter)
        md_row.addWidget(self.md_path_edit, stretch=1)
        md_row.addWidget(md_btn)
        md_row.addWidget(preview_btn)
        v.addLayout(md_row)

        v.addWidget(QLabel("PDF link (hosted on your website)"))
        self.pdf_edit = QLineEdit()
        self.pdf_edit.setPlaceholderText("https://www.hketotyo.gov.hk/…/newsletter.pdf")
        self.pdf_edit.textChanged.connect(self._invalidate_test_if_changed)
        v.addWidget(self.pdf_edit)

        v.addWidget(QLabel("Subject"))
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("ETO Korea Newsletter — June 2026")
        self.subject_edit.setText(settings.load_subject())
        self.subject_edit.editingFinished.connect(
            lambda: settings.save_subject(self.subject_edit.text())
        )
        self.subject_edit.textChanged.connect(self._invalidate_test_if_changed)
        v.addWidget(self.subject_edit)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#ddd;")
        v.addWidget(line)

        v.addWidget(QLabel("Test email(s) — separate several with commas"))
        test_row = QHBoxLayout()
        self.test_edit = QLineEdit()
        self.test_edit.setPlaceholderText("you@hketotyo.gov.hk, colleague@hketotyo.gov.hk")
        self.test_edit.setText(settings.load_test_emails())
        self.test_edit.editingFinished.connect(
            lambda: settings.save_test_emails(self.test_edit.text())
        )
        self.test_btn = QPushButton("Send test")
        self.test_btn.clicked.connect(self._send_test)
        test_row.addWidget(self.test_edit, stretch=1)
        test_row.addWidget(self.test_btn)
        v.addLayout(test_row)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setStyleSheet("color:#ddd;")
        v.addWidget(line2)

        checklist_head = QLabel("Checklist before sending")
        checklist_head.setStyleSheet("font-weight:600;")
        v.addWidget(checklist_head)

        self.step_compare = QLabel()
        self.step_test = QLabel()
        for lab in (self.step_compare, self.step_test):
            lab.setWordWrap(True)
            v.addWidget(lab)

        self.ready_check = QCheckBox("I reviewed the comparison and the test email")
        self.ready_check.setEnabled(False)
        self.ready_check.toggled.connect(self._update_gate)
        v.addWidget(self.ready_check)

        self.send_btn = QPushButton("Send to all subscribers")
        self.send_btn.setMinimumHeight(40)
        self.send_btn.clicked.connect(self._on_send_clicked)
        self._style_send_button(armed=False)
        v.addWidget(self.send_btn)

        self.send_hint = QLabel("Sending is a two-step action: the first click lets "
                                "you review and untick recipients.")
        self.send_hint.setWordWrap(True)
        self.send_hint.setStyleSheet("color:#888; font-size:11px;")
        v.addWidget(self.send_hint)

        v.addStretch()
        return panel

    # ---- helpers -----------------------------------------------------------

    def _icon(self, std: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(std)

    def _set_status(self, msg: str) -> None:
        self.statusBar().showMessage(msg)

    def _style_send_button(self, armed: bool) -> None:
        if armed:
            self.send_btn.setText("Confirm — send now")
            self.send_btn.setStyleSheet(
                f"background:{NAVY}; color:white; font-weight:600; border-radius:6px;"
            )
        else:
            self.send_btn.setText("Send to all subscribers")
            self.send_btn.setStyleSheet(
                f"color:{NAVY}; font-weight:600; border:2px solid {NAVY}; border-radius:6px;"
            )

    def _refresh_count(self) -> None:
        self.count_label.setText(f"Total subscribers: {len(self.subscribers)}")

    # ---- CSV upload --------------------------------------------------------

    def _upload_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open subscriber list", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            subs, warnings = load_subscribers(path)
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "Could not read file", str(exc))
            return

        self.subscribers = subs
        self._populate_table()
        self._refresh_count()
        self._disarm()  # any reload cancels an armed send
        # A new list restarts the pre-send checklist from scratch.
        self.compare_done = False
        self.test_sent = False
        self.compare_btn.setEnabled(True)
        self._update_checklist()

        msg = f"Loaded {len(subs)} contacts."
        flagged = sum(1 for s in subs if s.is_suspicious)
        if flagged:
            msg += f" {flagged} flagged as suspicious."
        if warnings:
            msg += f" {len(warnings)} row(s) skipped — see details."
            QMessageBox.warning(
                self, "Loaded with warnings",
                f"{msg}\n\n" + "\n".join(warnings[:20])
                + ("\n…" if len(warnings) > 20 else ""),
            )
        self._set_status(msg)

    def _populate_table(self) -> None:
        self.model.blockSignals(True)
        self.model.removeRows(0, self.model.rowCount())
        for sub in self.subscribers:
            select_item = QStandardItem()
            select_item.setCheckable(True)
            select_item.setCheckState(Qt.Unchecked)
            select_item.setData(sub.email, Qt.UserRole)

            name_item = QStandardItem(sub.name)
            email_item = QStandardItem(sub.email)
            company_item = QStandardItem(sub.company)
            joined_item = QStandardItem(sub.joined)
            flag_item = QStandardItem()
            if sub.is_suspicious:
                flag_item.setIcon(self._icon(QStyle.SP_MessageBoxWarning))
                flag_item.setToolTip(sub.suspicious)

            row = [select_item, name_item, email_item, company_item, joined_item, flag_item]
            if sub.is_suspicious:
                for it in row:
                    it.setBackground(AMBER_TINT)
            self.model.appendRow(row)
        self.model.blockSignals(False)
        self.proxy.invalidate()  # re-evaluate filter for the newly added rows

    # ---- search ------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self.proxy.set_needle(text)

    # ---- selection / arming ------------------------------------------------

    def _on_item_changed(self, item: QStandardItem) -> None:
        # Only the checkbox column matters; nothing else is editable.
        if item.column() == COL_SELECT and self.arming:
            self._update_selected_count()

    def _checked_emails(self) -> list[str]:
        emails: list[str] = []
        for row in range(self.model.rowCount()):
            it = self.model.item(row, COL_SELECT)
            if it.checkState() == Qt.Checked:
                emails.append(it.data(Qt.UserRole))
        return emails

    def _set_all_checks(self, state: Qt.CheckState) -> None:
        self.model.blockSignals(True)
        for row in range(self.model.rowCount()):
            self.model.item(row, COL_SELECT).setCheckState(state)
        self.model.blockSignals(False)

    def _update_selected_count(self) -> None:
        n = len(self._checked_emails())
        self._set_status(f"{n} recipient(s) selected. Click 'Confirm — send now' to send.")

    def _arm(self) -> None:
        if not self.subscribers:
            QMessageBox.information(self, "No contacts", "Upload a subscriber list first.")
            return
        self.arming = True
        self.table.setColumnHidden(COL_SELECT, False)
        self._set_all_checks(Qt.Checked)  # select everyone by default
        self._style_send_button(armed=True)
        self.send_hint.setText("All recipients are selected. Untick any address you "
                               "want to exclude, then click again to send.")
        self._update_selected_count()

    def _disarm(self) -> None:
        self._cancel_countdown(silent=True)
        self.arming = False
        self._style_send_button(armed=False)
        self.send_hint.setText("Sending is a two-step action: the first click lets "
                               "you review and untick recipients.")

    # ---- pre-send checklist gate --------------------------------------------

    def _update_checklist(self) -> None:
        def mark(label: QLabel, done: bool, text: str) -> None:
            if done:
                label.setText("✓ " + text)
                label.setStyleSheet("color:#3b6d11;")
            else:
                label.setText("○ " + text)
                label.setStyleSheet("color:#888;")

        mark(self.step_compare, self.compare_done,
             "Compare the list with MailerLite")
        mark(self.step_test, self.test_sent,
             "Send yourself a test email")
        self._update_gate()

    def _update_gate(self) -> None:
        """The send button unlocks only after both checklist steps are done
        AND the user has ticked the confirmation box."""
        both_done = self.compare_done and self.test_sent
        if not both_done and self.ready_check.isChecked():
            self.ready_check.blockSignals(True)
            self.ready_check.setChecked(False)
            self.ready_check.blockSignals(False)
        self.ready_check.setEnabled(both_done and not self._busy)

        counting_down = self._countdown_timer is not None
        self.send_btn.setEnabled(
            counting_down or (not self._busy and self.ready_check.isChecked())
        )
        if counting_down or self._busy:
            return
        if not both_done:
            self.send_btn.setToolTip(
                "Locked: run the comparison and send a test email "
                "(in any order), then tick the confirmation box."
            )
        elif not self.ready_check.isChecked():
            self.send_btn.setToolTip("Tick the confirmation box above to unlock.")
        else:
            self.send_btn.setToolTip("")

    # ---- test-freshness guard --------------------------------------------------

    def _draft_fingerprint(self) -> tuple:
        """A snapshot of everything that shapes the outgoing email. If any of
        it changes after a test, the test no longer proves anything."""
        md_path = self.md_path_edit.property("path")
        md_mtime = None
        if md_path:
            try:
                md_mtime = os.path.getmtime(md_path)
            except OSError:
                md_mtime = None
        return (
            self.subject_edit.text().strip(),
            md_path,
            self.pdf_edit.text().strip(),
            md_mtime,
        )

    def _invalidate_test_if_changed(self, *_args) -> bool:
        """Reset the 'test sent' tick if the draft changed since the test.
        Returns True if the tick was just reset."""
        if not self.test_sent:
            return False
        if self._draft_fingerprint() == self._tested_fingerprint:
            return False
        self.test_sent = False
        self._tested_fingerprint = None
        self._update_checklist()
        self._set_status(
            "Newsletter content changed since the last test — "
            "send a new test email before sending."
        )
        return True

    # ---- group comparison ----------------------------------------------------

    def _run_compare(self) -> None:
        if not self.subscribers:
            QMessageBox.information(self, "No contacts", "Upload a subscriber list first.")
            return
        client = self._client()
        if client is None:
            return
        service = self._service(client)
        self._run_async(
            service.compare_with_main_group,
            self.subscribers,
            on_done=self._on_compare_done,
            busy_msg="Comparing with the MailerLite group…",
        )

    def _on_compare_done(self, comparison: GroupComparison) -> None:
        self.compare_done = True
        self._update_checklist()
        if comparison.basis == "none":
            self._set_status(
                "Comparison done — the account has no subscribers yet; "
                "the whole CSV is new."
            )
        else:
            against = ("last send" if comparison.basis == "group"
                       else "whole account")
            self._set_status(
                f"Comparison done (vs {against}) — "
                f"new: {len(comparison.new_emails)}, "
                f"departed: {len(comparison.departed)}, "
                f"unsubscribed: {len(comparison.unsubscribed)}."
            )
        dlg = ComparisonDialog(
            comparison, len(self.subscribers), settings.load_group_name(), self
        )
        dlg.exec()

        if dlg.add_to_group_emails:
            wanted = {e.lower() for e in dlg.add_to_group_emails}
            subs = [s for s in self.subscribers if s.email.lower() in wanted]
            client = self._client()
            if client is None:
                return
            service = self._service(client)
            self._run_async(
                service.add_to_main_group,
                subs,
                on_done=lambda n: (
                    QMessageBox.information(
                        self, "Added",
                        f"Added {n} new sign-up(s) to the saved group."),
                    self._set_status(f"Added {n} new sign-up(s) to the saved group."),
                ),
                busy_msg="Adding new sign-ups to the saved group…",
            )
        elif dlg.import_departed:
            self._import_departed_into_list(dlg.import_departed)
        elif dlg.delete_departed_requested:
            client = self._client()
            if client is None:
                return
            service = self._service(client)
            self._run_async(
                service.delete_departed,
                dlg._delete_entries,
                on_done=self._on_delete_departed_done,
                busy_msg="Deleting departed contacts…",
            )

    def _import_departed_into_list(self, entries: list[dict]) -> None:
        """Merge departed contacts back into the loaded list and offer to save
        the merged list as a new CSV."""
        existing = {s.email.lower() for s in self.subscribers}
        added = 0
        for d in entries:
            email = (d.get("email") or "").strip()
            if not email or email.lower() in existing:
                continue
            self.subscribers.append(Subscriber(
                email=email,
                name=d.get("name") or "N/A",
                company=d.get("company") or "N/A",
            ))
            existing.add(email.lower())
            added += 1
        if not added:
            QMessageBox.information(
                self, "Nothing added",
                "All selected contacts were already in the list.")
            return
        self._populate_table()
        self._refresh_count()
        self._disarm()
        self._set_status(f"Added {added} departed contact(s) back into the list.")

        path, _ = QFileDialog.getSaveFileName(
            self, "Save the updated list", "subscribers-updated.csv",
            "CSV files (*.csv)"
        )
        if path:
            try:
                save_subscribers(path, self.subscribers)
            except OSError as exc:
                QMessageBox.critical(self, "Could not save", str(exc))
                return
            self._set_status(
                f"Added {added} contact(s) and saved the updated list to {path}."
            )

    def _on_delete_departed_done(self, result) -> None:
        deleted, skipped = result
        msg = f"Deleted {deleted} departed contact(s) from MailerLite."
        if skipped:
            msg += (
                f"\n\nKept {len(skipped)} (unsubscribed/bounced — their opt-out "
                "records are preserved):\n" + "\n".join(skipped[:15])
                + ("\n…" if len(skipped) > 15 else "")
            )
        QMessageBox.information(self, "Clean-up finished", msg)
        self._set_status(f"Deleted {deleted} departed contact(s).")

    # ---- send flow ---------------------------------------------------------

    def _current_draft(self) -> CampaignDraft | None:
        sender = settings.load_sender()
        subject = self.subject_edit.text().strip()
        from_email = sender.get("from_email", "").strip()
        md_path = self.md_path_edit.property("path")

        if not from_email:
            QMessageBox.warning(self, "Sender missing",
                                "Set the sender email in Settings first.")
            return None
        if not subject:
            QMessageBox.warning(self, "Subject missing", "Enter a subject line.")
            return None
        if not md_path:
            QMessageBox.warning(self, "Newsletter missing",
                                "Choose a Markdown file for the newsletter body.")
            return None
        try:
            markdown_text = open(md_path, encoding="utf-8").read()
        except OSError as exc:
            QMessageBox.critical(self, "Could not read newsletter", str(exc))
            return None

        # Remember the subject for next time.
        settings.save_subject(subject)

        return CampaignDraft(
            subject=subject,
            from_name=sender.get("from_name", "").strip() or "Newsletter",
            from_email=from_email,
            markdown=markdown_text,
            pdf_url=self.pdf_edit.text().strip() or None,
            social_links=settings.load_social_links(),
        )

    @staticmethod
    def _service(client: MailerLiteClient) -> NewsletterService:
        return NewsletterService(client, main_group=settings.load_group_name())

    def _client(self) -> MailerLiteClient | None:
        key = settings.load_api_key()
        if not key:
            QMessageBox.warning(self, "API key missing",
                                "Enter your MailerLite API key in Settings.")
            return None
        sender = settings.load_sender()
        return MailerLiteClient(
            api_key=key, verify_ssl=not sender.get("skip_ssl_verify", False)
        )

    def _on_send_clicked(self) -> None:
        # During the countdown the send button is the cancel button.
        if self._countdown_timer is not None:
            self._cancel_countdown()
            return

        # The Markdown file may have been edited on disk since the test;
        # re-check the fingerprint (which includes the file's mtime).
        if self._invalidate_test_if_changed():
            QMessageBox.warning(
                self, "Content changed",
                "The newsletter content changed since the last test email.\n"
                "Send a new test and tick the confirmation box again.",
            )
            self._disarm()
            return

        if not self.arming:
            self._arm()
            return

        # Second press -> confirm, then a cancellable 5-second countdown.
        emails = self._checked_emails()
        if not emails:
            QMessageBox.information(self, "No recipients",
                                    "No addresses are selected.")
            return
        draft = self._current_draft()
        if draft is None:
            return
        client = self._client()
        if client is None:
            return

        confirm = QMessageBox.question(
            self, "Confirm send",
            f"Send “{draft.subject}” to {len(emails)} subscriber(s) now?\n\n"
            "A 5-second countdown follows — you can still cancel during it.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        selected = [s for s in self.subscribers if s.email in set(emails)]

        # Duplicate-send guard: if a campaign with this exact subject already
        # went out recently (e.g. a previous attempt whose success was hidden
        # by a network error), warn before counting down.
        service = self._service(client)
        self._run_async(
            service.find_recent_same_subject,
            draft.subject,
            on_done=lambda existing: self._after_duplicate_check(
                existing, draft, selected, client
            ),
            busy_msg="Checking for a recent send with the same subject…",
        )

    def _after_duplicate_check(self, existing, draft, selected, client) -> None:
        if existing is not None:
            sent_at = (
                existing.get("finished_at")
                or existing.get("scheduled_for")
                or "unknown time"
            )
            resp = QMessageBox.warning(
                self, "Possible duplicate send",
                f"A campaign named “{draft.subject}” was already sent "
                f"(at {sent_at}).\n\n"
                "If a network error hid the success of a previous attempt, "
                "subscribers may already have this issue.\n\n"
                "Send it again anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                self._set_status("Send cancelled — a recent campaign with the "
                                 "same subject already exists.")
                return
        self._begin_countdown(draft, selected, client)

    # ---- countdown -----------------------------------------------------------

    def _begin_countdown(self, draft: CampaignDraft, selected, client) -> None:
        self._pending_send = (draft, selected, client)
        self._countdown = 5
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._countdown_tick)

        self.test_btn.setEnabled(False)
        self.compare_btn.setEnabled(False)
        self.ready_check.setEnabled(False)
        self.send_btn.setEnabled(True)
        self._show_countdown()
        self._countdown_timer.start()

    def _show_countdown(self) -> None:
        self.send_btn.setText(f"CANCEL — sending in {self._countdown} s")
        self.send_btn.setStyleSheet(
            "background:#a32d2d; color:white; font-weight:600; border-radius:6px;"
        )
        self._set_status(
            f"Sending in {self._countdown} second(s)… click the red button to cancel."
        )

    def _countdown_tick(self) -> None:
        self._countdown -= 1
        if self._countdown > 0:
            self._show_countdown()
            return
        # Time is up: fire the send.
        self._countdown_timer.stop()
        self._countdown_timer = None
        pending, self._pending_send = self._pending_send, None
        self._style_send_button(armed=True)
        self.compare_btn.setEnabled(True)
        self._update_gate()
        if pending is None:  # defensive; should not happen
            return
        draft, selected, client = pending
        service = self._service(client)
        self._run_async(
            service.send_to_selected,
            draft,
            selected,
            on_done=self._on_send_done,
            busy_msg="Sending…",
        )

    def _cancel_countdown(self, silent: bool = False) -> None:
        if self._countdown_timer is None:
            return
        self._countdown_timer.stop()
        self._countdown_timer = None
        self._pending_send = None
        self._style_send_button(armed=True)  # still armed; user may retry
        self.test_btn.setEnabled(True)
        self.compare_btn.setEnabled(bool(self.subscribers))
        self._update_gate()
        if not silent:
            self._set_status("Send cancelled. Nothing was sent.")

    def _on_send_done(self, campaign_id: object) -> None:
        QMessageBox.information(self, "Sent", "The newsletter has been sent.")
        self._disarm()
        # Re-lock the send button: an accidental re-send should require the
        # confirmation box to be ticked again, deliberately.
        self.ready_check.blockSignals(True)
        self.ready_check.setChecked(False)
        self.ready_check.blockSignals(False)
        self._update_gate()
        self.send_hint.setText(
            f"This issue was already sent (campaign {campaign_id}). "
            "To send again, tick the confirmation box again first."
        )

    def _send_test(self) -> None:
        raw = self.test_edit.text().strip()
        if not raw:
            QMessageBox.warning(self, "Test email missing",
                                "Enter one or more addresses to send the test to.")
            return
        test_emails, invalid = parse_email_list(raw)
        if invalid:
            QMessageBox.warning(
                self, "Invalid address",
                "These do not look like email addresses:\n\n"
                + "\n".join(invalid[:10])
                + ("\n…" if len(invalid) > 10 else "")
                + "\n\nFix or remove them, then try again.",
            )
            return
        if not test_emails:
            QMessageBox.warning(self, "Test email missing",
                                "No valid address found.")
            return
        draft = self._current_draft()
        if draft is None:
            return
        client = self._client()
        if client is None:
            return
        settings.save_test_emails(raw)
        service = self._service(client)

        def on_test_done(_r) -> None:
            self.test_sent = True
            self._tested_fingerprint = self._draft_fingerprint()
            self._update_checklist()
            QMessageBox.information(
                self, "Test sent",
                f"A test was sent to {len(test_emails)} address(es):\n\n"
                + "\n".join(test_emails)
                + "\n\nCheck it renders correctly before sending to everyone.")

        self._run_async(
            service.send_test,
            draft,
            test_emails,
            on_done=on_test_done,
            busy_msg="Sending test…",
        )

    # ---- async plumbing ----------------------------------------------------

    def _run_async(self, fn, *args, on_done, busy_msg: str) -> None:
        if self._thread is not None:
            QMessageBox.information(self, "Busy", "Please wait for the current task to finish.")
            return
        self._set_buttons_enabled(False)
        self._set_status(busy_msg)

        self._on_done = on_done
        self._thread = QThread()
        self._worker = Worker(fn, *args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # Progress and result slots run on the main thread (queued), because the
        # signals are emitted from the worker thread.
        self._worker.progress.connect(self._set_status, Qt.QueuedConnection)
        self._worker.finished.connect(self._on_worker_finished, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_worker_failed, Qt.QueuedConnection)
        # Ask the thread's own event loop to stop once the work signals fire.
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        # Clean up objects only after the thread has fully stopped. Using
        # deleteLater (never wait()) avoids a thread waiting on itself.
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_worker_finished(self, result) -> None:
        # Runs on the main thread. Stash the result; the callback fires once the
        # thread has actually finished (see _on_thread_finished).
        self._pending_result = result
        self._pending_ok = True

    def _on_worker_failed(self, message: str) -> None:
        self._pending_result = message
        self._pending_ok = False

    def _on_thread_finished(self) -> None:
        # The worker thread has stopped and its objects are scheduled for
        # deletion. Safe to drop references and react on the main thread.
        self._thread = None
        self._worker = None
        self._set_buttons_enabled(True)

        ok = getattr(self, "_pending_ok", None)
        result = getattr(self, "_pending_result", None)
        self._pending_ok = None
        self._pending_result = None

        if ok is True:
            callback = self._on_done
            self._on_done = None
            if callback is not None:
                callback(result)
        elif ok is False:
            QMessageBox.critical(self, "Error", str(result))
            self._set_status("Error: " + str(result))

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._busy = not enabled
        self.test_btn.setEnabled(enabled)
        self.compare_btn.setEnabled(enabled and bool(self.subscribers))
        self._update_gate()  # the send button additionally obeys the checklist

    # ---- settings ----------------------------------------------------------

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _preview_newsletter(self) -> None:
        md_path = self.md_path_edit.property("path")
        if not md_path:
            QMessageBox.information(self, "No newsletter",
                                    "Choose a Markdown file first.")
            return
        try:
            markdown_text = open(md_path, encoding="utf-8").read()
        except OSError as exc:
            QMessageBox.critical(self, "Could not read newsletter", str(exc))
            return
        html = markdown_to_email_html(
            markdown_text,
            title=self.subject_edit.text().strip() or "Newsletter preview",
            pdf_url=self.pdf_edit.text().strip() or None,
            social_links=settings.load_social_links(),
        )
        out = Path(tempfile.gettempdir()) / "eto_newsletter_preview.html"
        try:
            out.write_text(html, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Could not write preview", str(exc))
            return
        webbrowser.open(out.as_uri())
        self._set_status(
            "Preview opened in your browser. The Unsubscribe link is a "
            "placeholder — MailerLite personalises it per recipient."
        )

    def _choose_markdown(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose newsletter", "", "Markdown (*.md *.markdown);;All files (*)"
        )
        if path:
            self.md_path_edit.setText(path)
            self.md_path_edit.setProperty("path", path)
            self._invalidate_test_if_changed()


def _apply_light_theme(app: QApplication) -> None:
    """Force a light look regardless of the Windows dark-mode setting."""
    app.setStyle("Fusion")
    try:  # Qt 6.8+: also steer the platform colour scheme
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#f5f6f8"))
    pal.setColor(QPalette.WindowText, QColor("#222222"))
    pal.setColor(QPalette.Base, QColor("#ffffff"))
    pal.setColor(QPalette.AlternateBase, QColor("#f0f1f3"))
    pal.setColor(QPalette.Text, QColor("#222222"))
    pal.setColor(QPalette.Button, QColor("#e9eaee"))
    pal.setColor(QPalette.ButtonText, QColor("#222222"))
    pal.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipText, QColor("#222222"))
    pal.setColor(QPalette.PlaceholderText, QColor("#909090"))
    pal.setColor(QPalette.Highlight, QColor(NAVY))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.Link, QColor("#185fa5"))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#9a9a9a"))
    app.setPalette(pal)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(settings.APP_NAME)
    _apply_light_theme(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
