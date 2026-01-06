"""
Tkinter styles and themes for AFSUAM Measurement System.
"""

from tkinter import ttk
import tkinter as tk


# Theme definitions
THEMES = {
    "light": {
        "bg": "#FFFFFF",
        "fg": "#0f172a",
        "accent": "#1e40af",
        "success": "#16a34a",
        "warning": "#b45309",
        "error": "#dc2626",
        "frame_bg": "#f8fafc",
        "entry_bg": "#FFFFFF",
        "treeview_bg": "#FFFFFF",
        "treeview_selected": "#dbeafe",
        "border": "#e2e8f0"
    },
    "dark": {
        "bg": "#1e1e2e",
        "fg": "#cdd6f4",
        "accent": "#89b4fa",
        "success": "#a6e3a1",
        "warning": "#f9e2af",
        "error": "#f38ba8",
        "frame_bg": "#313244",
        "entry_bg": "#45475a",
        "treeview_bg": "#313244",
        "treeview_selected": "#45475a",
        "border": "#585b70"
    }
}


class ThemeManager:
    """Manages application themes."""
    
    _current_theme = "light"
    _root = None
    _widgets = []
    
    @classmethod
    def init(cls, root):
        """Initialize theme manager with root window."""
        cls._root = root
        cls._widgets = []
    
    @classmethod
    def get_current_theme(cls) -> str:
        """Get current theme name."""
        return cls._current_theme
    
    @classmethod
    def get_colors(cls) -> dict:
        """Get current theme colors."""
        return THEMES.get(cls._current_theme, THEMES["light"])
    
    @classmethod
    def set_theme(cls, theme_name: str):
        """Set and apply theme."""
        if theme_name not in THEMES:
            return
        
        cls._current_theme = theme_name
        colors = THEMES[theme_name]
        
        if cls._root:
            cls._root.configure(bg=colors["bg"])
            setup_styles(cls._root, theme_name)
    
    @classmethod
    def toggle_theme(cls):
        """Toggle between light and dark themes."""
        new_theme = "dark" if cls._current_theme == "light" else "light"
        cls.set_theme(new_theme)
        return new_theme


def setup_styles(root, theme: str = "light"):
    """
    Configure ttk styles for the application.
    
    Args:
        root: Tkinter root window
        theme: Theme name ("light" or "dark")
    """
    colors = THEMES.get(theme, THEMES["light"])
    style = ttk.Style()
    
    # Use clam as base theme
    style.theme_use("clam")
    
    # Base styles
    style.configure(
        ".",
        background=colors["bg"],
        foreground=colors["fg"],
        font=("Arial", 10)
    )
    
    # Frame styles
    style.configure(
        "TFrame",
        background=colors["bg"]
    )
    
    # Label styles
    style.configure(
        "TLabel",
        background=colors["bg"],
        foreground=colors["fg"]
    )
    
    style.configure(
        "Header.TLabel",
        font=("Arial", 12, "bold"),
        foreground=colors["accent"],
        background=colors["bg"]
    )
    
    style.configure(
        "Status.TLabel",
        font=("Arial", 10),
        foreground=colors["fg"],
        background=colors["bg"]
    )
    
    style.configure(
        "Success.TLabel",
        foreground=colors["success"],
        background=colors["bg"]
    )
    
    style.configure(
        "Warning.TLabel",
        foreground=colors["warning"],
        background=colors["bg"]
    )
    
    style.configure(
        "Error.TLabel",
        foreground=colors["error"],
        background=colors["bg"]
    )
    
    # LabelFrame styles
    style.configure(
        "TLabelframe",
        background=colors["bg"],
        bordercolor=colors["border"]
    )
    style.configure(
        "TLabelframe.Label",
        foreground=colors["accent"],
        background=colors["bg"],
        font=("Arial", 11, "bold")
    )
    
    # Button styles
    style.configure(
        "TButton",
        padding=6,
        background=colors["frame_bg"],
        foreground=colors["fg"]
    )
    
    style.configure(
        "Primary.TButton",
        font=("Arial", 10, "bold")
    )
    
    style.map(
        "TButton",
        background=[("active", colors["accent"])],
        foreground=[("active", "#FFFFFF")]
    )
    
    # Entry styles
    style.configure(
        "TEntry",
        fieldbackground=colors["entry_bg"],
        foreground=colors["fg"],
        insertcolor=colors["fg"]
    )
    
    # Combobox styles
    style.configure(
        "TCombobox",
        fieldbackground=colors["entry_bg"],
        background=colors["entry_bg"],
        foreground=colors["fg"]
    )
    
    # Radiobutton and Checkbutton
    style.configure(
        "TRadiobutton",
        background=colors["bg"],
        foreground=colors["fg"]
    )
    
    style.configure(
        "TCheckbutton",
        background=colors["bg"],
        foreground=colors["fg"]
    )
    
    # Treeview styles
    style.configure(
        "Treeview",
        background=colors["treeview_bg"],
        foreground=colors["fg"],
        fieldbackground=colors["treeview_bg"],
        font=("Arial", 10)
    )
    style.configure(
        "Treeview.Heading",
        font=("Arial", 10, "bold"),
        background=colors["frame_bg"],
        foreground=colors["fg"]
    )
    style.map(
        "Treeview",
        background=[("selected", colors["treeview_selected"])],
        foreground=[("selected", colors["fg"])]
    )
    
    # Notebook styles
    style.configure(
        "TNotebook",
        background=colors["bg"]
    )
    style.configure(
        "TNotebook.Tab",
        background=colors["frame_bg"],
        foreground=colors["fg"],
        padding=[10, 4]
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["bg"])],
        foreground=[("selected", colors["accent"])]
    )
    
    # Progressbar
    style.configure(
        "TProgressbar",
        background=colors["accent"],
        troughcolor=colors["frame_bg"]
    )
    
    # Scale
    style.configure(
        "TScale",
        background=colors["bg"],
        troughcolor=colors["frame_bg"]
    )
    
    # Separator
    style.configure(
        "TSeparator",
        background=colors["border"]
    )
    
    # PanedWindow
    style.configure(
        "TPanedwindow",
        background=colors["bg"]
    )
    
    # Configure root window
    root.configure(bg=colors["bg"])
    
    return style


