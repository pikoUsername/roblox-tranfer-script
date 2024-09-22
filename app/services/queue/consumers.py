import abc
import asyncio
import contextvars
import functools
import threading
import time
from multiprocessing.pool import ThreadPool, CLOSE
from typing import Union, List, Callable, Any

import pika
from pika.adapters.asyncio_connection import AsyncioConnection
from pika.exceptions import StreamLostError
from pika.exchange_type import ExchangeType
from loguru import logger

from app.services.interfaces import ListenerType, BasicConsumer
from app.services.helpers import run_listeners


DEFAULT_THREADS_COUNT = 1


# COPY PASTE FROM https://github.com/pika/pika/blob/main/examples/asyncio_consumer_example.py
class ExampleConsumer(BasicConsumer):
    """This is an example consumer that will handle unexpected interactions
    with RabbitMQ such as channel and connection closures.

    If RabbitMQ closes the connection, this class will stop and indicate
    that reconnection is necessary. You should look at the output, as
    there are limited reasons why the connection may be closed, which
    usually are tied to permission related issues or socket timeouts.

    If the channel is closed, it will indicate a problem with one of the
    commands that were issued and that should surface in the output as well.

    """
    # EXCHANGE = 'message'
    EXCHANGE_TYPE = ExchangeType.direct
    # QUEUE = 'text'
    # ROUTING_KEY = 'example.text'

    def __init__(self, amqp_url, exchange: str, queue: str, routing: str):
        """Create a new instance of the consumer class, passing in the AMQP
        URL used to connect to RabbitMQ.

        :param str amqp_url: The AMQP url to connect with

        """
        self.ROUTING_KEY = routing
        self.EXCHANGE = exchange
        self.QUEUE = queue

        self.should_reconnect = False
        self.was_consuming = False

        self._connection = None
        self._channel = None
        self._closing = False
        self._consumer_tag = None
        self._url = amqp_url
        self._consuming = False
        # In production, experiment with higher prefetch values
        # for higher consumer throughput
        self._prefetch_count = 1

    def connect(self):
        """This method connects to RabbitMQ, returning the connection handle.
        When the connection is established, the on_connection_open method
        will be invoked by pika.

        :rtype: pika.adapters.asyncio_connection.AsyncioConnection

        """
        logger.info(f'Connecting to {self._url}')
        return AsyncioConnection(
            parameters=pika.URLParameters(self._url),
            on_open_callback=self.on_connection_open,
            on_open_error_callback=self.on_connection_open_error,
            on_close_callback=self.on_connection_closed)

    def close_connection(self):
        self._consuming = False
        if self._connection.is_closing or self._connection.is_closed:
            logger.info('Connection is closing or already closed')
        else:
            logger.info('Closing connection')
            self._connection.close()

    def on_connection_open(self, _unused_connection):
        """This method is called by pika once the connection to RabbitMQ has
        been established. It passes the handle to the connection object in
        case we need it, but in this case, we'll just mark it unused.

        :param pika.adapters.asyncio_connection.AsyncioConnection _unused_connection:
           The connection

        """
        logger.info('Connection opened')
        self.open_channel()

    def on_connection_open_error(self, _unused_connection, err):
        """This method is called by pika if the connection to RabbitMQ
        can't be established.

        :param pika.adapters.asyncio_connection.AsyncioConnection _unused_connection:
           The connection
        :param Exception err: The error

        """
        logger.error(f'Connection open failed: {err}')
        self.reconnect()

    def on_connection_closed(self, _unused_connection, reason):
        """This method is invoked by pika when the connection to RabbitMQ is
        closed unexpectedly. Since it is unexpected, we will reconnect to
        RabbitMQ if it disconnects.

        :param pika.connection.Connection connection: The closed connection obj
        :param Exception reason: exception representing reason for loss of
            connection.

        """
        self._channel = None
        if self._closing:
            self._connection.ioloop.stop()
        else:
            logger.warning(f'Connection closed, reconnect necessary: {reason}')
            self.reconnect()

    def reconnect(self):
        """Will be invoked if the connection can't be opened or is
        closed. Indicates that a reconnect is necessary then stops the
        ioloop.

        """
        self.should_reconnect = True
        self.stop()

    def open_channel(self):
        """Open a new channel with RabbitMQ by issuing the Channel.Open RPC
        command. When RabbitMQ responds that the channel is open, the
        on_channel_open callback will be invoked by pika.

        """
        logger.info('Creating a new channel')
        self._connection.channel(on_open_callback=self.on_channel_open)

    def on_channel_open(self, channel):
        """This method is invoked by pika when the channel has been opened.
        The channel object is passed in so we can make use of it.

        Since the channel is now open, we'll declare the exchange to use.

        :param pika.channel.Channel channel: The channel object

        """
        logger.info('Channel opened')
        self._channel = channel
        self.add_on_channel_close_callback()
        self.setup_exchange(self.EXCHANGE)

    def add_on_channel_close_callback(self):
        """This method tells pika to call the on_channel_closed method if
        RabbitMQ unexpectedly closes the channel.

        """
        logger.info('Adding channel close callback')
        self._channel.add_on_close_callback(self.on_channel_closed)

    def on_channel_closed(self, channel, reason):
        """Invoked by pika when RabbitMQ unexpectedly closes the channel.
        Channels are usually closed if you attempt to do something that
        violates the protocol, such as re-declare an exchange or queue with
        different parameters. In this case, we'll close the connection
        to shutdown the object.

        :param pika.channel.Channel: The closed channel
        :param Exception reason: why the channel was closed

        """
        logger.warning(f'Channel {channel} was closed: {reason}')
        self.close_connection()

    def setup_exchange(self, exchange_name):
        """Setup the exchange on RabbitMQ by invoking the Exchange.Declare RPC
        command. When it is complete, the on_exchange_declareok method will
        be invoked by pika.

        :param str|unicode exchange_name: The name of the exchange to declare

        """
        logger.info(f'Declaring exchange: {exchange_name}')
        # Note: using functools.partial is not required, it is demonstrating
        # how arbitrary data can be passed to the callback when it is called
        cb = functools.partial(
            self.on_exchange_declareok, userdata=exchange_name)
        self._channel.exchange_declare(
            exchange=exchange_name,
            exchange_type=self.EXCHANGE_TYPE,
            callback=cb,
            durable=True,
            auto_delete=False,
            passive=False,
        )

    def on_exchange_declareok(self, _unused_frame, userdata):
        """Invoked by pika when RabbitMQ has finished the Exchange.Declare RPC
        command.

        :param pika.Frame.Method unused_frame: Exchange.DeclareOk response frame
        :param str|unicode userdata: Extra user data (exchange name)

        """
        logger.info(f'Exchange declared: {userdata}')
        self.setup_queue(self.QUEUE)

    def setup_queue(self, queue_name):
        """Setup the queue on RabbitMQ by invoking the Queue.Declare RPC
        command. When it is complete, the on_queue_declareok method will
        be invoked by pika.

        :param str|unicode queue_name: The name of the queue to declare.

        """
        logger.info(f'Declaring queue {queue_name}')
        cb = functools.partial(self.on_queue_declareok, userdata=queue_name)
        self._channel.queue_declare(queue=queue_name, callback=cb)

    def on_queue_declareok(self, _unused_frame, userdata):
        """Method invoked by pika when the Queue.Declare RPC call made in
        setup_queue has completed. In this method we will bind the queue
        and exchange together with the routing key by issuing the Queue.Bind
        RPC command. When this command is complete, the on_bindok method will
        be invoked by pika.

        :param pika.frame.Method _unused_frame: The Queue.DeclareOk frame
        :param str|unicode userdata: Extra user data (queue name)

        """
        queue_name = userdata
        logger.info(f'Binding {self.EXCHANGE} to {queue_name} with {self.ROUTING_KEY}')
        cb = functools.partial(self.on_bindok, userdata=queue_name)
        self._channel.queue_bind(
            queue_name,
            self.EXCHANGE,
            routing_key=self.ROUTING_KEY,
            callback=cb)

    def on_bindok(self, _unused_frame, userdata):
        """Invoked by pika when the Queue.Bind method has completed. At this
        point we will set the prefetch count for the channel.

        :param pika.frame.Method _unused_frame: The Queue.BindOk response frame
        :param str|unicode userdata: Extra user data (queue name)

        """
        logger.info(f'Queue bound: {userdata}')
        self.set_qos()

    def set_qos(self):
        """This method sets up the consumer prefetch to only be delivered
        one message at a time. The consumer must acknowledge this message
        before RabbitMQ will deliver another one. You should experiment
        with different prefetch values to achieve desired performance.

        """
        self._channel.basic_qos(
            prefetch_count=self._prefetch_count, callback=self.on_basic_qos_ok)

    def on_basic_qos_ok(self, _unused_frame):
        """Invoked by pika when the Basic.QoS method has completed. At this
        point we will start consuming messages by calling start_consuming
        which will invoke the needed RPC commands to start the process.

        :param pika.frame.Method _unused_frame: The Basic.QosOk response frame

        """
        logger.info(f'QOS set to: {self._prefetch_count}')
        self.start_consuming()

    def start_consuming(self):
        """This method sets up the consumer by first calling
        add_on_cancel_callback so that the object is notified if RabbitMQ
        cancels the consumer. It then issues the Basic.Consume RPC command
        which returns the consumer tag that is used to uniquely identify the
        consumer with RabbitMQ. We keep the value to use it when we want to
        cancel consuming. The on_message method is passed in as a callback pika
        will invoke when a message is fully received.

        """
        logger.info('Issuing consumer related RPC commands')
        self.add_on_cancel_callback()
        self._consumer_tag = self._channel.basic_consume(
            self.QUEUE, self.on_message)
        self.was_consuming = True
        self._consuming = True

    def add_on_cancel_callback(self):
        """Add a callback that will be invoked if RabbitMQ cancels the consumer
        for some reason. If RabbitMQ does cancel the consumer,
        on_consumer_cancelled will be invoked by pika.

        """
        logger.info('Adding consumer cancellation callback')
        self._channel.add_on_cancel_callback(self.on_consumer_cancelled)

    def on_consumer_cancelled(self, method_frame):
        """Invoked by pika when RabbitMQ sends a Basic.Cancel for a consumer
        receiving messages.

        :param pika.frame.Method method_frame: The Basic.Cancel frame

        """
        logger.info('Consumer was cancelled remotely, shutting down: %r',
                    method_frame)
        if self._channel:
            self._channel.close()

    def on_message(self, _unused_channel, basic_deliver, properties, body):
        """Invoked by pika when a message is delivered from RabbitMQ. The
        channel is passed for your convenience. The basic_deliver object that
        is passed in carries the exchange, routing key, delivery tag and
        a redelivered flag for the message. The properties passed in is an
        instance of BasicProperties with the message properties and the body
        is the message that was sent.

        :param pika.channel.Channel _unused_channel: The channel object
        :param pika.Spec.Basic.Deliver: basic_deliver method
        :param pika.Spec.BasicProperties: properties
        :param bytes body: The message body

        """
        logger.info(
            f'Received message # {basic_deliver.delivery_tag} from {properties.app_id}: {body}',
        )
        self.handle_message(body)
        self.acknowledge_message(basic_deliver.delivery_tag)

    @abc.abstractmethod
    def handle_message(self, body: Union[bytes, str]) -> None:
        pass

    def acknowledge_message(self, delivery_tag):
        """Acknowledge the message delivery from RabbitMQ by sending a
        Basic.Ack RPC method for the delivery tag.

        :param int delivery_tag: The delivery tag from the Basic.Deliver frame

        """
        logger.info(f'Acknowledging message {delivery_tag}', )
        self._channel.basic_ack(delivery_tag)

    def stop_consuming(self):
        """Tell RabbitMQ that you would like to stop consuming by sending the
        Basic.Cancel RPC command.

        """
        if self._channel:
            logger.info('Sending a Basic.Cancel RPC command to RabbitMQ')
            cb = functools.partial(
                self.on_cancelok, userdata=self._consumer_tag)
            self._channel.basic_cancel(self._consumer_tag, cb)

    def on_cancelok(self, _unused_frame, userdata):
        """This method is invoked by pika when RabbitMQ acknowledges the
        cancellation of a consumer. At this point we will close the channel.
        This will invoke the on_channel_closed method once the channel has been
        closed, which will in-turn close the connection.

        :param pika.frame.Method _unused_frame: The Basic.CancelOk frame
        :param str|unicode userdata: Extra user data (consumer tag)

        """
        self._consuming = False
        logger.info(
            'RabbitMQ acknowledged the cancellation of the consumer: %s',
            userdata)
        self.close_channel()

    def close_channel(self):
        """Call to close the channel with RabbitMQ cleanly by issuing the
        Channel.Close RPC command.

        """
        logger.info('Closing the channel')
        self._channel.close()

    def run(self):
        """Run the example consumer by connecting to RabbitMQ and then
        starting the IOLoop to block and allow the AsyncioConnection to operate.

        """
        self._connection = self.connect()
        self._connection.ioloop.run_forever()

    def stop(self):
        """Cleanly shutdown the connection to RabbitMQ by stopping the consumer
        with RabbitMQ. When RabbitMQ confirms the cancellation, on_cancelok
        will be invoked by pika, which will then closing the channel and
        connection. The IOLoop is started again because this method is invoked
        when CTRL-C is pressed raising a KeyboardInterrupt exception. This
        exception stops the IOLoop which needs to be running for pika to
        communicate with RabbitMQ. All of the commands issued prior to starting
        the IOLoop will be buffered but not processed.

        """
        if not self._closing:
            self._closing = True
            logger.info('Stopping...')
            if self._consuming:
                self.stop_consuming()
                self._connection.ioloop.run_forever()
            else:
                self._connection.ioloop.stop()
            logger.info('Stopped')


