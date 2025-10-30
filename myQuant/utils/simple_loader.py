import pandas as pd
import numpy as np
import os
import pytz
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Import timezone from SSOT
from ..config.defaults import DEFAULT_CONFIG
IST = pytz.timezone(DEFAULT_CONFIG['session']['timezone'])

def load_data_simple(file_path, process_as_ticks=True):
    """
    Simple data loader that preserves tick-by-tick processing by default.
    """
    logger.info(f"Loading data from: {file_path}")
    
    # Determine file type
    _, ext = os.path.splitext(file_path)
    is_log = ext.lower() == '.log'
    
    # Try to load the file
    try:
        if is_log:
            # LOG files are always tick data with no header
            df = pd.read_csv(file_path, 
                            names=['timestamp', 'price', 'volume'], 
                            parse_dates=['timestamp'],
                            header=None)
            data_type = "tick"
        else:
            # Try with header first
            try:
                df = pd.read_csv(file_path, parse_dates=['timestamp'])
                
                # Normalize column names to lowercase
                df.columns = [col.strip().lower() for col in df.columns]
                
                # Check if this is tick data or OHLCV
                if {'timestamp', 'price', 'volume'}.issubset(set(df.columns)):
                    data_type = "tick"
                elif {'timestamp', 'open', 'high', 'low', 'close', 'volume'}.issubset(set(df.columns)):
                    data_type = "ohlcv"
                else:
                    raise ValueError(f"Unrecognized columns: {list(df.columns)}")
                    
            except Exception:
                # Try again assuming no header, tick data
                df = pd.read_csv(file_path, 
                                names=['timestamp', 'price', 'volume'], 
                                parse_dates=['timestamp'],
                                header=None)
                data_type = "tick"
        
        # Set timestamp as index and make timezone-aware
        if 'timestamp' in df.columns:
            df = df.set_index('timestamp')
        
        df.index = pd.DatetimeIndex([
            pd.Timestamp(ts).tz_localize(IST) if pd.Timestamp(ts).tz is None 
            else pd.Timestamp(ts).tz_convert(IST) 
            for ts in df.index
        ])
        
        # Process based on preference
        if data_type == "tick" and not process_as_ticks:
            # Convert to OHLCV bars only if specifically requested
            logger.info("Converting tick data to 1-minute OHLCV bars")
            ohlc = df['price'].resample('1min').ohlc()
            volume = df['volume'].resample('1min').sum()
            df = pd.concat([ohlc, volume], axis=1).dropna()
        elif data_type == "tick" and process_as_ticks:
            # Keep as tick data but add OHLC columns for compatibility
            logger.info("Processing as tick-by-tick data")
            df['open'] = df['high'] = df['low'] = df['close'] = df['price']
        
        # Ensure proper formatting for all numeric columns
        for col in df.columns:
            if col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].round(2)
            elif col == 'volume':
                df[col] = df[col].fillna(0).astype(int)
        
        logger.info(f"Loaded {len(df)} {'ticks' if data_type == 'tick' and process_as_ticks else 'bars'}")
        return df
        
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}")
        raise
