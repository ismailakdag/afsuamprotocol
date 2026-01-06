"""
Tkinter styles for AFSUAM Measurement System.
"""

from tkinter import ttk


def setup_styles(root):
    """
    Configure ttk styles for the application.
    
    Args:
        root: Tkinter root window
    """
    style = ttk.Style()
    style.theme_use("clam")
    
    # Base styles
    style.configure(
        ".",
        background="#FFFFFF",
        foreground="#0f172a",
        font=("Arial", 10)
    )
    
    # Label styles
    style.configure(
        "TLabel",
        background="#FFFFFF",
        foreground="#0f172a"
    )
    
    style.configure(
        "Header.TLabel",
        font=("Arial", 12, "bold"),
        foreground="#1e40af"
    )
    
    style.configure(
        "Status.TLabel",
        font=("Arial", 10),
        foreground="#6b7280"
    )
    
    # Frame styles
    style.configure(
        "TFrame",
        background="#FFFFFF"
    )
    
    # LabelFrame styles
    style.configure(
        "TLabelframe",
        background="#FFFFFF"
    )
    style.configure(
        "TLabelframe.Label",
        foreground="#1e40af",
        font=("Arial", 11, "bold")
    )
    
    # Button styles
    style.configure(
        "TButton",
        padding=6
    )
    
    style.configure(
        "Primary.TButton",
        font=("Arial", 10, "bold")
    )
    
    # Treeview styles
    style.configure(
        "Treeview",
        background="#FFFFFF",
        foreground="#0f172a",
        fieldbackground="#FFFFFF",
        font=("Arial", 10)
    )
    style.configure(
        "Treeview.Heading",
        font=("Arial", 10, "bold"),
        background="#f1f5f9"
    )
    
    # Configure root window
    root.configure(bg="#FFFFFF")
    
    return style


def configure_treeview_tags(tree):
    """
    Configure color tags for treeview.
    
    Args:
        tree: ttk.Treeview widget
    """
    # Known tag (configured)
    tree.tag_configure("known", foreground="#16a34a")
    
    # Unknown tag
    tree.tag_configure("unknown", foreground="#dc2626")
    
    # RSSI quality colors
    tree.tag_configure("rssi_good", background="#dcfce7", foreground="#166534")
    tree.tag_configure("rssi_medium", background="#fef9c3", foreground="#854d0e")
    tree.tag_configure("rssi_poor", background="#fee2e2", foreground="#991b1b")
    
    # Timeout/stale
    tree.tag_configure("timeout", background="#e5e7eb", foreground="#6b7280")
