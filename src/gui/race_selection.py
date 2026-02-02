from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QTreeWidget, QTreeWidgetItem, QMessageBox,
    QProgressDialog, QFrame, QInputDialog
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPixmap
import sys
import os
import subprocess
import tempfile
import uuid
from src.f1_data import get_race_weekends_by_year, load_session


# Worker thread to fetch schedule without blocking UI
class FetchScheduleWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, year, parent=None):
        super().__init__(parent)
        self.year = year

    def run(self):
        try:
            # enable cache if available in project
            try:
                from src.f1_data import enable_cache
                enable_cache()
            except Exception:
                pass
            events = get_race_weekends_by_year(self.year)
            self.result.emit(events)
        except Exception as e:
            self.error.emit(str(e))

class RaceSelectionWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.loading_session = False
        
        # New helper variables for process management
        self._play_proc = None      # Stores the subprocess.Popen object of the viewer
        self._monitor_timer = None  # QTimer to check when viewer closes
        self._ready_timer = None    # QTimer to check for ready-file
        self._session_worker = None # Worker thread reference to prevent GC

        self.setWindowTitle("F1 Race Analysis")
        self.resize(1100, 750)
        self.setMinimumSize(900, 650)
        
        # Apply the requested Dark F1 Theme
        self._apply_theme()
        
        self._setup_ui()
        self.setWindowState(self.windowState())

    def _apply_theme(self):
        """Applies a dark F1-inspired theme using Qt StyleSheets."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                color: #e0e0e0;
                font-size: 14px;
                background-color: transparent;
            }
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 5px;
                color: #ffffff;
                font-size: 14px;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox::down-arrow {
                image: url(resources/down_arrow.png);
                width: 14px;
                height: 14px;
            }
            QTreeWidget {
                background-color: #252525;
                border: 1px solid #3d3d3d;
                color: #e0e0e0;
                alternate-background-color: #2a2a2a;
                font-size: 14px;
            }
            QTreeWidget::item {
                padding: 4px;
            }
            QTreeWidget::item:selected {
                background-color: #cc0000; /* F1 Red */
                color: white;
            }
            QHeaderView::section {
                background-color: #2d2d2d;
                color: #aaaaaa;
                padding: 5px;
                border: none;
                font-weight: bold;
            }
            QPushButton {
                background-color: #333333;
                border: 1px solid #444444;
                border-radius: 4px;
                padding: 8px 16px;
                color: white;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #444444;
            }
            QPushButton:pressed {
                background-color: #cc0000;
                border-color: #cc0000;
            }
            /* Nav Bar styling */
            QFrame#NavBar {
                background-color: #151515;
                border-bottom: 3px solid #cc0000;
            }
            QLabel#AppTitle {
                color: white;
                font-weight: bold;
                font-size: 20px;
            }
            /* Session Panel styling */
            QFrame#SessionPanel {
                background-color: #252525;
                border: 1px solid #333333;
                border-radius: 8px;
            }
            QLabel#SessionHeader {
                color: #cc0000;
                font-weight: bold;
                font-size: 18px;
            }
        """)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Use a tight layout for the whole window
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        central_widget.setLayout(main_layout)

        # === 1. Persistent Top Navigation Bar ===
        nav_bar = QFrame()
        nav_bar.setObjectName("NavBar")
        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(20, 15, 20, 15)
        nav_bar.setLayout(nav_layout)
        
        # Title with F1 branding
        app_title_layout = QHBoxLayout()
        app_title_layout.setSpacing(8)
        
        f1_label = QLabel("F1")
        f1_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #cc0000;")
            
        app_title = QLabel("Race Analysis")
        app_title.setObjectName("AppTitle")
        
        app_title_layout.addWidget(f1_label)
        app_title_layout.addWidget(app_title)
        
        nav_layout.addLayout(app_title_layout)
        nav_layout.addStretch()
        
        main_layout.addWidget(nav_bar)

        # === 2. Main Content Container ===
        content_container = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(20)
        content_container.setLayout(content_layout)
        
        # Year Selection Row
        year_layout = QHBoxLayout()
        year_label = QLabel("Select Season:")
        year_label.setStyleSheet("font-weight: bold; color: #aaaaaa;")
        
        self.year_combo = QComboBox()
        self.year_combo.setFixedWidth(120)
        current_year = 2025  # Update as needed
        for year in range(2010, current_year + 1):
            self.year_combo.addItem(str(year))
        self.year_combo.setCurrentText(str(current_year))
        self.year_combo.currentTextChanged.connect(self.load_schedule)
        
        year_layout.addWidget(year_label)
        year_layout.addWidget(self.year_combo)
        year_layout.addStretch()
        content_layout.addLayout(year_layout)

        # Split View: Schedule (Left) and Session Panel (Right)
        split_layout = QHBoxLayout()
        
        # Left: Schedule Tree
        self.schedule_tree = QTreeWidget()
        self.schedule_tree.setHeaderLabels(["Round", "Grand Prix", "Country", "Start Date"])
        self.schedule_tree.setRootIsDecorated(False)
        self.schedule_tree.setAlternatingRowColors(True)
        # Weight 3 means it takes up ~75% of width
        split_layout.addWidget(self.schedule_tree, 3) 
        
        # Right: Session Panel
        # We wrap it in a QFrame for styling (dark background, border)
        self.session_panel = QFrame()
        self.session_panel.setObjectName("SessionPanel")
        self.session_panel.setHidden(True) # Hidden until a race is selected
        
        panel_layout = QVBoxLayout()
        panel_layout.setContentsMargins(20, 20, 20, 20)
        self.session_panel.setLayout(panel_layout)
        
        header_lbl = QLabel("Available Sessions")
        header_lbl.setObjectName("SessionHeader")
        header_lbl.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(header_lbl)
        
        # Spacer
        panel_layout.addSpacing(10)

        # Container for dynamic buttons
        self.session_list_layout = QVBoxLayout()
        self.session_list_layout.setAlignment(Qt.AlignTop)
        self.session_list_layout.setSpacing(10)
        
        panel_layout.addLayout(self.session_list_layout)
        panel_layout.addStretch() # Push buttons to top
        
        # Weight 1 means it takes up ~25% of width
        split_layout.addWidget(self.session_panel, 1)

        content_layout.addLayout(split_layout)
        
        # Add content container to main layout
        main_layout.addWidget(content_container)
        
        # === 3. Footer with branding ===
        footer = QFrame()
        footer.setObjectName("Footer")
        footer.setStyleSheet("""
            #Footer {
                background-color: #1a1a1a;
                border-top: 1px solid #333333;
            }
        """)
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(20, 10, 20, 10)
        footer.setLayout(footer_layout)
        
        footer_text = QLabel("F1 Race Analysis - Telemetry Visualization Tool")
        footer_text.setStyleSheet("color: #666666; font-size: 12px;")
        footer_layout.addWidget(footer_text)
        footer_layout.addStretch()
        
        version_text = QLabel("v1.0.0")
        version_text.setStyleSheet("color: #666666; font-size: 12px;")
        footer_layout.addWidget(version_text)
        
        main_layout.addWidget(footer)

        # connect click handler
        self.schedule_tree.itemClicked.connect(self.on_race_clicked)

        # Load initial schedule
        self.load_schedule(str(current_year))
        
    def load_schedule(self, year):
        if self.loading_session:
            return
        self.loading_session = True
        self.schedule_tree.clear()
        # hide sessions panel while loading / when nothing selected
        try:
            self.session_panel.hide()
        except Exception:
            pass
        self.worker = FetchScheduleWorker(int(year))
        self.worker.result.connect(self.populate_schedule)
        self.worker.error.connect(self.show_error)
        self.worker.start()

    def populate_schedule(self, events):
        for event in events:
            # Ensure all columns are strings (QTreeWidgetItem expects text)
            round_str = str(event.get("round_number", ""))
            name = str(event.get("event_name", ""))
            country = str(event.get("country", ""))
            date = str(event.get("date", ""))

            event_item = QTreeWidgetItem([round_str, name, country, date])
            event_item.setData(0, Qt.UserRole, event)
            self.schedule_tree.addTopLevelItem(event_item)

        # Make sure the round column is wide enough to be visible
        try:
            self.schedule_tree.resizeColumnToContents(0)
            self.schedule_tree.resizeColumnToContents(1)
        except Exception:
            pass

        self.loading_session = False

    def on_race_clicked(self, item, column):
        ev = item.data(0, Qt.UserRole)
        # ensure the sessions panel is visible when a race is selected
        try:
            self.session_panel.show()
        except Exception:
            pass
        # determine sessions to show
        ev_type = (ev.get("type") or "").lower()
        sessions = ["Qualifying", "Race"]
        if "sprint" in ev_type:
            sessions.insert(0, "Sprint Qualifying")
            # show sprint-related session
            sessions.insert(2, "Sprint")

        # clear existing session widgets
        for i in reversed(range(self.session_list_layout.count())):
            w = self.session_list_layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        # add buttons for each session (launch playback in separate process)
        for s in sessions:
            btn = QPushButton(s)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _, sname=s, e=ev: self._on_session_button_clicked(e, sname)
            )
            self.session_list_layout.addWidget(btn)

    def _on_session_button_clicked(self, ev, session_label):
        """Launch main.py in a separate process to run the selected session.
        
        Now includes checks for existing instances and hides the GUI while playing.
        """
        # --- Check for existing instance ---
        if self._play_proc is not None:
            # Check if it's still running
            if self._play_proc.poll() is None:
                QMessageBox.warning(self, "Replay Running", "A replay is already in progress.\nPlease close it before starting a new one.")
                return
            else:
                # Process ended but wasn't cleared for some reason
                self._play_proc = None

        try:
            year = int(self.year_combo.currentText())
        except Exception:
            year = None

        try:
            round_no = int(ev.get("round_number"))
        except Exception:
            round_no = None

        # map button labels to CLI flags
        flag = None
        if session_label == "Qualifying":
            flag = "--qualifying"
        elif session_label == "Sprint Qualifying":
            flag = "--sprint-qualifying"
        elif session_label == "Sprint":
            flag = "--sprint"
        elif session_label == "Race":
            # Race is default, check explicit flag if needed or just omit
            pass

        main_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "main.py")
        )
        cmd = [sys.executable, main_path, "--viewer"]
        if year is not None:
            cmd += ["--year", str(year)]
        if round_no is not None:
            cmd += ["--round", str(round_no)]
        if flag:
            cmd.append(flag)

        # Show a modal loading dialog and load the session in a background thread.
        dlg = QProgressDialog("Loading session data...", None, 0, 0, self)
        dlg.setWindowTitle("Loading")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setRange(0, 0)
        dlg.setStyleSheet("background-color: #2d2d2d; color: white;")
        dlg.show()
        QApplication.processEvents()

        # Map label -> fastf1 session type code
        session_code = 'R'
        if session_label == "Qualifying":
            session_code = 'Q'
        elif session_label == "Sprint Qualifying":
            session_code = 'SQ'
        elif session_label == "Sprint":
            session_code = 'S'

        class FetchSessionWorker(QThread):
            result = Signal(object)
            error = Signal(str)

            def __init__(self, year, round_no, session_type, parent=None):
                super().__init__(parent)
                self.year = year
                self.round_no = round_no
                self.session_type = session_type

            def run(self):
                try:
                    try:
                        from src.f1_data import enable_cache
                        enable_cache()
                    except Exception:
                        pass
                    sess = load_session(self.year, self.round_no, self.session_type)
                    self.result.emit(sess)
                except Exception as e:
                    self.error.emit(str(e))

        def _on_loaded(session_obj):
            # create a unique ready-file path and pass it to the child
            ready_path = os.path.join(tempfile.gettempdir(), f"f1_ready_{uuid.uuid4().hex}")
            cmd_with_ready = list(cmd) + ["--ready-file", ready_path]

            try:
                # Store process in self._play_proc
                self._play_proc = subprocess.Popen(cmd_with_ready)
            except Exception as exc:
                try:
                    dlg.close()
                except Exception:
                    pass
                QMessageBox.critical(self, "Playback error", f"Failed to start playback:\n{exc}")
                self._play_proc = None
                return

            # Poll for ready file to know when window is up
            timer = QTimer(self)

            def _check_ready():
                try:
                    if os.path.exists(ready_path):
                        # Viewer is ready!
                        try:
                            dlg.close()
                        except Exception:
                            pass
                        timer.stop()
                        try:
                            os.remove(ready_path)
                        except Exception:
                            pass
                            
                        # === HIDE GUI Logic ===
                        self.hide()
                        
                        # Start monitoring for process exit to bring GUI back
                        self._start_replay_monitor()
                        return

                    # Check if process died early
                    if self._play_proc.poll() is not None:
                        try:
                            dlg.close()
                        except Exception:
                            pass
                        timer.stop()
                        self._play_proc = None
                        QMessageBox.critical(self, "Playback error", "Playback process exited before signaling readiness")
                except Exception:
                    pass

            timer.timeout.connect(_check_ready)
            timer.start(200)
            self._ready_timer = timer

        def _on_error(msg):
            try:
                dlg.close()
            except Exception:
                pass
            QMessageBox.critical(self, "Load error", f"Failed to load session data:\n{msg}")

        worker = FetchSessionWorker(year, round_no, session_code)
        worker.result.connect(_on_loaded)
        worker.error.connect(_on_error)
        self._session_worker = worker
        worker.start()

    def _start_replay_monitor(self):
        """Starts a timer to poll the running replay process.
        When it exits, show the GUI again.
        """
        self._monitor_timer = QTimer(self)
        self._monitor_timer.setInterval(500) # Check every 500ms
        self._monitor_timer.timeout.connect(self._check_replay_exit)
        self._monitor_timer.start()

    def _check_replay_exit(self):
        """Called periodically to check if the viewer process has closed."""
        if self._play_proc is None:
            self._monitor_timer.stop()
            self.show()
            return
            
        # If poll() is not None, the process finished
        if self._play_proc.poll() is not None:
            self._monitor_timer.stop()
            self._play_proc = None
            
            # Restore GUI
            self.show()
            if self.isMinimized():
                self.showNormal()
            self.raise_()
            self.activateWindow()

    def show_error(self, message):
        QMessageBox.critical(self, "Error", f"Failed to load schedule: {message}")
        self.loading_session = False
