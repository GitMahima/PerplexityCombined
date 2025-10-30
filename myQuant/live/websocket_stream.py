"""
live/websocket_stream.py

SmartAPI WebSocket streaming module for unified trading system.

Features:
- Multiple instrument streams (up to 3 per SmartAPI account)
- User-selectable feed type: LTP, Quote, SnapQuote
- Event-driven tick delivery to tick buffer and OHLC aggregator
- Robust reconnect and error handling
- Integration with GUI controls and manual refresh
- Angel One compatible exchange type mapping

Usage:
- Import and call `start_stream()` from the live runner or GUI.
- All ticks passed safely for simulation; never to live order endpoints.
"""

import logging
import threading
import json
import pytz
from datetime import datetime

# Import timezone from SSOT
from ..utils.time_utils import IST

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # Capital 'A' - correct package name
except ImportError:
    SmartWebSocketV2 = None  # Install with pip install smartapi-python

# Import Angel One exchange type mapper
from ..utils.exchange_mapper import map_to_angel_exchange_type

logger = logging.getLogger(__name__)

# Suppress known SmartAPI WebSocket callback signature mismatch
class SmartAPIWebSocketFilter(logging.Filter):
    """Filter out known SmartAPI library bugs that don't affect functionality."""
    def filter(self, record):
        # Suppress: SmartWebSocketV2._on_close() takes 2 positional arguments but 4 were given
        if "SmartWebSocketV2._on_close()" in record.getMessage() and "positional arguments" in record.getMessage():
            return False
        return True

# Apply filter to websocket logger
logging.getLogger('websocket').addFilter(SmartAPIWebSocketFilter())

# Phase 1.5: Pre-convergence instrumentation
_pre_convergence_instrumentor = None

def set_pre_convergence_instrumentor(instrumentor):
    """Set the Phase 1.5 instrumentor for pre-convergence measurements."""
    global _pre_convergence_instrumentor
    import sys
    _pre_convergence_instrumentor = instrumentor
    logger.info(f"🔬 [WEBSOCKET_STREAM] Instrumentor SET: {instrumentor is not None}, Type: {type(instrumentor).__name__ if instrumentor else 'None'}")
    logger.info(f"🔬 [WEBSOCKET_STREAM] Module ID when SET: {id(sys.modules[__name__])}")
    logger.info(f"🔬 [WEBSOCKET_STREAM] Variable ID when SET: {id(_pre_convergence_instrumentor)}")