def configure_treeview_tags(tree, theme: str = "light"):
    """
    Configure color tags for treeview.
    
    Args:
        tree: ttk.Treeview widget
        theme: Current theme name
    """
    colors = THEMES.get(theme, THEMES["light"])
    
    # Known tag (configured)
    tree.tag_configure("known", foreground=colors["success"])
    
    # Unknown tag
    tree.tag_configure("unknown", foreground=colors["error"])
    
    # RSSI quality colors
    if theme == "dark":
        tree.tag_configure("rssi_good", background="#1e3a2a", foreground=colors["success"])
        tree.tag_configure("rssi_medium", background="#3a2e1e", foreground=colors["warning"])
        tree.tag_configure("rssi_poor", background="#3a1e1e", foreground=colors["error"])
        tree.tag_configure("timeout", background="#2a2a2a", foreground="#6b7280")
    else:
        tree.tag_configure("rssi_good", background="#dcfce7", foreground="#166534")
        tree.tag_configure("rssi_medium", background="#fef9c3", foreground="#854d0e")
        tree.tag_configure("rssi_poor", background="#fee2e2", foreground="#991b1b")
        tree.tag_configure("timeout", background="#e5e7eb", foreground="#6b7280")


class StatusIndicator(tk.Canvas):
    """LED-style status indicator widget."""
    
    def __init__(self, parent, size=16, **kwargs):
        super().__init__(parent, width=size, height=size, 
                         highlightthickness=0, **kwargs)
        
        self.size = size
        self._state = "off"
        
        colors = ThemeManager.get_colors()
        self.configure(bg=colors["bg"])
        
        self._draw()
    
    def _draw(self):
        """Draw the indicator."""
        self.delete("all")
        
        colors = {
            "off": "#6b7280",
            "connected": "#22c55e",
            "connecting": "#f59e0b",
            "error": "#ef4444"
        }
        
        color = colors.get(self._state, colors["off"])
        
        # Draw LED circle
        pad = 2
        self.create_oval(
            pad, pad, 
            self.size - pad, self.size - pad,
            fill=color, outline=""
        )
        
        # Add highlight for 3D effect
        if self._state != "off":
            self.create_arc(
                pad + 1, pad + 1,
                self.size - pad - 1, self.size - pad - 1,
                start=45, extent=90,
                style=tk.ARC, outline="white", width=1
            )
    
    def set_state(self, state: str):
        """Set indicator state: off, connected, connecting, error."""
        self._state = state
        self._draw()
