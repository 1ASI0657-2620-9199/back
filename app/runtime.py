"""Development transport: durable outboxes, asynchronous local delivery.

Service repositories remain independent. Replace this transport with a broker
adapter in the Docker increment; this module is not Kafka or RabbitMQ.
"""
import asyncio
import logging
import threading

from .eventing import Outbox

logger = logging.getLogger(__name__)


class LocalEventDispatcher:
    def __init__(self, financial, budgets, notifications, devices):
        self.notifications = notifications
        self.sources = [
            ("financial", Outbox(financial.database)),
            ("budgets", Outbox(budgets.database)),
            ("devices", Outbox(devices.database)),
            ("notifications", Outbox(notifications.database)),
        ]
        self.handlers = {
            "TransactionRecorded": budgets.consume,
            "BudgetThresholdReached": notifications.consume,
            "ConsumptionReadingNormalized": notifications.consume,
            "NotificationDelivered": lambda event: None,
            "NotificationFailed": lambda event: None,
        }
        self.lock = threading.Lock()

    def dispatch_once(self) -> int:
        count = 0
        with self.lock:
            for name, outbox in self.sources:
                for event in outbox.pending():
                    try:
                        self.handlers[event.event_type](event)
                    except Exception as error:
                        logger.warning("Event delivery failed: source=%s type=%s error=%s",
                                       name, event.event_type, type(error).__name__)
                        outbox.failed(event.event_id, error)
                    else:
                        outbox.delivered(event.event_id)
                        count += 1
            self.notifications.deliver_pending()
        return count

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.dispatch_once)
            except Exception:
                logger.exception("Local event worker will retry after an unexpected error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.4)
            except asyncio.TimeoutError:
                pass

    def status(self):
        return {"transport": "local-durable-outbox", "sources": {name: source.counts() for name, source in self.sources}}