class URLConsumer(ExampleConsumer):
    """
    Не умеет работать в многопоточном режиме.
    Является простым обработчиком оберткой для UrlHandler-а
    """
    _saved_data: dict

    def __init__(self, *args, **kwargs):
        if not hasattr(self.__class__, "_saved_data"):
            self.__class__._saved_data = {}

        saved_data = self.__class__._saved_data

        self._listeners: List[ListenerType] = saved_data.get(
            "listeners", []
        )
        self._error_listeners: List[Callable] = saved_data.get(
            "error_listeners", []
        )
        data = kwargs.pop("workflow_data", {})
        if saved_data:
            self.workflow_data = saved_data["workflow_data"]
            self.workflow_data.update(data)
        else:
            self.workflow_data = data

        self.workflow_data.update(data=self.workflow_data)

        super().__init__(*args, **kwargs)

    def emit_startup(self, workflow: dict):
        run_listeners(workflow, self._listeners, "setup")

    def emit_shutdown(self, workflow: dict):
        run_listeners(workflow, self._listeners, "close")

    def handle_message(self, body: Union[bytes, str]) -> None:

        logger.info(f"Handling body, with body: {body}")

        self.workflow_data.update(body=body)

        run_listeners(data=self.workflow_data, listeners=self._listeners)

    def close_connection(self):
        self.emit_shutdown(self.workflow_data)

        super().close_connection()

    def run(self):
        self.emit_startup(self.workflow_data)

        super().run()

    def add_listener(self, listener: ListenerType):
        self._listeners.append(listener)

    def reconnect(self):
        self.__class__._saved_data = {
            "listeners": self._listeners,
            "error_listeners": self._error_listeners,
            "workflow_data": self.workflow_data,
        }
        super().reconnect()


