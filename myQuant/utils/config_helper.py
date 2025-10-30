"""
config_helper.py - Configuration helpers (SSOT via config.defaults)
Enforces: create -> validate -> freeze (MappingProxyType) workflow.
"""
from typing import Dict, Any
from copy import deepcopy
from types import MappingProxyType
import logging
import json
import os

logger = logging.getLogger(__name__)

from ..config.defaults import DEFAULT_CONFIG

# Import Angel One compatibility validation
try:
    from exchange_mapper import validate_exchange_compatibility
except ImportError:
    # Fallback if exchange_mapper not available
    def validate_exchange_compatibility(exchange_code, symbol=None):
        """Fallback validation - just check basic exchange codes"""
        valid_exchanges = ['NFO', 'NSE', 'BFO']
        if exchange_code not in valid_exchanges:
            raise ValueError(f"Invalid exchange code: {exchange_code}")
        return True

MISSING = object()

def create_config_from_defaults() -> Dict[str, Any]:
    """Return a deep copy of DEFAULT_CONFIG for GUI mutation."""
    if not isinstance(DEFAULT_CONFIG, dict):
        raise RuntimeError("DEFAULT_CONFIG missing or invalid")
    return deepcopy(DEFAULT_CONFIG)

def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal validator: checks required top-level sections and logging presence.
    Returns {'valid': bool, 'errors': [...]}.
    GUI should run full validation before freeze.
    """
    errors = []
    required_sections = ['strategy', 'risk', 'capital', 'instrument', 'session', 'logging']
    for s in required_sections:
        if s not in cfg:
            errors.append(f"Missing section: {s}")
    # logging: enforce canonical key 'logfile'
    try:
        log = cfg['logging']
        if 'logfile' not in log or not log['logfile']:
            errors.append("logging.logfile is required")
    except Exception:
        errors.append("Invalid logging section")

    # Validate instrument mapping consistency
    instrument_errors = validate_instrument_consistency(cfg)
    errors.extend(instrument_errors.get('errors', []))

    return {"valid": len(errors) == 0, "errors": errors}

def freeze_config(cfg: Dict[str, Any]) -> MappingProxyType:
    """Return an immutable MappingProxyType of the config (deepcopy then freeze)."""
    if not isinstance(cfg, dict):
        raise TypeError("freeze_config expects a dict")
    # persist a copy for reproducibility
    try:
        os.makedirs("results", exist_ok=True)
        # do not overwrite existing snapshot; caller should save with run_id
    except Exception:
        pass
    return MappingProxyType(deepcopy(cfg))

class ConfigAccessor:
    """Strict accessor to read from frozen MappingProxyType; raises on missing keys."""
    def __init__(self, frozen_cfg: MappingProxyType):
        if not isinstance(frozen_cfg, MappingProxyType):
            raise TypeError("ConfigAccessor requires a frozen MappingProxyType")
        self._cfg = frozen_cfg

    def get(self, path: str, default=MISSING):
        """
        Path like 'strategy.fast_ema' returns value or raises KeyError if missing and no default.
        """
        parts = path.split('.')
        curr = self._cfg
        for p in parts:
            if isinstance(curr, MappingProxyType) and p in curr:
                curr = curr[p]
            elif isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                if default is MISSING:
                    raise KeyError(f"Missing config key: {path}")
                return default
        return curr

    # Backwards-compatible convenience helpers for common sections.
    # These keep callers throughout the codebase working (e.g. researchStrategy).
    def _section_get(self, section: str, param: str, default=MISSING):
        """Internal helper: map section+param -> get('section.param')."""
        return self.get(f"{section}.{param}", default)

    def get_strategy_param(self, param: str, default=MISSING):
        """Get value from the 'strategy' section."""
        return self._section_get("strategy", param, default)

    def get_risk_param(self, param: str, default=MISSING):
        return self._section_get("risk", param, default)

    def get_capital_param(self, param: str, default=MISSING):
        return self._section_get("capital", param, default)

    def get_instrument_param(self, param: str, default=MISSING):
        return self._section_get("instrument", param, default)

    def get_session_param(self, param: str, default=MISSING):
        return self._section_get("session", param, default)

    def get_logging_param(self, param: str, default=MISSING):
        return self._section_get("logging", param, default)

    def get_backtest_param(self, param: str, default=MISSING):
        """Get value from the 'backtest' section (convenience for backtest callers)."""
        return self._section_get("backtest", param, default)

    def get_current_instrument_param(self, param_name: str, default=MISSING):
        """
        Get instrument parameter for currently selected instrument from instrument_mappings (SSOT).
        
        For forward tests, uses instrument_type if available.
        For backtests, uses symbol directly (as it matches instrument names).
        
        Args:
            param_name: Parameter to get ('lot_size', 'tick_size', 'exchange', 'type')
            default: Default value if parameter not found
            
        Returns:
            Parameter value from instrument_mappings
            
        Raises:
            KeyError: If instrument not found in mappings or param doesn't exist
        """
        # Try to get instrument_type first (for forward tests)
        instrument_key = self.get_instrument_param('instrument_type', None)
        
        # Fall back to symbol (for backtests where symbol = instrument name)
        if instrument_key is None:
            instrument_key = self.get_instrument_param('symbol')
        
        instrument_mappings = self.get('instrument_mappings', {})
        
        if instrument_key not in instrument_mappings:
            if default is MISSING:
                raise KeyError(f"Instrument '{instrument_key}' not found in instrument_mappings")
            return default
            
        instrument_info = instrument_mappings[instrument_key]
        if param_name not in instrument_info:
            if default is MISSING:
                raise KeyError(f"Parameter '{param_name}' not found for instrument '{instrument_key}'")
            return default
            
        return instrument_info[param_name]

    def get_instrument_mapping_param(self, symbol: str, param_name: str, default=MISSING):
        """
        Get instrument parameter for a specific symbol from instrument_mappings (SSOT).
        
        Args:
            symbol: Instrument symbol ('NIFTY', 'BANKNIFTY', etc.)
            param_name: Parameter to get ('lot_size', 'tick_size', 'exchange', 'type')
            default: Default value if parameter not found
            
        Returns:
            Parameter value from instrument_mappings
            
        Raises:
            KeyError: If symbol not found in mappings or param doesn't exist
        """
        instrument_mappings = self.get('instrument_mappings', {})
        
        if symbol not in instrument_mappings:
            if default is MISSING:
                raise KeyError(f"Symbol '{symbol}' not found in instrument_mappings")
            return default
            
        instrument_info = instrument_mappings[symbol]
        if param_name not in instrument_info:
            if default is MISSING:
                raise KeyError(f"Parameter '{param_name}' not found for symbol '{symbol}'")
            return default
            
        return instrument_info[param_name]


def validate_instrument_consistency(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate instrument configuration consistency.
    
    Ensures:
    1. Current instrument symbol exists in instrument_mappings
    2. All required parameters exist for each instrument
    3. No conflicting definitions between sections
    
    Args:
        cfg: Configuration dictionary to validate
        
    Returns:
        Dict with validation results: {'valid': bool, 'errors': List[str]}
    """
    errors = []
    
    try:
        # Check if instrument_mappings exists
        if 'instrument_mappings' not in cfg:
            errors.append("Missing required section: instrument_mappings")
            return {"valid": False, "errors": errors}
            
        instrument_mappings = cfg['instrument_mappings']
        if not isinstance(instrument_mappings, dict):
            errors.append("instrument_mappings must be a dictionary")
            return {"valid": False, "errors": errors}
            
        # Check if current instrument_type exists in mappings (not symbol - symbols are specific contracts)
        if 'instrument' in cfg and 'instrument_type' in cfg['instrument']:
            current_instrument_type = cfg['instrument']['instrument_type']
            if current_instrument_type not in instrument_mappings:
                errors.append(f"Current instrument_type '{current_instrument_type}' not found in instrument_mappings")
        
        # Validate each instrument mapping has required parameters
        required_params = ['lot_size', 'exchange', 'tick_size', 'type']
        for symbol, mapping in instrument_mappings.items():
            if not isinstance(mapping, dict):
                errors.append(f"Instrument mapping for '{symbol}' must be a dictionary")
                continue
                
            for param in required_params:
                if param not in mapping:
                    errors.append(f"Missing required parameter '{param}' for instrument '{symbol}'")
                    
            # Validate parameter types
            if 'lot_size' in mapping:
                try:
                    lot_size = int(mapping['lot_size'])
                    if lot_size <= 0:
                        errors.append(f"Invalid lot_size for '{symbol}': must be positive integer")
                except (ValueError, TypeError):
                    errors.append(f"Invalid lot_size for '{symbol}': must be numeric")
                    
            if 'tick_size' in mapping:
                try:
                    tick_size = float(mapping['tick_size'])
                    if tick_size <= 0:
                        errors.append(f"Invalid tick_size for '{symbol}': must be positive number")
                except (ValueError, TypeError):
                    errors.append(f"Invalid tick_size for '{symbol}': must be numeric")
            
            # Exchange validation handled by WebSocket connection attempt
            # No need for upfront validation - connection failure will indicate issues
        
        # Check for minimum required instruments
        if len(instrument_mappings) == 0:
            errors.append("instrument_mappings cannot be empty")
            
    except Exception as e:
        errors.append(f"Error validating instrument configuration: {str(e)}")
    
    return {"valid": len(errors) == 0, "errors": errors}



