"""
Tag Configuration Manager for AFSUAM Measurement System.

This module handles loading, saving, and managing tag configurations
from JSON files.
"""

import json
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class Tag:
    """Represents a single RFID tag with its configuration."""
    suffix: str
    label: str
    location: str = ""
    
    def matches_epc(self, epc: str) -> bool:
        """Check if EPC ends with this tag's suffix."""
        return epc.upper().endswith(self.suffix.upper())


@dataclass  
class TagManager:
    """
    Manages tag configuration loading and tag matching.
    
    This class provides:
    - Loading/saving tag configs from JSON
    - Matching EPCs to configured tags
    - Access to tag suffixes, labels, and locations
    """
    
    config_file: str = "tag_config.json"
    tags: List[Tag] = field(default_factory=list)
    antenna_settings: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        """Load configuration after initialization."""
        self.load()
    
    @property
    def suffixes(self) -> List[str]:
        """Get list of all tag suffixes."""
        return [t.suffix for t in self.tags]
    
    @property
    def labels(self) -> List[str]:
        """Get list of all tag labels."""
        return [t.label for t in self.tags]
    
    @property
    def locations(self) -> List[str]:
        """Get list of all tag locations."""
        return [t.location for t in self.tags]
    
    @property 
    def count(self) -> int:
        """Get number of configured tags."""
        return len(self.tags)
    
    def load(self) -> bool:
        """
        Load tag configuration from JSON file.
        
        Returns:
            True if loaded successfully
        """
        if not os.path.exists(self.config_file):
            print(f"Tag config file not found: {self.config_file}")
            return False
        
        try:
            with open(self.config_file, "r") as f:
                data = json.load(f)
            
            # Parse tags
            tags_data = data.get("tags", [])
            self.tags = []
            for t in tags_data:
                tag = Tag(
                    suffix=t.get("suffix", "").strip().upper(),
                    label=t.get("label", "").strip(),
                    location=t.get("location", "").strip()
                )
                if tag.suffix:
                    self.tags.append(tag)
            
            # Parse antenna settings
            self.antenna_settings = data.get("antenna_settings", {})
            
            print(f"Loaded {len(self.tags)} tags from {self.config_file}")
            return True
            
        except Exception as e:
            print(f"Error loading tag config: {e}")
            return False
    
    def save(self) -> bool:
        """
        Save current tag configuration to JSON file.
        
        Returns:
            True if saved successfully
        """
        try:
            data = {
                "tags": [
                    {
                        "suffix": t.suffix,
                        "label": t.label,
                        "location": t.location
                    }
                    for t in self.tags
                ],
                "antenna_settings": self.antenna_settings
            }
            
            with open(self.config_file, "w") as f:
                json.dump(data, f, indent=4)
            
            print(f"Saved {len(self.tags)} tags to {self.config_file}")
            return True
            
        except Exception as e:
            print(f"Error saving tag config: {e}")
            return False
    
    def find_tag_by_suffix(self, suffix: str) -> Optional[Tag]:
        """Find tag by suffix (case-insensitive)."""
        suffix_upper = suffix.upper()
        for tag in self.tags:
            if tag.suffix == suffix_upper:
                return tag
        return None
    
    def find_tag_by_epc(self, epc: str) -> Optional[Tag]:
        """Find tag that matches given EPC."""
        for tag in self.tags:
            if tag.matches_epc(epc):
                return tag
        return None
    
    def get_label_for_suffix(self, suffix: str) -> str:
        """Get label for tag suffix."""
        tag = self.find_tag_by_suffix(suffix)
        return tag.label if tag else ""
    
    def get_location_for_suffix(self, suffix: str) -> str:
        """Get location for tag suffix."""
        tag = self.find_tag_by_suffix(suffix)
        return tag.location if tag else ""
    
    def get_tag_info(self, epc: str) -> Tuple[str, str, str]:
        """
        Get tag info for EPC.
        
        Args:
            epc: Tag EPC
        
        Returns:
            Tuple of (suffix, label, location)
        """
        tag = self.find_tag_by_epc(epc)
        if tag:
            return (tag.suffix, tag.label, tag.location)
        
        # Not a configured tag, extract suffix from EPC
        suffix = epc[-4:] if len(epc) >= 4 else epc
        return (suffix, "", "")
    
    def is_known_tag(self, epc: str) -> bool:
        """Check if EPC matches a configured tag."""
        return self.find_tag_by_epc(epc) is not None
    
    def add_tag(self, suffix: str, label: str, location: str = "") -> bool:
        """
        Add a new tag to configuration.
        
        Returns:
            True if added (False if suffix already exists)
        """
        if self.find_tag_by_suffix(suffix):
            return False
        
        self.tags.append(Tag(
            suffix=suffix.upper().strip(),
            label=label.strip(),
            location=location.strip()
        ))
        return True
    
    def remove_tag(self, suffix: str) -> bool:
        """
        Remove tag by suffix.
        
        Returns:
            True if removed
        """
        tag = self.find_tag_by_suffix(suffix)
        if tag:
            self.tags.remove(tag)
            return True
        return False
    
    def get_missed_tags(self, seen_suffixes: set) -> List[Tag]:
        """Get list of tags that were not seen."""
        return [t for t in self.tags if t.suffix not in seen_suffixes]
