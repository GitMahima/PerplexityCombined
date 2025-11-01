"""
core/position_manager.py - Unified Position Manager for Backtest & Live Trading

Handles:
- Long-only, intraday-only position management
- F&O support with lot sizes and tick sizes
- Advanced order type simulation
- Tiered take-profits with partial exits
- Trailing stop loss management
- Commission and cost modeling
- Risk management and capital tracking
"""

import pandas as pd
import numpy as np
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
import logging
import uuid
from ..utils.config_helper import ConfigAccessor
from ..utils.time_utils import now_ist, is_within_session, apply_buffer_to_time

logger = logging.getLogger(__name__)

def compute_number_of_lots(cfg_accessor: ConfigAccessor, current_capital: float, price: float) -> int:
    """
    Compute number of lots (integer) using only current capital and instrument.lot_size (exchange fixed).
    Formula:
        lots = floor( current_capital / (lot_size * price) )

    - cfg_accessor: strict accessor (GUI-validated, frozen config)
    - current_capital: cash available to deploy (float)
    - price: current price / LTP (float)

    Returns int >= 0.
    """
    try:
        if current_capital is None or price is None:
            return 0
        current_capital = float(current_capital)
        price = float(price)
        if current_capital <= 0 or price <= 0:
            return 0
        
        # Safety check: prevent trading with obviously wrong option prices
        if price > 10000:  # Options shouldn't cost more than ₹10,000
            logger.warning(f"Rejecting trade: price ₹{price:,.2f} appears to be incorrect for options trading")
            return 0

        # instrument_mappings.lot_size is the single SSOT for contract size
        units_per_lot = cfg_accessor.get_current_instrument_param('lot_size')
        units_per_lot = int(units_per_lot)

        units_value_per_lot = units_per_lot * price
        if units_value_per_lot <= 0:
            return 0

        lots = int(current_capital // units_value_per_lot)
        if lots < 0:
            return 0
        return lots
    except Exception as e:
        logger.exception("compute_number_of_lots failed")
        return 0

class PositionType(Enum):
    LONG = "LONG"  # Only long supported

class PositionStatus(Enum):
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"

class ExitReason(Enum):
    TAKE_PROFIT_1 = "Take Profit 1"
    TAKE_PROFIT_2 = "Take Profit 2"
    TAKE_PROFIT_3 = "Take Profit 3"
    TAKE_PROFIT_4 = "Take Profit 4"
    STOP_LOSS = "Base SL"  # Standardized to "Base SL" throughout codebase
    TRAILING_STOP = "Trailing Stop"
    SESSION_END = "Session End"
    STRATEGY_EXIT = "Strategy Exit"

@dataclass
class Position:
    position_id: str
    symbol: str
    entry_time: datetime
    entry_price: float
    initial_quantity: int
    current_quantity: int
    lot_size: int
    tick_size: float

    stop_loss_price: float
    tp_levels: List[float] = field(default_factory=list)
    tp_percentages: List[float] = field(default_factory=list)
    tp_executed: List[bool] = field(default_factory=list)

    trailing_enabled: bool = False
    trailing_activation_points: float = 0.0
    trailing_distance_points: float = 0.0
    trailing_activated: bool = False
    trailing_stop_price: Optional[float] = None
    highest_price: float = 0.0

    status: PositionStatus = PositionStatus.OPEN
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_commission: float = 0.0
    original_reserved_capital: float = 0.0

    exit_transactions: List[Dict] = field(default_factory=list)

    def update_unrealized_pnl(self, current_price: float):
        if self.current_quantity > 0:
            self.unrealized_pnl = (current_price - self.entry_price) * self.current_quantity
        else:
            self.unrealized_pnl = 0.0

    def get_total_pnl(self, current_price: float) -> float:
        self.update_unrealized_pnl(current_price)
        return self.realized_pnl + self.unrealized_pnl

    def update_trailing_stop(self, current_price: float):
        if not self.trailing_enabled or self.current_quantity == 0:
            return
        if current_price > self.highest_price:
            self.highest_price = current_price
        if not self.trailing_activated:
            profit_points = current_price - self.entry_price
            if profit_points >= self.trailing_activation_points:
                self.trailing_activated = True
                self.trailing_stop_price = current_price - self.trailing_distance_points
                logger.info(f"Trailing stop activated for {self.position_id} at {self.trailing_stop_price}")
        elif self.trailing_activated:
            new_stop = self.highest_price - self.trailing_distance_points
            if new_stop > (self.trailing_stop_price or 0):
                self.trailing_stop_price = new_stop

@dataclass
class Trade:
    trade_id: str
    position_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    commission: float
    net_pnl: float
    exit_reason: str
    duration_minutes: int
    lot_size: int
    lots_traded: int = 0

    def __post_init__(self):
        if self.lot_size > 0:
            self.lots_traded = self.quantity // self.lot_size
        else:
            self.lots_traded = self.quantity

class PositionManager:
    def __init__(self, config: Dict[str, Any], strategy_callback=None, **kwargs):
        # Existing initialization...
        self.config = config
        self.config_accessor = ConfigAccessor(config)
        self.strategy_callback = strategy_callback
        try:
            self.initial_capital = self.config_accessor.get_capital_param('initial_capital')
            self.current_capital = self.initial_capital
            self.reserved_margin = 0.0
            self.risk_per_trade_percent = self.config_accessor.get_risk_param('risk_per_trade_percent')
            self.max_position_value_percent = self.config_accessor.get_risk_param('max_position_value_percent')
            self.base_sl_points = self.config_accessor.get_risk_param('base_sl_points')
            self.tp_points = self.config_accessor.get_risk_param('tp_points')
            self.tp_percentages = self.config_accessor.get_risk_param('tp_percents')
            self.use_trailing_stop = self.config_accessor.get_risk_param('use_trail_stop')
            self.trailing_activation_points = self.config_accessor.get_risk_param('trail_activation_points')
            self.trailing_distance_points = self.config_accessor.get_risk_param('trail_distance_points')
            self.commission_percent = self.config_accessor.get_risk_param('commission_percent')
            self.commission_per_trade = self.config_accessor.get_risk_param('commission_per_trade')
            self.stt_percent = self.config_accessor.get_risk_param('stt_percent')
            self.exchange_charges_percent = self.config_accessor.get_risk_param('exchange_charges_percent')
            self.gst_percent = self.config_accessor.get_risk_param('gst_percent')
            self.slippage_points = self.config_accessor.get_risk_param('slippage_points')
        except KeyError as e:
            logger.error(f"PositionManager config error: missing {e}")
            raise

        self.positions: Dict[str, Position] = {}
        self.completed_trades: List[Trade] = []
        self.daily_pnl = 0.0
        self.session_config = config['session']
        logger.info(f"PositionManager initialized with capital: {self.initial_capital:,}")

    def _ensure_timezone(self, dt):
        """Ensure datetime is timezone-aware for consistent comparisons"""
        if dt is None:
            return None
        if dt.tzinfo is None:
            from ..utils.time_utils import IST
            return IST.localize(dt)
        return dt

    def calculate_lot_aligned_quantity(self, desired_quantity: int, lot_size: int) -> int:
        if lot_size <= 1:  # Equity
            return max(1, desired_quantity)
        lots = max(1, round(desired_quantity / lot_size))
        return lots * lot_size

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """
        Capital-driven sizing (deterministic):
        - Use self.current_capital as available capital.
        - Use canonical instrument.lot_size (SSOT).
        - Return total quantity (units), aligned to lot boundary.
        """
        try:
            if entry_price <= 0:
                return 0
            canonical_lot = int(self.config_accessor.get_current_instrument_param('lot_size'))
            lots = compute_number_of_lots(self.config_accessor, self.current_capital, entry_price)
            total_quantity = int(lots) * canonical_lot
            return total_quantity
        except Exception:
            logger.exception("calculate_position_size failed")
            return 0

    def calculate_position_size_in_lots(self, entry_price: float, stop_loss_price: float) -> tuple:
        """
        Return (lots, total_quantity, lot_size) using canonical instrument.lot_size
        and deterministic capital-driven sizing (100% available capital).
        """
        try:
            if entry_price <= 0:
                canonical_lot = int(self.config_accessor.get_current_instrument_param('lot_size'))
                return 0, 0, canonical_lot
            canonical_lot = int(self.config_accessor.get_current_instrument_param('lot_size'))
            lots = compute_number_of_lots(self.config_accessor, self.current_capital, entry_price)
            total_quantity = int(lots) * canonical_lot
            return int(lots), int(total_quantity), int(canonical_lot)
        except Exception:
            logger.exception("calculate_position_size_in_lots failed")
            canonical_lot = int(self.config_accessor.get_current_instrument_param('lot_size'))
            return 0, 0, canonical_lot

    def calculate_total_costs(self, price: float, quantity: int, is_buy: bool = True) -> Dict[str, float]:
        turnover = price * quantity
        commission = max(self.commission_per_trade, turnover * (self.commission_percent / 100))
        stt = turnover * (self.stt_percent / 100) if not is_buy else 0.0
        exchange_charges = turnover * (self.exchange_charges_percent / 100)
        taxable_amount = commission + exchange_charges
        gst = taxable_amount * (self.gst_percent / 100)
        total_costs = commission + stt + exchange_charges + gst
        return {
            'commission': commission,
            'stt': stt,
            'exchange_charges': exchange_charges,
            'gst': gst,
            'total_costs': total_costs,
            'turnover': turnover
        }

    def open_position(self, symbol: str, entry_price: float, timestamp: datetime,
                      order_type: OrderType = OrderType.MARKET,
                      base_sl_points_override: Optional[float] = None) -> Optional[str]:
        """
        Open a new long position.
        
        Args:
            symbol: Trading symbol
            entry_price: Entry price
            timestamp: Entry timestamp
            order_type: Market or limit order
            base_sl_points_override: Optional override for base_sl_points (used by SL Regression)
        
        Returns:
            Position ID if successful, None otherwise
        """
        if order_type == OrderType.MARKET:
            actual_entry_price = entry_price + self.slippage_points
        else:
            actual_entry_price = entry_price
        
        # Use override if provided (SL Regression), otherwise use config default
        base_sl = base_sl_points_override if base_sl_points_override is not None else self.base_sl_points
        stop_loss_price = actual_entry_price - base_sl
        
        lots, quantity, lot_size_used = self.calculate_position_size_in_lots(
            actual_entry_price, stop_loss_price)

        if lots <= 0 or quantity <= 0:
            logger.warning("Cannot open position: invalid lot size calculated")
            return None

        entry_costs = self.calculate_total_costs(actual_entry_price, quantity, is_buy=True)
        required_capital = entry_costs['turnover'] + entry_costs['total_costs']
        if required_capital > self.current_capital:
            logger.warning(f"Insufficient capital: required {required_capital:,.2f}, available {self.current_capital:,.2f}")
            return None
        position_id = str(uuid.uuid4())[:8]
        tp_levels = [actual_entry_price + tp for tp in self.tp_points]
        
        # Get lot_size and tick_size from SSOT
        lot_size = self.config_accessor.get_current_instrument_param('lot_size')
        tick_size = self.config_accessor.get_current_instrument_param('tick_size')
        
        position = Position(
            position_id=position_id,
            symbol=symbol,
            entry_time=timestamp,
            entry_price=actual_entry_price,
            initial_quantity=quantity,
            current_quantity=quantity,
            lot_size=lot_size,
            tick_size=tick_size,
            stop_loss_price=stop_loss_price,
            tp_levels=tp_levels,
            tp_percentages=self.tp_percentages.copy(),
            tp_executed=[False] * len(self.tp_points),
            trailing_enabled=self.use_trailing_stop,
            trailing_activation_points=self.trailing_activation_points,
            trailing_distance_points=self.trailing_distance_points,
            highest_price=actual_entry_price,
            total_commission=entry_costs['total_costs'],
            original_reserved_capital=required_capital
        )
        self.current_capital -= required_capital
        self.reserved_margin += required_capital
        self.positions[position_id] = position
        logger.info("Opened position %s", position_id)
        logger.info("Lots: %s (%s total units)", lots, quantity)
        logger.info("Entry price: â‚¹%.2f per unit", actual_entry_price)
        logger.info("Stop loss: â‚¹%.2f", stop_loss_price)
        logger.info("Take profit levels: %s", [f'â‚¹{tp:.2f}' for tp in tp_levels])
        # Format numeric value with grouping first, then pass as %s to logger to avoid %-format parsing issues.
        logger.info("Position value: ₹%s", f"{quantity * actual_entry_price:,.2f}")
        return position_id

    def close_position_partial(self, position_id: str, exit_price: float,
                               quantity_to_close: int, timestamp: datetime,
                               exit_reason: str) -> bool:
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found")
            return False
        position = self.positions[position_id]
        if quantity_to_close <= 0 or quantity_to_close > position.current_quantity:
            logger.error(f"Invalid quantity to close: {quantity_to_close}")
            return False
        exit_costs = self.calculate_total_costs(exit_price, quantity_to_close, is_buy=False)
        gross_pnl = (exit_price - position.entry_price) * quantity_to_close
        commission = exit_costs['total_costs']
        net_pnl = gross_pnl - commission
        proceeds = exit_costs['turnover'] - exit_costs['total_costs']
        self.current_capital += proceeds
        position.current_quantity -= quantity_to_close
        position.realized_pnl += net_pnl
        position.total_commission += commission
        duration = int((timestamp - position.entry_time).total_seconds() / 60)
        lots_closed = quantity_to_close // position.lot_size if position.lot_size > 0 else quantity_to_close
        trade = Trade(
            trade_id=str(uuid.uuid4())[:8],
            position_id=position_id,
            symbol=position.symbol,
            entry_time=position.entry_time,
            exit_time=timestamp,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=quantity_to_close,
            gross_pnl=gross_pnl,
            commission=commission,
            net_pnl=net_pnl,
            exit_reason=exit_reason,
            duration_minutes=duration,
            lot_size=position.lot_size,
            lots_traded=lots_closed
        )
        self.completed_trades.append(trade)
        position.exit_transactions.append({
            'timestamp': timestamp,
            'price': exit_price,
            'quantity': quantity_to_close,
            'reason': exit_reason,
            'pnl': net_pnl
        })
        if position.current_quantity == 0:
            position.status = PositionStatus.CLOSED
            self.reserved_margin -= position.original_reserved_capital
            del self.positions[position_id]
            logger.info(f"Fully closed position {position_id}")
        else:
            position.status = PositionStatus.PARTIALLY_CLOSED
            logger.info(f"Partially closed position {position_id}: {quantity_to_close} @ â‚¹{exit_price}")
        self.daily_pnl += net_pnl
        logger.info("Closed position %s", position_id)
        logger.info("Lots closed: %s (%s units)", lots_closed, quantity_to_close)
        logger.info("Exit price: ₹%.2f per unit", exit_price)
        logger.info("P&L: ₹%.2f (%s)", net_pnl, exit_reason)
        
        # Call strategy callback with exit info (already standardized from ExitReason enum)
        if self.strategy_callback:
            exit_info = {
                'position_id': position_id,
                'exit_reason': exit_reason,  # Already "Base SL", "Trailing Stop", etc.
                'exit_price': exit_price,
                'quantity': quantity_to_close,
                'pnl': net_pnl,
                'timestamp': timestamp
            }
            self.strategy_callback(exit_info)
        
        return True

    def close_position_full(self, position_id: str, exit_price: float,
                            timestamp: datetime, exit_reason: str) -> bool:
        if position_id not in self.positions:
            return False
        position = self.positions[position_id]
        return self.close_position_partial(position_id, exit_price, position.current_quantity, timestamp, exit_reason)

    def check_exit_conditions(self, position_id: str, current_price: float, timestamp: datetime) -> List[Tuple[int, str]]:
        if position_id not in self.positions:
            return []
        position = self.positions[position_id]
        exits = []
        
        # Update trailing stop
        position.update_trailing_stop(current_price)
        
        # Check Stop Loss (simple comparison)
        if current_price <= position.stop_loss_price:
            logger.info(f"🛑 STOP LOSS triggered: price ₹{current_price:.2f} <= SL ₹{position.stop_loss_price:.2f}")
            exits.append((position.current_quantity, ExitReason.STOP_LOSS.value))
            return exits
        
        # Check Trailing Stop (simple comparison)
        if (position.trailing_activated and position.trailing_stop_price and 
            current_price <= position.trailing_stop_price):
            logger.info(f"🔄 TRAILING STOP triggered: price ₹{current_price:.2f} <= trailing ₹{position.trailing_stop_price:.2f}")
            exits.append((position.current_quantity, ExitReason.TRAILING_STOP.value))
            return exits
        
        # Check Take Profit levels (simple comparison)
        for i, (tp_level, tp_percentage, tp_executed) in enumerate(zip(position.tp_levels, position.tp_percentages, position.tp_executed)):
            if not tp_executed and current_price >= tp_level:
                logger.info(f"🎯 TAKE PROFIT {i+1} triggered: price ₹{current_price:.2f} >= TP{i+1} ₹{tp_level:.2f}")
                position.tp_executed[i] = True
                # --- FIX: Lot-aligned TP exit calculation ---
                if i < len(position.tp_levels) - 1:
                    total_lots = position.initial_quantity // position.lot_size
                    lots_to_exit = max(1, int(total_lots * tp_percentage))
                    remaining_lots = position.current_quantity // position.lot_size
                    if lots_to_exit > remaining_lots:
                        lots_to_exit = remaining_lots
                    exit_quantity = lots_to_exit * position.lot_size
                else:
                    exit_quantity = position.current_quantity  # Last TP: exit all remaining
                # Logging for verification
                exit_lots = exit_quantity // position.lot_size if position.lot_size > 0 else exit_quantity
                logger.info(f"🎯 TP{i+1} Exit: {exit_lots} lots ({exit_quantity} units)")
                # --- END FIX ---
                if exit_quantity > 0:
                    reason = f"Take Profit {i+1}"
                    exits.append((exit_quantity, reason))
        return exits

    def process_positions(self, row, timestamp, session_config=None):
        """Enhanced position processing with session awareness"""
        current_price = row['close']
        
        # Debug logging
        if len(self.positions) > 0:
            logger.debug(f"[DEBUG] process_positions called: {len(self.positions)} active positions, price=₹{current_price:.2f}")
        
        # Ensure timezone-aware
        timestamp = self._ensure_timezone(timestamp)
        
        # Check for session exit using the new method
        if self.should_exit_for_session_end(timestamp):
            # Close all positions for session end
            for position_id in list(self.positions.keys()):
                self.close_position_full(position_id, current_price, timestamp, ExitReason.SESSION_END.value)
            return
        
        for position_id in list(self.positions.keys()):
            position = self.positions.get(position_id)
            if not position or position.status == PositionStatus.CLOSED:
                continue
            
            exits = self.check_exit_conditions(position_id, current_price, timestamp)
            for exit_quantity, exit_reason in exits:
                if exit_quantity > 0:
                    self.close_position_partial(position_id, current_price, exit_quantity, timestamp, exit_reason)
                if position_id not in self.positions:
                    break

    def get_portfolio_value(self, current_price: float) -> float:
        total_value = self.current_capital
        for position in self.positions.values():
            if position.status != PositionStatus.CLOSED:
                position.update_unrealized_pnl(current_price)
                total_value += position.unrealized_pnl
        return total_value

    def get_open_positions(self) -> List[Dict[str, Any]]:
        open_positions = []
        for position in self.positions.values():
            if position.status != PositionStatus.CLOSED:
                open_positions.append({
                    'id': position.position_id,
                    'symbol': position.symbol,
                    'type': 'long',
                    'quantity': position.current_quantity,
                    'entry_price': position.entry_price,
                    'entry_time': position.entry_time,
                    'stop_loss': position.stop_loss_price,
                    'take_profits': position.tp_levels,
                    'trailing_active': position.trailing_activated,
                    'trailing_stop': position.trailing_stop_price,
                    'unrealized_pnl': position.unrealized_pnl
                })
        return open_positions

    def get_trade_history(self) -> List[Dict[str, Any]]:
        trades = []
        for trade in self.completed_trades:
            trades.append({
                'trade_id': trade.trade_id,
                'position_id': trade.position_id,
                'symbol': trade.symbol,
                'entry_time': trade.entry_time,
                'exit_time': trade.exit_time,
                'entry_price': trade.entry_price,
                'exit_price': trade.exit_price,
                'quantity': trade.quantity,
                'gross_pnl': trade.gross_pnl,
                'commission': trade.commission,
                'net_pnl': trade.net_pnl,
                'exit_reason': trade.exit_reason,
                'duration_minutes': trade.duration_minutes,
                'return_percent': (trade.net_pnl / (trade.entry_price * trade.quantity)) * 100
            })
        return trades

    def get_performance_summary(self) -> Dict[str, Any]:
        if not self.completed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'profit_factor': 0.0,
                'max_win': 0.0,
                'max_loss': 0.0,
                'total_commission': 0.0                
            }
        winning_trades = [t for t in self.completed_trades if t.net_pnl > 0]
        losing_trades = [t for t in self.completed_trades if t.net_pnl < 0]
        total_pnl = sum(t.net_pnl for t in self.completed_trades)
        total_commission = sum(t.commission for t in self.completed_trades)
        gross_profit = sum(t.net_pnl for t in winning_trades)
        gross_loss = abs(sum(t.net_pnl for t in losing_trades))
        return {
            'total_trades': len(self.completed_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': (len(winning_trades) / len(self.completed_trades)) * 100,
            'total_pnl': total_pnl,
            'avg_win': gross_profit / len(winning_trades) if winning_trades else 0,
            'avg_loss': gross_loss / len(losing_trades) if losing_trades else 0,
            'profit_factor': gross_profit / gross_loss if gross_loss > 0 else 0,
            'max_win': max(t.net_pnl for t in self.completed_trades),
            'max_loss': min(t.net_pnl for t in self.completed_trades),
            'total_commission': total_commission
        }

    def reset(self, initial_capital: Optional[float] = None):
        if initial_capital:
            self.initial_capital = initial_capital
        self.current_capital = self.initial_capital
        self.reserved_margin = 0.0
        self.daily_pnl = 0.0
        self.positions.clear()
        self.completed_trades.clear()
        logger.info(f"Position Manager reset with capital: {self.initial_capital:,}")

    # Legacy compatibility methods for backtest engine
    def enter_position(self, side: str, price: float, quantity: int, timestamp: datetime,
                       **kwargs) -> Optional[str]:
        if side.upper() != 'BUY':
            logger.warning("This system only supports LONG positions")
            return None
        # Legacy entry: ignore external lot overrides and size deterministically from available capital
        symbol = kwargs.get('symbol', 'NIFTY')
        tick_size = kwargs.get('tick_size', 0.05)
        canonical_lot = int(self.config_accessor.get_instrument_param('lot_size'))
        return self.open_position(symbol, price, timestamp, canonical_lot, tick_size)

    def exit_position(self, position_id: str, price: float, timestamp: datetime, reason: str):
        self.close_position_full(position_id, price, timestamp, reason)

    def can_enter_position(self) -> bool:
        try:
            return len(self.positions) < self.config_accessor.get_strategy_param('max_positions_per_day')
        except KeyError as e:
            logger.error(f"PositionManager config error: missing {e}")
            raise

    def calculate_position_size_gui_driven(self, entry_price: float, stop_loss_price: float, 
                                           user_capital: float, user_risk_pct: float) -> dict:
        """
        GUI-driven position sizing with comprehensive feedback

        Returns:
            dict with position details and capital analysis
        """
        # Simplified GUI preview using 100% capital rule (consistent with runtime)
        if entry_price <= 0:
            return {"error": "Invalid price inputs"}
        try:
            canonical_lot = int(self.config_accessor.get_current_instrument_param('lot_size'))
            usable_capital = float(user_capital)
            max_affordable_shares = int(usable_capital // entry_price)
            final_lots = max_affordable_shares // canonical_lot
            aligned_quantity = final_lots * canonical_lot

            position_value = aligned_quantity * entry_price
            capital_utilization = (position_value / usable_capital) * 100 if usable_capital > 0 else 0

            return {
                "recommended_quantity": aligned_quantity,
                "recommended_lots": final_lots,
                "position_value": position_value,
                "capital_utilization_pct": capital_utilization,
                "approach_used": "capital_limited"
            }
        except Exception:
            logger.exception("calculate_position_size_gui_driven failed")
            return {"error": "internal error"}

    def should_exit_for_session_end(self, current_time: datetime) -> bool:
        """
        Check if position should exit based on user-defined session end and buffer
        """
        # Get session configuration if not already cached
        if not hasattr(self, '_cached_session_end') or not hasattr(self, '_cached_end_buffer'):
            # Use session_config which is already initialized in the constructor
            self.session_end = time(
                self.session_config['end_hour'],
                self.session_config['end_min']
            )
            self.end_buffer = self.session_config['end_buffer_minutes']
            
            # Cache these values to avoid recalculation
            self._cached_session_end = self.session_end
            self._cached_end_buffer = self.end_buffer
        else:
            # Use cached values
            self.session_end = self._cached_session_end
            self.end_buffer = self._cached_end_buffer
        
        # Calculate effective end time with buffer
        effective_end = apply_buffer_to_time(
            self.session_end, self.end_buffer, is_start=False)
        
        # Simple comparison
        return current_time.time() >= effective_end

# Configuration conventions should live in the module docstring at the top or in README.
