import logging
from sqlalchemy.orm import Session
from app.repositories.price_repo import PriceRepository
from app.repositories.stock_repo import StockRepository
from app.models.models import CorporateAction, PriceDaily
import datetime
from typing import List

logger = logging.getLogger(__name__)

class CorporateActionsService:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.price_repo = PriceRepository(db)

    def record_corporate_action(self, symbol: str, action_type: str, execution_date: datetime.date, 
                                ratio_from: float = None, ratio_to: float = None, amount: float = None) -> CorporateAction:
        """
        Record a corporate action for a stock to be processed.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            raise ValueError(f"Stock {symbol} not found")

        action = CorporateAction(
            stock_id=stock.id,
            action_type=action_type.upper(),
            execution_date=execution_date,
            ratio_from=ratio_from,
            ratio_to=ratio_to,
            amount=amount,
            is_applied=False
        )
        self.db.add(action)
        self.db.commit()
        self.db.refresh(action)
        return action

    def apply_pending_actions(self) -> int:
        """
        Load all unapplied corporate actions, adjust historical price data, and mark them applied.
        """
        pending = self.db.query(CorporateAction).filter(CorporateAction.is_applied == False).order_by(CorporateAction.execution_date.asc()).all()
        applied_count = 0

        for action in pending:
            try:
                success = self._apply_action(action)
                if success:
                    action.is_applied = True
                    self.db.add(action)
                    applied_count += 1
            except Exception as e:
                logger.error(f"Failed to apply corporate action {action.id} (Type: {action.action_type}): {e}")

        if applied_count > 0:
            self.db.commit()
            logger.info(f"Applied {applied_count} corporate actions successfully.")
        return applied_count

    def _apply_action(self, action: CorporateAction) -> bool:
        stock_id = action.stock_id
        ex_date = action.execution_date
        
        # Load all daily prices prior to execution date
        historical_prices = self.db.query(PriceDaily).filter(
            PriceDaily.stock_id == stock_id,
            PriceDaily.timestamp < ex_date
        ).all()

        if not historical_prices:
            logger.info(f"No historical prices to adjust for action {action.id} on stock {stock_id}")
            return True

        if action.action_type == "SPLIT" or action.action_type == "BONUS":
            # Ratio semantics: ratio_from = OLD share count, ratio_to = NEW share count.
            # e.g. a 1:2 split/bonus => ratio_from = 1, ratio_to = 2 (1 old share becomes 2 new shares).
            # Value is conserved: old_qty * old_price == new_qty * new_price.
            # To make HISTORICAL prices comparable with post-event prices:
            #   price_factor = ratio_from / ratio_to  (1/2 = 0.5  -> historical prices scaled down)
            #   vol_factor   = ratio_to / ratio_from  (2/1 = 2.0  -> historical volumes scaled up)
            r_from = float(action.ratio_from)
            r_to = float(action.ratio_to)
            
            if r_from <= 0 or r_to <= 0:
                logger.error(f"Invalid split ratio values for action {action.id}: {r_from} -> {r_to}")
                return False
                
            price_factor = r_from / r_to
            vol_factor = r_to / r_from
            
            logger.info(f"Applying split adjustment factor {price_factor} to {len(historical_prices)} rows.")
            for price in historical_prices:
                price.open = float(price.open) * price_factor
                price.high = float(price.high) * price_factor
                price.low = float(price.low) * price_factor
                price.close = float(price.close) * price_factor
                if price.adj_close is not None:
                    price.adj_close = float(price.adj_close) * price_factor
                price.volume = int(price.volume * vol_factor)
                self.db.add(price)

        elif action.action_type == "DIVIDEND":
            # Dividend adjustment: adjust historical adj_close only
            # Factor = 1.0 - (dividend_amount / close_on_ex_date)
            # Let's find close on the ex-date or closest day after it
            ex_price = self.db.query(PriceDaily).filter(
                PriceDaily.stock_id == stock_id,
                PriceDaily.timestamp >= ex_date
            ).order_by(PriceDaily.timestamp.asc()).first()

            if not ex_price:
                logger.error(f"Cannot find close price on or after ex-date {ex_date} for dividend adjustment.")
                return False

            div_amount = float(action.amount)
            close_val = float(ex_price.close)
            if close_val <= 0:
                return False
                
            factor = 1.0 - (div_amount / close_val)
            if factor <= 0 or factor >= 1.0:
                logger.warning(f"Abnormal dividend adjustment factor: {factor}. Skipping.")
                return False

            logger.info(f"Applying dividend adjustment factor {factor} to {len(historical_prices)} rows.")
            for price in historical_prices:
                if price.adj_close is not None:
                    price.adj_close = float(price.adj_close) * factor
                    self.db.add(price)
                
        else:
            logger.warning(f"Unknown corporate action type: {action.action_type}")
            return False

        return True
