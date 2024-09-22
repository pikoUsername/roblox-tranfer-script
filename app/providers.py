from loguru import logger

from app.settings import Settings
from app.repos import TokenRepository
from app.services.interfaces import BasicDBConnector
from app.services.queue.publisher import BasicMessageSender


async def get_token_service(settings: Settings, connection: BasicDBConnector) -> TokenRepository:
	token_service = TokenRepository(connection, settings.db_tokens_table)

	await token_service.create_tokens_table()

	return token_service


def get_publisher(settings: Settings):
	logger.info("Setting up basicMessageSender")

	publisher = BasicMessageSender(
		settings.queue_dsn,
		queue=settings.send_queue_name,
		exchange=settings.send_queue_exchange_name,
		routing=settings.send_queue_name,
	)

	publisher.connect()
	logger.info("Connection to publisher has been established")

	return publisher