class MultiThreadedConsumer(URLConsumer):
    """
    Является паралельной Версией URLConsumer,
    которая имеет возможность поддерживать несколько тредов.

    Каждый тред имеет свой workflow_data который можно использвать
    для того что бы туда засунуть driver и использвать несколько
    браузеров одновременно(ну там GIL не будет проблемой в основном).

    TODO
    """
    _thread_pool_save: ThreadPool = None

    def __init__(self, *args, **kwargs):
        self._threads_count = kwargs.pop("threads_count", DEFAULT_THREADS_COUNT)

        self._local = threading.local()
        self.default_workflow_data = kwargs.get("workflow_data", {})

        super().__init__(*args, **kwargs)

        self.workflow_data = contextvars.ContextVar(
            "workflow_data"
        )

    def create_pool(self) -> ThreadPool:
        return ThreadPool(
            self._threads_count,
            initializer=self.setup_thread,
            initargs=(self._local, self.workflow_data, self.default_workflow_data, self._listeners)
        )

    def emit_startup(self, workflow: dict):
        if not self._thread_pool_save or self._thread_pool_save._state == CLOSE:
            logger.info("Initializing thread pool")
            self._thread_pool_save = self.create_pool()

    @staticmethod
    def setup_thread(local, workflow_data: contextvars.ContextVar, default_data: dict, listeners: List[ListenerType]):
        # https://ru.stackoverflow.com/questions/787715/runtimeerror-there-is-no-current-event-loop-in-thread-thread-2
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        logger.info(f"Executing in {threading.get_ident()}")

        data = {}
        if default_data:
            data.update(default_data)
        data.update(data=data)

        run_listeners(data, listeners, "setup")

        workflow_data.set(data)

        local.listeners = listeners
        local.workflow_data = workflow_data

    def submit_to_all_threads(self, func, value, chunk_size=None) -> List[Any]:
        """
        Вызвает переданную функцию в каждом потоке,
        копируя значения из value, либо передавая его по ссылке.

        * Следует обратить внимание что value должен быть
          либо: local, или ContextVar, либо по другому может возникнуть
          ошибка race condition которое сложно дебажить

        :param func:
        :param value:
        :param chunk_size:
        :return:
        """
        return self._thread_pool_save.map(
            func, [value for _ in range(self._threads_count)], chunk_size
        )

    def emit_shutdown(self, workflow: dict):
        if self._thread_pool_save:
            if self._thread_pool_save._state == CLOSE:
                return
            logger.info("closing all threads")

            # then it will apply to all threads, in hope they will accept it
            # naive approach, but I wasn't able to find better solution.
            self.submit_to_all_threads(self._close_thread, self._local)

            self._thread_pool_save.close()

    @staticmethod
    def _close_thread(local):
        data = local.workflow_data.get()
        listeners = local.listeners

        run_listeners(data, listeners, "close")

    @staticmethod
    def handle_message_in_thread(local, body):
        logger.info(f"Handling in {threading.get_ident()} Thread")

        data = local.workflow_data.get()

        data.update(body=body)
        run_listeners(data, local.listeners)

    def handle_message(self, body: Union[bytes, str]) -> None:
        self._thread_pool_save.apply(
            self.handle_message_in_thread,
            (self._local, body),
        )


