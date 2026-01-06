"""
Live Monitor Tab.

Displays real-time tag data from RFID reader.
"""

import tkinter as tk
from tkinter import ttk
import time
from typing import Optional


class LiveMonitorTab(ttk.Frame):
    """
    Live monitor tab showing real-time tag data.
    
    Features:
    - Dual antenna views
    - Statistics panels
    - All discovered tags table
    """
    
    def __init__(self, parent, reader, tag_manager, **kwargs):
        """
        Initialize live monitor tab.
        
        Args:
            parent: Parent widget
            reader: RFIDReader instance
            tag_manager: TagManager instance
        """
        super().__init__(parent, padding=10, **kwargs)
        
        self.reader = reader
        self.tag_manager = tag_manager
        self._current_antennas = [1, 2]
        
        self._build_ui()
    
    def set_current_antennas(self, antennas: list):
        """Update current antenna list."""
        self._current_antennas = antennas
    
    def _build_ui(self):
        """Build UI components."""
        # Antenna views container
        antenna_container = ttk.Frame(self)
        antenna_container.pack(fill=tk.BOTH, expand=True)
        
        # Antenna 1 panel
        self._build_antenna_panel(
            antenna_container,
            "📡 Antenna 1 - Phased Array",
            "ant1"
        )
        
        # Antenna 2 panel
        self._build_antenna_panel(
            antenna_container,
            "📡 Antenna 2 - Reference",
            "ant2"
        )
        
        # Statistics
        self._build_stats_panel()
        
        # Target tags combined view
        self._build_targets_panel()
        
        # All discovered tags
        self._build_all_tags_panel()
    
    def _build_antenna_panel(self, parent, title: str, prefix: str):
        """Build single antenna panel."""
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        cols = ("Tag", "Location", "Suffix", "Reads", "RSSI", "Phase")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=70, anchor=tk.CENTER)
        tree.column("Tag", width=50)
        tree.column("Location", width=100, anchor=tk.W)
        
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        setattr(self, f"tree_{prefix}", tree)
    
    def _build_stats_panel(self):
        """Build statistics panel."""
        stats_container = ttk.Frame(self)
        stats_container.pack(fill=tk.X, pady=5)
        
        # Ant1 stats
        stats1_fr = ttk.LabelFrame(stats_container, text="📊 Ant1 Stats", padding=5)
        stats1_fr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.lbl_ant1_stats = ttk.Label(
            stats1_fr,
            text="Tags: 0/8 | RSSI: -/-/- | Reads: 0",
            font=("Courier New", 9)
        )
        self.lbl_ant1_stats.pack(anchor=tk.W)
        
        # Ant2 stats
        stats2_fr = ttk.LabelFrame(stats_container, text="📊 Ant2 Stats", padding=5)
        stats2_fr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.lbl_ant2_stats = ttk.Label(
            stats2_fr,
            text="Tags: 0/8 | RSSI: -/-/- | Reads: 0",
            font=("Courier New", 9)
        )
        self.lbl_ant2_stats.pack(anchor=tk.W)
    
    def _build_targets_panel(self):
        """Build target tags combined view."""
        frame = ttk.LabelFrame(self, text="Target Tags (Combined)", padding=10)
        frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        cols = ("Tag", "Location", "Suffix", "Reads", "RSSI", "Phase", "Doppler", "Ant")
        self.tree_targets = ttk.Treeview(frame, columns=cols, show="headings", height=6)
        
        for c in cols:
            self.tree_targets.heading(c, text=c)
            self.tree_targets.column(c, width=80, anchor=tk.CENTER)
        self.tree_targets.column("Tag", width=50)
        self.tree_targets.column("Location", width=120, anchor=tk.W)
        
        self.tree_targets.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_targets.yview)
        self.tree_targets.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _build_all_tags_panel(self):
        """Build all discovered tags panel."""
        frame = ttk.LabelFrame(self, text="All Discovered Tags (last 5s)", padding=10)
        frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        cols = ("Suffix", "Type", "EPC", "RSSI", "Phase", "Count", "Ant", "LastSeen")
        self.tree_all = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        
        for c in cols:
            self.tree_all.heading(c, text=c)
            self.tree_all.column(c, width=70, anchor=tk.CENTER)
        self.tree_all.column("EPC", width=200)
        
        # Color tags
        self.tree_all.tag_configure("known", foreground="#16a34a")
        self.tree_all.tag_configure("unknown", foreground="#dc2626")
        
        self.tree_all.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_all.yview)
        self.tree_all.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
    
    def update(self):
        """Update all displays with current data."""
        if not self.reader or not self.reader.connected:
            return
        
        inventory = self.reader.get_all_data()
        now = time.time()
        
        # Split by antenna
        inv1, inv2 = self._split_by_antenna(inventory)
        
        # Update antenna views
        self._update_antenna_tree(self.tree_ant1, inv1)
        self._update_antenna_tree(self.tree_ant2, inv2)
        
        # Update stats
        self._update_stats(inv1, inv2)
        
        # Update combined targets
        self._update_targets(inventory)
        
        # Update all tags
        self._update_all_tags(inventory, now)
    
    def _split_by_antenna(self, inventory: dict) -> tuple:
        """Split inventory by antenna ID."""
        inv1, inv2 = {}, {}
        for epc, info in inventory.items():
            ant = info.get("antenna", 1)
            if ant == 2:
                inv2[epc] = info
            else:
                inv1[epc] = info
        return inv1, inv2
    
    def _update_antenna_tree(self, tree, inventory: dict):
        """Update antenna treeview."""
        tree.delete(*tree.get_children())
        
        for tag in self.tag_manager.tags:
            info = None
            for epc, data in inventory.items():
                if epc.endswith(tag.suffix):
                    info = data
                    break
            
            if info is None:
                tree.insert("", tk.END, values=(
                    tag.label, tag.location, tag.suffix,
                    0, "-", "-"
                ))
            else:
                tree.insert("", tk.END, values=(
                    tag.label, tag.location, tag.suffix,
                    info.get("count", 0),
                    f"{info.get('rssi', -99):.1f}",
                    f"{info.get('phase', 0):.0f}"
                ))
    
    def _update_stats(self, inv1: dict, inv2: dict):
        """Update statistics labels."""
        def calc_stats(inv):
            rssi_vals = []
            total_reads = 0
            tags_seen = 0
            
            for epc, info in inv.items():
                suffix = epc[-4:] if len(epc) >= 4 else ""
                if suffix in self.tag_manager.suffixes:
                    rssi_vals.append(info.get("rssi", -99))
                    total_reads += info.get("count", 0)
                    tags_seen += 1
            
            if rssi_vals:
                return (
                    tags_seen,
                    min(rssi_vals),
                    max(rssi_vals),
                    sum(rssi_vals) / len(rssi_vals),
                    total_reads
                )
            return (0, 0, 0, 0, 0)
        
        total = self.tag_manager.count
        
        s1 = calc_stats(inv1)
        if 1 in self._current_antennas:
            self.lbl_ant1_stats.config(
                text=f"Tags: {s1[0]}/{total} | RSSI: {s1[1]:.0f}/{s1[2]:.0f}/{s1[3]:.0f} | Reads: {s1[4]}"
            )
        else:
            self.lbl_ant1_stats.config(text="DISABLED")
        
        s2 = calc_stats(inv2)
        if 2 in self._current_antennas:
            self.lbl_ant2_stats.config(
                text=f"Tags: {s2[0]}/{total} | RSSI: {s2[1]:.0f}/{s2[2]:.0f}/{s2[3]:.0f} | Reads: {s2[4]}"
            )
        else:
            self.lbl_ant2_stats.config(text="DISABLED")
    
    def _update_targets(self, inventory: dict):
        """Update combined targets view."""
        self.tree_targets.delete(*self.tree_targets.get_children())
        
        for tag in self.tag_manager.tags:
            info = None
            for epc, data in inventory.items():
                if epc.endswith(tag.suffix):
                    info = data
                    break
            
            if info is None:
                self.tree_targets.insert("", tk.END, values=(
                    tag.label, tag.location, tag.suffix,
                    0, "-99.0", "0", "0.0", "-"
                ))
            else:
                self.tree_targets.insert("", tk.END, values=(
                    tag.label, tag.location, tag.suffix,
                    info.get("count", 0),
                    f"{info.get('rssi', -99):.1f}",
                    f"{info.get('phase', 0):.0f}",
                    f"{info.get('doppler', 0):.1f}",
                    info.get("antenna", 1)
                ))
    
    def _update_all_tags(self, inventory: dict, now: float):
        """Update all discovered tags view."""
        self.tree_all.delete(*self.tree_all.get_children())
        
        # Sort by RSSI
        items = sorted(
            inventory.items(),
            key=lambda x: x[1].get("rssi", -99),
            reverse=True
        )
        
        for epc, data in items:
            age = now - data.get("seen_time", now)
            if age <= 5.0:
                suffix = epc[-4:] if len(epc) >= 4 else epc
                is_known = suffix in self.tag_manager.suffixes
                
                self.tree_all.insert(
                    "", tk.END,
                    values=(
                        suffix,
                        "KNOWN" if is_known else "UNKNOWN",
                        epc,
                        f"{data.get('rssi', -99):.1f}",
                        f"{data.get('phase', 0):.0f}",
                        data.get("count", 0),
                        data.get("antenna", 1),
                        data.get("timestamp", "")
                    ),
                    tags=("known" if is_known else "unknown",)
                )
