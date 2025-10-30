"""
live/data_simulator.py

COMPLETELY OPTIONAL file-based data simulation for forward testing.

CRITICAL PRINCIPLES:
- This module is ONLY activated when user explicitly enables file simulation in GUI
- Does NOT interfere with live trading functionality in any way
- Does NOT provide fallback data when live streams fail
- Completely user-driven and user-controlled
- If file simulation is not working, the system fails fast with clear error messages

USAGE:
- User enables "File Simulation" checkbox in GUI
- User selects data file via Browse button  
- System uses ONLY this file data, no other sources
- If file is invalid/missing: clear error, no trading
"""

import pandas as pd
import os
import time
import logging
from datetime import datetime
from typing import Dict, Optional

try:
    from ..utils.time_utils import now_ist
except ImportError:
    from myQuant.utils.time_utils import now_ist

logger = logging.getLogger(__name__)

class DataSimulator:
    """Optional file-based data simulator. Does not affect live trading."""
    
    def __init__(self, file_path: str = None):
        self.file_path = file_path
        self.data = None
        self.index = 0
        # Fixed delay for consistent simulation speed
        self.tick_delay = 0.0005  # 100 tps - good balance of speed and visibility
        self.loaded = False
        self.completed = False  # Flag to prevent repeated completion messages
        
    def load_data(self) -> bool:
        """Load data from file. Returns True if successful."""
        if not self.file_path or not os.path.exists(self.file_path):
            logger.warning(f"Data file not found: {self.file_path}")
            return False
            
        try:
            logger.info(f"Loading simulation data from: {self.file_path}")
            
            # Read CSV file
            self.data = pd.read_csv(self.file_path)
            
            # Standardize columns
            if 'close' in self.data.columns:
                self.data['price'] = self.data['close']
            elif 'Close' in self.data.columns:
                self.data['price'] = self.data['Close']
            elif 'ltp' in self.data.columns:
                self.data['price'] = self.data['ltp']
            elif 'LTP' in self.data.columns:
                self.data['price'] = self.data['LTP']
            
            # Ensure we have a price column
            if 'price' not in self.data.columns:
                # Use first numeric column as price
                numeric_cols = self.data.select_dtypes(include=['number']).columns
                if len(numeric_cols) > 0:
                    self.data['price'] = self.data[numeric_cols[0]]
                else:
                    raise ValueError("No numeric price column found")
            
            # Add default volume if not present
            if 'volume' not in self.data.columns:
                self.data['volume'] = 1000
                
            self.index = 0
            self.loaded = True
            
            # Provide user with time estimates
            total_ticks = len(self.data)
            estimated_time = total_ticks * self.tick_delay
            
            if estimated_time < 60:
                time_str = f"{estimated_time:.0f} seconds"
            elif estimated_time < 3600:
                time_str = f"{estimated_time/60:.1f} minutes"  
            else:
                time_str = f"{estimated_time/3600:.1f} hours"
                
            logger.info(f"📁 Loaded {total_ticks:,} data points for simulation")
            logger.info(f"⏱️  Estimated completion time: ~{time_str}")
                
            return True
            
        except Exception as e:
            logger.error(f"Failed to load simulation data: {e}")
            return False
    
    def get_next_tick(self) -> Optional[Dict]:
        """Get next tick from file data. Returns None if no data or end reached."""
        if not self.loaded or self.data is None:
            return None
            
        # Check if we've reached end
        if self.index >= len(self.data):
            if not self.completed:
                self.completed = True
                logger.info("📋 Simulation completed successfully - all data processed")
            return None  # Signal completion, don't restart
            
        # Progress reporting (every 10% for user feedback, less frequent to avoid GUI overload)
        if self.index % max(1, len(self.data) // 10) == 0:
            progress = (self.index / len(self.data)) * 100
            logger.info(f"📊 Simulation progress: {progress:.0f}% ({self.index}/{len(self.data)})")
            
        # Get current data point
        row = self.data.iloc[self.index]
        self.index += 1
        
        # Extract timestamp from CSV (if available), otherwise use current time
        if 'timestamp' in self.data.columns:
            # CSV has timestamp column - use it (data simulation mode)
            csv_timestamp = pd.to_datetime(row['timestamp'])
            tick_timestamp = csv_timestamp
            logger.debug(f"Using CSV timestamp: {csv_timestamp}")
        elif 'Timestamp' in self.data.columns:
            # Handle capitalized column name
            csv_timestamp = pd.to_datetime(row['Timestamp'])
            tick_timestamp = csv_timestamp
            logger.debug(f"Using CSV timestamp: {csv_timestamp}")
        elif 'datetime' in self.data.columns:
            # Alternative timestamp column name
            csv_timestamp = pd.to_datetime(row['datetime'])
            tick_timestamp = csv_timestamp
            logger.debug(f"Using CSV datetime: {csv_timestamp}")
        else:
            # No timestamp in CSV - fall back to current time
            tick_timestamp = now_ist()
            if self.index == 1:  # Log warning only once
                logger.warning("CSV file has no timestamp column - using current time for trade times")
        
        # Create tick with timestamp from CSV
        tick = {
            "timestamp": tick_timestamp,
            "price": float(row['price']),
            "volume": int(row.get('volume', 1000))
        }
        
        # Apply configurable delay (isolated from live trading)
        if self.tick_delay > 0:
            time.sleep(self.tick_delay)
        
        return tick
    

    
    def get_estimated_completion_time(self) -> str:
        """Estimate remaining time for user planning."""
        if not self.loaded or self.tick_delay == 0:
            return "Unknown"
        
        remaining_ticks = len(self.data) - self.index
        remaining_seconds = remaining_ticks * self.tick_delay
        
        if remaining_seconds < 60:
            return f"{remaining_seconds:.0f} seconds"
        elif remaining_seconds < 3600:
            return f"{remaining_seconds/60:.1f} minutes"
        else:
            return f"{remaining_seconds/3600:.1f} hours"