class ReconnectingURLConsumer:
    """This is an example consumer that will reconnect if the nested
    ExampleConsumer indicates that a reconnect is necessary.

    """

    def __init__(self, amqp_url: str, consumer: ExampleConsumer, **kwargs):
        self._reconnect_delay = 0
        self._amqp_url = amqp_url
        self._consumer = consumer
        self._kwargs = kwargs
        self._consumer_type = type(consumer)
        self.tries = 0
        self.max_tries = 2

    def run(self):
        while True:
            try:
                self._consumer.run()
            except StreamLostError:
                logger.error("Stream lost error, trying to reconnect!")

                if self.tries > self.max_tries:
                    self._consumer.stop()
                    break

                self._maybe_reconnect()

                self.tries += 1
            finally:
                self._consumer.stop()
                break

    def _maybe_reconnect(self):
        if self._consumer.should_reconnect:
            self._consumer.stop()
            reconnect_delay = self._get_reconnect_delay()
            logger.info(f'Reconnecting after {reconnect_delay} seconds')
            time.sleep(reconnect_delay)
            self._consumer = self._consumer_type(self._amqp_url, **self._kwargs)

    def _get_reconnect_delay(self):
        if self._consumer.was_consuming:
            self._reconnect_delay = 1
        else:
            self._reconnect_delay += 1
        if self._reconnect_delay > 30:
            self._reconnect_delay = 30
        return self._reconnect_delay
