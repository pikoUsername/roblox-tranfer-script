import asyncio
import functools
import json
import ssl
import time
from enum import Enum
from typing import Dict
from typing import Optional

from loguru import logger
from pydantic import BaseModel, validator

import pika
from pika.exceptions import AMQPConnectionError

from app.settings import Settings


def sync(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.get_event_loop().run_until_complete(f(*args, **kwargs))

    return wrapper


class Priority(Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 10


class Headers(BaseModel):
    job_id: str
    priority: Priority
    task_type: Optional[str] = None

    @validator("priority", pre=True)
    def _convert_priority(cls, value):
        return Priority[value]


class BasicPikaClient:
    def __init__(self, url: str, queue: str, exchange: str, routing: str):
        self.amqp_url = url

        self.queue = queue
        self.exchange = exchange
        self.routing = routing

    def setup(self):
        logger.info(f"Declaring exchange: {self.exchange}")
        logger.info(f"Declaring queue: {self.queue}")

        self.declare_exchange(self.exchange)
        self.declare_queue(self.queue)

    def connect(self):
        self._init_connection_parameters()
        self._connect()
        self.setup()

    def _connect(self):
        tries = 0
        while True:
            try:
                self.connection = pika.BlockingConnection(self.parameters)
                self.channel = self.connection.channel()
                if self.connection.is_open:
                    logger.info("Channel is open")
                    break
            except (AMQPConnectionError, Exception) as e:
                time.sleep(5)
                tries += 1
                logger.info(f"Trying to reconnect to RabbitMQ server, tries # {tries}")
                if tries == 20:
                    raise AMQPConnectionError(e)

    def _init_connection_parameters(self):
        self.parameters = pika.URLParameters(self.amqp_url)
        if self.amqp_url.startswith("amqps"):
            # SSL Context for TLS configuration of Amazon MQ for RabbitMQ
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            ssl_context.set_ciphers("ECDHE+AESGCM:!ECDSA")
            self.parameters._ssl_options = pika.SSLOptions(context=ssl_context)

    def check_connection(self):
        if not self.connection or self.connection.is_closed:
            self.connect()

    def close(self):
        if not self.channel.is_closed:
            self.channel.close()
        if not self.connection.is_closed:
            self.connection.close()

        logger.info("Sender connection closed")

    def declare_queue(
        self, queue_name, exclusive: bool = False
    ):
        self.check_connection()
        logger.debug(f"Trying to declare queue({queue_name})...")
        self.channel.queue_declare(
            queue=queue_name,
            exclusive=exclusive,
            durable=True,
            auto_delete=False,
            passive=False,
        )

    def declare_exchange(self, exchange_name: str, exchange_type: str = "direct"):
        self.check_connection()
        self.channel.exchange_declare(
            exchange=exchange_name,
            exchange_type=exchange_type,
            durable=True,
            auto_delete=False,
            passive=False,
        )

    def bind_queue(self, exchange_name: str, queue_name: str, routing_key: str):
        self.check_connection()
        self.channel.queue_bind(
            exchange=exchange_name, queue=queue_name, routing_key=routing_key
        )

    def unbind_queue(self, exchange_name: str, queue_name: str, routing_key: str):
        self.channel.queue_unbind(
            queue=queue_name, exchange=exchange_name, routing_key=routing_key
        )


class BasicMessageSender(BasicPikaClient):
    def send_message(
        self,
        body: Dict,
        headers: Optional[Headers] = None,
        exchange_name: str = None,
        routing_key: str = None,
    ):
        if not exchange_name:
            exchange_name = self.exchange
        if not routing_key:
            routing_key = self.routing
        body = bytes(json.dumps(body), 'utf8')
        if self.channel.is_open:
            self.channel.basic_publish(
                exchange=exchange_name,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE,
                    priority=headers.priority.value if headers else None,
                    headers=headers.dict() if headers else None,
                    content_type="application/json",
                ),
            )
            logger.info(
                f"Sent message. Exchange: {exchange_name}, Routing Key: {routing_key}, Body: {body[:128]}"
            )
        else:
            self.check_connection()
            logger.error("RETURN CHANNEL UNEXPECTEDLY CLOSED BY PEER, TRY TO INCREASE HEARTBEAT")
