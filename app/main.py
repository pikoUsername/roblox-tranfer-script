from aiohttp import ClientSession
from dotenv import load_dotenv
from loguru import logger

from app.browser import auth_browser
from app.providers import get_token_service, get_publisher
from app.services.db import get_db_conn
from app.services.driver import get_driver, convert_browser_cookies_to_aiohttp
from app.services.queue.consumers import URLConsumer
from app.log import configure_logging
from app.settings import get_settings
from app import handlers
from app.services.queue.consumers import ReconnectingURLConsumer

import nest_asyncio

nest_asyncio.apply()


async def main():
    load_dotenv()

    settings = get_settings()

    configure_logging(settings.loggers)
    connection = await get_db_conn(settings.db_dsn)
    token_service = await get_token_service(settings, connection)
    driver = get_driver(settings)
    publisher = get_publisher(settings)

    await auth_browser(driver, token_service)

    cookies = convert_browser_cookies_to_aiohttp(driver.get_cookies())
    session = ClientSession(cookies=cookies)

    workflow_data = {
        "settings": settings,
        "connection": connection,
        "driver": driver,
        "token_service": token_service,
        "session": session,
        "publisher": publisher,
    }
    # ссанина
    kw = {
        "amqp_url": settings.queue_dsn,
        "queue": settings.queue_name,
        "exchange": settings.exchange_name,
        "routing": settings.queue_name,
        "workflow_data": workflow_data
    }
    root_consumer = URLConsumer(**kw)
    # root_consumer = MultiThreadedConsumer(**kw)
    consumer = ReconnectingURLConsumer(
        consumer=root_consumer,
        **kw,
    )

    root_consumer.add_listener(handlers.DataHandler())
    root_consumer.add_listener(handlers.UrlHandler())
    root_consumer.add_listener(handlers.ReturnSignalHandler())

    logger.info("Starting application")

    try:
        consumer.run()
    except:
        driver.close()
        await connection.close()
        await session.close()