class WebSocketTickStreamer:
    def __init__(self, api_key, client_code, feed_token, symbol_tokens, feed_type="Quote", on_tick=None, auth_token=None):
        """
        api_key: SmartAPI API key
        client_code: User/Account code
        feed_token: Obtained from SmartAPI session
        auth_token: JWT token for authentication (required for SmartWebSocketV2)
        symbol_tokens: list of dicts [{"symbol": ..., "token": ..., "exchange": ...}]
        feed_type: 'LTP', 'Quote', or 'SnapQuote'
        on_tick: callback(tick_dict, symbol) called when new tick arrives
        """
        if SmartWebSocketV2 is None:
            raise ImportError("SmartWebSocketV2 (smartapi) package not available.")
        self.api_key = api_key
        self.auth_token = auth_token
        self.client_code = client_code
        self.feed_token = feed_token
        self.symbol_tokens = symbol_tokens[:3]  # SmartAPI allows max 3
        self.feed_type = feed_type
        self.on_tick = on_tick or (lambda tick, symbol: None)
        self.ws = None
        self.running = False
        self.thread = None
        # Robustness priority: Always allow auto-reconnection
        # User must explicitly confirm stop via GUI dialog

    def _on_open(self, ws):
        logger.info("WebSocket connection OPEN")
        
        # SmartWebSocketV2.subscribe() expects: subscribe(mode, token_list)
        # mode: 1=LTP, 2=Quote, 3=SnapQuote
        mode_map = {"LTP": 1, "Quote": 2, "SnapQuote": 3}
        mode = mode_map.get(self.feed_type, 1)  # Default to LTP
        
        # Build token list with exchange type mapping
        token_list = []
        for s in self.symbol_tokens:
            try:
                # Convert exchange code to Angel One exchange_type integer
                angel_exchange_type = map_to_angel_exchange_type(s['exchange'])
                token_entry = {
                    "exchangeType": angel_exchange_type,  # Use Angel One integer format
                    "tokens": [s['token']]
                }
                token_list.append(token_entry)
                logger.info(f"Mapped {s['exchange']} -> exchange_type={angel_exchange_type} for {s['symbol']}")
            except ValueError as e:
                logger.error(f"Exchange mapping failed for {s['symbol']}: {e}")
                continue
        
        if token_list:
            try:
                # SmartWebSocketV2.subscribe() signature: subscribe(correlation_id, mode, token_list)
                # Try different subscription patterns based on SmartAPI version
                correlation_id = "myQuant_stream"
                self.ws.subscribe(correlation_id, mode, token_list)
                logger.info(f"Subscribed to {len(token_list)} stream(s): {[s['symbol'] for s in self.symbol_tokens]} [mode={mode}, feed_type={self.feed_type}]")
            except TypeError as e:
                logger.error(f"WebSocket subscription failed with signature error: {e}")
                # Fallback: Try without correlation_id
                try:
                    self.ws.subscribe(mode, token_list)
                    logger.info(f"Subscribed (fallback) to {len(token_list)} stream(s)")
                except Exception as e2:
                    logger.error(f"WebSocket subscription fallback also failed: {e2}")
        else:
            logger.error("No valid exchange mappings found - WebSocket subscription failed")

    def _on_data(self, ws, message):
        # Robustness priority: Process all ticks, always allow reconnection
        # Stop only when user explicitly confirms via GUI dialog
        try:
            # Phase 1.5: Start pre-convergence measurement
            global _pre_convergence_instrumentor
            
            # DEBUG: Check instrumentor state with detailed info
            if not hasattr(self, '_instrumentor_debug_logged'):
                import sys
                logger.info(f"🔬 [DEBUG] Module ID: {id(sys.modules[__name__])}")
                logger.info(f"🔬 [DEBUG] Instrumentor variable ID: {id(_pre_convergence_instrumentor)}")
                logger.info(f"🔬 [DEBUG] Instrumentor value: {_pre_convergence_instrumentor}")
                logger.info(f"🔬 [DEBUG] Instrumentor is None: {_pre_convergence_instrumentor is None}")
                self._instrumentor_debug_logged = True
            
            if _pre_convergence_instrumentor:
                _pre_convergence_instrumentor.start_websocket_tick()
            else:
                # Debug: Log when instrumentor is None during tick processing
                if not hasattr(self, '_instrumentor_warning_logged'):
                    logger.warning("🔬 [WEBSOCKET_STREAM._on_data] Instrumentor is NONE during tick processing")
                    self._instrumentor_warning_logged = True
            
            # Handle both JSON string and dict object from SmartAPI
            if isinstance(message, str):
                # Phase 1.5: Measure JSON parsing
                if _pre_convergence_instrumentor:
                    with _pre_convergence_instrumentor.measure_websocket('json_parse'):
                        data = json.loads(message)
                else:
                    data = json.loads(message)
            else:
                data = message  # Already a dictionary
            
            # Phase 1.5: Measure dict creation/processing
            if _pre_convergence_instrumentor:
                with _pre_convergence_instrumentor.measure_websocket('dict_creation'):
                    # Use timezone-aware timestamp for consistency with strategy
                    # Create timestamp if needed
                    ts = datetime.now(IST)
            else:
                # Use timezone-aware timestamp for consistency with strategy
                # Create timestamp if needed
                ts = datetime.now(IST)
            # Extract price with better error handling and logging
            raw_price = data.get("ltp", data.get("last_traded_price", 0))
            if raw_price == 0:
                # Log raw data when price is missing/zero for debugging
                logger.warning(f"Zero price received in WebSocket data: {data}")
            
            # CRITICAL FIX: SmartAPI returns prices in paise, convert to rupees
            # Raw price from SmartAPI is in paise (smallest currency unit)
            # Need to divide by 100 to get actual rupee price
            actual_price = float(raw_price) / 100.0
            
            tick = {
                "timestamp": ts,
                "price": actual_price,  # Use converted price in rupees
                "volume": int(data.get("volume", 0)),
                "symbol": data.get("tradingsymbol", data.get("symbol", "")),
                "exchange": data.get("exchange", ""),
                "raw": data
            }
            
            # Validate reasonable price range for options  
            if tick['price'] > 5000:  # Still log if something seems wrong
                logger.warning(f"🚨 Unusually high option price: {tick['symbol']} @ ₹{tick['price']} - Raw data: {data}")
            elif tick['price'] < 0.01:  # Also log extremely low prices
                logger.warning(f"🚨 Unusually low option price: {tick['symbol']} @ ₹{tick['price']} - Raw data: {data}")
            
            if self.on_tick:
                # Phase 1.5: End websocket measurement before callback
                if _pre_convergence_instrumentor:
                    _pre_convergence_instrumentor.end_websocket_tick()
                
                self.on_tick(tick, tick['symbol'])
            else:
                # End measurement even if no callback
                if _pre_convergence_instrumentor:
                    _pre_convergence_instrumentor.end_websocket_tick()
                    
        except Exception as e:
            logger.error(f"Error in streamed tick: {e}")
            # End measurement on error too
            if _pre_convergence_instrumentor:
                _pre_convergence_instrumentor.end_websocket_tick()

    def _on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code=None, close_msg=None):
        """Handle WebSocket closure - always allows auto-reconnection for robustness."""
        was_running = self.running
        self.running = False
        
        if was_running:
            # Connection lost - SmartAPI will auto-reconnect (robustness priority)
            logger.warning(f"WebSocket connection LOST (code: {close_status_code}, msg: {close_msg}) - auto-reconnect ENABLED")
        else:
            logger.info(f"WebSocket connection closed (code: {close_status_code})")

    def start_stream(self):
        if self.running:
            logger.info("WebSocket stream already running.")
            return
        
        self.running = True
        self.ws = SmartWebSocketV2(
            auth_token=self.auth_token,
            api_key=self.api_key,
            client_code=self.client_code,
            feed_token=self.feed_token
        )
        self.ws.on_open = self._on_open
        self.ws.on_data = self._on_data
        self.ws.on_error = self._on_error
        self.ws.on_close = self._on_close
        self.thread = threading.Thread(target=self.ws.connect)
        self.thread.daemon = True
        self.thread.start()
        logger.info("WebSocket thread started.")

    def stop_stream(self):
        """Stop WebSocket stream - user must confirm via GUI dialog before calling."""
        self.running = False
        
        if self.ws is not None:
            try:
                self.ws.close_connection()  # Correct method for SmartWebSocketV2
                logger.warning("⚠️ WebSocket stream stopped (user-confirmed) - will auto-reconnect if connection restored")
            except Exception as e:
                logger.warning(f"Error during WebSocket close: {e}")

