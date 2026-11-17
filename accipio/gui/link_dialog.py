"""Settings > Other Accipios: the window where this PC is set up to collect downloads from other Accipios, to send to
one, and where the paired ones are listed and forgotten.

Everything here takes effect at once and the window has only Close: pairing and Forget cannot wait for an OK, and
a pairing code is only of use while the listener really runs. The window talks to a ``handover.Service`` and
saves the three link settings through ``save(accept, addresses, port)`` (the main window's ``apply_link``).
"""
from __future__ import annotations

import ipaddress
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton, QSpinBox,
                             QTableWidget, QTableWidgetItem, QVBoxLayout)
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background

from accipio.core import link
from accipio.core.settings import Settings
from accipio.gui import style

ADDRESS_ROLE = Qt.ItemDataRole.UserRole          # the address (or peer id) a row stands for, whatever the sorting
LISTEN, ADDRESS, NETWORK = range(3)
NAME, WHERE, ROLE = range(3)
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
GONE = "not on this PC now"


def range_name(address: str) -> str:
    """What an address's range says about its network: "" when it says nothing."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return ""
    if ip.is_loopback:
        return "This PC only"
    if ip in (TAILSCALE_V4 if ip.version == 4 else TAILSCALE_V6):
        return "Tailscale"
    if ip.is_private:
        return "Local network"
    return ""


def network_names_of() -> Dict[str, str]:
    """Address -> the name of the adapter that has it ("Wi-Fi", "Tailscale"), from Qt; an address that Qt does not
    know, or when QtNetwork cannot be used, is named by its range (``range_name``)."""
    names: Dict[str, str] = {}
    try:
        from PyQt6.QtNetwork import QNetworkInterface
        for interface in QNetworkInterface.allInterfaces():
            adapter = interface.humanReadableName()
            for entry in interface.addressEntries():
                address = entry.ip().toString().split("%")[0]
                names[address] = range_name(address) if range_name(address) in ("This PC only", "Tailscale") \
                    else (adapter or range_name(address))
    except Exception:
        return {}
    return names


def _minutes(seconds: float) -> str:
    whole = int(seconds + 0.999)
    return f"{whole // 60}:{whole % 60:02d}"


def _table(columns: List[str]) -> QTableWidget:
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(style.ROW_HEIGHT)
    table.setShowGrid(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setSectionsClickable(True)
    header.setStretchLastSection(True)
    table.setSortingEnabled(True)
    return table


class LinkDialog(QDialog):
    def __init__(self, service, settings_of: Callable[[], Settings], save: Callable[[bool, List[str], int], None],
                 parent=None, network_names: Optional[Callable[[], Dict[str, str]]] = None,
                 addresses_of: Callable[[], List[str]] = link.local_addresses):
        super().__init__(parent)
        self.setWindowTitle("Other Accipios")
        self.service = service
        self.node = service.node
        self.settings_of, self.save = settings_of, save
        self.network_names = network_names or network_names_of
        self.addresses_of = addresses_of
        self._filling = False
        self._code_until = 0.0                    # when the shown code runs out, on the node's clock; 0 = none shown
        self._known_peers: List[str] = []
        self.resize(640, 700)
        layout = QVBoxLayout(self)

        self.unavailable_label = QLabel(service.unavailable)
        self.unavailable_label.setWordWrap(True)
        self.unavailable_label.setStyleSheet(f"color: {style.WARNING_COLOR};")
        self.unavailable_label.setVisible(bool(service.unavailable))
        layout.addWidget(self.unavailable_label)

        top = QFormLayout()
        self.name = QLineEdit(self.node.name)
        self.name.setToolTip("The name the other Accipios show for this one. They learn a new name the next time "
                             "they pair with this one.")
        self.name.editingFinished.connect(self._renamed)
        top.addRow("This Accipio's name", self.name)
        layout.addLayout(top)

        layout.addWidget(self._build_collect())
        layout.addWidget(self._build_send())
        layout.addWidget(self._build_paired(), 1)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.tick)
        self.clock.start()
        self.load()
        if service.unavailable:
            for part in (self.name, self.collect_group, self.send_group, self.paired_group):
                part.setEnabled(False)

    # ---- the three groups --------------------------------------------------------------------------------------

    def _build_collect(self) -> QGroupBox:
        group = self.collect_group = QGroupBox("This PC collects")
        column = QVBoxLayout(group)
        self.accept_box = QCheckBox("Accept downloads from the Accipios paired below")
        self.accept_box.setToolTip("Off until you switch it on. While it is on, a paired Accipio can have this PC "
                                   "download links for it, on the ticked addresses only.")
        self.accept_box.toggled.connect(self._changed)
        column.addWidget(self.accept_box)
        column.addWidget(QLabel("Listen on these addresses of this PC (nothing is ticked until you tick it):"))
        self.addresses = _table(["Listen", "Address", "Network"])
        self.addresses.setMinimumHeight(140)             # the header and four rows
        self.addresses.horizontalHeader().resizeSection(LISTEN, 60)
        self.addresses.horizontalHeader().resizeSection(ADDRESS, 220)
        self.addresses.itemChanged.connect(lambda item: self._changed() if item.column() == LISTEN else None)
        column.addWidget(self.addresses)
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port"))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setKeyboardTracking(False)           # a change is saved when typing ends, not at each digit
        self.port.valueChanged.connect(self._changed)
        port_row.addWidget(self.port)
        port_row.addStretch(1)
        column.addLayout(port_row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        column.addWidget(self.status)
        code_row = QHBoxLayout()
        self.show_code = QPushButton("Show a pairing code")
        self.show_code.setToolTip("Shows a code for a few minutes. Type it, with this PC's address, on the Accipio "
                                  "that is to send downloads here. Needs something to be listened on.")
        self.show_code.clicked.connect(self.begin_pairing)
        code_row.addWidget(self.show_code)
        self.code_left = QLabel("")
        code_row.addWidget(self.code_left)
        code_row.addStretch(1)
        column.addLayout(code_row)
        self.code = QLabel("")
        big = QFont(self.font())
        big.setPointSize(big.pointSize() + 8)
        big.setBold(True)
        self.code.setFont(big)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code.setVisible(False)
        column.addWidget(self.code)
        return group

    def _build_send(self) -> QGroupBox:
        group = self.send_group = QGroupBox("This PC sends: pair with a collector")
        form = QFormLayout(group)
        row = QHBoxLayout()
        self.host = QLineEdit()
        self.host.setPlaceholderText("the collector's address")
        self.send_port = QSpinBox()
        self.send_port.setRange(1, 65535)
        self.send_port.setValue(link.DEFAULT_PORT)
        self.send_port.setPrefix("port ")
        row.addWidget(self.host, 1)
        row.addWidget(self.send_port)
        form.addRow("Address", row)
        code_row = QHBoxLayout()
        self.typed_code = QLineEdit()
        self.typed_code.setPlaceholderText("the code the collector shows")
        self.typed_code.returnPressed.connect(self.pair_now)
        self.pair_button = QPushButton("Pair")
        self.pair_button.clicked.connect(self.pair_now)
        code_row.addWidget(self.typed_code, 1)
        code_row.addWidget(self.pair_button)
        form.addRow("Code", code_row)
        return group

    def _build_paired(self) -> QGroupBox:
        group = self.paired_group = QGroupBox("Paired Accipios")
        column = QVBoxLayout(group)
        self.paired = _table(["Name", "Address", "Role"])
        self.paired.setMinimumHeight(90)
        self.paired.horizontalHeader().resizeSection(NAME, 180)
        self.paired.horizontalHeader().resizeSection(WHERE, 180)
        self.paired.itemSelectionChanged.connect(self._selection_changed)
        column.addWidget(self.paired)
        row = QHBoxLayout()
        self.forget_button = QPushButton("Forget")
        self.forget_button.setToolTip("End the pairing with the selected Accipio, from this side")
        self.forget_button.clicked.connect(self.forget_selected)
        row.addWidget(self.forget_button)
        row.addStretch(1)
        column.addLayout(row)
        return group

    # ---- reading the state in -----------------------------------------------------------------------------------

    def load(self) -> None:
        """Fill every part from the live settings and the node (opening, and after a change made elsewhere)."""
        current = self.settings_of()
        self._filling = True
        try:
            self.accept_box.setChecked(current.link_accept)
            self.port.setValue(min(max(current.link_port, self.port.minimum()), self.port.maximum()))
            self._fill_addresses(current.link_addresses)
        finally:
            self._filling = False
        self._fill_paired()
        self._show_status()

    def _fill_addresses(self, ticked: List[str]) -> None:
        here = list(self.addresses_of())
        names = self.network_names()
        rows = here + [a for a in ticked if a not in here]
        table = self.addresses
        table.setSortingEnabled(False)               # the tick is part of the row; sorting while filling would shuffle it
        table.setRowCount(len(rows))
        for position, address in enumerate(rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            tick.setCheckState(Qt.CheckState.Checked if address in ticked else Qt.CheckState.Unchecked)
            tick.setData(ADDRESS_ROLE, address)
            network = names.get(address) or range_name(address) if address in here else GONE
            cells = [tick, QTableWidgetItem(address), QTableWidgetItem(network)]
            for column, item in enumerate(cells):
                if column != LISTEN:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                table.setItem(position, column, item)
            if address not in here:
                cells[NETWORK].setForeground(self.palette().placeholderText())
                cells[ADDRESS].setToolTip("This address is saved, but this PC does not have it right now.")
        table.setSortingEnabled(True)

    def ticked(self) -> List[str]:
        found = [self.addresses.item(r, LISTEN).data(ADDRESS_ROLE) for r in range(self.addresses.rowCount())
                 if self.addresses.item(r, LISTEN).checkState() == Qt.CheckState.Checked]
        order = {a: i for i, a in enumerate(self.settings_of().link_addresses)}
        return sorted(found, key=lambda a: (order.get(a, len(order)), found.index(a)))

    def tick_address(self, address: str, on: bool = True) -> None:
        """Tick (or untick) the row of an address, as a click would; the row is found by its address."""
        for r in range(self.addresses.rowCount()):
            item = self.addresses.item(r, LISTEN)
            if item.data(ADDRESS_ROLE) == address:
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                return
        raise KeyError(address)

    def _fill_paired(self) -> None:
        peers = self.node.peers()
        self._known_peers = [p.id for p in peers]
        table = self.paired
        keep = self.selected_peer()
        table.setSortingEnabled(False)
        table.setRowCount(len(peers))
        for position, peer in enumerate(peers):
            where = f"{peer.host}:{peer.port}" if peer.host else ""
            role = "collects for this PC" if peer.host else "sends to this PC"
            cells = [QTableWidgetItem(peer.name or peer.id), QTableWidgetItem(where), QTableWidgetItem(role)]
            for column, item in enumerate(cells):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(ADDRESS_ROLE, peer.id)
                table.setItem(position, column, item)
        table.setSortingEnabled(True)
        if keep is not None:
            for r in range(table.rowCount()):
                if table.item(r, NAME).data(ADDRESS_ROLE) == keep.id:
                    table.selectRow(r)
        self._selection_changed()

    def selected_peer(self) -> Optional[link.Peer]:
        rows = self.paired.selectionModel().selectedRows() if self.paired.selectionModel() else []
        if not rows:
            return None
        return self.node.peer(self.paired.item(rows[0].row(), NAME).data(ADDRESS_ROLE))

    def _selection_changed(self) -> None:
        self.forget_button.setEnabled(self.selected_peer() is not None)

    # ---- this PC collects -----------------------------------------------------------------------------------------

    def _changed(self, *_) -> None:
        if self._filling:
            return
        self.save(self.accept_box.isChecked(), self.ticked(), self.port.value())
        self._show_status()

    def _show_status(self) -> None:
        text = self.service.status_text()
        self.status.setText(text)
        problems = bool(self.service.problems) or bool(self.service.unavailable)
        self.status.setStyleSheet(f"color: {style.WARNING_COLOR};" if problems else "")
        listening = bool(self.service.listening)
        self.show_code.setEnabled(listening and not self.service.unavailable)
        if not listening and self._code_until:
            self.node.cancel_pairing()
            self._end_code("")

    def _renamed(self) -> None:
        text = " ".join(self.name.text().split())
        if text:
            self.node.rename(text)
        self.name.setText(self.node.name)

    # ---- the pairing code (this PC collects) ------------------------------------------------------------------------

    def begin_pairing(self) -> None:
        try:
            code = self.node.begin_pairing()
        except link.LinkError as error:
            self.result_label.setText(str(error))
            return
        self._code_until = self.node.clock() + self.node.pairing_left()
        self.code.setText(code)
        self.code.setVisible(True)
        self._known_peers = [p.id for p in self.node.peers()]
        self.result_label.setText("")
        self.tick()

    def _end_code(self, message: str) -> None:
        self._code_until = 0.0
        self.code.setVisible(False)
        self.code.setText("")
        self.code_left.setText("")
        if message:
            self.result_label.setText(message)

    def tick(self) -> None:
        """Once a second: the code's countdown, and whether it has been used."""
        if not self._code_until:
            return
        left = self.node.pairing_left()
        if left > 0:
            self.code_left.setText(f"valid for {_minutes(left)}")
            return
        before = set(self._known_peers)
        new = [p for p in self.node.peers() if p.id not in before]
        self._fill_paired()
        if new:
            self._end_code(f"Paired with {new[0].name or new[0].id}.")
        elif self.node.clock() >= self._code_until:
            self._end_code("The pairing code has run out. Show a new one if you still need it.")
        else:
            # used by an Accipio that was paired already (no new name in the list), or tried too often: the node
            # cannot tell which
            self._end_code("The pairing code has been used, or was tried too often, and no longer works. Show a new "
                           "one if you still need it.")

    # ---- this PC sends: pair with a collector -------------------------------------------------------------------------

    def pair_now(self) -> None:
        host, code = self.host.text().strip(), self.typed_code.text()
        if not host or not link.tidy_code(code):
            self._say("Type the collector's address and the code it shows.", True)
            return
        box = QProgressDialog(f"Pairing with {host}…", "Cancel", 0, 0, self)
        box.setWindowTitle("Pair")
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.setMinimumDuration(0)
        box.show()
        self.pair_button.setEnabled(False)
        try:
            peer = call_in_background(link.pair, self.node, host, self.send_port.value(), code,
                                      cancel_signal=box.canceled)
        except BackgroundCancelled:
            self._say("Pairing was cancelled.", True)
            return
        except link.LinkError as error:                  # WrongCode, Away, Refused, LinkUnavailable: each its own sentence
            self._say(str(error), True)
            return
        except Exception as error:
            self._say(f"Pairing failed: {error}", True)
            return
        finally:
            box.close()
            self.pair_button.setEnabled(True)
        self.typed_code.clear()
        self._fill_paired()
        self._say(f"Paired with {peer.name or host}. Pick it for a place under Rules > Places.", False)

    def _say(self, text: str, problem: bool) -> None:
        self.result_label.setText(text)
        self.result_label.setStyleSheet(f"color: {style.WARNING_COLOR};" if problem else "")

    # ---- paired Accipios ----------------------------------------------------------------------------------------------

    def confirm_forget(self, peer: link.Peer) -> bool:
        """The yes/no question before Forget (tests replace it)."""
        answer = QMessageBox.question(
            self, "Forget " + (peer.name or peer.id),
            f"End the pairing with {peer.name or peer.id}?\n\n"
            "Downloads sent there that have not finished can no longer be followed here, and places that use it "
            "must be pointed somewhere else. It can send nothing to this PC any more either. You can pair again "
            "later.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def forget_selected(self) -> None:
        peer = self.selected_peer()
        if peer is None or not self.confirm_forget(peer):
            return
        self.service.forget(peer.id)
        self._fill_paired()
        self._say(f"Forgot {peer.name or peer.id}.", False)

    # ---- closing ---------------------------------------------------------------------------------------------------

    def done(self, result: int) -> None:
        self.clock.stop()
        if self._code_until:
            self.node.cancel_pairing()                    # a code nobody sees any more must not stay good
            self._code_until = 0.0
        super().done(result)