# Example usage for integration testing (not run in production as-is)
if __name__ == "__main__":
    import os
    import time
    # Load session info from external auth token file
    session_path = r"C:\Users\user\projects\angelalgo\auth_token.json"
    if not os.path.exists(session_path):
        print("External auth token file missing—run smartapi login first.")
        exit(1)
    with open(session_path, "r") as f:
        token_data = json.load(f)
        # Convert external format to internal format for test
        session = {
            "jwt_token": token_data["data"]["auth_token"],
            "client_code": token_data["data"]["client_id"],
            "feed_token": None  # Will need to be set properly in real usage
        }
    api_key = session["profile"]["api_key"]
    client_code = session["client_code"]
    feed_token = session["feed_token"]
    # Load three sample tokens from symbol cache
    from ..utils.cache_manager import load_symbol_cache
    symbols = load_symbol_cache()
    test_tokens = [v for (k, v) in list(symbols.items())[:3]]
    def print_tick(tick, symbol):
        print(f"[{tick['timestamp']}] {symbol}: â‚¹{tick['price']} Vol:{tick['volume']}")
    streamer = WebSocketTickStreamer(
        api_key, client_code, feed_token,
        test_tokens, feed_type="Quote", on_tick=print_tick
    )
    streamer.start_stream()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        streamer.stop_stream()
